"""LLM 게이트웨이에 **SDK 없이** 한 번 — 원인 분리용.

`llm check`는 우리 어댑터(langchain·스트리밍·형식 강제·재시도·간격)를 다 거친다. 거기서 실패하면 원인이 우리
조립인지 게이트웨이인지 모른다. 여기는 httpx 요청 하나에 config의 주소·헤더·TLS·프록시 방침만 싣고, 받은 것을
**가공 없이** 돌려준다(상태 코드·헤더·본문 앞부분). 이것이 되고 `llm check`가 안 되면 우리 쪽이다.

보낸 헤더는 **이름만** 돌려준다 — 값이 키다.
"""
import time

import httpx

from src.config.schema_llm import LlmConfig
from src.infrastructure.tls import verify_arg
from src.presentation.api.diag_env import mask_url

# Ingress가 60초에 끊는다. config의 timeout_s가 더 길어도 이 요청은 그 안에서 끝나야 한다.
RAW_TIMEOUT_CAP_S = 50.0
_BODY_HEAD = 4000


async def raw_call(cfg: LlmConfig, prompt: str, *, transport=None, ticker=time.perf_counter) -> dict:
    """**던지지 않는다.** 실패도 같은 모양(`ok: False`)이다."""
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {cfg.api_key.get_secret_value()}",
               **cfg.gateway_headers()}
    body = {"model": cfg.model, "temperature": cfg.temperature,
            "messages": [{"role": "user", "content": prompt}]}
    if cfg.max_tokens is not None:
        body["max_tokens"] = cfg.max_tokens
    timeout = min(cfg.timeout_s, RAW_TIMEOUT_CAP_S)
    shown = {"url": mask_url(url), "trust_env_proxy": cfg.trust_env_proxy, "timeout_s": timeout,
             "sent_headers": sorted(headers)}
    client_args = {"timeout": timeout, "verify": verify_arg(cfg.tls), "trust_env": cfg.trust_env_proxy}
    if transport is not None:
        client_args["transport"] = transport
    started = ticker()
    try:
        async with httpx.AsyncClient(**client_args) as client:
            response = await client.post(url, json=body, headers=headers)
    except Exception as exc:                                       # noqa: BLE001
        return {"ok": False, **shown, "ms": round((ticker() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}"}
    reported = None
    try:
        payload = response.json()
        reported = payload.get("model") if isinstance(payload, dict) else None
    except ValueError:
        pass
    return {"ok": response.status_code < 400, **shown, "status": response.status_code,
            "ms": round((ticker() - started) * 1000, 1), "headers": dict(response.headers),
            "body": response.text[:_BODY_HEAD], "reported_model": reported}
