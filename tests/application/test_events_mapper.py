from datetime import datetime, timezone

from src.application.events import case_status_event, map_update_to_events, report_ready_event
from src.domain.case import PlanTask

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CLOCK = lambda: T


def test_select는_라운드_시작을_execute는_태스크_종료를_낸다():
    running = PlanTask(id="t-1", goal="g", role="data_prober", status="running")
    events = map_update_to_events({"select": {"plan_tasks": [running]}}, case_id="c-1", clock=CLOCK)
    assert [e.event for e in events] == ["round_started"]
    assert events[0].data["dispatched"] == ["t-1"] and events[0].case_id == "c-1"

    done = PlanTask(id="t-1", goal="g", role="data_prober", status="ok",
                    result_evidence_ids=["ev-1"], result_summary="확인")
    events = map_update_to_events({"execute": {"plan_tasks": [done]}}, case_id="c-1", clock=CLOCK)
    assert [e.event for e in events] == ["task_finished"]
    assert events[0].data == {"task_id": "t-1", "role": "data_prober", "status": "ok",
                              "evidence_ids": ["ev-1"], "error": None}


def test_integrate의_ask만_질문_이벤트를_낸다():
    ask = map_update_to_events({"integrate": {"decision": "ask", "question": "계획 변경?"}},
                               case_id="c-1", clock=CLOCK)
    assert [e.event for e in ask] == ["question_raised"] and ask[0].data["question"] == "계획 변경?"
    assert map_update_to_events({"integrate": {"decision": "continue"}},
                                case_id="c-1", clock=CLOCK) == []


def test_노드명은_봉투_밖으로_새지_않고_미지의_노드는_무시된다():
    events = map_update_to_events({"conclude": {"verdict": None}}, case_id="c-1", clock=CLOCK)
    assert events == []
    assert map_update_to_events({"유령노드": {"x": 1}}, case_id="c-1", clock=CLOCK) == []
    assert map_update_to_events({"execute": "형태이상"}, case_id="c-1", clock=CLOCK) == []
    dumped = map_update_to_events({"select": {"plan_tasks": []}}, case_id="c-1", clock=CLOCK)
    assert all("select" not in str(e.model_dump()) for e in dumped)


def test_round_hint가_있으면_라운드_번호가_봉투에_실린다():
    # I1: select 노드 자체는 부분상태에 round를 싣지 않는다 — round_hint를
    # 주면(usecase._stream_and_collect가 select 청크마다 세는 카운터) 그걸
    # round_started.data["round"]에 싣고, 안 주면(기존 호출부) 지금처럼 생략된다.
    running = PlanTask(id="t-1", goal="g", role="data_prober", status="running")
    events = map_update_to_events({"select": {"plan_tasks": [running]}},
                                  case_id="c-1", clock=CLOCK, round_hint=2)
    assert events[0].data["round"] == 2
    plain = map_update_to_events({"select": {"plan_tasks": [running]}}, case_id="c-1", clock=CLOCK)
    assert "round" not in plain[0].data


def test_상태_전이와_보고서_준비_이벤트():
    s = case_status_event("c-1", "awaiting_human", clock=CLOCK, reason="질문 대기")
    assert s.event == "case_status_changed" and s.data == {"status": "awaiting_human",
                                                           "reason": "질문 대기"}
    r = report_ready_event("c-1", "output/c-1.md", clock=CLOCK)
    assert r.event == "report_ready" and r.data["path"].endswith("c-1.md")


def test_conclude와_verify는_판정_이벤트를_낸다():
    from src.domain.case import CauseLink, Verdict
    verdict = Verdict(verdict_type="data_loss", confidence="high",
                      root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                      narrative="계획 동기화 누락")
    fresh = map_update_to_events({"conclude": {"verdict": verdict}},
                                 case_id="c-1", clock=lambda: T)
    assert [e.event for e in fresh] == ["verdict_formed"]
    assert fresh[0].data == {"verdict_type": "data_loss", "confidence": "high",
                             "rewritten": False}

    # verify가 verdict를 실을 때는 강등 통과뿐이다(재작성도 실패해 낮은 확신으로 통과).
    demoted = map_update_to_events({"verify": {"verdict": verdict, "verify_problems": []}},
                                   case_id="c-1", clock=lambda: T)
    assert demoted[0].data["rewritten"] is True


