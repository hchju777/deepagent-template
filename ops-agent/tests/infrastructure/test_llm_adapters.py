"""LLM 어댑터 둘이 **같은 계약**을 지키는가 — 진짜 HTTP로 검증한다."""
import pytest

from src.config.schema_llm import LlmConfig
from src.infrastructure.llm_factory import build_llm
from tests.conftest import T0
from tests.infrastructure.conftest import CLIENT_KEY, MODEL_ID, PASS_KEY

ADAPTERS = ["http", "chat_model"]


def _cfg(base_url, adapter="http", **kw):
    # 테스트는 재시도하지 않는다 — 실패 경로를 검증하는 데 백오프를 기다릴 이유가 없다.
    return LlmConfig(adapter=adapter, model="gauss-o-flash", model_id=MODEL_ID,
                     base_url=base_url, pass_key=PASS_KEY, client_key=CLIENT_KEY,
                     max_retries=0, **kw)


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_두_어댑터가_같은_게이트웨이에서_같은_답을_낸다(gateway, clock, adapter):
    base_url, recorder = gateway
    recorder.reply = "pong"
    reply = await build_llm(_cfg(base_url, adapter), clock=clock).ask("Reply: pong")
    assert reply.status == "ok", reply.error
    assert reply.text == "pong"
    assert reply.model == "gauss-o-flash"
    assert reply.asked_at == T0              # 주입된 시계


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_헤더_셋이_실제로_소켓에_나간다(gateway, clock, adapter):
    """가짜 게이트웨이가 헤더를 검사해 틀리면 401을 준다 — 200이 곧 증거다."""
    base_url, recorder = gateway
    reply = await build_llm(_cfg(base_url, adapter), clock=clock).ask("hi")
    assert reply.status == "ok", reply.error
    sent = recorder.requests[-1]["headers"]
    assert sent["x-fabrix-client"] == PASS_KEY
    assert sent["x-openapi-token"] == CLIENT_KEY     # OPENAI가 아니라 OPENAPI다
    assert sent["x-llm-model-id"] == MODEL_ID


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_키가_틀리면_예외가_아니라_error_값이다(gateway, clock, adapter):
    base_url, _ = gateway
    cfg = _cfg(base_url, adapter)
    cfg = cfg.model_copy(update={"pass_key": cfg.pass_key.__class__("wrong")})
    reply = await build_llm(cfg, clock=clock).ask("hi")
    assert reply.status == "error"
    assert "401" in reply.error or "Unauthorized" in reply.error or "bad headers" in reply.error


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_게이트웨이가_죽어_있어도_던지지_않는다(clock, adapter):
    reply = await build_llm(_cfg("http://127.0.0.1:9/v1", adapter), clock=clock).ask("hi")
    assert reply.status == "error" and reply.error
    assert reply.asked_at == T0, "실패에도 언제 실패했는지가 남아야 한다"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_요청한_모델이_요청에_실린다(gateway, clock, adapter):
    base_url, recorder = gateway
    await build_llm(_cfg(base_url, adapter), clock=clock).ask("hi")
    assert recorder.requests[-1]["body"]["model"] == "gauss-o-flash"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_게이트웨이가_돌려준_모델_이름을_기록한다(gateway, clock, adapter):
    # config가 요청한 것과 다르면 "설정이 안 먹었다"는 뜻이고, 답은 오므로
    # 아무도 알아채지 못한다 — 그래서 기록해 둔다.
    base_url, _ = gateway
    reply = await build_llm(_cfg(base_url, adapter), clock=clock).ask("hi")
    assert reply.reported_model == "gauss-o-flash"


async def test_200인데_모양이_다르면_실패로_본다(gateway, clock):
    """조용히 빈 문자열을 돌려주면 엔진은 'LLM이 아무 말도 안 했다'로 읽는다."""
    base_url, recorder = gateway
    recorder.payload = {"id": "x", "result": "OpenAI 규약이 아닌 모양"}
    reply = await build_llm(_cfg(base_url, "http"), clock=clock).ask("hi")
    assert reply.status == "error"
    assert "OpenAI 규약과 다르다" in reply.error


async def test_경과_시간을_잰다(gateway, clock):
    base_url, _ = gateway
    ticks = iter([100.0, 100.25])
    from src.infrastructure.llm_http import HttpChatAdapter
    reply = await HttpChatAdapter(_cfg(base_url), clock=clock,
                                  ticker=lambda: next(ticks)).ask("hi")
    assert reply.latency_s == 0.25, "경과는 Clock이 아니라 ticker가 잰다"
