from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.presentation.report_html import render_html
from src.presentation.report_model import build_report_model

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _model(**kw):
    record = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp",
                        symptom=kw.pop("symptom", "OEE 512%"), t0=T,
                        created_at=T, updated_at=T)
    return build_report_model(record, verdict=kw.pop("verdict", None), evidence=[],
                              case_file=kw.pop("case_file", {"round": 1}), clock=lambda: T)


def test_다섯_절이_전부_나온다():
    html = render_html(_model())
    for heading in ("1. 요약", "2. 판정", "3. 조치 권고", "4. 증거", "5. 조사 경위"):
        assert heading in html
    assert html.startswith("<!DOCTYPE html>")


def test_단계_체크리스트가_표로_나온다():
    html = render_html(_model())
    assert "<table" in html and "가설 수립" in html


def test_사람이_쓰지_않은_텍스트도_이스케이프한다():
    # 증상·narrative·component에는 LLM이 쓴 텍스트와 대상 시스템 응답이 섞인다.
    # 그대로 넣으면 보고서를 여는 것만으로 임의 마크업이 실행된다.
    verdict = Verdict(verdict_type="data_loss", confidence="high",
                      root_cause=CauseLink(component="<script>alert(1)</script>",
                                           evidence_ids=["ev-1"]),
                      narrative="n")
    html = render_html(_model(symptom="<img src=x onerror=alert(1)>", verdict=verdict))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_부분_스냅샷은_눈에_띄게_표시된다():
    html = render_html(_model(case_file={"partial": True, "round": 2,
                                         "salvage_error": "RuntimeError: x"}))
    assert "실패 시점 부분 스냅샷" in html and "구제 실패" in html


def test_렌더링이_실패해도_raise하지_않는다():
    class Broken:
        @property
        def record(self): raise RuntimeError("깨짐")
    out = render_html(Broken())
    assert "보고서 조립 실패" in out and out.startswith("<!DOCTYPE html>")
