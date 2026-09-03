from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.domain.store import EvidenceRecord
from src.presentation.report import render_report, write_report

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
RECORD = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="OEE 512%",
                    t0=T, target_locator="rest:/oee", created_at=T, updated_at=T,
                    status="closed", closed_reason="조사 완료")
VERDICT = Verdict(verdict_type="stale_data", confidence="high",
                  narrative="plan-sync가 키를 못 썼다.\n분모가 옛 값으로 남았다.",
                  root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-2"]),
                  contributing=[CauseLink(component="twin-aggregator", evidence_ids=["ev-3"],
                                          relation="키 부재 시 옛 값 폴백")],
                  recommendations=["plan:7:today 재생성", "폴백 로직 개선"],
                  caveats=["배포 버전 미검증"])
EVIDENCE = [EvidenceRecord(id="ev-1", source="rest:/oee", body_digest="a" * 64, as_of=T),
            EvidenceRecord(id="ev-2", source="redis:plan:7", body_digest="b" * 64,
                           as_of=T, complete=False)]
CASE_FILE = {
    "plan_tasks": [
        {"id": "t-1", "goal": "mongo 조회", "role": "data_prober", "status": "ok",
         "result_evidence_ids": ["ev-1"], "result_summary": "확인", "error": None,
         "input_evidence_ids": [], "priority": 10},
        {"id": "t-2", "goal": "재계산", "role": "recompute_verifier", "status": "error",
         "result_evidence_ids": [], "result_summary": None, "error": "타임아웃",
         "input_evidence_ids": [], "priority": 20},
        {"id": "t-3", "goal": "코드 추적", "role": "code_tracer", "status": "pending",
         "result_evidence_ids": [], "result_summary": None, "error": None,
         "input_evidence_ids": [], "priority": 30}],
    "hypotheses": [{"id": "h-1", "statement": "서빙 이상", "status": "refuted",
                    "supporting_ids": [], "refuting_ids": ["ev-1"]},
                   {"id": "h-2", "statement": "계산 이상", "status": "supported",
                    "supporting_ids": ["ev-2"], "refuting_ids": []}],
    "round": 2, "qa_log": [{"kind": "round_cap"}], "verify_problems": []}


def test_보고서는_5절을_모두_담고_에러율과_불완전_증거를_드러낸다():
    text = render_report(RECORD, verdict=VERDICT, evidence=EVIDENCE,
                         case_file=CASE_FILE, clock=lambda: T)
    for heading in ("## 1. 요약", "## 2. 판정", "## 3. 조치 권고", "## 4. 증거", "## 5. 조사 경위"):
        assert heading in text
    assert "mx/gumi" in text and "OEE 512%" in text and "stale_data" in text
    assert "1/3" in text                                  # 태스크 에러율
    assert "plan-sync" in text and "ev-2" in text
    assert "twin-aggregator" in text and "키 부재 시 옛 값 폴백" in text
    assert "배포 버전 미검증" in text
    assert "⚠" in text                                     # 불완전 증거 표시
    assert "t-3" in text and "pending" in text              # 미조사 명시
    assert "h-1" in text and "서빙 이상" in text            # 기각 가설
    assert "라운드" in text and "2" in text


def test_판정도_케이스파일도_없으면_없음을_명시한다():
    bare = RECORD.model_copy(update={"closed_reason": "awaiting_human 타임아웃 — 미해결 종결"})
    text = render_report(bare, verdict=None, evidence=[], case_file=None, clock=lambda: T)
    assert "판정 없음" in text and "타임아웃" in text
    assert text.count("없음") >= 3                          # 권고·증거·경위 빈 섹션
    assert "## 5. 조사 경위" in text


def test_파일로_먼저_쓴다(tmp_path):
    path = write_report("본문", output_dir=str(tmp_path / "out"), case_id="c-1")
    assert path.endswith("c-1.md")
    from pathlib import Path
    assert Path(path).read_text(encoding="utf-8") == "본문"
    assert write_report("x", output_dir="/proc/불가/경로", case_id="c-1") == ""   # 실패는 빈 문자열


def test_형태가_망가진_케이스파일에서도_5절_구조는_지킨다():
    broken = {"plan_tasks": 5, "hypotheses": "이상", "round": "둘",
              "qa_log": {"kind": "dict가 아님"}, "verify_problems": 7}
    text = render_report(RECORD, verdict=VERDICT, evidence=EVIDENCE,
                         case_file=broken, clock=lambda: T)
    for heading in ("## 1. 요약", "## 2. 판정", "## 3. 조치 권고", "## 4. 증거", "## 5. 조사 경위"):
        assert heading in text
    assert "조립 실패" not in text
    assert "plan-sync" in text                    # 판정은 정상 렌더


def test_evidence_summaries가_있으면_요지_열에_실리고_없으면_digest로_정직하게_표기한다():
    # I4: §4 "요지" 열은 원래 body_digest[:12]였다(요지가 아니다) — evidence_summaries가
    # 주어지면 그걸 쓰고, id가 그 딕셔너리에 없으면(개별 조회 실패 등) digest로
    # 폴백한다. 아예 안 주어지면 열 이름을 "본문 digest"로 정직하게 바꾼다.
    with_summaries = render_report(RECORD, verdict=VERDICT, evidence=EVIDENCE,
                                   case_file=CASE_FILE, clock=lambda: T,
                                   evidence_summaries={"ev-1": "OEE 조회 응답 요약"})
    assert "요지" in with_summaries and "본문 digest" not in with_summaries
    assert "OEE 조회 응답 요약" in with_summaries
    assert "b" * 12 in with_summaries          # ev-2는 딕셔너리에 없어 digest로 폴백

    without_summaries = render_report(RECORD, verdict=VERDICT, evidence=EVIDENCE,
                                      case_file=CASE_FILE, clock=lambda: T)
    assert "본문 digest" in without_summaries and "요지" not in without_summaries
    assert "a" * 12 in without_summaries and "b" * 12 in without_summaries


def test_항목이_전부_비dict면_없음을_낸다():
    junk = {"plan_tasks": [1, "x"], "hypotheses": [None], "round": 1,
            "qa_log": [], "verify_problems": []}
    text = render_report(RECORD, verdict=None, evidence=[], case_file=junk, clock=lambda: T)
    assert "## 5. 조사 경위" in text and "없음" in text
    assert "| t-" not in text                      # 빈 표 머리만 남지 않음
