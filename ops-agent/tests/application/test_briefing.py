"""리드에게 주는 재료 — **config에서 나오는가, 그리고 무엇이 안 새는가.**

여기서 지키는 성질 셋:

1. 부를 수 있는 목록은 **생성**된다 — 손으로 적으면 config와 갈라지고, 갈라진 쪽이
   곧 LLM이 믿는 세계가 된다.
2. **접속 정보가 안 섞인다** — 리드는 "무엇을 부를 수 있는가"만 알면 되고,
   "어디에 붙어 있는가"는 어댑터의 일이다.
3. **대상 데이터의 이름을 우리가 안 적는다**(decisions ⑮) — 적어 주면 조사는
   우리가 아는 만큼만 본다.
"""
import json

import pytest

from src.application import briefing
from src.application.state import CaseState
from src.domain.case import EvidenceRef

from tests.application.conftest import DATABASE, SECRET, TOPIC, site_config as site, task


@pytest.fixture
def state(case) -> CaseState:
    return CaseState(case=case)


# ── 목록은 생성된다 ────────────────────────────────────────────────

def test_등재_목록이_config에서_나온다(state):
    """**손으로 적은 목록이면 이 테스트가 실패한다.**

    config에 REST 항목을 더했는데 프롬프트를 안 고치면 리드는 그게 있는 줄도
    모른다. 반대면 없는 것을 계속 부른다. 둘 다 "조사가 이상하다"로만 보인다.
    """
    catalog = briefing.action_catalog(site())
    assert 'rest.query(entry="summary_badge"' in catalog

    more = site(rest={"base_url": "https://h/api", "entries": {
        "summary_badge": {"method": "POST", "path": "/summary/badge"},
        "summary_prod_status": {"method": "POST", "path": "/summary/prod_status",
                                "params": {"line_code": {"type": "list",
                                                         "required": True}}}}})
    grown = briefing.action_catalog(more)
    assert 'entry="summary_prod_status"' in grown
    assert "line_code: list" in grown and "line_code?" not in grown   # required는 ?가 없다


def test_이_사이트에_없는_시스템은_목록에_없다():
    """Kafka가 없는 사이트에 `kafka.tail`을 알려 주면 리드는 그걸 계획에 넣는다.

    실행기가 거부하므로 사고는 안 나지만, **라운드 하나가 통째로 낭비된다** —
    상한이 4라운드인데 하나를 그렇게 쓰면 25%다.
    """
    catalog = briefing.action_catalog(site(kafka=None))
    assert "kafka." not in catalog
    assert "mongo.find" in catalog


def test_등재_목록에_인자_이름이_전부_실린다():
    catalog = briefing.action_catalog(site())
    assert "- mongo.find(collection, filter, sort?, limit?, projection?)" in catalog
    assert "- mongo.list_collections()" in catalog


# ── 접속 정보가 안 샌다 ────────────────────────────────────────────

def test_프롬프트에_접속_정보가_안_섞인다(state):
    """**비밀번호·url·계정이 LLM에게 나가면 그건 게이트웨이 로그에 남는다.**

    사내 게이트웨이가 프롬프트를 어떻게 보관하는지 우리는 모른다. 모르는 곳에
    비밀번호를 보내지 않는 것이 유일하게 지킬 수 있는 규칙이다.
    """
    cfg = site()
    blob = "\n".join(briefing.integrate_fields(state, site_config=cfg,
                                               max_rounds=4).values())
    for leak in (SECRET, "redis://h:6379", "mongodb://h:27017", "dmfReadOnly",
                 "https://h/api", "h:9092"):
        assert leak not in blob, f"접속 정보가 샜다 — {leak}"


def test_대상_데이터의_이름을_우리가_안_적는다(state):
    """토픽·DB 이름을 실어 주면 조사는 **우리가 적어 준 곳만** 본다(decisions ⑮).

    이름은 `list_collections`·`list_topics`·`scan`으로 리드가 찾는다.
    """
    blob = "\n".join(briefing.integrate_fields(state, site_config=site(),
                                               max_rounds=4).values())
    assert TOPIC not in blob and DATABASE not in blob


# ── 블록 ───────────────────────────────────────────────────────────

