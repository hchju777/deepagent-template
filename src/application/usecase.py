"""엔진 유스케이스 — 케이스 조사의 시작과 재개 (스펙 §2.4, §2.6).

그래프 배선(graph.py)을 감싸 호출 표면을 좁힌다: 초기 CaseState 조립과
스레드 설정만 여기서 하고, 통제·판단 로직은 전부 노드에 있다.
"""
from langgraph.types import Command

from src.application.graph import build_engine


async def investigate_case(case, *, deps, checkpointer=None, thread_id=None,
                           interaction_policy="autonomous",
                           question_policy="default_and_log") -> dict:
    """새 조사를 연다 — 초기 CaseState를 조립해 그래프를 완주(또는 interrupt)까지 돌린다."""
    graph = build_engine(deps, checkpointer=checkpointer)
    initial_state = {
        "case": case,
        "interaction_policy": interaction_policy,
        "autonomous_question_policy": question_policy,
    }
    config = {"configurable": {"thread_id": thread_id or case.id}}
    return await graph.ainvoke(initial_state, config=config)


async def resume_case(answer, *, deps, checkpointer, thread_id) -> dict:
    """park된(§2.4) 조사를 사람의 답변으로 재개한다 — ask_human의 interrupt를 통과시킨다."""
    graph = build_engine(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(Command(resume=answer), config=config)
