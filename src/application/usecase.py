"""엔진 유스케이스 — 케이스 조사의 시작과 재개 (스펙 §2.4, §2.6).

그래프 배선(graph.py)을 감싸 호출 표면을 좁힌다: 초기 CaseState 조립과
스레드 설정만 여기서 하고, 통제·판단 로직은 전부 노드에 있다.
"""
from langgraph.types import Command

from src.application.graph import build_engine


async def investigate_case(case, *, deps, checkpointer=None, thread_id=None,
                           interaction_policy="autonomous",
                           question_policy=None) -> dict:
    """새 조사를 연다 — 초기 CaseState를 조립해 그래프를 완주(또는 interrupt)까지 돌린다.

    question_policy가 None이면 deps.engine_cfg.autonomous_question_policy를 쓴다(M7) —
    호출부가 매번 정책을 직접 실어 나르지 않아도 site/app config가 그대로 반영된다.

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
    initial_state = {
        "case": case,
        "interaction_policy": interaction_policy,
        "autonomous_question_policy": resolved_question_policy,
    }
    config = {"configurable": {"thread_id": thread_id or case.id}}
    return await graph.ainvoke(initial_state, config=config)


async def resume_case(answer, *, deps, checkpointer, thread_id) -> dict:
    """park된(§2.4) 조사를 사람의 답변으로 재개한다 — ask_human의 interrupt를 통과시킨다."""
    graph = build_engine(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(Command(resume=answer), config=config)
