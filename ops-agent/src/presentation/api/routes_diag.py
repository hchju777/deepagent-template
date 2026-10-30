"""진단용 — `/internal/v1/ops/diag/...`: boot·doctor·LLM(CLI 명령)과, CLI에 없는 env·네트워크·egress·trace·LLM 원시 호출.

앱 안에서 하는 진단은 **실패를 응답 본문의 값으로** 돌려준다(HTTP 200) — 진단 도구가 500을 내면 "무엇이
안 되는가"를 묻는 자리에서 답 대신 오류가 온다. config가 안 읽히면 `error` 한 칸이다.
"""

from fastapi import APIRouter

from src.config.loader import load_registry, load_site_config
from src.infrastructure.tls import verify_arg
from src.presentation.api.context import ApiContext
from src.presentation.api.diag_env import env_report, mask_url
from src.presentation.api.diag_llm import raw_call
from src.presentation.api.diag_net import connect_steps, host_port, resolve_v4, trace
from src.presentation.api.diag_sites import RealSiteProbes, survey
from src.presentation.api.jobs import DOCTOR_S, SHORT_S
from src.presentation.api.models import (DoctorRequest, EgressRequest, LlmAskRequest, LlmCheckRequest,
                                         LlmRawRequest, SitesRequest, TraceRequest, Wait)
from src.presentation.api.routes_ops import start

_NET_TIMEOUT_S = 5.0
_PROXY_NAMES = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")


def _llm_targets(app) -> list[tuple[str, object]]:
    """기본 llm과, 주소가 다른 역할. 같은 게이트웨이를 두 번 재지 않는다."""
    if app.llm is None:
        return []
    found, seen = [], set()
    for name, role in (("llm", None), ("llm:lead", "lead"), ("llm:conclude", "conclude"), ("llm:report", "report")):
        cfg = app.llm if role is None else app.llm_for(role)
        if cfg.base_url and cfg.base_url not in seen:
            seen.add(cfg.base_url)
            found.append((name, cfg))
    return found


