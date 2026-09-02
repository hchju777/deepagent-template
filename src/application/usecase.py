"""엔진 유스케이스 — 케이스 조사의 시작과 재개 (스펙 §2.4, §2.6).

그래프 배선(graph.py)을 감싸 호출 표면을 좁힌다: 초기 CaseState 조립과
스레드 설정만 여기서 하고, 통제·판단 로직은 전부 노드에 있다.
"""
from langgraph.types import Command

from src.application.graph import build_engine
from src.domain.case import EvidenceRef


def _initial_state(case, interaction_policy, question_policy,
                   initial_evidence: list[EvidenceRef] | None) -> dict:
    """초기 CaseState를 조립하는 순수 함수 — investigate_case에서 분리해 그래프
    없이도(build_engine·ainvoke 없이) 조립 결과만 단위 테스트할 수 있게 한다.

    initial_evidence가 주어지면 그대로 evidence 키에 싣는다 — 순찰 게이트가
    연 케이스의 T0 스냅샷(gate.evidence_refs_for_case)을 엔진에 그대로
    넘기는 경로(§계획 3 브리지). None이면 evidence 키 자체를 넣지 않아
    CaseState 기본값(빈 리스트)이 그대로 쓰인다.
    """
    state = {
        "case": case,
        "interaction_policy": interaction_policy,
        "autonomous_question_policy": question_policy,
    }
    if initial_evidence is not None:
        state["evidence"] = initial_evidence
    return state


async def investigate_case(case, *, deps, checkpointer=None, thread_id=None,
                           interaction_policy="autonomous",
                           question_policy=None,
                           initial_evidence: list[EvidenceRef] | None = None) -> dict:
    """새 조사를 연다 — 초기 CaseState를 조립해 그래프를 완주(또는 interrupt)까지 돌린다.

    question_policy가 None이면 deps.engine_cfg.autonomous_question_policy를 쓴다(M7) —
    호출부가 매번 정책을 직접 실어 나르지 않아도 site/app config가 그대로 반영된다.

    initial_evidence는 순찰 게이트가 이미 확보한 T0 스냅샷(gate.evidence_refs_for_case)을
    싣는 자리다 — 없으면(사람이 연 케이스 등) 엔진이 처음부터 증거 없이 시작한다.

    interaction_policy가 "autonomous"가 아니거나(해석된) question_policy가 "park"면
    ask_human에서 interrupt가 걸릴 수 있다 — 그런데 checkpointer가 없으면 스레드를
    되돌릴 방법이 없어 그 조사는 영영 멈춘 채로 남는다. 그래프 안(노드)에서 이를
    거부하면 "노드는 raise하지 않는다"는 계약을 어기게 되므로, 그래프 밖인 이
    함수 서두에서 기동 자체를 거부한다(§기동 거부 철학).
    """
    resolved_question_policy = (question_policy if question_policy is not None
                                else deps.engine_cfg.autonomous_question_policy)
    if ((interaction_policy != "autonomous" or resolved_question_policy == "park")
            and checkpointer is None):
        raise ValueError(
            "interrupt 경로에는 checkpointer가 필요하다 — interaction_policy가 "
            "autonomous가 아니거나 question_policy가 park면 ask_human에서 멈출 수 있다")
    graph = build_engine(deps, checkpointer=checkpointer)
    initial_state = _initial_state(case, interaction_policy, resolved_question_policy,
                                   initial_evidence)
    config = {"configurable": {"thread_id": thread_id or case.id}}
    return await graph.ainvoke(initial_state, config=config)


async def resume_case(answer, *, deps, checkpointer, thread_id) -> dict:
    """park된(§2.4) 조사를 사람의 답변으로 재개한다 — ask_human의 interrupt를 통과시킨다."""
    graph = build_engine(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(Command(resume=answer), config=config)
