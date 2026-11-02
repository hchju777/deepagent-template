"""운영용 — `/internal/v1/ops/...`: 작업, 스케줄, 리포트, 메일, 출력 파일."""
import mimetypes
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from src.presentation.api.context import ApiContext
from src.presentation.api.jobs import REPORT_S, SHORT_S, Job
from src.presentation.api.models import (WAIT_MAX_S, MailSendRequest, ReportAggregateRequest,
                                         ReportResendRequest, ReportRunRequest, ScenarioQuery,
                                         ScheduleListRequest, Wait)
from src.presentation.api.sent import ledger_view, pick_scenario, report_day, today_status
from src.report import ledger
from src.report.publish import report_path, subject_for_day


async def start(ctx: ApiContext, argv: list[str], *, timeout_s: float, wait_s: float,
                job_id: str | None = None, on_done=None) -> JSONResponse:
    job, busy = ctx.runner.submit(argv, timeout_s=timeout_s, job_id=job_id, on_done=on_done)
    return await respond(ctx, job, busy, wait_s=wait_s)


async def respond(ctx: ApiContext, job: Job | None, busy: Job | None, *, wait_s: float) -> JSONResponse:
    """200(끝남) · 202(아직 — `/jobs/{id}`로 본다) · 409(다른 작업이 돌고 있다 — 그 작업을 같이 준다)."""
    if busy is not None:
        return JSONResponse({"detail": "다른 작업이 돌고 있다 — 끝난 뒤 다시", "running": busy.view()},
                            status_code=409)
    await ctx.runner.wait(job, wait_s)
    return JSONResponse(job.view(), status_code=200 if job.finished else 202)


def _summary(job: Job) -> dict:
    view = job.view()
    del view["stdout"], view["stderr"]
    return view


def _scenario_today(req: ReportAggregateRequest) -> list[str]:
    argv = []
    if req.scenario:
        argv += ["--scenario", req.scenario]
    if req.today:
        argv += ["--today", req.today.isoformat()]
    return argv


def resolve_output(directory: Path, name: str) -> Path | None:
    """output 폴더 **안의** 파일만. `..`·절대 경로·밖을 가리키는 심볼릭 링크는 None — 경로를 다 풀고 나서 본다."""
    base = directory.resolve()
    try:
        target = (base / name).resolve()
    except (OSError, RuntimeError):
        return None
    if not target.is_relative_to(base) or target == base:
        return None
    return target


