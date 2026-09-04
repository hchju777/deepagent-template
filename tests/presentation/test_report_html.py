from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.presentation.report_html import render_html
from src.domain.report_model import build_report_model

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


def _rich_model(evidence_summaries=None):
    """두 렌더러를 대조하기 위한, 모든 절에 값이 있는 모델."""
    from src.domain.store import EvidenceRecord
    record = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp",
                        symptom="OEE 512%", t0=T, created_at=T, updated_at=T)
    verdict = Verdict(verdict_type="stale_data", confidence="high",
                      root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                      contributing=[CauseLink(component="twin-state", evidence_ids=["ev-2"],
                                              relation="캐시가 따라가지 못했다")],
                      recommendations=["plan-sync 재기동"],
                      caveats=["ev-2는 불완전"],
                      narrative="plan-sync가 line 7의 계획을 갱신하지 못했다")
    case_file = {"hypotheses": [{"id": "h1", "statement": "캐시 지연", "status": "refuted",
                                 "refuting_ids": ["ev-2"]}],
                 "plan_tasks": [{"id": "t1", "role": "data_prober", "status": "ok"}],
                 "round": 2, "qa_log": [{"kind": "auto_answered", "question": "q", "answer": "a"}],
                 "verify_problems": ["없는 id ev-9 인용"], "verify_attempts": 1}
    evidence = [EvidenceRecord(id="ev-1", source="rest:/oee", body_digest="abcdef123456",
                               as_of=T, complete=True)]
    return build_report_model(record, verdict=verdict, evidence=evidence,
                              case_file=case_file, clock=lambda: T,
                              evidence_summaries=evidence_summaries)


def test_HTML은_마크다운이_말하는_것을_빠뜨리지_않는다():
    # 계획 7의 명분은 "두 렌더러가 같은 모델을 보므로 다른 말을 할 수 없다"였다.
    # 유도만 공유하고 렌더가 갈라지면 그 명분이 기본 산출물(HTML) 쪽에서 무너진다.
    from src.presentation.report import render_md
    model = _rich_model()
    md, html = render_md(model), render_html(model)
    for fact in ("plan-sync가 line 7의 계획을 갱신하지 못했다",   # narrative
                 "QA 로그",                                      # §5 QA 로그 절
                 "auto_answered",                                # 그 내용
                 "없는 id ev-9 인용",                            # 검증 문제
                 "plan-sync 재기동",                             # 권고
                 "캐시가 따라가지 못했다"):                       # 기여 요인 relation
        assert fact in md, f"마크다운에 {fact!r}가 없다(테스트 전제 오류)"
        assert fact in html, f"HTML이 {fact!r}를 빠뜨렸다"


def test_근본_원인이_없으면_HTML도_없음이라고_적는다():
    # 항목이 사라지면 "확인 안 했다"와 "없다"를 구별할 수 없다.
    verdict = Verdict(verdict_type="inconclusive", confidence="low", root_cause=None,
                      contributing=[CauseLink(component="twin-state", evidence_ids=["ev-2"])],
                      narrative="원인 미상")
    html = render_html(_model(verdict=verdict))
    assert "근본 원인" in html and "없음" in html


def test_요지가_없으면_HTML도_열_이름을_정직하게_적는다():
    # evidence_summaries가 없으면 실제로 보여주는 것은 body_digest다.
    # "요지"라고 적으면 digest를 요약으로 오해한다(마크다운은 이미 고친 문제).
    html = render_html(_rich_model(evidence_summaries=None))
    assert "본문 digest" in html and "<th>요지</th>" not in html
    with_summaries = render_html(_rich_model(evidence_summaries={"ev-1": "OEE 512로 관측"}))
    assert "<th>요지</th>" in with_summaries and "OEE 512로 관측" in with_summaries


def test_HTML도_지식_digest를_보여준다():
    # HTML이 기본 포맷이다(schema_app.ReportConfig). 마크다운에만 테스트를 두면
    # 정작 사람이 읽는 쪽이 무방비다 — 표 렌더링에서 실제로 겪은 자리다.
    html = render_html(_model(case_file={
        "round": 1,
        "knowledge_digests": {"topology": "a" * 64, "target_api": "c" * 64}}))
    assert "지식 digest" in html and "target_api=cccccccc" in html
