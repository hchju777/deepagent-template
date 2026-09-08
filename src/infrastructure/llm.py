"""LLM 어댑터 — 실구현은 사내 게이트웨이(FabriX/Gauss), 테스트는 ScriptedLLM.

노드·서브에이전트가 요구하는 표면은 async ainvoke(messages) -> .content 뿐이다.

**조립은 여기 하나뿐이다.** 예전에는 daemon·api/assembly·__main__이 각자
`make_llm` 클로저를 복붙했고, 그 셋이 갈라지면 한 경로만 다른 LLM을 쓰는 사고가
난다 — 규율 8이 발행 배선에 대해 말하는 것과 같은 함정이다. 세 호출부 전부
`build_llm_factory` 하나를 통과한다.

TLS: 사내 게이트웨이 인증서는 기본 신뢰 저장소에 없다. 이때 흔한 처방인
`ssl._create_default_https_context = ssl._create_unverified_context`는
**프로세스 전체**의 검증을 끈다 — pymongo·redis·aiokafka·httpx(대상 REST
프로버)·aiosmtplib까지 전부다. LLM 하나 붙이려고 대상 시스템 접속 다섯 종의
인증서 검증을 같이 끄는 셈이라 여기서는 쓰지 않는다. 대신 이 커넥션 전용
httpx 클라이언트에만 verify를 지정해 범위를 닫는다(전역·env·requests 패치 없음).
`tests/infrastructure/test_llm.py`가 "전역을 안 건드린다"를 단정한다 —
산문 규율은 읽지 않으면 무력하다.
"""
import ssl
import sys
from typing import Any, Callable

from langchain_core.messages import AIMessage

from src.config.schema_app import LlmGateway

# 사내 게이트웨이는 OpenAI 규약의 Authorization 헤더를 안 본다. 그런데 ChatOpenAI는
# api_key가 없으면 OPENAI_API_KEY env를 뒤지다 죽으므로 자리를 채워야만 한다 —
# 이 값은 소켓에 나가도 아무 뜻이 없는 sentinel이고, 진짜 인증은 헤더 셋이 한다.
_SENTINEL_API_KEY = "EMPTY"


def _verify_arg(cfg: LlmGateway):
    """httpx의 verify 인자 — SSLContext(번들이 있을 때) 또는 bool.

    스키마가 `ca_bundle`과 `tls_verify=false`의 동시 지정을 이미 거부하므로
    여기서 둘의 우선순위를 정할 일은 없다.

    경로 문자열을 그대로 넘기지 않는 이유: httpx 0.28이 `verify=<str>`을
    deprecate했고(requirements가 `httpx>=0.28,<1`이라 마이너 상승에서 사라질 수
    있다), 무엇보다 `ssl.create_default_context`는 여기서 만든 컨텍스트 하나만
    바꾼다 — `ssl._create_default_https_context`를 갈아끼우는 것과 달리 전역에
    아무 흔적을 안 남긴다.
    """
    if cfg.ca_bundle:
        return ssl.create_default_context(cafile=cfg.ca_bundle)
    return cfg.tls_verify


def ca_bundle_problems(cfg: LlmGateway) -> list[str]:
    """CA 번들이 실재하고 PEM으로 읽히는가 — 기동 검증이 부른다.

    경로 오타를 런타임까지 미루면 밤에 첫 조사가 TLS로 죽는다. 여기서 실제로
    로드해 보는 것 말고는 "읽히는 PEM인가"를 확인할 방법이 없다.
    """
    if not cfg.ca_bundle:
        return []
    try:
        _verify_arg(cfg)
    except Exception as exc:                                   # noqa: BLE001
        return [f"llm.gateway.ca_bundle을 읽을 수 없다 — {cfg.ca_bundle}: {exc}"]
    return []


def build_llm_factory(cfg: LlmGateway, *, warn=None) -> Callable[[str], Any]:
    """역할 이름을 받아 채팅 모델을 돌려주는 팩토리를 만든다.

    게이트웨이가 모델을 하나만 열기 때문에 role은 지금 **무시되고** 셋 다 같은
    인스턴스를 받는다. 그래도 인자를 남기는 이유가 둘 있다: 테스트가
    `llm_factory=`로 갈아끼우는 이음매가 이 모양이고, "judge/subagent/lead
    세 자리가 LLM을 쓴다"는 사실이 호출부에 남는다. 게이트웨이가 모델을 더
    열면 여기서 role로 갈라지면 된다.

    인스턴스를 공유하는 것은 절약이기도 하다 — 사이트마다 새로 만들면 같은
    게이트웨이를 향한 httpx 커넥션 풀이 사이트 수만큼 생긴다.
    """
    if not cfg.tls_verify:
        # 검증이 꺼진 채로 조용히 도는 것이 이 리포에서 제일 위험한 상태다
        # (기동 거부 철학의 형제 — 못 막을 것은 최소한 시끄럽게 만든다).
        (warn or _stderr)("[llm] TLS 검증이 꺼져 있다 — llm.gateway.tls_verify=false. "
                          "사내 루트 CA를 구하면 ca_bundle로 바꿔라")

    cached: dict[str, Any] = {}

    def make(role: str) -> Any:
        if "model" not in cached:
            cached["model"] = _build_chat_model(cfg)
        return cached["model"]

    return make


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _build_chat_model(cfg: LlmGateway) -> Any:
    import httpx
    from langchain_openai import ChatOpenAI   # 지연 import — 스텁 전용 환경 배려

    verify = _verify_arg(cfg)
    return ChatOpenAI(
        base_url=cfg.base_url,
        api_key=_SENTINEL_API_KEY,
        model=cfg.model_id,
        temperature=0,
        default_headers={
            "X-FABRIX-CLIENT": cfg.pass_key.get_secret_value(),
            "X-OPENAPI-TOKEN": cfg.client_key.get_secret_value(),
            "X-LLM-MODEL-ID": cfg.model_id,
        },
        # 동기·비동기 둘 다 줘야 한다 — 조사 경로는 ainvoke지만 ChatOpenAI가
        # 동기 호출을 할 때 별도 클라이언트를 스스로 만들면 그 하나가 verify를
        # 안 물려받아 사내 인증서에서 조용히 실패한다.
        http_client=httpx.Client(verify=verify),
        http_async_client=httpx.AsyncClient(verify=verify),
    )


class ScriptedLLM:
    """예약된 응답을 순서대로 재생한다 — 결정론 테스트의 축(스펙 §5.5)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("스크립트 소진 — 예약된 응답보다 호출이 많다")
        return AIMessage(content=self._responses.pop(0))
