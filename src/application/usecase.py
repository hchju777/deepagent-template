"""엔진 유스케이스 — 케이스 조사의 시작과 재개 (스펙 §2.4, §2.6).

그래프 배선(graph.py)을 감싸 호출 표면을 좁힌다: 초기 CaseState 조립과
스레드 설정만 여기서 하고, 통제·판단 로직은 전부 노드에 있다.

on_event(계획 5 — 보고·채널): 주어지면 ainvoke 대신 astream(stream_mode="updates")로
돌며 각 덩어리를 map_update_to_events로 이벤트 봉투화해 싱크에 넘긴다. on_event가
None이면(기본값) 지금까지와 동일하게 ainvoke 한 번으로 완주까지 기다린다 — 기존
호출부·테스트는 아무것도 바뀌지 않는다. clock은 그 이벤트의 시각을 재는 데만
쓰는 on_event의 짝 인자다(호출부가 결정론 clock을 넘기지 않으면 now()로 폴백).
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
    case.id 그대로다) 통째로 case_id로 본다. resume_case가 명시적 case_id 없이
    불릴 때만 쓰는 폴백이다 — 호출부가 case_id를 알고 있으면(워커 등) 그걸 직접
    넘겨 이 파싱에 기대지 않는 편이 낫다."""
    return thread_id.split("#", 1)[0]


async def _stream_and_collect(graph, input_state, config, on_event: Callable[[EngineEvent], None],
                              case_id: str, clock: Callable[[], datetime]) -> dict:
    """astream(stream_mode="updates")으로 돌며 각 덩어리를 이벤트로 봉투화해 싱크에
    넘기고, 최종 state를 aget_state로 얻어 ainvoke와 같은 dict로 돌려준다.

    clock은 호출부(investigate_case/resume_case)가 이미 해석해 넘긴다 — 여기서는
    now()를 직접 부르지 않는다(호출부 docstring 참고: 워커가 자신의 self._clock을
    넘겨 결정론 테스트·타임존 정책을 일관되게 유지한다).

    싱크 호출은 이벤트마다 try/except로 감싼다 — on_event는 부수효과일 뿐이라
    싱크가 raise해도 조사(스트리밍 루프)는 계속된다.

    interrupt로 멈춘 경우 ainvoke가 반환에 얹었던 "__interrupt__" 키를
    StateSnapshot.interrupts(LangGraph가 이미 태스크별 interrupt를 모아 노출하는
    필드)에서 복원한다. 이렇게 해야 워커의 `"__interrupt__" in result` 파킹
    판정이 스트리밍 경로에서도 그대로 유지된다.

    round_counter(I1): select 노드는 자신의 부분상태에 round를 싣지 않으므로
    (application/events.py 참고), 여기서 select 청크를 볼 때마다 +1 해 그
    값을 round_hint로 map_update_to_events에 넘긴다 — round_started.data에
    실제 라운드 번호가 실리게 하는 유일한 자리(스트리밍 루프)다.
    """
    round_counter = 0
    async for update in graph.astream(input_state, config=config, stream_mode="updates"):
        if "select" in update:
            round_counter += 1
        events = map_update_to_events(update, case_id=case_id, clock=clock,
                                      round_hint=round_counter or None)
        for event in events:
            try:
                on_event(event)
            except Exception:                                          # noqa: BLE001
                pass
    state = await graph.aget_state(config)
    result = dict(state.values)
    interrupts = tuple(getattr(state, "interrupts", ()))
    if interrupts:
        result["__interrupt__"] = interrupts
    return result


async def investigate_case(case, *, deps, checkpointer=None, thread_id=None,
                           interaction_policy="autonomous",
                           question_policy=None,
                           initial_evidence: list[EvidenceRef] | None = None,
                           engine=None, on_event: Callable[[EngineEvent], None] | None = None,
                           clock: Callable[[], datetime] | None = None) -> dict:
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

    clock은 on_event와 짝이다 — on_event가 주어졌을 때만 이벤트 시각을 재는 데
    쓰인다. 호출부(워커 등)가 자신의 결정론 clock을 넘길 수 있게 하려는 것으로,
    직접 넘기지 않으면(None) datetime.now(timezone.utc)로 폴백한다 — 이 함수가
    이벤트 스트리밍이 실제로 관측되는 경계이기 때문이다(프로젝트 관례상 now()를
    직접 부르는 자리는 CLI 경계와 부팅 기동 점검뿐이었는데, on_event 스트리밍
    경계도 같은 성격이라 이 폴백만 예외로 남긴다 — 호출부가 clock을 넘기면 이
    폴백 자체가 쓰이지 않는다).
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
    resolved_clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
    return await _stream_and_collect(graph, initial_state, config, on_event, case_id=case.id,
                                     clock=resolved_clock)


async def resume_case(answer, *, deps, checkpointer, thread_id, engine=None,
                      on_event: Callable[[EngineEvent], None] | None = None,
                      case_id: str | None = None,
                      clock: Callable[[], datetime] | None = None) -> dict:
    """park된(§2.4) 조사를 사람의 답변으로 재개한다 — ask_human의 interrupt를 통과시킨다.

    engine이 주어지면 build_engine을 우회한다(investigate_case와 동일한 이유).
    on_event·clock도 investigate_case와 동일하게 동작한다.

    case_id는 이벤트 봉투에 실을 case_id를 명시적으로 정한다 — resume_case는
    Case 객체 없이 thread_id만 받으므로, 호출부(워커 등)가 이미 case_id를 알고
    있으면 그걸 그대로 넘기는 편이 워커의 사설 스레드 명명 규칙
    (f"{case_id}#{n}")에 이 함수가 결합되는 것보다 낫다. None이면(호출부가
    case_id를 모르는 경우) _case_id_from_thread로 thread_id를 파싱해 폴백한다.
    """
    graph = engine if engine is not None else build_engine(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    if on_event is None:
        return await graph.ainvoke(Command(resume=answer), config=config)
    resolved_clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
    resolved_case_id = case_id if case_id is not None else _case_id_from_thread(thread_id)
    return await _stream_and_collect(graph, Command(resume=answer), config, on_event,
                                     case_id=resolved_case_id, clock=resolved_clock)
