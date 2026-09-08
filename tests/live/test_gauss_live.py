"""사내 LLM 게이트웨이에 **실제로 질의하는** 테스트 — 사내망에서만 돈다.

기본 실행에서는 빠진다(`pytest.ini`의 `addopts = -m "not live_llm"`).
사내에서 돌리려면:

    pytest tests/live -m live_llm -v

`tests/infrastructure/test_llm.py`가 오프라인에서 잠그는 것은 **배선**이다
(헤더가 실리는가, verify가 어디로 가는가, 전역을 안 건드리는가). 여기 있는
것은 그것으로 절대 알 수 없는 것들이다: 게이트웨이가 실제로 응답하는가,
인증이 진짜로 헤더 셋으로 이뤄지는가, 그리고 **이 모델이 조사 엔진이 요구하는
JSON을 낼 수 있는가**.

마지막 항목이 제일 중요하다. 노드(frame/integrate/conclude/verify)와 접수는
전부 LLM 응답을 JSON으로 파싱한다 — 모델이 바뀌면 여기가 먼저 깨지고,
그 증상은 "조사가 도중에 조용히 멈춘다"로 나타난다.

이 파일은 config/app.json의 **실제 설정**을 읽는다(env가 아니라). 사내에서
운영할 때 쓰는 base_url·키·TLS 선택 그대로 물어봐야 의미가 있기 때문이다.
"""
import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pydantic import SecretStr

from src.config.loader import ConfigError, load_app_config
from src.infrastructure.llm import build_llm_factory, ca_bundle_problems

pytestmark = pytest.mark.live_llm

CONFIG_ROOT = Path(os.environ.get("AGENT_CONFIG_ROOT", "config"))


@pytest.fixture(scope="module")
def gateway():
    """실제 config에서 게이트웨이 설정을 읽는다 — 없으면 건너뛴다."""
    load_dotenv()
    try:
        app = load_app_config(CONFIG_ROOT, env=os.environ)
    except (ConfigError, FileNotFoundError) as exc:
        pytest.skip(f"{CONFIG_ROOT}/app.json을 읽을 수 없다 — {exc}")
    cfg = app.llm.gateway
    problems = ca_bundle_problems(cfg)
    if problems:
        pytest.skip(f"ca_bundle 문제 — {problems[0]}")
    return cfg


@pytest.fixture(scope="module")
def model(gateway):
    return build_llm_factory(gateway, warn=lambda _m: None)("lead")


async def _say(model, prompt: str) -> str:
    reply = await model.ainvoke(prompt)
    return str(getattr(reply, "content", reply))


# ── 붙는가 ─────────────────────────────────────────────────────────────

async def test_게이트웨이가_응답한다(model):
    text = await _say(model, "Reply with exactly: pong")
    assert text.strip(), "빈 응답 — 게이트웨이가 200을 줬지만 내용이 없다"


async def test_설정한_모델이_실제로_쓰인다(gateway, model):
    # 헤더의 X-LLM-MODEL-ID와 body의 model 중 어느 쪽이 이기는지는 게이트웨이가
    # 정한다. 응답 메타데이터가 model_id를 되돌려주면 우리가 고른 모델이 실제로
    # 돌았다는 뜻이다 — 안 돌려주는 게이트웨이도 있으므로 없으면 건너뛴다.
    reply = await model.ainvoke("hi")
    reported = (reply.response_metadata or {}).get("model_name")
    if not reported:
        pytest.skip("게이트웨이가 응답에 모델 이름을 안 싣는다")
    assert gateway.model_id in reported, (
        f"config는 {gateway.model_id}를 요청했는데 게이트웨이는 {reported}로 응답했다")


# ── 인증이 정말 헤더 셋으로 이뤄지는가 ─────────────────────────────────