def test_증거_한_건은_반드시_한_줄이다(case):
    """한 줄이 한 증거다. 날것의 줄바꿈이 실리면 **가짜 항목**이 생기고,
    리드는 있지도 않은 증거 id를 인용하게 된다.

    **진짜 개행을 넣어야 한다.** 이미 이스케이프된 `\\n`을 넣고 "한 줄이다"를
    확인하면 아무것도 검사하지 않은 것이다 — 처음에 그렇게 썼고, 방어를 지워도
    통과했다.
    """
    state = CaseState(case=case, evidence=[
        EvidenceRef(id="t-1.e1", source="mongo.find", summary="첫 줄\n둘째 줄"),
        EvidenceRef(id="t-2.e1", source="rest.query", summary="가\r\n나")])
    block = briefing.evidence_block(state)
    assert len(block.splitlines()) == 2
    assert "둘째 줄" in block          # 눕힐 뿐, 잘라 버리지는 않는다


def test_가설과_태스크도_한_줄씩이다(case):
    """`goal`은 리드가 쓴 문장이고 `error`는 대상 시스템의 예외 메시지다 —
    둘 다 여러 줄이 정상이다."""
    from src.domain.case import Hypothesis

    state = CaseState(case=case,
                      hypotheses=[Hypothesis(id="h-1", statement="가\n나")],
                      plan_tasks=[task("t-1", goal="가\n나", status="error",
                                       error="Traceback\n  File ...")])
    assert len(briefing.hypotheses_block(state).splitlines()) == 1
    assert len(briefing.tasks_block(state).splitlines()) == 1


async def test_실행기가_실제로_낸_증거도_한_줄이다(case, clock):
    """**소비자로 직접 확인한다.** "생산자가 이스케이프한다"는 주석을 믿지 않는다 —
    이 리포에서 주석이 주장하는 배선이 실제로는 없던 사례가 여러 번 있었다.
    """
    from src.application.runner_probe import ProbeRunner
    from src.infrastructure.stubs import StubMongoReader

    class Bundle:
        redis = kafka = rest = None
        mongo = StubMongoReader({"c": [{"msg": "첫 줄\n둘째 줄"}]}, clock=clock)

        def available(self):
            return ["mongo"]

    outcome = await ProbeRunner(Bundle(), clock=clock).run(
        task("t-1", action="mongo.find", params={"collection": "c", "filter": {}}),
        case=case)
    assert outcome.status == "ok", outcome.error
    state = CaseState(case=case, evidence=list(outcome.evidence))
    assert len(briefing.evidence_block(state).splitlines()) == 1


def test_잘린_증거는_잘렸다고_적힌다(case):
    """잘린 표본으로는 "없다"를 주장할 수 없다 — 안 보인 것이 상한 밖일 수 있다."""
    state = CaseState(case=case, evidence=[
        EvidenceRef(id="t-1.e1", source="mongo.find", summary="3건", complete=False)])
    assert "⚠" in briefing.evidence_block(state)


def test_실패한_태스크는_오류가_적힌다(case):
    """`[error]`를 "조회했더니 비어 있다"로 읽으면 없는 이상을 만들어 낸다."""
    state = CaseState(case=case, plan_tasks=[
        task("t-1", status="error", error="ConnectTimeout")])
    assert "[error]" in briefing.tasks_block(state)
    assert "ConnectTimeout" in briefing.tasks_block(state)


def test_아직_없는_것은_없다고_적는다(case):
    state = CaseState(case=case)
    assert briefing.hypotheses_block(state) == "(아직 없다)"
    assert briefing.evidence_block(state) == "(아직 없다)"
    assert briefing.tasks_block(state) == "(아직 없다)"


# ── 자리 이름이 실제로 채워지는 것과 같은가 ────────────────────────

def test_선언한_자리와_실제로_채우는_것이_같다(state):
    """**여기가 갈라지면 프롬프트 검사가 거짓말을 한다.**

    `_load_lead_prompt`는 이 상수로 템플릿을 검사한다. 상수에만 있고 실제로는 안
    채우는 이름이 있으면 그 `{...}`는 검사를 통과한 채 LLM에게 날것으로 나간다.
    """
    cfg = site()
    assert set(briefing.frame_fields(state, site_config=cfg)) == set(briefing.FRAME_SLOTS)
    assert set(briefing.integrate_fields(state, site_config=cfg, max_rounds=4)) \
        == set(briefing.INTEGRATE_SLOTS)


