"""운영용 — `/internal/v1/ops/...`: 작업, 스케줄, 리포트, 메일, 출력 파일."""
import mimetypes
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from src.presentation.api.context import ApiContext
from src.presentation.api.jobs import REPORT_S, SHORT_S, Job
from src.config.loader import load_scenarios
from src.presentation.api.mail_view import recipients_view
from src.presentation.api.models import (WAIT_MAX_S, CleanupRequest, MailSendRequest, MailTestRequest,
                                         ReportAggregateRequest, ReportResendRequest, ReportRunRequest,
                                         ScenarioQuery, ScheduleListRequest, Wait)
from src.presentation.api.sent import ledger_view, pick_scenario, report_day, today_status
from src.report import ledger, retention
from src.report.publish import report_path, subject_for_day


async def start(ctx: ApiContext, argv: list[str], *, timeout_s: float, wait_s: float,
                job_id: str | None = None, on_done=None, extra: dict | None = None) -> JSONResponse:
    job, busy = ctx.runner.submit(argv, timeout_s=timeout_s, job_id=job_id, on_done=on_done)
    return await respond(ctx, job, busy, wait_s=wait_s, extra=extra)


async def respond(ctx: ApiContext, job: Job | None, busy: Job | None, *, wait_s: float,
                  extra: dict | None = None) -> JSONResponse:
    """200(끝남) · 202(아직 — `/jobs/{id}`로 본다) · 409(다른 작업이 돌고 있다 — 그 작업을 같이 준다).

    `extra`는 경로가 정한 것(무엇을 실었나)을 작업 보기 옆에 붙인다 — 작업 출력만으로는 안 보이는 결정이다."""
    if busy is not None:
        return JSONResponse({"detail": "다른 작업이 돌고 있다 — 끝난 뒤 다시", "running": busy.view()},
                            status_code=409)
    await ctx.runner.wait(job, wait_s)
    return JSONResponse({**job.view(), **(extra or {})}, status_code=200 if job.finished else 202)


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


