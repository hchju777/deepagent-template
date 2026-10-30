"""운영용 — `/internal/v1/ops/...`: 작업, 스케줄 목록, 리포트, 메일, 출력 파일."""
import mimetypes
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from src.presentation.api.context import ApiContext
from src.presentation.api.jobs import REPORT_S, SHORT_S, Job
from src.presentation.api.models import (WAIT_MAX_S, MailSendRequest, ReportAggregateRequest,
                                         ReportRunRequest, ScheduleListRequest, Wait)


async def start(ctx: ApiContext, argv: list[str], *, timeout_s: float, wait_s: float) -> JSONResponse:
    job, busy = ctx.runner.submit(argv, timeout_s=timeout_s)
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

    @router.post("/schedule/list", tags=["스케줄"], summary="`schedule --list` — 무엇이 언제 도는가")
    async def schedule_list(req: ScheduleListRequest | None = None):
        req = req or ScheduleListRequest()
        argv = ["schedule", "--list"] + (["--scenario", req.scenario] if req.scenario else [])
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.post("/report/aggregate", tags=["리포트"], summary="`report aggregate` — 읽어서 숫자만(1800초)")
    async def report_aggregate(req: ReportAggregateRequest | None = None):
        req = req or ReportAggregateRequest()
        return await start(ctx, ["report", "aggregate", *_scenario_today(req)],
                           timeout_s=REPORT_S, wait_s=req.wait_s)

    @router.post("/report/run", tags=["리포트"],
                 summary="`report run` — 집계·서술·파일·메일(dry_run 기본 true, 1800초)")
    async def report_run(req: ReportRunRequest | None = None):
        req = req or ReportRunRequest()
        argv = ["report", "run", *_scenario_today(req)]
        if req.dry_run:
            argv.append("--dry-run")
        if req.no_mail:
            argv.append("--no-mail")
        return await start(ctx, argv, timeout_s=REPORT_S, wait_s=req.wait_s)

    @router.post("/mail/describe", tags=["메일"], summary="`mail describe` — 누구에게 보내게 돼 있나")
    async def mail_describe(req: Wait | None = None):
        req = req or Wait()
        return await start(ctx, ["mail", "describe"], timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.post("/mail/send", tags=["메일"],
                 summary="`mail send` — dry_run 기본 true. to를 주면 config 수신자는 무시하고 그 주소들에만")
    async def mail_send(req: MailSendRequest | None = None):
        req = req or MailSendRequest()
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