# ── 예시 (`{example}`) — **이게 곧 다음 출력이다** ─────────────────────
# 사내 모델로 재 보니 리드는 판단해서 고르는 게 아니라 예시의 틀을 채운다.
# `id`·`action`·`params`를 그대로 베끼고 `goal`만 자기 말로 바꿨다. 그래서 예시는
# "모양을 보여 주는 것"이 아니라 **우리가 원하는 첫 수 그 자체**여야 한다.

def _example(cfg, phase) -> dict:
    return json.loads(briefing.example_block(cfg, phase=phase))


def test_frame_예시에는_이름을_받는_읽기가_없다():
    """**제일 중요한 성질이다.**

    모델은 `params` 값도 그대로 베낀다. 예시에 `"collection": "..."` 같은 값이
    있으면 **진짜로 그 이름을 조회하고**, 빈 결과가 "데이터가 없다"로 읽힌다 —
    ⑮가 경고하는 바로 그 오독이고, 판정이 없는 이상을 보고하게 된다.

    그래서 frame 예시는 **인자에 대상 이름이 안 들어가는 읽기만** 쓴다.
    """
    from src.domain.actions import DISCOVERED_ARGS

    for task in _example(site(), "frame")["tasks"]:
        named = set(task["params"]) & DISCOVERED_ARGS
        assert not named, f"{task['action']}가 찾아야 아는 이름을 받는다 — {named}"


def test_integrate_예시의_이름_자리는_지시문_모양이다():
    """integrate 시점엔 증거 블록에 진짜 이름이 있으므로 이름을 받는 읽기를 보여 준다.

    단 그 값은 **바꿔 넣으라는 지시로 읽혀야** 한다. `"..."`처럼 완결된 값을 두면
    모델이 그대로 베낀다 — frame에서 `params: {}`를 그대로 베낀 것과 같은 이유다.
    """
    from src.domain.actions import DISCOVERED_ARGS

    placeholders = [v for task in _example(site(), "integrate")["tasks"]
                    for k, v in task["params"].items() if k in DISCOVERED_ARGS]
    assert placeholders, "이름을 받는 읽기가 하나도 없다 — 2라운드가 뭘 하라는 것인가"
    for value in placeholders:
        # 진짜 이름에는 공백이 없다. 공백 + 한국어면 "바꿔 넣어라"로 읽힌다.
        assert " " in value and any("가" <= ch <= "힣" for ch in value), value


def test_예시가_이_사이트에_없는_시스템을_안_쓴다():
    """**예시를 손으로 적으면 이 테스트가 실패한다.**

    Kafka 없는 사이트에 `kafka.list_topics`가 예시로 박혀 있으면 모델은 그걸
    **그대로 부른다.** 실행기가 거부하므로 사고는 안 나지만 라운드가 낭비되고,
    상한이 4라운드면 하나가 25%다.
    """
    lean = site(kafka=None, mongodb=None)
    for phase in ("frame", "integrate"):
        actions = [t["action"] for t in _example(lean, phase)["tasks"]]
        assert actions, f"{phase} 예시에 태스크가 하나도 없다"
        assert not any(a.startswith(("kafka.", "mongo.")) for a in actions), actions


def test_frame_예시가_가설을_둘_이상_세운다():
    """하나면 모델도 하나만 낸다 — 그러면 그것만 확인하고 조사가 끝난다.
    경쟁 가설이 있어야 조사가 갈라진다."""
    assert len(_example(site(), "frame")["hypotheses"]) >= 2


def test_예시가_실제로_프롬프트_재료에_실린다(state):
    """`{example}` 자리가 채워지는지 — 안 실리면 약한 모델은 베낄 것이 없다."""
    fields = briefing.frame_fields(state, site_config=site())
    assert "mongo.list_collections" in fields["example"]


def test_생성한_예시는_반드시_JSON으로_읽힌다():
    """예시가 깨진 JSON이면 모델이 그 모양을 베낀다 — 매 라운드 파싱이 실패한다."""
    for phase in ("frame", "integrate"):
        for cfg in (site(), site(kafka=None), site(kafka=None, mongodb=None),
                    site(redis=None, kafka=None, mongodb=None)):
            body = _example(cfg, phase)
            assert body["tasks"] and isinstance(body["tasks"], list)
