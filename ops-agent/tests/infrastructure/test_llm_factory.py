"""조립이 한 곳뿐인가 — 호출부가 각자 고르면 한 경로만 다른 LLM을 쓴다."""
from src.config.schema_llm import LlmConfig, TlsConfig
from src.infrastructure.llm_factory import build_llm

GATEWAY = dict(model="gauss-o-flash", model_id="339", base_url="https://g.example/v1",
               pass_key="p", client_key="c")


def test_adapter_값이_구현을_고른다(clock):
    picked = {adapter: type(build_llm(LlmConfig(adapter=adapter, **GATEWAY),
                                      clock=clock, warn=lambda _m: None)).__name__
              for adapter in ("chat_model", "http")}
    picked["echo"] = type(build_llm(LlmConfig(adapter="echo", model="dev"),
                                    clock=clock)).__name__
    assert picked == {"chat_model": "ChatModelAdapter", "http": "HttpChatAdapter",
                      "echo": "EchoAdapter"}


def test_만들기만_해서는_네트워크를_타지_않는다(clock):
    # 지연 생성 — `llm describe`처럼 설정만 보는 명령이 게이트웨이에 붙으면 안 된다.
    build_llm(LlmConfig(**GATEWAY), clock=clock, warn=lambda _m: None)


def test_검증이_꺼져_있으면_조립에서_경고한다(clock):
    warnings = []
    build_llm(LlmConfig(**GATEWAY, tls=TlsConfig(verify=False)), clock=clock,
              warn=warnings.append)
    assert warnings and "TLS 검증이 꺼져 있다" in warnings[0]


def test_echo는_TLS_경고를_내지_않는다(clock):
    # 네트워크를 안 타므로 경고할 것이 없다 — 무의미한 경고는 진짜 경고를 묻는다.
    warnings = []
    build_llm(LlmConfig(adapter="echo", model="dev", tls=TlsConfig(verify=False)),
              clock=clock, warn=warnings.append)
    assert warnings == []


async def test_포트_표면은_ask와_describe_뿐이다():
    import inspect

    from src.domain.llm import LlmPort
    surface = {n for n in dir(LlmPort) if not n.startswith("_")}
    assert surface == {"ask", "describe"}, f"LLM 포트 표면이 넓어졌다 — {surface}"
    assert inspect.iscoroutinefunction(LlmPort.ask)
