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
