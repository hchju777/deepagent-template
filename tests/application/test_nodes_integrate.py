from src.application.nodes import make_nodes, route_after_integrate
from src.application.state import CaseState
from src.domain.case import Case, Hypothesis, PlanTask
from tests.application.test_nodes_frame import T, _deps


def _state(**kw):
    base = dict(case=Case(id="c-1", gbm="mx", fct="gumi", origin="patrol",
                          symptom="s", t0=T))
    base.update(kw)
    return CaseState(**base)


async def test_보드_갱신과_id_충돌_드롭():
    deps = _deps(['{"hypotheses": [{"id": "h-1", "statement": "갱신", "status": "supported"}], '
                  '"new_tasks": [{"id": "t-1", "goal": "충돌", "role": "data_prober"}, '
                  '{"id": "t-9", "goal": "신규", "role": "data_prober"}], '
                  '"decision": "continue"}'])
    state = _state(plan_tasks=[PlanTask(id="t-1", goal="기존", role="data_prober", status="ok")],
                   hypotheses=[Hypothesis(id="h-1", statement="원래")])
    update = await make_nodes(deps)["integrate"](state)
    new_ids = [t.id for t in update["plan_tasks"]]
    assert "t-9" in new_ids and "t-1" not in new_ids            # 충돌 드롭
    assert any(e["kind"] == "task_id_collision" for e in update["qa_log"])
    assert update["round"] == 1 and update["decision"] == "continue"


async def test_autonomous_default_and_log는_질문을_기록하고_계속한다():
    deps = _deps(['{"decision": "ask", "question": "계획 변경이 있었나요?"}'])
    update = await make_nodes(deps)["integrate"](_state())
    assert update["decision"] == "continue"
    assert any(e["kind"] == "auto_answered" for e in update["qa_log"])

    deps2 = _deps(['{"decision": "ask", "question": "확인 필요"}'])
    state2 = _state(autonomous_question_policy="park")
    update2 = await make_nodes(deps2)["integrate"](state2)
    assert update2["decision"] == "ask" and update2["question"] == "확인 필요"


async def test_라운드_상한은_강제_conclude():
    deps = _deps(['{"decision": "continue"}'])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"max_rounds": 1})
    update = await make_nodes(deps)["integrate"](_state())
    assert update["decision"] == "conclude"
    assert any(e["kind"] == "round_cap" for e in update["qa_log"])


async def test_파싱_이중_실패는_강제_conclude():
    deps = _deps(["엉망", "또 엉망"])
    update = await make_nodes(deps)["integrate"](_state())
    assert update["decision"] == "conclude"
    assert any(e["kind"] == "integrate_parse_failure" for e in update["qa_log"])


def test_route_after_integrate():
    assert route_after_integrate(_state(decision="continue")) == "select"
    assert route_after_integrate(_state(decision="ask")) == "ask_human"
    assert route_after_integrate(_state(decision="conclude")) == "conclude"
