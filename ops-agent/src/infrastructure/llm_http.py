"""OpenAI 호환 엔드포인트에 **평범한 HTTP로** 묻는 어댑터.

## 왜 langchain 없이 한 벌을 더 두는가

1. **의존성이 막힌 환경**: 사내 PyPI 미러에 langchain 계열이 없거나 버전이
   낮으면 `chat_model` 어댑터를 쓸 수 없다. 규약(OpenAI 호환)은 같으므로
   `adapter: "http"`로 바꾸면 같은 게이트웨이에 그대로 붙는다.
2. **디버깅**: 401이 났을 때 "헤더가 정말 나갔는가"를 확인하려면 요청을 직접
   만드는 쪽이 빠르다. langchain 내부를 헤집을 필요가 없다.

둘은 같은 `LlmPort`를 구현하므로 엔진 입장에서는 구별되지 않는다.
"""
import time
from typing import Any

from src.config.schema_llm import LlmConfig
from src.domain.base import Clock
from src.domain.llm import LlmPort, LlmReply
from src.infrastructure.tls import verify_arg


class HttpChatAdapter(LlmPort):
    def __init__(self, cfg: LlmConfig, *, clock: Clock, ticker=time.perf_counter):
        self._cfg = cfg
        self._clock = clock
        self._ticker = ticker          # 경과 시간은 Clock과 다른 양이다

    def describe(self) -> str:
        return self._cfg.describe()

    async def ask(self, prompt: str) -> LlmReply:
        import httpx

        url = f"{self._cfg.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json",
                   # OpenAI 규약의 자리. 사내 게이트웨이는 보지 않지만, 비워 두면
                   # 일부 프록시가 400을 돌려주므로 sentinel을 채운다.
                   "Authorization": f"Bearer {self._cfg.api_key.get_secret_value()}",
                   **self._cfg.gateway_headers()}
        body = {"model": self._cfg.model,
                "temperature": self._cfg.temperature,
                "messages": [{"role": "user", "content": prompt}]}

        started = self._ticker()
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_s,
                                         verify=verify_arg(self._cfg.tls),
                                         trust_env=self._cfg.trust_env_proxy) as client:
                response = await client.post(url, json=body, headers=headers)
            if response.status_code >= 400:
                return self._failed(f"HTTP {response.status_code} — {response.text[:400]}",
                                    started)
            return self._from_payload(response.json(), started)
        except Exception as exc:                                   # noqa: BLE001
            return self._failed(f"{type(exc).__name__}: {exc}", started)

    def _from_payload(self, payload: Any, started: float) -> LlmReply:
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            # 200인데 모양이 다르면 그것도 실패다. 조용히 빈 문자열을 돌려주면
            # 엔진은 "LLM이 아무 말도 안 했다"로 읽고 그 이유를 영원히 모른다.
            return self._failed(f"응답 모양이 OpenAI 규약과 다르다 — {str(payload)[:300]}",
                                started)
        return LlmReply(status="ok", asked_at=self._clock(), model=self._cfg.model,
                        text=text or "", reported_model=payload.get("model"),
                        latency_s=round(self._ticker() - started, 3))

    def _failed(self, error: str, started: float) -> LlmReply:
        return LlmReply(status="error", asked_at=self._clock(), model=self._cfg.model,
                        error=error, latency_s=round(self._ticker() - started, 3))
