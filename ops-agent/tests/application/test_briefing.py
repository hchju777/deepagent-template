"""리드에게 주는 재료 — **config에서 나오는가, 그리고 무엇이 안 새는가.**

여기서 지키는 성질 셋:

1. 부를 수 있는 목록은 **생성**된다 — 손으로 적으면 config와 갈라지고, 갈라진 쪽이
   곧 LLM이 믿는 세계가 된다.
2. **접속 정보가 안 섞인다** — 리드는 "무엇을 부를 수 있는가"만 알면 되고,
   "어디에 붙어 있는가"는 어댑터의 일이다.
3. **대상 데이터의 이름을 우리가 안 적는다**(decisions ⑮) — 적어 주면 조사는
   우리가 아는 만큼만 본다.
"""
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
