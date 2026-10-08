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


async def test_스트리밍이면_조각을_모아_한_답으로_주고_첫_토큰_시각을_적는다(gateway, clock):
    """사내 게이트웨이의 180초 끊김은 총 시간이 아니라 **유휴** 상한일 가능성이 크다 — 스트리밍이면 출력이 시작된
    뒤로는 바이트가 흐른다. 호출부는 전과 같은 `LlmReply` 하나를 받는다."""
    base_url, recorder = gateway
    recorder.reply = "pong-streamed"
    reply = await build_llm(_cfg(base_url, "chat_model", stream=True), clock=clock).ask("hi")
    assert reply.status == "ok", reply.error
    assert reply.text == "pong-streamed" and reply.first_token_s is not None and reply.latency_s is not None
    assert recorder.requests[-1]["body"].get("stream") is True
    plain = await build_llm(_cfg(base_url, "chat_model", stream=False), clock=clock).ask("hi")
    assert plain.first_token_s is None and not recorder.requests[-1]["body"].get("stream")


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_토큰_상한이_요청에_실린다(gateway, clock, adapter):
    base_url, recorder = gateway
    await build_llm(_cfg(base_url, adapter, max_tokens=321), clock=clock).ask("hi")
    body = recorder.requests[-1]["body"]
    # langchain-openai는 `max_completion_tokens`(OpenAI의 현재 이름)로, 우리 http 어댑터는 `max_tokens`로 내보낸다.
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 321
    await build_llm(_cfg(base_url, adapter), clock=clock).ask("hi")
    body = recorder.requests[-1]["body"]
    assert "max_tokens" not in body and "max_completion_tokens" not in body   # 안 적으면 안 보낸다


async def test_close는_chat_model의_httpx_클라이언트를_닫는다(gateway, clock):
    """사내 Windows: 조사가 끝나고 프로세스가 내려갈 때 `ConnectionResetError(10054)` 트레이스백 — 안 닫은 httpx 풀을
    proactor가 치우며 내는 소음이다. 원인부터 없앤다: 어댑터가 자기 클라이언트를 닫는다."""
    base_url, _ = gateway
    llm = build_llm(_cfg(base_url, "chat_model"), clock=clock)
    await llm.close()                                     # 아직 클라이언트가 없어도 조용하다
    assert (await llm.ask("hi")).status == "ok"
    await llm.close()
    assert llm._http.is_closed and llm._ahttp.is_closed
    await llm.close()                                     # 두 번 닫아도 조용하다
    http = build_llm(_cfg(base_url, "http"), clock=clock)
    await http.close()                                    # 호출마다 열고 닫으므로 할 일이 없다 — 계약은 같다


# ── R2-3 ② — 429는 기다렸다 한 번 더, 호출 간 최소 간격 ──

def _adapter(cfg, *, clock, sleep):
    from src.infrastructure.llm_chat_model import ChatModelAdapter
    from src.infrastructure.llm_http import HttpChatAdapter
    return (HttpChatAdapter if cfg.adapter == "http" else ChatModelAdapter)(cfg, clock=clock, sleep=sleep)


