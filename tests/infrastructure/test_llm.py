"""사내 게이트웨이 조립의 계약.

여기 있는 것들은 **사내에서만 확인 가능한 것**("게이트웨이가 200을 준다")이
아니라, 오프라인에서 잠글 수 있는 배선이다: 헤더가 실리는가, verify가
어디로 가는가, 그리고 무엇보다 **전역을 안 건드리는가**.

마지막 항목이 이 파일이 존재하는 주된 이유다. 사내 인증서를 통과시키는
흔한 처방은 `ssl._create_default_https_context`를 통째로 바꾸는 것인데,
그러면 pymongo·redis·aiokafka·httpx(대상 REST 프로버)·aiosmtplib의 검증까지
같이 꺼진다 — LLM 하나 붙이려고 대상 시스템 접속 다섯 종의 인증서 검증을
잃는 것이다. 산문 규율은 읽지 않으면 무력하므로 테스트가 지킨다
(규율 9를 `tests/domain/test_ports.py`가 지키는 것과 같은 이유).
"""
import os
import ssl

import certifi
import httpx
import pytest
from pydantic import ValidationError

from src.config.schema_app import LlmConfig, LlmGateway
from src.infrastructure.llm import ScriptedLLM, build_llm_factory, ca_bundle_problems


def _cfg(**over) -> LlmGateway:
    base = {"base_url": "https://llm.test/v1", "pass_key": "PASS-SECRET",
            "client_key": "CLIENT-SECRET", "model_id": "gauss-x"}
    return LlmGateway(**{**base, **over})


def _record_httpx(monkeypatch) -> list:
    """httpx 클라이언트 생성 시의 verify 인자를 기록한다.

    함수로 갈아끼우면 안 된다 — openai SDK가 `isinstance(client, httpx.Client)`로
    레거시 클라이언트를 가리므로 `httpx.Client`는 **클래스로** 남아야 한다.
    """
    seen: list = []

    class RecordingClient(httpx.Client):
        def __init__(self, **kw):
            seen.append(("sync", kw.get("verify")))
            super().__init__(**kw)

    class RecordingAsyncClient(httpx.AsyncClient):
        def __init__(self, **kw):
            seen.append(("async", kw.get("verify")))
            super().__init__(**kw)

    monkeypatch.setattr(httpx, "Client", RecordingClient)
    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    return seen


# 파일이 로드되는 시점, 즉 아직 아무도 오염시키지 않았을 때의 값을 잡아둔다.
# 테스트 안에서 before를 잡으면 **앞선 테스트가 이미 오염시킨 값**을 기준으로
# 삼게 되어 가드가 조용히 무력해진다(실제로 그랬다 — 구 처방을 되살린 RED
# 실험에서 전역 가드 셋만 통과했다).
_PRISTINE_SSL_CONTEXT = ssl._create_default_https_context
_CA_ENV_KEYS = ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")
_PRISTINE_CA_ENV = {k: os.environ.get(k) for k in _CA_ENV_KEYS}


@pytest.fixture(autouse=True)
def _전역은_어느_테스트에서도_안_바뀐다():
    """이 파일의 **모든** 테스트에 붙는다.

    가드를 테스트 하나에만 두면 그 테스트만 지키고, 순서가 바뀌면 그것마저
    무력해진다. 조립 경로 어디서든 전역이 오염되면 여기서 걸린다.
    """
    import requests
    request_before = requests.Session.request
    yield
    assert ssl._create_default_https_context is _PRISTINE_SSL_CONTEXT, (
        "전역 SSL 컨텍스트가 바뀌었다 — 대상 시스템 어댑터(Mongo/Redis/Kafka/REST)의 "
        "인증서 검증까지 같이 꺼진다")
    assert {k: os.environ.get(k) for k in _CA_ENV_KEYS} == _PRISTINE_CA_ENV
    assert requests.Session.request is request_before


# ── 인증: Authorization이 아니라 헤더 셋이 한다 ────────────────────────

def test_게이트웨이_헤더_셋이_실린다():
    model = build_llm_factory(_cfg())("lead")
    assert model.default_headers == {
        "X-FABRIX-CLIENT": "PASS-SECRET",
        "X-OPENAPI-TOKEN": "CLIENT-SECRET",
        "X-LLM-MODEL-ID": "gauss-x",
    }


def test_base_url과_model이_config에서_온다():
    model = build_llm_factory(_cfg())("lead")
    assert model.openai_api_base == "https://llm.test/v1"
    assert model.model_name == "gauss-x"
    assert model.temperature == 0      # 결정론 — 스펙 §5.5


def test_api_key는_sentinel이고_진짜_비밀이_아니다():
    # ChatOpenAI는 api_key가 없으면 OPENAI_API_KEY env를 뒤지다 죽는다. 자리를
    # 채우되 그 값이 비밀로 오인되면 안 된다 — 인증은 헤더가 한다.
    model = build_llm_factory(_cfg())("lead")
    assert model.openai_api_key.get_secret_value() == "EMPTY"


# ── TLS: 범위가 이 커넥션 하나로 닫히는가 ──────────────────────────────

def test_기본은_검증_켬(monkeypatch):
    seen = _record_httpx(monkeypatch)
    build_llm_factory(_cfg())("lead")
    assert seen == [("sync", True), ("async", True)]


