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


async def test_라운드_상한이_ask를_삼키면_qa_log에_기록한다():
    # park 정책이라 라운드 상한 검사 시점까지 decision이 "ask"로 살아있다 —
    # 상한이 그걸 conclude로 덮어쓰면 질문이 사람에게 닿지 못하고 사라진다(M9b).
    deps = _deps(['{"decision": "ask", "question": "확인 필요"}'])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"max_rounds": 1})
    state = _state(autonomous_question_policy="park")
    update = await make_nodes(deps)["integrate"](state)
    assert update["decision"] == "conclude" and update["question"] is None
    dropped = [e for e in update["qa_log"] if e["kind"] == "question_dropped_by_round_cap"]
    assert dropped == [{"kind": "question_dropped_by_round_cap", "question": "확인 필요"}]


async def test_integrate는_new_tasks의_LLM_주입_상태를_pending으로_강제한다():
    # new_tasks도 frame의 tasks와 같은 위험이 있다(C1) — LLM이 status=running을 실어도
    # State에는 pending·빈 결과로 들어가야 한다.
    deps = _deps(['{"new_tasks": [{"id": "t-9", "goal": "신규", "role": "data_prober", '
                  '"status": "running", "result_evidence_ids": ["ev-x"], '
                  '"error": "가짜 오류"}], "decision": "continue"}'])
    update = await make_nodes(deps)["integrate"](_state())
    task = [t for t in update["plan_tasks"] if t.id == "t-9"][0]
    assert task.status == "pending"
    assert task.result_evidence_ids == []
    assert task.error is None


async def test_integrate_프롬프트에_사람의_답변이_실린다():
    # qa_log의 human_answer가 프롬프트에 없으면 재개 후 리드가 같은 질문을 또 던진다(C2).
    deps = _deps(['{"decision": "continue"}'])
    state = _state(qa_log=[{"kind": "human_answer", "question": "계획 변경이 있었나요?",
                            "answer": "예, D-1로 하루 밀림"}])
    await make_nodes(deps)["integrate"](state)
    prompt_text = str(deps.lead_llm.calls[0])
    assert "계획 변경이 있었나요?" in prompt_text
    assert "예, D-1로 하루 밀림" in prompt_text


async def test_파싱_이중_실패는_강제_conclude():
    deps = _deps(["엉망", "또 엉망"])
    update = await make_nodes(deps)["integrate"](_state())
    assert update["decision"] == "conclude"
    assert any(e["kind"] == "integrate_parse_failure" for e in update["qa_log"])


def test_route_after_integrate():
    assert route_after_integrate(_state(decision="continue")) == "select"
    assert route_after_integrate(_state(decision="ask")) == "ask_human"
    assert route_after_integrate(_state(decision="conclude")) == "conclude"
