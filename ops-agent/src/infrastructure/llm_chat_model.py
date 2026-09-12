"""LangChain 채팅 모델로 묻는 어댑터 — 사내 코드와 같은 경로.

8단계의 LangGraph 노드들이 LangChain 모델 객체를 직접 받는 자리가 있으므로
이 어댑터가 기본값이다. `client()`가 그 객체를 꺼내 준다.

## 동기·비동기 클라이언트를 둘 다 주는 이유

조사 경로는 `ainvoke`만 쓰지만, `ChatOpenAI`가 동기 호출을 할 때 별도
클라이언트를 **스스로 만들면** 그 하나가 `verify` 설정을 물려받지 못해
사내 인증서에서 조용히 실패한다. 둘 다 지정해 그 구멍을 닫는다.
"""
import time

from src.config.schema_llm import LlmConfig
from src.domain.base import Clock
from src.domain.llm import LlmPort, LlmReply
from src.infrastructure.tls import verify_arg


class ChatModelAdapter(LlmPort):
    def __init__(self, cfg: LlmConfig, *, clock: Clock, ticker=time.perf_counter):
        self._cfg = cfg
        self._clock = clock
        self._ticker = ticker
        self._client = None

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
                http_client=httpx.Client(verify=verify, trust_env=trust_env),
                http_async_client=httpx.AsyncClient(verify=verify, trust_env=trust_env))
        return self._client

    async def ask(self, prompt: str) -> LlmReply:
        started = self._ticker()
        try:
            message = await self.client().ainvoke([{"role": "user", "content": prompt}])
        except Exception as exc:                                   # noqa: BLE001
            return LlmReply(status="error", asked_at=self._clock(), model=self._cfg.model,
                            error=f"{type(exc).__name__}: {exc}",
                            latency_s=round(self._ticker() - started, 3))
        metadata = getattr(message, "response_metadata", None) or {}
        return LlmReply(status="ok", asked_at=self._clock(), model=self._cfg.model,
                        text=str(getattr(message, "content", message) or ""),
                        reported_model=metadata.get("model_name"),
                        latency_s=round(self._ticker() - started, 3))