def test_verify가_문제만_실은_청크는_판정_이벤트가_아니다():
    # verify_problems만 있는 청크는 판정이 아니라 conclude에 대한 재작성 요구다.
    assert map_update_to_events({"verify": {"verify_problems": ["없는 id ev-9 인용"]}},
                                case_id="c-1", clock=lambda: T) == []


def test_collect_events는_페이지를_끝까지_읽는다():
    # 보고서는 부분 Timeline을 내면 안 된다 — since의 limit 한 페이지로 끝내면 200건
    # 넘는 조사의 뒷부분이 조용히 빠진다.
    from src.application.events import collect_events
    from src.domain.events import EngineEvent, InMemoryEventStore
    store = InMemoryEventStore()
    for i in range(5):
        store.append(EngineEvent(event="round_started", case_id="c-1", at=T, data={"round": i}))
    assert [e.seq for e in collect_events(store, "c-1", page=2).events] == [1, 2, 3, 4, 5]
    assert collect_events(store, "없음").events == []


def test_collect_events는_스토어_장애를_raise_대신_돌려준다():
    # 리뷰 M1(규율 1·8): 읽기 장애 하나가 보고서 파일·report_ready·메일을 전부 막았다.
    from src.application.events import collect_events
    from src.domain.events import InMemoryEventStore

    class _Broken(InMemoryEventStore):
        def since(self, *a, **k):
            raise RuntimeError("case_events read failed")
    log = collect_events(_Broken(), "c-1")
    assert log.events == [] and log.error == "RuntimeError: case_events read failed"


def test_collect_events는_커서를_마지막_seq로_옮긴다():
    # 리뷰 D1: seq가 연속이면 `cursor += page`도 맞아 보인다. prune 뒤 3부터 시작하면
    # 그 변형은 같은 페이지를 영원히 읽는다 — 스토어가 11번째 호출에서 끊는다.
    from datetime import timedelta
    from src.application.events import collect_events
    from src.domain.events import EngineEvent, InMemoryEventStore

    class _Counting(InMemoryEventStore):
        calls = 0

        def since(self, *a, **k):
            type(self).calls += 1
            if type(self).calls > 10:
                raise RuntimeError("페이지를 끝없이 읽는다")
            return super().since(*a, **k)
    store = _Counting()
    for i in range(6):
        at = T - timedelta(hours=1) if i < 2 else T
        store.append(EngineEvent(event="round_started", case_id="c-1", at=at, data={"round": i}))
    store.prune_before(T)                                   # seq 1·2가 걷혀 3부터 남는다
    log = collect_events(store, "c-1", page=2)
    assert log.error is None and [e.seq for e in log.events] == [3, 4, 5, 6]


def test_collect_events는_부분_읽기를_버린다():
    # 리뷰 D4: 반쯤 읽은 Timeline은 "완전한 Timeline"으로 읽힌다 — 둘째 페이지에서
    # 죽으면 첫 페이지도 내지 않는다.
    from src.application.events import collect_events
    from src.domain.events import EngineEvent, InMemoryEventStore

    class _SecondPageFails(InMemoryEventStore):
        def since(self, case_id, after_seq=0, limit=200):
            if after_seq:
                raise RuntimeError("page 2 failed")
            return super().since(case_id, after_seq, limit)
    store = _SecondPageFails()
    for i in range(4):
        store.append(EngineEvent(event="round_started", case_id="c-1", at=T, data={"round": i}))
    log = collect_events(store, "c-1", page=2)
    assert log.events == [] and log.error == "RuntimeError: page 2 failed"