def test_ca_bundle을_주면_그_번들로_검증한다(monkeypatch):
    # 경로 문자열을 그대로 넘기지 않는다 — httpx 0.28이 `verify=<str>`을
    # deprecate했고, SSLContext는 전역에 흔적을 안 남긴다.
    seen = _record_httpx(monkeypatch)
    build_llm_factory(_cfg(ca_bundle=certifi.where()))("lead")
    contexts = [verify for _, verify in seen]
    assert len(contexts) == 2
    assert all(isinstance(c, ssl.SSLContext) for c in contexts)
    assert all(c.verify_mode == ssl.CERT_REQUIRED for c in contexts)
    assert contexts[0].get_ca_certs(), "번들의 CA가 로드되지 않았다"


def test_ca_bundle_경로가_틀리면_기동_검증이_잡는다():
    # 문자열 타입은 맞고 파일만 없다 — config 로딩은 이걸 못 본다.
    problems = ca_bundle_problems(_cfg(ca_bundle="/없는/경로/ca.pem"))
    assert len(problems) == 1 and "/없는/경로/ca.pem" in problems[0]


def test_ca_bundle이_없으면_검사할_것도_없다():
    assert ca_bundle_problems(_cfg()) == []


def test_tls_verify_false는_이_커넥션에만_적용된다(monkeypatch):
    seen = _record_httpx(monkeypatch)
    build_llm_factory(_cfg(tls_verify=False), warn=lambda _m: None)("lead")
    assert seen == [("sync", False), ("async", False)]


def test_verify에_경로_문자열을_넘기지_않는다(monkeypatch):
    # httpx 0.28의 DeprecationWarning이고, requirements가 httpx<1이라 마이너
    # 상승에서 사라질 수 있다. 회귀하면 여기서 걸린다.
    seen = _record_httpx(monkeypatch)
    build_llm_factory(_cfg(ca_bundle=certifi.where()))("lead")
    assert not any(isinstance(verify, str) for _, verify in seen)


def test_동기_클라이언트도_같은_verify를_받는다(monkeypatch):
    # 비동기만 주면 ChatOpenAI가 동기 호출 시 자기 클라이언트를 따로 만들고,
    # 그 하나가 verify를 안 물려받아 사내 인증서에서 조용히 실패한다.
    seen = _record_httpx(monkeypatch)
    build_llm_factory(_cfg(ca_bundle=certifi.where()))("lead")
    assert {kind for kind, _ in seen} == {"sync", "async"}
    assert seen[0][1] is seen[1][1], "동기·비동기가 서로 다른 verify를 받았다"


# ── 전역 오염 금지 — 이 파일의 주된 이유 ───────────────────────────────
# 단정은 위의 autouse 픽스처가 모든 테스트에 대해 한다. 여기 있는 것은
# "검증을 끈 채로 조립해도 전역은 그대로다"라는 제일 위험한 경로를 명시적으로
# 한 번 더 밟아 주는 것이다(픽스처가 그 결과를 본다).

def test_검증을_꺼도_전역은_그대로다():
    build_llm_factory(_cfg(tls_verify=False), warn=lambda _m: None)("lead")


def test_tls_verify_false면_경고를_한_번_낸다():
    warnings: list[str] = []
    factory = build_llm_factory(_cfg(tls_verify=False), warn=warnings.append)
    factory("lead")
    factory("judge")
    assert len(warnings) == 1 and "tls_verify" in warnings[0]


# ── 모델은 하나 ────────────────────────────────────────────────────────

def test_세_역할이_같은_인스턴스를_받는다():
    # 게이트웨이가 모델을 하나만 연다. 역할마다 새로 만들면 같은 게이트웨이를
    # 향한 httpx 커넥션 풀이 사이트 수만큼 생긴다.
    factory = build_llm_factory(_cfg())
    assert factory("lead") is factory("subagent") is factory("judge")


def test_팩토리를_만드는_것만으로는_모델을_안_만든다(monkeypatch):
    # 조립은 기동 경로다 — judge=rule만 쓰는 배치가 게이트웨이 연결 준비 때문에
    # 느려지거나 실패하면 안 된다.
    seen = _record_httpx(monkeypatch)
    build_llm_factory(_cfg())
    assert seen == []


# ── 비밀 취급 ──────────────────────────────────────────────────────────

def test_config_repr에_비밀이_새지_않는다():
    cfg = _cfg()
    text = repr(cfg) + str(cfg) + repr(LlmConfig(gateway=cfg))
    assert "PASS-SECRET" not in text and "CLIENT-SECRET" not in text


# ── 스키마가 막는 것 ───────────────────────────────────────────────────

def test_ca_bundle과_tls_verify_false를_함께_쓰면_거부(monkeypatch):
    with pytest.raises(ValidationError):
        _cfg(ca_bundle=certifi.where(), tls_verify=False)


@pytest.mark.parametrize("field", ["base_url", "model_id"])
def test_빈_필수값은_거부(field):
    with pytest.raises(ValidationError):
        _cfg(**{field: "   "})


def test_모르는_키는_거부():
    # StrictModel — 오타난 키가 조용히 무시되면 "설정했는데 안 먹는다"가 된다.
    with pytest.raises(ValidationError):
        _cfg(tls_verfiy=False)


# ── ScriptedLLM — 테스트 대역 자신의 계약 ──────────────────────────────

async def test_스크립트_LLM은_순서대로_응답하고_소진되면_시끄럽게():
    llm = ScriptedLLM(['{"a": 1}', "두번째"])
    r1 = await llm.ainvoke([("user", "질문1")])
    assert r1.content == '{"a": 1}'
    r2 = await llm.ainvoke([("user", "질문2")])
    assert r2.content == "두번째"
    assert len(llm.calls) == 2
    with pytest.raises(RuntimeError):
        await llm.ainvoke([("user", "초과")])
