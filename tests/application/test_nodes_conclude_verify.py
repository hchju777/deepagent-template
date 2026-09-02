from src.application.nodes import make_nodes
from src.application.state import CaseState
from src.domain.case import Case, EvidenceRef, PlanTask
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
