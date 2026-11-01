"""앱 조립 — 경로 셋(루트·운영·진단)을 한 컨텍스트 위에 얹는다.

시계·env·리포 루트는 **받는다**(기본값 없음). 진짜 값은 `app.py`(uvicorn이 찾는 경계)에서 한 번만 만든다 —
CLI가 `__main__`에서만 `datetime.now()`를 부르는 것과 같은 규율이다.
"""
import contextlib
import sys
from pathlib import Path

from fastapi import FastAPI

from src.domain.base import Clock
from src.presentation.api.context import ApiContext
from src.presentation.api.jobs import JobRunner
from src.presentation.api.routes_diag import diag_router
from src.presentation.api.routes_ops import ops_router
from src.presentation.api.routes_root import root_router
from src.presentation.api.scheduler import ApiScheduler
from src.schedule.runner import wait_or_stop

# 사내 원본과 같은 접두. Ingress 규칙이 이 모양을 본다.
OPS = "/internal/v1/ops"
DIAG = f"{OPS}/diag"


def create_app(*, root: Path, env: dict[str, str], clock: Clock, build: dict[str, str],
               runner: JobRunner | None = None, python: str = sys.executable, site_probes=None,
               trace_probe=None, llm_transport=None, sleeper=None) -> FastAPI:
    """`sleeper`는 스케줄러가 다음 시각까지 자는 함수 — 테스트가 가짜 시계와 함께 넣는다."""
    runner = runner or JobRunner(root=root, clock=clock, python=python)
    ctx = ApiContext(root=root, env=env, clock=clock, build=build, started_at=clock(), runner=runner,
                     site_probes=site_probes, trace_probe=trace_probe, llm_transport=llm_transport,
                     scheduler=ApiScheduler(config_root=root / "config", runner=runner, clock=clock,
                                            sleeper=sleeper or wait_or_stop))

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        ctx.scheduler.start()
        try:
            yield
        finally:
            await ctx.scheduler.stop()

    app = FastAPI(title="ops-agent", version=build.get("commit") or "dev", lifespan=lifespan,
                  description="운영 리포트·메일·진단. 작업은 한 번에 하나(돌고 있으면 409). 스케줄은 이 안에서 돈다.")
    app.state.ctx = ctx
    app.include_router(root_router(ctx))
    app.include_router(ops_router(ctx), prefix=OPS)
    app.include_router(diag_router(ctx), prefix=DIAG)
    return app
