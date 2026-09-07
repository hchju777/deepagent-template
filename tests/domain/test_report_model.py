from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.domain.report_model import build_report_model

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _record(**kw):
    base = dict(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="OEE 512%",
                t0=T, created_at=T, updated_at=T)
    base.update(kw)
    return CaseRecord(**base)


def _verdict(**kw):
    base = dict(verdict_type="data_loss", confidence="high",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                narrative="계획 동기화 누락")
    base.update(kw)
    return Verdict(**base)


def _marks(model):
    return {s.stage: s.mark for s in model.stages}


def test_정상_종결은_여섯_단계가_전부_통과다():
    case_file = {"hypotheses": [{"id": "h1"}], "plan_tasks": [{"id": "t1", "status": "ok"}],
                 "round": 2, "qa_log": [], "verify_problems": [], "verify_attempts": 0}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert set(_marks(model).values()) == {"ok"}


def test_태스크_에러는_조사_실행을_실패로_표시한다():
    case_file = {"hypotheses": [{"id": "h1"}],
                 "plan_tasks": [{"id": "t1", "status": "ok"}, {"id": "t2", "status": "error"}],
                 "round": 1, "qa_log": [], "verify_problems": [], "verify_attempts": 0}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert _marks(model)["execute"] == "fail"
    assert model.task_error_rate == "1/2"


def test_재작성_후_통과는_경고로_구별한다():
    # verify_attempts >= 1인데 문제가 비었다 = 재작성했거나 강등 통과다.
    # ok로 뭉개면 "깨끗하게 통과한 판정"과 구별되지 않는다.
    case_file = {"hypotheses": [{"id": "h1"}], "plan_tasks": [{"id": "t1", "status": "ok"}],
                 "round": 1, "qa_log": [], "verify_problems": [], "verify_attempts": 1}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert _marks(model)["verify"] == "warn"


def test_판정이_없으면_판정과_검증이_미도달이다():
    model = build_report_model(_record(), verdict=None, evidence=[],
                               case_file={"hypotheses": [{"id": "h1"}], "round": 0},
                               clock=lambda: T)
    marks = _marks(model)
    assert marks["conclude"] == "skip" and marks["verify"] == "skip"
    assert marks["integrate"] == "skip"     # round 0


def test_가설이_없는데_판정만_있으면_가설_수립_실패다():
    # frame 파싱 실패는 hypotheses 없이 degraded verdict만 만든다.
    model = build_report_model(
        _record(), verdict=_verdict(verdict_type="degraded", confidence="low",
                                    root_cause=None, narrative="frame 출력 파싱 실패"),
        evidence=[], case_file={"round": 0}, clock=lambda: T)
    marks = _marks(model)
    assert marks["frame"] == "fail" and marks["conclude"] == "fail"


def test_옛_스냅샷에_verify_attempts가_없으면_통과로_본다():
    # 계획 7 이전에 쓰인 케이스 파일에는 이 키가 없다 — 없다고 경고를 띄우면
    # 과거 보고서가 전부 의심스러워 보인다.
    case_file = {"hypotheses": [{"id": "h1"}], "plan_tasks": [{"id": "t1", "status": "ok"}],
                 "round": 1, "qa_log": [], "verify_problems": []}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert _marks(model)["verify"] == "ok"


def test_케이스_파일이_없어도_모델이_만들어진다():
    model = build_report_model(_record(), verdict=None, evidence=[], case_file=None,
                               clock=lambda: T)
    assert len(model.stages) == 6 and model.task_error_rate == "없음"
    assert model.partial is False and model.salvage_error is None


def test_실패_시점_부분_스냅샷은_모델에_표시된다():
    model = build_report_model(_record(), verdict=None, evidence=[],
                               case_file={"partial": True, "salvage_error": "RuntimeError: x"},
                               clock=lambda: T)
    assert model.partial is True and model.salvage_error == "RuntimeError: x"


def test_통합_파싱_실패는_결과_통합을_실패로_표시한다():
    case_file = {"hypotheses": [{"id": "h1"}], "plan_tasks": [{"id": "t1", "status": "ok"}],
                 "round": 1, "qa_log": [{"kind": "integrate_parse_failure", "error": "x"}],
                 "verify_problems": [], "verify_attempts": 0}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert _marks(model)["integrate"] == "fail"


def test_형태가_어긋난_케이스_파일에도_raise하지_않는다():
    # case_file은 Store의 원시 dict라 옛 스냅샷이거나 타입이 어긋날 수 있다.
    model = build_report_model(_record(), verdict=None, evidence=[],
                               case_file={"plan_tasks": 5, "hypotheses": "x", "round": "이상",
                                          "qa_log": None, "verify_problems": 7},
                               clock=lambda: T)
    assert model.plan_tasks == [] and model.round_no is None
    assert len(model.stages) == 6


def test_verify가_돌지_않았으면_검증은_미도달이다():
    # frame 파싱 실패는 frame → END다(verify를 거치지 않는다). 그런데 conclude가
    # degraded verdict를 만들어 놓으므로 "verdict 있음 + 문제 없음"만 보면 ✅가 나온다
    # — 아무것도 조사하지 않은 실패 케이스에 초록 체크가 붙는 조용한 오답이다.
    model = build_report_model(
        _record(), verdict=_verdict(verdict_type="degraded", confidence="low",
                                    root_cause=None, narrative="frame 출력 파싱 실패"),
        evidence=[], case_file={"round": 0}, clock=lambda: T)
    assert _marks(model)["verify"] == "skip"


