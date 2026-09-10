"""LLM 설정 — 반쯤 채운 인증을 기동에서 막는다."""
import pytest
from pydantic import ValidationError

from src.config.schema_llm import LlmConfig

GATEWAY = dict(model="gauss-o-flash", model_id="339", base_url="https://g.example/v1",
               pass_key="pass-123", client_key="client-456")


def test_사내_게이트웨이_설정이_통과한다():
    cfg = LlmConfig(**GATEWAY)
    assert cfg.adapter == "chat_model" and cfg.temperature == 0.0


def test_키_하나만_채우면_거부한다():
    # 반쯤 인증된 상태로 나가면 게이트웨이는 401을 주고, 그 메시지로는
    # 어느 키가 빠졌는지 알 수 없다.
    with pytest.raises(ValidationError, match="둘 다 있거나 둘 다 없어야"):
        LlmConfig(model="m", base_url="https://g", pass_key="p")


def test_게이트웨이를_쓰면_model_id가_필요하다():
    with pytest.raises(ValidationError, match="model_id도 필요하다"):
        LlmConfig(model="m", base_url="https://g", pass_key="p", client_key="c")


def test_네트워크_어댑터는_base_url이_필요하다():
    with pytest.raises(ValidationError, match="base_url이 필요하다"):
        LlmConfig(adapter="http", model="m")


def test_echo_어댑터는_base_url이_없어도_된다():
    assert LlmConfig(adapter="echo", model="dev").base_url == ""


def test_헤더_값에_ASCII_밖_문자가_있으면_거부한다():
    # 런타임에 나는 UnicodeEncodeError로는 원인이 키라는 것을 짐작할 수 없다.
    with pytest.raises(ValidationError, match="ASCII 밖 문자"):
        LlmConfig(**{**GATEWAY, "pass_key": "패스키"})


def test_에러_메시지에_키_값을_담지_않는다():
    try:
        LlmConfig(**{**GATEWAY, "pass_key": "비밀키값"})
    except ValidationError as exc:
        assert "비밀키값" not in str(exc), "에러 메시지로 비밀값이 샜다"
    else:
        raise AssertionError("거부되지 않았다")


def test_헤더_조립은_한_곳뿐이고_이름이_정확하다():
    headers = LlmConfig(**GATEWAY).gateway_headers()
    assert headers == {"X-FABRIX-CLIENT": "pass-123",
                       "X-OPENAPI-TOKEN": "client-456",      # OPENAI가 아니다
                       "X-LLM-MODEL-ID": "339"}


def test_게이트웨이_키가_없으면_사내_헤더를_안_붙인다():
    # 다른 OpenAI 호환 엔드포인트(OpenAI 본체·vLLM·Ollama)는 api_key만 쓴다.
    cfg = LlmConfig(model="gpt-4o-mini", base_url="https://api.example/v1", api_key="sk-x")
    assert cfg.gateway_headers() == {}


def test_임의_헤더를_더할_수_있다():
    # 다른 사내 게이트웨이로 갈아끼울 때 코드를 고치지 않아도 되게.
    cfg = LlmConfig(model="m", base_url="https://g", headers={"X-Tenant": "mx"})
    assert cfg.gateway_headers() == {"X-Tenant": "mx"}


def test_temperature_범위를_검사한다():
    with pytest.raises(ValidationError, match="temperature는 0~2"):
        LlmConfig(**{**GATEWAY, "temperature": 3.0})


def test_비밀값은_repr에_안_찍힌다():
    cfg = LlmConfig(**GATEWAY)
    assert "pass-123" not in repr(cfg) and "client-456" not in repr(cfg)


def test_describe에_비밀값이_없다():
    described = LlmConfig(**GATEWAY).describe()
    assert "pass-123" not in described
    assert "gauss-o-flash" in described and "339" in described


# ── 게이트웨이가 실제로 어느 모델로 답하는가 ───────────────────────────
#
# 사내 게이트웨이는 `GET /models`에 405를 준다 — 어느 model_id가 어느 모델인지
# 런타임에 알아낼 방법이 없다. 그리고 body의 `model`은 검증조차 안 된다(없는
# 이름을 적어도 정상 응답이 온다). 확인할 수 있는 유일한 경로가 응답에 실려 오는
# 이름이고, 그것을 사람이 한 번 확인해 config에 박제한다.

def test_박제한_이름과_일치하면_문제없다():
    cfg = LlmConfig(**GATEWAY, expect_reported_model="openai/gpt-oss-120b")
    assert cfg.reported_model_problem("openai/gpt-oss-120b") is None


def test_박제한_뒤_게이트웨이가_모델을_바꾸면_잡는다():
    """이게 이 필드의 존재 이유다 — 답은 계속 오므로 다른 방법으로는 모른다."""
    cfg = LlmConfig(**GATEWAY, expect_reported_model="openai/gpt-oss-120b")
    problem = cfg.reported_model_problem("meta/llama-3-70b")
    assert problem and "게이트웨이가 모델을 바꿨다" in problem


def test_박제_전에_불일치면_적어야_할_줄을_알려준다():
    # 실패 메시지가 "틀렸다"로 끝나면 사람은 무엇을 해야 할지 모른다.
    problem = LlmConfig(**GATEWAY).reported_model_problem("openai/gpt-oss-120b")
    assert problem
    assert '"expect_reported_model": "openai/gpt-oss-120b"' in problem


def test_요청한_이름으로_답하면_박제가_필요없다():
    # 보통의 게이트웨이 — body의 model을 존중한다.
    cfg = LlmConfig(model="gpt-4o-mini", base_url="https://api.example/v1")
    assert cfg.reported_model_problem("gpt-4o-mini") is None


def test_접두사만_다르면_같은_모델로_본다():
    # `gpt-oss-120b`를 요청하면 `openai/gpt-oss-120b`로 답하는 게이트웨이가 흔하다.
    cfg = LlmConfig(model="gpt-oss-120b", base_url="https://g/v1")
    assert cfg.reported_model_problem("openai/gpt-oss-120b") is None


def test_이름을_안_실어_주면_확인하지_않는다():
    """확인할 수 없는 것을 실패로 만들면 그 신호는 곧 무시된다."""
    assert LlmConfig(**GATEWAY).reported_model_problem(None) is None
    assert LlmConfig(**GATEWAY).reported_model_problem("") is None


def test_describe가_실제_모델을_같이_보인다():
    described = LlmConfig(**GATEWAY, expect_reported_model="openai/gpt-oss-120b").describe()
    assert "gauss-o-flash" in described and "실제=openai/gpt-oss-120b" in described


def test_박제가_요청_이름과_같으면_describe를_어지럽히지_않는다():
    described = LlmConfig(model="m", base_url="https://g", expect_reported_model="m").describe()
    assert "실제=" not in described
