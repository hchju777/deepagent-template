"""LangChain 채팅 모델로 묻는 어댑터 — 사내 코드와 같은 경로.

8단계의 LangGraph 노드들이 LangChain 모델 객체를 직접 받는 자리가 있으므로
이 어댑터가 기본값이다. `client()`가 그 객체를 꺼내 준다.

## 동기·비동기 클라이언트를 둘 다 주는 이유

조사 경로는 `ainvoke`만 쓰지만, `ChatOpenAI`가 동기 호출을 할 때 별도
클라이언트를 **스스로 만들면** 그 하나가 `verify` 설정을 물려받지 못해
사내 인증서에서 조용히 실패한다. 둘 다 지정해 그 구멍을 닫는다.
"""
import asyncio
import time

from src.config.schema_llm import LlmConfig
from src.domain.base import Clock
from src.domain.llm import LlmPort, LlmReply
from src.infrastructure.llm_format import response_format
from src.infrastructure.llm_pacing import pacer_for, quota_wait
from src.infrastructure.tls import verify_arg


class ChatModelAdapter(LlmPort):
    def __init__(self, cfg: LlmConfig, *, clock: Clock, ticker=time.perf_counter, sleep=asyncio.sleep):
        self._cfg = cfg
        self._clock = clock
        self._ticker = ticker
        self._sleep = sleep
        self._pacer = pacer_for(cfg.base_url)
        self._client = None
        self._http = self._ahttp = None

    def describe(self) -> str:
        return self._cfg.describe()

    def client(self):
        """LangChain 모델 객체. 8단계의 그래프 노드가 이것을 받는다.

        지연 생성 — 어댑터를 만드는 것만으로 httpx 커넥션 풀이 생기면
        `config show` 같은 명령도 게이트웨이 쪽에 자원을 잡는다.
        """
        if self._client is None:
            import httpx
            from langchain_openai import ChatOpenAI

            verify = verify_arg(self._cfg.tls)
            trust_env = self._cfg.trust_env_proxy
            extra = {}
            # langchain-openai가 TCP keepalive를 위해 자체 transport를 끼워 넣고,
            # 그때 httpx의 프록시 자동 감지가 꺼진다며 매 호출 경고를 찍는다.
            # 우리는 클라이언트를 직접 주므로 그 transport를 쓰지 않는다 — 빈
            # 튜플로 끈다. **버전에 따라 이 필드가 없을 수 있으므로 확인하고 넘긴다**
            # (3.11.3과 3.11.15가 argparse에서 갈린 것과 같은 교훈).
            if "http_socket_options" in ChatOpenAI.model_fields:
                extra["http_socket_options"] = ()
            if self._cfg.max_tokens is not None:
                extra["max_tokens"] = self._cfg.max_tokens
            # 유휴 상한도 같은 확인 — 사내 langchain-openai에 그 필드가 없으면 config가 말없이 무시된다(기본 상한 그대로).
            if self._cfg.stream_idle_s is not None and "stream_chunk_timeout" in ChatOpenAI.model_fields:
                extra["stream_chunk_timeout"] = self._cfg.stream_idle_s
            self._http = httpx.Client(verify=verify, trust_env=trust_env)
            self._ahttp = httpx.AsyncClient(verify=verify, trust_env=trust_env)
            self._client = ChatOpenAI(
                base_url=self._cfg.base_url,
                **extra,
                # 사내 게이트웨이는 이 값을 보지 않지만, 없으면 ChatOpenAI가
                # OPENAI_API_KEY env를 뒤지다 죽는다. 진짜 인증은 헤더 셋이 한다.
                api_key=self._cfg.api_key.get_secret_value(),
                model=self._cfg.model,
                temperature=self._cfg.temperature,
                timeout=self._cfg.timeout_s,
                max_retries=self._cfg.max_retries,
                default_headers=self._cfg.gateway_headers(),
                http_client=self._http, http_async_client=self._ahttp)
        return self._client

    async def close(self) -> None:
        # 우리가 만든 클라이언트는 우리가 닫는다 — ChatOpenAI는 받은 클라이언트의 수명을 모른다.
        for client in (self._ahttp, self._http):
            try:
                if client is None or client.is_closed:
                    continue
                await client.aclose() if hasattr(client, "aclose") else client.close()
            except Exception:                                      # noqa: BLE001
                pass

    async def ask(self, prompt: str, *, schema: dict | None = None) -> LlmReply:
        # 429면 본문의 시각까지 기다렸다 **한 번만** 다시 묻는다 — 대기는 실패가 아니다(`llm_pacing`).
        waited, limited, source = 0.0, 0, ""
        for attempt in (0, 1):
            await self._pacer.wait_turn(self._cfg.min_interval_s, sleep=self._sleep)
            reply, quota = await self._once(prompt, schema)
            if quota is None or attempt:
                return reply.model_copy(update={"waited_s": waited, "rate_limited": limited, "rate_source": source})
            wait, source = quota_wait(quota[0], quota[1], now=self._clock(), cap=self._cfg.rate_wait_max_s)
            await self._sleep(wait)
            waited, limited = waited + wait, limited + 1
        return reply                                                 # 도달하지 않는다 — 형식상

    async def _once(self, prompt: str, schema: dict | None = None):
        """한 번 묻는다. `(답, 429면 (본문, 헤더) 아니면 None)` — SDK는 429를 예외(`status_code`)로 올린다."""
        started = self._ticker()
        messages = [{"role": "user", "content": prompt}]
        first = None
        parts: list[str] = []
        # 호출마다 넘긴다 — langchain-openai는 호출 kwargs를 요청 본문에 그대로 합친다(`_get_request_payload`). 모델 객체에
        # 박으면 자유 질문(`llm ask`)까지 JSON을 강요한다.
        fmt = response_format(self._cfg.response_format, schema)
        extra = {"response_format": fmt} if fmt is not None else {}
        try:
            if self._cfg.stream:
                # 조각을 모아 한 답으로 — 호출부는 스트리밍을 모른다. 첫 조각 시각은 유휴 상한을 보는 재료다.
                metadata = {}
                async for chunk in self.client().astream(messages, **extra):
                    if first is None:
                        first = round(self._ticker() - started, 3)
                    parts.append(str(getattr(chunk, "content", "") or ""))
                    metadata = getattr(chunk, "response_metadata", None) or metadata
                text = "".join(parts)
            else:
                message = await self.client().ainvoke(messages, **extra)
                metadata = getattr(message, "response_metadata", None) or {}
                text = str(getattr(message, "content", message) or "")
        except Exception as exc:                                   # noqa: BLE001
            # 받은 조각은 버리지 않는다 — 끝 표시만 빠진 답일 수 있다(사내 측정 #4). 쓸지는 답을 아는 쪽(`lead.ask_json`)이 정한다.
            failed = LlmReply(status="error", asked_at=self._clock(), model=self._cfg.model,
                              error=f"{type(exc).__name__}: {exc}", partial_text="".join(parts),
                              latency_s=round(self._ticker() - started, 3), first_token_s=first)
            return failed, _quota_info(exc)
        return LlmReply(status="ok", asked_at=self._clock(), model=self._cfg.model,
                        text=text, reported_model=metadata.get("model_name"),
                        latency_s=round(self._ticker() - started, 3), first_token_s=first), None


def _quota_info(exc: Exception):
    """429 예외에서 `(본문, 헤더)` — openai SDK의 `APIStatusError`는 `status_code`·`body`·`response`를 든다. 아니면 None."""
    if getattr(exc, "status_code", None) != 429:
        return None
    response = getattr(exc, "response", None)
    # SDK는 본문에서 `error` 안쪽만 예외에 남긴다(`body.get("error", body)`) — 시각이 `error`의 형제면 거기엔 없다. 원문도 같이 본다.
    raw = None
    if response is not None:
        try:
            raw = response.json()
        except Exception:                                          # noqa: BLE001
            raw = None
    return {"sdk": getattr(exc, "body", None), "raw": raw}, getattr(response, "headers", None)