def diag_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    probes = ctx.site_probes or RealSiteProbes()

    @router.post("/boot", tags=["진단 · CLI"], summary="`boot` — 기동 검증")
    async def boot(req: Wait | None = None):
        req = req or Wait()
        return await start(ctx, ["boot"], timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.post("/doctor", tags=["진단 · CLI"], summary="`doctor` — 한 사이트를 네 시스템으로(60초)")
    async def doctor(req: DoctorRequest | None = None):
        req = req or DoctorRequest()
        argv = ["doctor"] + (["--gbm", req.gbm] if req.gbm else []) + (["--fct", req.fct] if req.fct else [])
        return await start(ctx, argv, timeout_s=DOCTOR_S, wait_s=req.wait_s)

    @router.post("/llm/check", tags=["진단 · CLI"], summary="`llm check` — 붙는가·한국어·JSON")
    async def llm_check(req: LlmCheckRequest | None = None):
        req = req or LlmCheckRequest()
        argv = ["llm", "check"] + (["--role", req.role] if req.role else [])
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.post("/llm/ask", tags=["진단 · CLI"], summary="`llm ask` — 한 번 묻고 한 번 받는다(2000자까지)")
    async def llm_ask(req: LlmAskRequest):
        # `--` 뒤에 둔다 — 질문이 `-h`로 시작해도 옵션으로 읽히지 않는다.
        argv = ["llm", "ask"] + (["--role", req.role] if req.role else []) + ["--", req.prompt]
        return await start(ctx, argv, timeout_s=SHORT_S, wait_s=req.wait_s)

    @router.post("/llm/raw", tags=["진단 · 앱 안"], summary="SDK 없이 httpx로 게이트웨이에 한 번 — 원인 분리용")
    async def llm_raw(req: LlmRawRequest | None = None):
        req = req or LlmRawRequest()
        try:
            app = ctx.app_config()
        except Exception as exc:                                   # noqa: BLE001 — 값으로 돌려준다
            return {"ok": False, "error": str(exc)}
        cfg = app.llm if req.role is None else app.llm_for(req.role)
        if cfg is None or not cfg.base_url:
            return {"ok": False, "error": "app.json에 llm(base_url)이 없다"}
        return await raw_call(cfg, req.prompt, transport=ctx.llm_transport)

    @router.get("/env", tags=["진단 · 앱 안"], summary="config가 참조하는 ${VAR} — 있음/없음과 모양만")
    async def env():
        return env_report(ctx.config_root, ctx.env)

    @router.post("/doctor/all", tags=["진단 · 앱 안"], summary="켜진 사이트마다 Redis·Mongo 접속만(동시 몇 개)")
    async def doctor_all(req: SitesRequest | None = None):
        req = req or SitesRequest()
        return await survey(ctx.config_root, ctx.env, probes, mode="ping", gbm=req.gbm,
                            concurrency=req.concurrency)

    @router.post("/net", tags=["진단 · 앱 안"], summary="메일 에이전트·LLM 게이트웨이까지 DNS → TCP → TLS")
    async def net():
        try:
            app = ctx.app_config()
        except Exception as exc:                                   # noqa: BLE001 — 값으로 돌려준다
            return {"error": str(exc)}
        targets = []
        if app.mail.api_base:
            targets.append(("mail", app.mail.api_base, app.mail.tls, None))
        for name, cfg in _llm_targets(app):
            targets.append((name, cfg.base_url, cfg.tls, cfg.trust_env_proxy))
        rows = []
        for name, url, tls, trust_env in targets:
            row = {"name": name, "url": mask_url(url)}
            if trust_env is not None:
                row["trust_env_proxy"] = trust_env
            try:
                verify = verify_arg(tls)
            except Exception as exc:                               # noqa: BLE001
                row["steps"] = [{"step": "tls", "ok": False, "ms": None,
                                 "detail": f"TLS 설정을 못 만든다 — {type(exc).__name__}: {exc}"}]
                rows.append(row)
                continue
            row["steps"] = await connect_steps(url, verify=verify, timeout=_NET_TIMEOUT_S)
            rows.append(row)
        # 프록시 env가 있으면 실제 클라이언트는 그 길로 갈 수 있다 — 여기 단계는 직접 연결이다. 이름만 말한다.
        proxies = sorted({key.upper() for key in ctx.env if key.upper() in _PROXY_NAMES and ctx.env[key]})
        return {"targets": rows, "proxy_env": proxies}

    @router.post("/net/egress", tags=["진단 · 앱 안"],
                 summary="법인 Redis CLIENT INFO·Mongo whatsmyuri — 밖에서 보이는 우리 IP")
    async def egress(req: EgressRequest | None = None):
        req = req or EgressRequest()
        return await survey(ctx.config_root, ctx.env, probes, mode="egress", gbm=req.gbm, fct=req.fct,
                            concurrency=req.concurrency)

    @router.post("/net/trace", tags=["진단 · 앱 안"],
                 summary="실제 포트로 TCP SYN TTL 1..N — 도중 장비(ICMP 오류 큐, Linux)")
    async def net_trace(req: TraceRequest | None = None):
        req = req or TraceRequest()
        try:
            url = _trace_url(ctx, req)
        except Exception as exc:                                   # noqa: BLE001 — 값으로 돌려준다
            return {"target": req.target, "error": str(exc)}
        host, port = host_port(url)
        head = {"target": req.target, "host": host, "port": port}
        try:
            address = await resolve_v4(host)
        except Exception as exc:                                   # noqa: BLE001
            return {**head, "error": f"IPv4 주소를 못 풀었다 — {type(exc).__name__}: {exc}"}
        kwargs = {"probe": ctx.trace_probe} if ctx.trace_probe is not None else {}
        result = await trace(address, port, max_hops=req.max_hops, hop_timeout_s=req.hop_timeout_s, **kwargs)
        return {**head, "addr": address, **result}

    return router


def _trace_url(ctx: ApiContext, req: TraceRequest) -> str:
    """대상은 config에 적힌 곳뿐이다 — 임의의 host:port를 받으면 이 API가 포트 스캐너가 된다."""
    if req.target in ("mail", "llm"):
        app = ctx.app_config()
        url = app.mail.api_base if req.target == "mail" else (app.llm.base_url if app.llm else "")
        if not url:
            raise LookupError(f"app.json에 {req.target} 주소가 없다")
        return url
    entries = [e for e in load_registry(ctx.config_root).active()
               if (req.gbm is None or e.gbm == req.gbm) and (req.fct is None or e.fct == req.fct)]
    if len(entries) != 1:
        names = ", ".join(str(e) for e in entries) or "없음"
        raise LookupError(f"사이트를 하나로 좁혀라(gbm·fct) — 후보: {names}")
    site, _ = load_site_config(ctx.config_root, entries[0].gbm, entries[0].fct, env=ctx.env)
    system = site.infra.redis if req.target == "redis" else site.infra.mongodb
    if system is None:
        raise LookupError(f"{entries[0]}에 {req.target} 설정이 없다")
    return system.url
