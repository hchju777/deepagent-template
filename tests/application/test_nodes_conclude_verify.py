from src.application.nodes import make_nodes, route_after_verify
from src.application.state import CaseState
from src.domain.case import Case, CauseLink, EvidenceRef, PlanTask, Verdict
from tests.application.test_nodes_frame import T, _deps

VERDICT_JSON = ('{"verdict_type": "stale_data", "confidence": "high", "narrative": "n", '
                '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1"]}}')


def _state(**kw):
    base = dict(case=Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T))
    base.update(kw)
    return CaseState(**base)


async def test_증거_전멸은_LLM_없이_degraded():
    deps = _deps([])            # 스크립트 없음 — LLM 호출되면 RuntimeError로 테스트가 실패한다
    state = _state(plan_tasks=[PlanTask(id="t-1", goal="g", role="data_prober",
                                        status="error", error="타임아웃")])
    update = await make_nodes(deps)["conclude"](state)
    assert update["verdict"].verdict_type == "degraded"
    assert any("타임아웃" in c for c in update["verdict"].caveats)


async def test_재작성_요청은_verify_problems를_프롬프트에_싣는다():
    deps = _deps([VERDICT_JSON])
    state = _state(evidence=[EvidenceRef(id="ev-1", source="mongo:twin_state", summary="s")],
                   verify_problems=["없는 id ev-9 인용"])
    update = await make_nodes(deps)["conclude"](state)
    assert update["verdict"].root_cause.component == "plan-sync"
    assert "없는 id ev-9 인용" in str(deps.lead_llm.calls[0])


def _verdict(ids, caveats=()):
    return Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                   root_cause=CauseLink(component="plan-sync", evidence_ids=list(ids)),
                   caveats=list(caveats))


async def test_없는_id_인용은_재작성_경로():
    deps = _deps([])
    state = _state(verdict=_verdict(["ev-9"]))
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_attempts"] == 1 and update["verify_problems"]
    assert route_after_verify(state.model_copy(update=update)) == "conclude"


async def test_재실패는_강등_통과():
    deps = _deps([])
    state = _state(verdict=_verdict(["ev-9"]), verify_attempts=1)
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_problems"] == []
    assert update["verdict"].confidence == "low"
    assert any("검증 미통과" in c for c in update["verdict"].caveats)


async def test_불완전_증거는_caveat_명시를_요구한다():
    deps = _deps([])
    eid = deps.store.put_evidence("c-1", "kafka:edge.raw", [1, 2], complete=False)
    ok_ref = EvidenceRef(id=eid, source="kafka:edge.raw", summary="s", complete=False)
    bad = _state(evidence=[ok_ref], verdict=_verdict([eid]))
    update = await make_nodes(deps)["verify"](bad)
    assert update["verify_problems"]                       # caveat에 없음 → 문제

    good = _state(evidence=[ok_ref], verdict=_verdict([eid], caveats=[f"불완전 증거 {eid} 기반"]))
    update2 = await make_nodes(deps)["verify"](good)
    assert update2["verify_problems"] == []


async def test_불완전_증거_caveat은_토큰_경계로_매칭된다():
    deps = _deps([])
    for n in range(10):
        deps.store.put_evidence("c-1", "kafka:edge.raw", [n], complete=(n != 0))
    ref1 = EvidenceRef(id="ev-1", source="kafka:edge.raw", summary="s", complete=False)
    # caveat이 ev-10만 언급 — ev-1 미명시로 판정되어야 한다
    state = _state(evidence=[ref1], verdict=_verdict(["ev-1"], caveats=["불완전 증거 ev-10 기반"]))
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_problems"]


async def test_verdict_없이_verify에_들어와도_raise하지_않는다():
    deps = _deps([])
    update = await make_nodes(deps)["verify"](_state())
    assert update["verify_problems"] == []