def test_미완_태스크가_남으면_조사_실행은_경고다():
    # 실패 시점 부분 스냅샷에서 running이 남은 채 끝났으면 "2 ok"만 보여주는 것은
    # 끝내 못 돈 태스크를 표식에서 지우는 것이다.
    case_file = {"hypotheses": [{"id": "h1"}],
                 "plan_tasks": [{"id": "t1", "status": "ok"}, {"id": "t2", "status": "ok"},
                                {"id": "t3", "status": "running"}],
                 "round": 1, "qa_log": [], "verify_problems": [], "verify_attempts": 0}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    stage = next(s for s in model.stages if s.stage == "execute")
    assert stage.mark == "warn" and "미완" in stage.note


# ---- 계획 14: Timeline ------------------------------------------------------------------
from src.domain.events import EngineEvent  # noqa: E402


def _ev(seq, kind, **data):
    return EngineEvent(event=kind, case_id="c-1", at=T, seq=seq, data=data)


def test_Timeline은_이벤트_로그를_seq_순으로_요약한다():
    # 요약 문구는 코드가 만든다 — 어휘 6종(규율 7)을 소비할 뿐 새 종류는 없다.
    events = [_ev(1, "case_status_changed", status="open", reason="finding"),
              _ev(2, "round_started", round=1, dispatched=["t-1"]),
              _ev(3, "task_finished", task_id="t-1", role="data_prober", status="ok",
                  evidence_ids=["ev-1"], error=None),
              _ev(4, "question_raised", question="계획 변경?"),
              _ev(5, "verdict_formed", verdict_type="stale_data", confidence="high", rewritten=False),
              _ev(6, "report_ready", path="/r/c-1.html")]
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={},
                               clock=lambda: T, events=list(reversed(events)))   # 순서를 섞어도
    assert [e.seq for e in model.timeline] == [1, 2, 3, 4, 5, 6]
    assert [e.summary for e in model.timeline] == [
        "상태 → open (finding)", "라운드 1 시작 — 태스크 1개",
        "태스크 t-1(data_prober) ok — 증거 1개", "질문: 계획 변경?",
        "판정 stale_data (high)", "보고서 /r/c-1.html"]
    assert model.timeline[0].at == T and model.timeline_source == "events"


def test_이벤트_스토어가_없으면_Timeline_없음을_명시한다():
    # 조용한 생략 금지 — 빈 목록과 "이 프로세스에는 로그가 없다"는 다른 말이다.
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={}, clock=lambda: T)
    assert model.timeline == [] and model.timeline_source == "none"
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={},
                               clock=lambda: T, events=[])
    assert model.timeline == [] and model.timeline_source == "events"


def test_모르는_data_형태에도_Timeline은_raise하지_않는다():
    events = [_ev(1, "task_finished"),
              _ev(2, "verdict_formed", verdict_type="stale_data", confidence="low", rewritten=True),
              EngineEvent(event="round_started", case_id="c-1", at=T, data={"round": 2})]   # seq 없음
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={},
                               clock=lambda: T, events=events)
    assert [e.summary for e in model.timeline] == [
        "라운드 2 시작 — 태스크 0개", "태스크 ?(?) ? — 증거 0개", "판정 stale_data (low) · 재작성 뒤 강등"]


def test_이벤트_로그_읽기_실패는_없음과_다른_말이다():
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={}, clock=lambda: T,
                               events=[_ev(1, "report_ready", path="p")],
                               timeline_error="RuntimeError: case_events read failed")
    assert model.timeline_source == "unavailable" and model.timeline == []
    assert model.timeline_error == "RuntimeError: case_events read failed"


def test_Timeline_요약은_비정형_data에서_행을_잃지_않는다():
    # 리뷰 L4·L5: reason None에 "(None)", 비-리스트 dispatched/evidence_ids가 TypeError로
    # except에 삼켜져 행이 조용히 사라짐, at None 항목이 조용히 빠짐, data가 dict가 아님.
    events = [_ev(1, "case_status_changed", status="open", reason=None),
              _ev(2, "round_started", round=1, dispatched="t-1"),
              _ev(3, "task_finished", task_id="t-1", role="r", status="ok", evidence_ids="ev-1"),
              EngineEvent.model_construct(event="question_raised", case_id="c-1", at=T, seq=4, data="x"),
              EngineEvent.model_construct(event="report_ready", case_id="c-1", at=None, seq=5,
                                          data={"path": "p"}),
              EngineEvent.model_construct(event="report_ready", case_id="c-1", at="쓰레기", seq=6,
                                          data={"path": "q"})]
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={}, clock=lambda: T,
                               events=events)
    assert [e.summary for e in model.timeline] == [
        "상태 → open", "라운드 1 시작 — 태스크 0개", "태스크 t-1(r) ok — 증거 0개", "질문: ?", "보고서 p",
        "보고서 q"]
    assert model.timeline[4].at is None and model.timeline[5].at is None


def test_이벤트_종류가_문자열이_아니어도_행을_잃지_않는다():
    # 리뷰 L2(a): 미지 kind 폴백이 str()을 안 거쳐 TimelineEntry 검증에 걸리고 except가
    # 삼켜 행이 조용히 빠졌다.
    ev = EngineEvent.model_construct(event=123, case_id="c-1", at=T, seq=1, data={})
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={}, clock=lambda: T,
                               events=[ev])
    assert [(e.event, e.summary) for e in model.timeline] == [("123", "123")]