async def test_틀린_키는_거부된다(gateway):
    """`api_key="EMPTY"` sentinel이 우연히 인증을 통과시키고 있지 않은지 본다.

    이게 통과해 버리면 우리가 "헤더로 인증한다"고 믿는 배선이 사실은 아무것도
    인증하지 않는 것이고, 키를 잘못 넣어도 아무도 모르게 된다.
    """
    broken = gateway.model_copy(update={"client_key": SecretStr("wrong-key")})
    model = build_llm_factory(broken, warn=lambda _m: None)("lead")
    try:
        reply = await model.ainvoke("hi")
    except Exception as exc:                                   # noqa: BLE001
        text = f"{type(exc).__name__}: {exc}".lower()
        # 연결 실패로 통과하면 안 된다 — 그건 "인증이 막았다"가 아니라 "닿지도
        # 못했다"이고, 이 테스트가 물으려는 것을 하나도 확인하지 못한 것이다.
        if any(w in text for w in ("connect", "timeout", "resolve", "certificate")):
            pytest.fail(f"인증이 아니라 연결에서 실패했다 — 이 테스트는 아무것도 확인하지 못했다: {exc}")
        assert any(w in text for w in ("401", "403", "unauthor", "forbidden",
                                       "authent", "invalid", "denied")), (
            f"거부되긴 했는데 인증 실패로 보이지 않는다: {exc}")
    else:
        pytest.fail(f"틀린 client_key로도 응답이 왔다 — 헤더가 인증하고 있지 않다: "
                    f"{str(reply.content)[:80]!r}")


# ── 조사 엔진이 실제로 요구하는 것 ─────────────────────────────────────

async def test_모델이_JSON만_내놓을_수_있다(model):
    """노드·접수가 전부 응답을 json.loads한다 — 여기가 깨지면 조사가 멈춘다.

    코드펜스(```json)를 두르는 모델이 흔하므로 그것까지 벗겨 보고 판정한다.
    벗겨야 통과한다면 그것 자체가 알아야 할 사실이다.
    """
    text = await _say(model, '아래 JSON만 출력하고 다른 말은 하지 마라: '
                             '{"status": "ok", "count": 2}')
    stripped = text.strip()
    fenced = stripped.startswith("```")
    if fenced:
        stripped = stripped.strip("`")
        stripped = stripped[4:] if stripped.lower().startswith("json") else stripped
    data = json.loads(stripped.strip())
    assert data == {"status": "ok", "count": 2}
    assert not fenced, ("모델이 JSON을 코드펜스로 감싼다 — 노드의 파서가 이걸 "
                        "벗기는지 확인해야 한다(src/application/nodes.py)")


async def test_한국어_지시를_따른다(model):
    # 이 시스템의 프롬프트는 전부 한국어다. 모델이 한국어 지시를 무시하면
    # 조사 품질이 아니라 파싱부터 무너진다.
    text = await _say(model, "'확인'이라는 두 글자만 출력하라. 다른 말은 하지 마라.")
    assert "확인" in text


async def test_같은_질문에_같은_답을_준다(model):
    # temperature=0으로 만든다(스펙 §5.5의 결정론). 게이트웨이가 이를 무시하면
    # 벤치 시나리오를 실LLM으로 돌릴 때 재현이 안 된다 — 알고는 있어야 한다.
    prompt = "3 더하기 4는? 숫자만 답하라."
    first, second = await _say(model, prompt), await _say(model, prompt)
    assert first.strip() == second.strip(), (
        f"temperature=0인데 응답이 갈렸다: {first.strip()!r} vs {second.strip()!r}")


# ── 조사 경로가 쓰는 표면 ───────────────────────────────────────────────

async def test_노드가_쓰는_ainvoke_표면_그대로_동작한다(model):
    # 노드는 messages 리스트를 넘긴다(문자열이 아니라). 실제 호출 모양으로
    # 한 번 밟아 둔다 — 문자열만 되고 리스트는 안 되는 조합이 있다.
    from langchain_core.messages import HumanMessage, SystemMessage

    reply = await model.ainvoke([SystemMessage(content="너는 짧게 답한다."),
                                 HumanMessage(content="1+1은?")])
    assert str(reply.content).strip()
