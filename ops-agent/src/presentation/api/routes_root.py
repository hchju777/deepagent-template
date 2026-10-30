"""루트 — `/health`(프로세스가 사는가), `/ready`(일을 받을 수 있는가), `/version`(무엇이 떠 있는가)."""
import hashlib
import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.config.envresolve import resolve_env
from src.config.loader import ConfigError, load_registry, load_scenarios
from src.presentation.api.context import ApiContext


def root_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter(tags=["상태"])

    @router.get("/health", summary="프로세스가 산다")
    async def health():
        return {"status": "ok"}

    @router.get("/ready", summary="설정·비밀 키·스케줄러 — 하나라도 아니면 503")
    async def ready():
        checks = [_config(ctx), _secrets(ctx), _scheduler(ctx)]
        ok = all(check["ok"] for check in checks)
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    @router.get("/version", summary="빌드 커밋·시각, config 해시, 시나리오와 다음 실행")
    async def version():
        return {"commit": ctx.build.get("commit", ""), "built_at": ctx.build.get("built_at", ""),
                "started_at": ctx.started_at.isoformat(), "config": config_hash(ctx),
                "scenarios": _scenarios(ctx)}

    return router


def _config(ctx: ApiContext) -> dict:
    try:
        load_registry(ctx.config_root)
        load_scenarios(ctx.config_root)
        ctx.app_config()
    except Exception as exc:                                       # noqa: BLE001 — 권한·디스크 오류도 "준비 안 됨"이다
        return {"name": "config", "ok": False, "detail": str(exc) if isinstance(exc, ConfigError)
                else f"{type(exc).__name__}: {exc}"}
    return {"name": "config", "ok": True, "detail": "app.json·registry·시나리오가 읽힌다"}


def _secrets(ctx: ApiContext) -> dict:
    """앱 전역(app.json·시나리오)이 참조하는 `${VAR}`만 본다. 사이트 config의 비밀은 `/diag/env`·`boot`가 말한다 —
    28개 중 한 사이트의 키가 비었다고 API 전체를 준비 안 됨으로 만들면, 그 사이트를 고치는 데 쓸 진단까지 막힌다."""
    files = [ctx.config_root / "app.json", *sorted((ctx.config_root / "scenarios").glob("*.json"))]
    missing: set[str] = set()
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                            # 못 읽는 파일은 config 칸이 말한다
        missing.update(resolve_env(raw, ctx.env)[1])
    if missing:
        return {"name": "secrets", "ok": False, "detail": f"env가 비어 있다 — {', '.join(sorted(missing))}"}
    return {"name": "secrets", "ok": True, "detail": "app.json·시나리오가 참조하는 env가 다 있다"}


def _scheduler(ctx: ApiContext) -> dict:
    if ctx.scheduler_alive is None:
        return {"name": "scheduler", "ok": True, "detail": "스케줄러 없음(2단계에서 붙는다)"}
    alive = bool(ctx.scheduler_alive())
    return {"name": "scheduler", "ok": alive, "detail": "돌고 있다" if alive else "스케줄러 태스크가 죽었다"}


def config_hash(ctx: ApiContext) -> dict:
    """**치환 전 원문**의 해시 — `${VAR}` 자리 그대로라 비밀이 섞이지 않고, 사내 config가 리포와 같은지만 말한다."""
    files = {"app.json": ctx.config_root / "app.json"}
    for path in sorted((ctx.config_root / "scenarios").glob("*.json")):
        files[f"scenarios/{path.name}"] = path
    whole = hashlib.sha256()
    each = {}
    for name, path in files.items():
        try:
            data = path.read_bytes()
        except OSError:
            each[name] = None
            continue
        each[name] = hashlib.sha256(data).hexdigest()[:12]
        whole.update(name.encode("utf-8") + b"\0" + data + b"\0")
    return {"hash": whole.hexdigest()[:12], "files": each}


def _scenarios(ctx: ApiContext) -> list[dict] | dict:
    try:
        scenarios = load_scenarios(ctx.config_root)
    except Exception as exc:                                       # noqa: BLE001
        return {"error": str(exc) if isinstance(exc, ConfigError) else f"{type(exc).__name__}: {exc}"}
    now = ctx.clock()
    rows = []
    for name, scenario in scenarios.items():
        on = scenario.enabled and scenario.schedule.enabled
        # interval의 기준점은 프로세스가 뜬 시각이다 — 스케줄러와 같은 규칙(`ScheduleSpec.next_after`).
        upcoming = scenario.schedule.next_after(now, anchor=ctx.started_at) if on else None
        rows.append({"name": name, "title": scenario.title, "enabled": scenario.enabled,
                     "schedule": scenario.schedule.describe(),
                     "next_run": upcoming.isoformat() if upcoming else None})
    return rows
