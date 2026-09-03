"""엔진 유스케이스 — 케이스 조사의 시작과 재개 (스펙 §2.4, §2.6).

그래프 배선(graph.py)을 감싸 호출 표면을 좁힌다: 초기 CaseState 조립과
스레드 설정만 여기서 하고, 통제·판단 로직은 전부 노드에 있다.

on_event(계획 5 — 보고·채널): 주어지면 ainvoke 대신 astream(stream_mode="updates")로
돌며 각 덩어리를 map_update_to_events로 이벤트 봉투화해 싱크에 넘긴다. on_event가
None이면(기본값) 지금까지와 동일하게 ainvoke 한 번으로 완주까지 기다린다 — 기존
호출부·테스트는 아무것도 바뀌지 않는다.
"""
from datetime import datetime, timezone
from typing import Callable

from langgraph.types import Command

from src.application.events import map_update_to_events
from src.application.graph import build_engine
from src.domain.case import EvidenceRef
from src.domain.events import EngineEvent


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


def _case_id_from_thread(thread_id: str) -> str:
    """thread_id에서 case_id를 뽑는다 — 워커의 스레드 명명 규칙(f"{case_id}#{n}",
    worker.py._next_thread_id)을 따른다. "#"이 없으면(신규 조사의 기본 thread_id는
    case.id 그대로다) 통째로 case_id로 본다."""
    return thread_id.split("#", 1)[0]


async def _stream_and_collect(graph, input_state, config, on_event: Callable[[EngineEvent], None],
                              case_id: str) -> dict:
    """astream(stream_mode="updates")으로 돌며 각 덩어리를 이벤트로 봉투화해 싱크에
    넘기고, 최종 state를 aget_state로 얻어 ainvoke와 같은 dict로 돌려준다.

    싱크 호출은 이벤트마다 try/except로 감싼다 — on_event는 부수효과일 뿐이라
    싱크가 raise해도 조사(스트리밍 루프)는 계속된다.

    interrupt로 멈춘 경우 ainvoke가 반환에 얹었던 "__interrupt__" 키를 aget_state가
    돌려준 StateSnapshot.tasks에서 복원한다 — 각 PregelTask.interrupts를 모아 하나로
    합친다(interrupt가 걸린 노드는 next 하나뿐이라 보통 tasks도 하나지만, 형태는
    LangGraph가 정하므로 방어적으로 전부 모은다). 이렇게 해야 워커의
    `"__interrupt__" in result` 파킹 판정이 스트리밍 경로에서도 그대로 유지된다.
    """
    clock = lambda: datetime.now(timezone.utc)   # 이벤트 시각은 여기(스트리밍 경계)에서 직접 잰다
    async for update in graph.astream(input_state, config=config, stream_mode="updates"):
        for event in map_update_to_events(update, case_id=case_id, clock=clock):
            try:
                on_event(event)
            except Exception:                                          # noqa: BLE001
                pass
    state = await graph.aget_state(config)
    result = dict(state.values)
    interrupts = tuple(i for task in state.tasks for i in task.interrupts)
    if interrupts:
        result["__interrupt__"] = interrupts
    return result


async def investigate_case(case, *, deps, checkpointer=None, thread_id=None,
                           interaction_policy="autonomous",
                           question_policy=None,
                           initial_evidence: list[EvidenceRef] | None = None,
                           engine=None, on_event: Callable[[EngineEvent], None] | None = None) -> dict:
    """새 조사를 연다 — 초기 CaseState를 조립해 그래프를 완주(또는 interrupt)까지 돌린다.

    question_policy가 None이면 deps.engine_cfg.autonomous_question_policy를 쓴다(M7) —
    호출부가 매번 정책을 직접 실어 나르지 않아도 site/app config가 그대로 반영된다.

    initial_evidence는 순찰 게이트가 이미 확보한 T0 스냅샷(gate.evidence_refs_for_case)을
    싣는 자리다 — 없으면(사람이 연 케이스 등) 엔진이 처음부터 증거 없이 시작한다.

    engine이 주어지면 build_engine을 우회하고 그 컴파일된 그래프를 그대로 쓴다 —
    호출부(큐 워커 등)가 그래프를 한 번 컴파일해 재사용하거나, 테스트에서 가짜
    엔진을 주입할 수 있게 하기 위해서다.

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
    graph = engine if engine is not None else build_engine(deps, checkpointer=checkpointer)
    initial_state = _initial_state(case, interaction_policy, resolved_question_policy,
                                   initial_evidence)
    config = {"configurable": {"thread_id": thread_id or case.id}}
    if on_event is None:
        return await graph.ainvoke(initial_state, config=config)
    return await _stream_and_collect(graph, initial_state, config, on_event, case_id=case.id)


async def resume_case(answer, *, deps, checkpointer, thread_id, engine=None,
                      on_event: Callable[[EngineEvent], None] | None = None) -> dict:
    """park된(§2.4) 조사를 사람의 답변으로 재개한다 — ask_human의 interrupt를 통과시킨다.

    engine이 주어지면 build_engine을 우회한다(investigate_case와 동일한 이유).
    on_event도 investigate_case와 동일하게 동작한다 — 이벤트 봉투의 case_id는
    thread_id에서 뽑는다(_case_id_from_thread) — resume_case는 Case 객체 없이
    thread_id만 받기 때문이다.
    """
    graph = engine if engine is not None else build_engine(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    if on_event is None:
        return await graph.ainvoke(Command(resume=answer), config=config)
    return await _stream_and_collect(graph, Command(resume=answer), config, on_event,
                                     case_id=_case_id_from_thread(thread_id))