def ops_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs", tags=["작업"], summary="최근 작업(새것부터, 출력 없이)")
    async def jobs():
        running = ctx.runner.running()
        return {"running": running.id if running else None,
                "jobs": [_summary(job) for job in ctx.runner.recent()]}

    @router.get("/jobs/{job_id}", tags=["작업"], summary="작업 하나 — wait_s만큼 끝나기를 기다린다")
    async def job(job_id: str, wait_s: float = Query(default=0.0, ge=0, le=WAIT_MAX_S)):
        found = ctx.runner.get(job_id)
        if found is None:
            return JSONResponse({"detail": f"모르는 작업 — {job_id} (최근 50개만 남는다)"}, status_code=404)
        await ctx.runner.wait(found, wait_s)
        return found.view()

    def schedule_view() -> dict:
        """스케줄러의 보기에 시나리오마다 `today`(오늘 기준일 장부·슬롯·건너뜀)를 붙인다."""
        view = ctx.scheduler.view()
        scenarios = ctx.scheduler.scenarios
        now = ctx.clock()
        for row in view["scenarios"]:
            try:
                row["today"] = today_status(ctx.output_dir(), row["name"], scenarios[row["name"]], now=now,
                                            last=row["last"], firing=ctx.scheduler.firing(row["name"]),
                                            paused=view["paused"])
            except Exception as exc:                               # noqa: BLE001
                row["today"] = {"error": f"{type(exc).__name__}: {exc}"}
        return view

    @router.get("/schedule", tags=["스케줄"],
                summary="앱 안 스케줄 — 켜짐·cron·다음 시각·최근 결과·오늘 기준일 장부(today.missed: 슬롯을 건너뛰었다)")
    async def schedule():
        return schedule_view()

    @router.post("/schedule/pause", tags=["스케줄"], summary="일시정지(메모리 — 재시작하면 다시 켜진다)")
    async def schedule_pause():
        ctx.scheduler.pause()
        return schedule_view()

    @router.post("/schedule/resume", tags=["스케줄"], summary="일시정지를 푼다")
    async def schedule_resume():
        ctx.scheduler.resume()
        return schedule_view()

    @router.post("/schedule/list", tags=["스케줄"], summary="`schedule --list` — 무엇이 언제 도는가")
    async def schedule_list(req: Annotated[ScheduleListRequest, Query()]):
        argv = ["schedule", "--list"] + (["--scenario", req.scenario] if req.scenario else [])
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.post("/report/aggregate", tags=["리포트"], summary="`report aggregate` — 읽어서 숫자만(1800초)")
    async def report_aggregate(req: Annotated[ReportAggregateRequest, Query()]):
        return await start(ctx, ["report", "aggregate", *_scenario_today(req)],
                           timeout_s=REPORT_S, wait_s=req.wait_s)

    @router.post("/report/run", tags=["리포트"],
                 summary="`report run` — 집계·서술·파일·메일(dry_run 기본 true, 1800초). 장부가 있으면 409(force로 덮는다)")
    async def report_run(req: Annotated[ReportRunRequest, Query()]):
        if not (req.dry_run or req.no_mail or req.force):
            # CLI도 같은 장부를 보고 멈춘다(종료 코드 4) — 여기서 먼저 보는 것은 30분짜리 작업을 띄우지 않고 바로 답하려고.
            picked, _ = pick_scenario(ctx.config_root, req.scenario)
            if picked is not None:
                name, scenario = picked
                record = ledger_view(ctx.output_dir(), name, report_day(scenario, req.today or ctx.clock().date()))
                if record is not None:
                    return JSONResponse({"detail": "이미 보냈다 — force로 덮어쓴다", "ledger": record}, status_code=409)
        job_id = ctx.runner.new_id()
        argv = ["report", "run", *_scenario_today(req)]
        if req.dry_run:
            argv.append("--dry-run")
        if req.no_mail:
            argv.append("--no-mail")
        if req.force:
            argv.append("--force")
        argv += ["--trigger", "manual", "--job-id", job_id]
        return await start(ctx, argv, timeout_s=REPORT_S, wait_s=req.wait_s, job_id=job_id)

    @router.post("/report/resend", tags=["리포트"],
                 summary="그날 HTML을 그대로 다시 보낸다(집계·LLM 없이 `mail send --file`). 장부가 있으면 409(force로 덮는다)")
    async def report_resend(req: Annotated[ReportResendRequest, Query()]):
        picked, why = pick_scenario(ctx.config_root, req.scenario)
        if picked is None:
            return JSONResponse({"detail": why}, status_code=400)
        name, scenario = picked
        output_dir = ctx.output_dir()
        html = report_path(output_dir, name, req.day)
        if not html.is_file():
            return JSONResponse({"detail": f"그날 리포트 파일이 없다 — {html.name}"}, status_code=404)
        try:
            mail = ctx.app_config().mail
        except Exception as exc:                                   # noqa: BLE001
            return JSONResponse({"detail": f"app.json을 못 읽었다 — {exc}"}, status_code=400)
        if not mail.enabled:
            # 꺼져 있으면 `mail send`가 건너뛰고도 0으로 끝난다 — 그걸 발송으로 알고 장부를 쓰면 안 된다.
            return JSONResponse({"detail": "mail.enabled=false — 보내지 않는다"}, status_code=409)
        record = ledger_view(output_dir, name, req.day)
        if record is not None and not req.force:
            return JSONResponse({"detail": "이미 보냈다 — force로 덮어쓴다", "ledger": record}, status_code=409)
        subject = subject_for_day(scenario, req.day)
        full_subject = f"{mail.subject_prefix} {subject}".strip() if mail.subject_prefix else subject

        def write_ledger(job: Job) -> None:
            # `mail send`에는 장부가 없다 — 그날 HTML을 보낸 것은 이 경로가 안다. 끝난 뒤(202로 먼저 돌아갔어도) 쓴다.
            if job.status != "ok":
                return
            problem = ledger.write(output_dir, ledger.SentRecord(
                scenario=name, day=req.day, sent_at=ctx.clock(), subject=full_subject,
                recipients=len(mail.recipients), trigger="manual", job_id=job.id))
            if problem:
                job.status = "failed"
                job.stderr += f"\n[작업 실행기] 메일은 나갔는데 장부를 못 썼다 — {problem}"

        argv = ["mail", "send", f"--file={html}", f"--subject={subject}"]
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s, on_done=write_ledger)

    @router.get("/report/sent", tags=["리포트"], summary="발송 장부 — 기준일 최근 순")
    async def report_sent():
        return {"dir": str(ctx.output_dir() / ledger.SENT_DIR), "records": ledger.all_records(ctx.output_dir())}

    @router.get("/report/sent/{day}", tags=["리포트"], summary="그 기준일의 장부(YYYY-MM-DD)")
    async def report_sent_day(day: date, q: Annotated[ScenarioQuery, Query()]):
        picked, why = pick_scenario(ctx.config_root, q.scenario)
        if picked is None:
            return JSONResponse({"detail": why}, status_code=400)
        record = ledger_view(ctx.output_dir(), picked[0], day)
        if record is None:
            return JSONResponse({"detail": f"장부가 없다 — {picked[0]} {day}"}, status_code=404)
        return record

    @router.post("/mail/describe", tags=["메일"], summary="`mail describe` — 누구에게 보내게 돼 있나")
    async def mail_describe(req: Annotated[Wait, Query()]):
        return await start(ctx, ["mail", "describe"], timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.post("/mail/send", tags=["메일"],
                 summary="`mail send` — dry_run 기본 true. to를 주면 config 수신자는 무시하고 그 주소들에만")
    async def mail_send(req: Annotated[MailSendRequest, Query()]):
        # `--subject=값` — 제목이 `-`로 시작해도 옵션으로 읽히지 않는다.
        argv = ["mail", "send", f"--subject={req.subject}"]
        if req.dry_run:
            argv.append("--dry-run")
        argv += [f"--to={address}" for address in req.to]
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.get("/output", tags=["출력"], summary="output 폴더의 파일(최근 순)")
    async def output_list():
        directory = ctx.output_dir()
        zone = ctx.clock().tzinfo
        files = []
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_file():
                    stat = path.stat()
                    files.append((stat.st_mtime, {
                        "name": path.name, "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=zone).isoformat()}))
        files.sort(key=lambda pair: pair[0], reverse=True)
        return {"dir": str(directory), "files": [row for _, row in files]}

    @router.get("/output/{name:path}", tags=["출력"], summary="output 파일 하나 — HTML은 그대로 보인다")
    async def output_file(name: str):
        target = resolve_output(ctx.output_dir(), name)
        if target is None:
            return JSONResponse({"detail": "output 폴더 밖 경로는 열지 않는다"}, status_code=400)
        if not target.is_file():
            return JSONResponse({"detail": f"없다 — {name}"}, status_code=404)
        media, _ = mimetypes.guess_type(target.name)
        media = media or "application/octet-stream"
        if media.startswith("text/") or media in ("application/json",):
            media += "; charset=utf-8"
        # filename을 안 준다 — 주면 attachment가 붙어 브라우저가 HTML을 보여 주지 않고 내려받는다.
        return FileResponse(target, media_type=media)

    return router