def _at(seconds):
    from datetime import timedelta
    return (T0 + timedelta(seconds=seconds)).isoformat()


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_429면_nextAccessTime까지_기다렸다_한_번_다시_묻는다(gateway, clock, adapter):
    """사내 10-08: 할당량에 걸리면 0초 만에 재시도해 두 번 다 날렸다. 본문의 시각까지 기다린 뒤 한 번 더 — 실패로 안 센다."""
    base_url, recorder = gateway
    recorder.rate_limit = [{"error": {"message": "quota exceeded", "nextAccessTime": _at(7)}}]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    reply = await _adapter(_cfg(base_url, adapter), clock=clock, sleep=sleep).ask("hi")
    assert reply.status == "ok" and reply.text == "pong", reply.error
    assert slept == [7.0] and reply.waited_s == 7.0 and reply.rate_limited == 1
    assert len(recorder.requests) == 2


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_두_번째도_429면_오류이고_더_기다리지_않는다(gateway, clock, adapter):
    base_url, recorder = gateway
    recorder.rate_limit = [{"nextAccessTime": _at(3)}, {"nextAccessTime": _at(60)}]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    reply = await _adapter(_cfg(base_url, adapter), clock=clock, sleep=sleep).ask("hi")
    assert reply.status == "error" and "429" in reply.error and reply.rate_limited == 1 and reply.waited_s == 3.0
    assert slept == [3.0] and len(recorder.requests) == 2


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_시각이_없으면_Retry_After나_기본값을_상한_안에서_기다린다(gateway, clock, adapter):
    base_url, recorder = gateway
    recorder.rate_limit = [{"error": {"message": "quota"}}]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    reply = await _adapter(_cfg(base_url, adapter, rate_wait_max_s=10), clock=clock, sleep=sleep).ask("hi")
    assert reply.status == "ok" and slept == [10.0]                    # 기본 60초를 상한 10초가 자른다
    recorder.rate_limit = [{"error": {"message": "quota"}}]
    recorder.retry_after = "4"
    reply = await _adapter(_cfg(base_url, adapter, rate_wait_max_s=10), clock=clock, sleep=sleep).ask("hi")
    assert reply.status == "ok" and slept == [10.0, 4.0]


async def test_최소_간격은_같은_게이트웨이의_어댑터_둘이_같이_지킨다(gateway, clock):
    """리드와 판정이 다른 어댑터여도 할당량은 하나다 — 둘째 어댑터의 첫 호출도 간격을 기다린다."""
    base_url, recorder = gateway
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    lead = _adapter(_cfg(base_url, "http", min_interval_s=5), clock=clock, sleep=sleep)
    verdict = _adapter(_cfg(base_url, "chat_model", min_interval_s=5), clock=clock, sleep=sleep)
    assert (await lead.ask("a")).status == "ok" and slept == []
    assert (await verdict.ask("b")).status == "ok" and len(slept) == 1 and 4.0 < slept[0] <= 5.0


# ── R2-3 ③ — 답 스키마는 response_format으로 나간다 ──

_CLOSED = {"title": "reply", "type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
           "additionalProperties": False}
_OPEN = {"title": "frame_reply", "type": "object",
         "properties": {"tasks": {"type": "array", "items": {"type": "object"}}},
         "required": ["tasks"], "additionalProperties": False}


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_스키마를_주면_response_format으로_나가고_닫힌_것만_strict다(gateway, clock, adapter):
    """사내 10-08: 빠른 모델이 따옴표를 빠뜨려 JSON이 깨졌다 — 서버가 문법을 강제하게 한다. `params`가 자유형인 액션 턴은
    strict가 못 되고, 닫힌 스키마(판정 턴)만 strict다. 자유 질문(`llm ask`)에는 안 보낸다."""
    base_url, recorder = gateway
    llm = build_llm(_cfg(base_url, adapter), clock=clock)
    assert (await llm.ask("hi", schema=_OPEN)).status == "ok"
    sent = recorder.requests[-1]["body"]["response_format"]
    assert sent["type"] == "json_schema" and sent["json_schema"]["name"] == "frame_reply"
    assert sent["json_schema"]["strict"] is False and sent["json_schema"]["schema"] == _OPEN
    await llm.ask("hi", schema=_CLOSED)
    assert recorder.requests[-1]["body"]["response_format"]["json_schema"]["strict"] is True
    await llm.ask("hi")
    assert "response_format" not in recorder.requests[-1]["body"]
    await build_llm(_cfg(base_url, adapter, response_format="json_object"), clock=clock).ask("hi", schema=_OPEN)
    assert recorder.requests[-1]["body"]["response_format"] == {"type": "json_object"}
    await build_llm(_cfg(base_url, adapter, response_format="none"), clock=clock).ask("hi", schema=_OPEN)
    assert "response_format" not in recorder.requests[-1]["body"]


async def test_스트리밍에서도_response_format이_나간다(gateway, clock):
    base_url, recorder = gateway
    reply = await build_llm(_cfg(base_url, "chat_model", stream=True), clock=clock).ask("hi", schema=_CLOSED)
    assert reply.status == "ok" and recorder.requests[-1]["body"]["stream"] is True
    assert recorder.requests[-1]["body"]["response_format"]["json_schema"]["strict"] is True