def only_once(request: Request, name: str) -> None:
    """문자열 칸에 같은 이름이 여럿 오면 FastAPI는 **마지막 것만 조용히** 쓴다. 칸 모양(Swagger의 문자열 한 칸)은 그대로 두고
    여기서 거른다 — 다른 422와 같은 모양으로."""
    given = request.query_params.getlist(name)
    if len(given) > 1:
        raise RequestValidationError([{"type": "value_error", "loc": ("query", name), "input": given,
                                       "msg": f"{name}는 한 주소만 — {len(given)}개를 줬다"}])


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
                                            paused=view["paused"], started_at=ctx.started_at)
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
        html = report_path(output_dir, name, req.date)
        if not html.is_file():
            return JSONResponse({"detail": f"그날 리포트 파일이 없다 — {html.name}"}, status_code=404)
        try:
            mail = ctx.app_config().mail
        except Exception as exc:                                   # noqa: BLE001
            return JSONResponse({"detail": f"app.json을 못 읽었다 — {exc}"}, status_code=400)
        if not mail.enabled:
            # 꺼져 있으면 `mail send`가 건너뛰고도 0으로 끝난다 — 그걸 발송으로 알고 장부를 쓰면 안 된다.
            return JSONResponse({"detail": "mail.enabled=false — 보내지 않는다"}, status_code=409)
        record = ledger_view(output_dir, name, req.date)
        if record is not None and not req.force:
            return JSONResponse({"detail": "이미 보냈다 — force로 덮어쓴다", "ledger": record}, status_code=409)
        subject = subject_for_day(scenario, req.date)
        full_subject = f"{mail.subject_prefix} {subject}".strip() if mail.subject_prefix else subject

        def write_ledger(job: Job) -> None:
            # `mail send`에는 장부가 없다 — 그날 HTML을 보낸 것은 이 경로가 안다. 끝난 뒤(202로 먼저 돌아갔어도) 쓴다.
            if job.status != "ok":
                return
            problem = ledger.write(output_dir, ledger.SentRecord(
                scenario=name, day=req.date, sent_at=ctx.clock(), subject=full_subject,
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

    @router.get("/mail/recipients", tags=["메일"], summary="config 수신자 — 주소는 가리고(첫 글자·도메인) 수만")
    async def mail_recipients():
        try:
            mail = ctx.app_config().mail
        except Exception as exc:                                   # noqa: BLE001
            return JSONResponse({"detail": f"app.json을 못 읽었다 — {exc}"}, status_code=400)
        return recipients_view(mail)

    @router.post("/mail/test", tags=["메일"],
                 summary="한 사람에게만 시험 발송 — date의 리포트가 있으면 그 파일을, 없으면 짧은 본문. 장부는 안 쓴다")
    async def mail_test(req: Annotated[MailTestRequest, Query()], request: Request):
        only_once(request, "to")
        try:
            mail = ctx.app_config().mail
        except Exception as exc:                                   # noqa: BLE001
            return JSONResponse({"detail": f"app.json을 못 읽었다 — {exc}"}, status_code=400)
        if not mail.enabled:
            # 꺼져 있으면 `mail send`가 건너뛰고도 0으로 끝난다 — 시험이 '성공'으로 보이는데 아무도 못 받는다.
            return JSONResponse({"detail": "mail.enabled=false — 보내지 않는다"}, status_code=409)
        report, why = None, "date를 안 줬다"
        if req.date is not None:
            picked, problem = pick_scenario(ctx.config_root, req.scenario)
            if picked is None:
                return JSONResponse({"detail": problem}, status_code=400)
            name, scenario = picked
            # 보낸 리포트 이름만 — `.preview.html`(안 보낸 실행의 결과)은 그날 리포트가 아니다.
            path = report_path(ctx.output_dir(), name, req.date)
            if path.is_file():
                report = path
                subject = f"[테스트] {subject_for_day(scenario, req.date)}"
            else:
                why = f"그날 리포트 파일이 없다 — {path.name}"
        # `--to`는 config 수신자를 **덮는다**(더하지 않는다 — `__main__._mail_to`). 장부는 안 쓴다: 한 사람에게 간 시험이
        # '그날 나갔다'로 보이면 스케줄이 그날을 건너뛴다.
        argv = ["mail", "send", f"--to={req.to}"]
        if report is not None:
            argv += [f"--subject={subject}", f"--file={report}"]
            shown = {"to": req.to, "body": "report", "file": report.name}
        else:
            argv.append("--subject=[테스트] 메일 연결 확인")
            shown = {"to": req.to, "body": "short", "why": why}
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s, extra={"test": shown})

    @router.post("/mail/send", tags=["메일"],
                 summary="`mail send` — dry_run 기본 true. to를 주면 config 수신자는 무시하고 그 주소들에만")
    async def mail_send(req: Annotated[MailSendRequest, Query()]):
        # `--subject=값` — 제목이 `-`로 시작해도 옵션으로 읽히지 않는다.
        argv = ["mail", "send", f"--subject={req.subject}"]
        if req.dry_run:
            argv.append("--dry-run")
        argv += [f"--to={address}" for address in req.to]
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.get("/output", tags=["출력"], summary="output 폴더의 파일(최근 순)과 보관 정책(keep_days)·지울 예정")
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
        plans, problem = retention_plans(None)
        policy = {"error": problem} if problem else [retention.describe(plan, directory) for _, plan in plans]
        return {"dir": str(directory), "files": [row for _, row in files], "policy": policy}

    def retention_plans(only: str | None):
        """([(시나리오, 보관 계획)], None) 또는 (None, 이유). 같은 규칙(`src/report/retention.py`)을 `report run`도 쓴다."""
        try:
            scenarios = load_scenarios(ctx.config_root)
        except Exception as exc:                                   # noqa: BLE001
            return None, str(exc)
        if only is not None and only not in scenarios:
            return None, f"모르는 시나리오 — {only}. 있는 것: {', '.join(scenarios) or '없음'}"
        directory = ctx.output_dir()
        return [(name, retention.plan(directory, name, scenario.keep_days))
                for name, scenario in scenarios.items() if only in (None, name)], None

    @router.post("/output/cleanup", tags=["출력"],
                 summary="보관 정책(keep_days)대로 지운다 — dry_run 기본 true. 같은 시나리오의 HTML과 장부만")
    async def output_cleanup(req: Annotated[CleanupRequest, Query()]):
        plans, problem = retention_plans(req.scenario)
        if problem:
            return JSONResponse({"detail": problem}, status_code=400)
        directory = ctx.output_dir()
        results = []
        for _, plan in plans:
            row = retention.describe(plan, directory)
            if not req.dry_run and plan.keep_days is not None:
                deleted, problems = retention.apply(plan)
                row["deleted"] = [item.path.relative_to(directory).as_posix() for item in deleted]
                row["problems"] = problems
            results.append(row)
        return {"dry_run": req.dry_run, "results": results}

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
