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
