from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.presentation.report_model import build_report_model

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
