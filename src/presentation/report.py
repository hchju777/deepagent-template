"""보고서 마크다운 렌더러 — 스펙 §5.1.

데이터 유도는 report_model.build_report_model이 한다 — 이 모듈은 그 결과를
마크다운으로 옮기고 파일로 쓸 뿐이다. HTML 렌더러(report_html.py)가 같은 모델을
보므로 두 렌더러가 다른 말을 할 수 없다.

"무엇을 확인 안 했나"의 명시(§5)가 신뢰의 조건이다 — 조용한 생략 금지의
보고서판. 그래서 각 하위 항목이 비면 값을 지우는 대신 "없음"을 적는다.
"""
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from src.domain.case import Verdict
from src.domain.cases import CaseRecord
from src.domain.store import EvidenceRecord
from src.presentation.report_model import ReportModel, build_report_model

Clock = Callable[[], datetime]

_UNINVESTIGATED = {"pending", "cancelled"}   # §5: 미조사로 명시할 태스크 상태
_MARKS = {"ok": "✅", "fail": "❌", "warn": "⚠", "skip": "⬜"}


def render_report(record: CaseRecord, *, verdict: Verdict | None,
                   evidence: list[EvidenceRecord], case_file: dict | None,
                   clock: Clock, evidence_summaries: dict[str, str] | None = None) -> str:
    """스펙 §5.1의 5절 보고서를 md로 조립한다(호출부 호환 유지).

    순수 함수 — case_file의 형태가 기대와 어긋나도 raise하지 않고
    "없음"으로 채운 보고서(또는 실패 시 최소 안내문)를 반환한다.

    evidence_summaries(증거 id → 요지 문자열)가 주어지면 §4 증거 표의 "요지" 열에
    그것을 쓴다 — 주지 않으면(None, 기본값) §4는 body_digest 앞 12자를 보여주되
    열 이름을 "본문 digest"로 정직하게 표기한다(digest는 요지가 아니다).
    """
    try:
        model = build_report_model(record, verdict=verdict, evidence=evidence,
                                   case_file=case_file, clock=clock,
                                   evidence_summaries=evidence_summaries)
        return render_md(model)
    except Exception as exc:            # noqa: BLE001 — 최후의 그물: 유도와 렌더 어느
        # 쪽이 예상 못 한 형태를 만나도 조사 종결은 막지 않는다(계약)
        return (f"# 케이스 {getattr(record, 'id', '?')} 보고서\n\n"
                f"보고서 조립 실패: {type(exc).__name__}: {exc}\n")


def render_md(model: ReportModel) -> str:
    """ReportModel에서 5절 마크다운을 조립한다. 절대 raise하지 않는다.

    render_report 래퍼를 거치지 않는 호출부가 있다(daemon._render_case_mail의 평문
    파트, case show --report의 즉석 렌더). 여기가 무방비면 어긋난 case_file 하나가
    메일 발송을 로그 한 줄 없이 삼킨다 — 발행 층의 except가 그것을 흡수하기 때문이다.
    HTML 렌더러와 같은 계약을 진다.
    """
    try:
        return _render_md(model)
    except Exception as exc:            # noqa: BLE001 — 최후의 그물(계약)
        case_id = getattr(getattr(model, "record", None), "id", "?")
        return (f"# 케이스 {case_id} 보고서\n\n"
                f"보고서 조립 실패: {type(exc).__name__}: {exc}\n")


def _render_md(model: ReportModel) -> str:
    sections = [
        f"# 케이스 {model.record.id} 보고서",
        "",
        f"작성 시각: {model.generated_at.isoformat()}",
        "",
        _section1(model),
        "",
        _section2(model.record, model.verdict),
        "",
        _section3(model.verdict),
        "",
        _section4(model.evidence, model.evidence_summaries),
        "",
        _section5(model),
    ]
    return "\n".join(sections) + "\n"


def write_report(text: str, *, output_dir: str, case_id: str, suffix: str = "md") -> str:
    """`{output_dir}/{case_id}.{suffix}`에 UTF-8로 쓰고 경로를 반환한다.

    디렉터리가 없으면 만든다. 실패(권한·경로 오류 등)는 raise 대신 빈
    문자열로 알린다 — 호출자(usecase)가 report_ready 대신 실패를 보고한다.
    """
    try:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{case_id}.{suffix}"
        path.write_text(text, encoding="utf-8")
        return str(path)
    except OSError:
        return ""


def _verdict_headline(record: CaseRecord, verdict: Verdict | None) -> str:
    if verdict is None:
        reason = record.closed_reason or "미상"
        return f"판정 없음(종결 사유: {reason})"
    first_line = verdict.narrative.splitlines()[0] if verdict.narrative else ""
    return f"{verdict.verdict_type} — {first_line}"


def _stage_table(stages) -> list[str]:
    """§1의 조사 단계 체크리스트.

    표 앞뒤에 빈 줄을 둔다 — 빈 줄 없이 앞 불릿에 붙으면 GFM이 리스트 항목의
    느슨한 계속으로 흡수해 표가 아니라 평문으로 렌더한다(§5 태스크 표에서 겪은
    것과 같은 함정, mistune으로 실측 확인).
    """
    lines = ["", "| 단계 | 상태 | 비고 |", "|---|---|---|"]
    for s in stages:
        lines.append(f"| {s.label} | {_MARKS.get(s.mark, '?')} | {s.note or '없음'} |")
    lines.append("")
    return lines


def _section1(model: ReportModel) -> str:
    record = model.record
    confidence = model.verdict.confidence if model.verdict is not None else "없음"
    rows = [
        f"- 케이스 id: {record.id}",
        f"- 스코프: {record.gbm}/{record.fct}",
        f"- 개설 경로: {record.origin}",
        f"- 증상: {record.symptom}",
        f"- T0: {record.t0.isoformat()}",
        f"- 판정: {_verdict_headline(record, model.verdict)}",
        f"- 신뢰도: {confidence}",
        f"- 태스크 에러율: {model.task_error_rate}",
        "- 조사 단계:",
    ]
    return "## 1. 요약\n" + "\n".join(rows + _stage_table(model.stages))


def _section2(record: CaseRecord, verdict: Verdict | None) -> str:
    if verdict is None:
        return "## 2. 판정\n" + _verdict_headline(record, verdict)

    lines = ["## 2. 판정"]
    if verdict.root_cause is not None:
        rc = verdict.root_cause
        ids = ", ".join(rc.evidence_ids) or "없음"
        lines.append(f"- 근본 원인: {rc.component} (증거: {ids})")
    else:
        lines.append("- 근본 원인: 없음")

    lines.append("- 기여 요인:")
    if not verdict.contributing:
        lines.append("  없음")
    else:
        for c in verdict.contributing:
            ids = ", ".join(c.evidence_ids) or "없음"
            relation = f" — {c.relation}" if c.relation else ""
            lines.append(f"  - {c.component} (증거: {ids}){relation}")

    lines.append("- caveat:")
    if not verdict.caveats:
        lines.append("  없음")
    else:
        for cv in verdict.caveats:
            lines.append(f"  - {cv}")

    return "\n".join(lines)


def _section3(verdict: Verdict | None) -> str:
    recs = verdict.recommendations if verdict is not None else []
    if not recs:
        return "## 3. 조치 권고\n없음"
    body = "\n".join(f"{i}. {r}" for i, r in enumerate(recs, start=1))
    return "## 3. 조치 권고\n" + body


def _section4(evidence: list[EvidenceRecord], evidence_summaries: dict[str, str] | None) -> str:
    if not evidence:
        return "## 4. 증거\n없음"
    # evidence_summaries가 주어지면(있는 id만) "요지" 열을 쓰고, 아예 안 주어졌으면
    # digest뿐이라는 걸 열 이름으로 정직하게 표기한다(I4) — 개별 id가 요지 조회에
    # 실패해(daemon._publish_report가 건너뛴 경우) 빠져 있으면 그 행만 digest로 폴백한다.
    column = "요지" if evidence_summaries is not None else "본문 digest"
    lines = ["## 4. 증거",
             f"| id | 출처 | as_of | 완전성 | effective_as_of | {column} |",
             "|---|---|---|---|---|---|"]
    for ev in evidence:
        as_of = ev.as_of.isoformat() if ev.as_of else "-"
        eff = ev.effective_as_of.isoformat() if ev.effective_as_of else "-"
        complete = "완전" if ev.complete else "⚠ 불완전"
        digest = (ev.body_digest or "")[:12]
        summary = (evidence_summaries or {}).get(ev.id, digest)
        lines.append(f"| {ev.id} | {ev.source} | {as_of} | {complete} | {eff} | {summary} |")
    return "\n".join(lines)


def _qa_entry_summary(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("kind", entry))
    return str(entry)


def _section5(model: ReportModel) -> str:
    lines = ["## 5. 조사 경위"]
    # 부분 스냅샷임을 숨기면 "라운드 2"가 완결된 조사처럼 읽힌다 — 조용한 생략이다.
    if model.partial:
        lines.append("- 스냅샷: 실패 시점 부분 스냅샷(조사 미완)")
        if model.salvage_error:
            lines.append(f"- 조사 흔적 구제 실패: {model.salvage_error}")
    lines.append(f"- 라운드: {model.round_no if model.round_no is not None else '없음'}")
    lines.append("- 태스크 현황:")

    task_rows = []
    for t in model.plan_tasks:
        status = t.get("status", "?")
        note = "미조사" if status in _UNINVESTIGATED else (t.get("error") or "")
        task_rows.append(f"| {t.get('id', '?')} | {t.get('role', '?')} | {status} | {note} |")
    if not task_rows:
        lines.append("  없음")
    else:
        # M1/R2: 표를 앞의 "- 태스크 현황:" 불릿과 완전히 떼어낸다. 들여쓰기 제거만으론
        # 부족했다 — 빈 줄 없이 붙어 있으면 GFM이 이 표 줄들을 앞 리스트 항목의
        # "느슨한 계속(lazy continuation)" 텍스트로 흡수해 표가 아니라 평문으로 렌더한다
        # (mistune(GFM 표 플러그인)으로 직접 렌더해 확인). 표 앞뒤로 빈 줄을 둬
        # 리스트를 확실히 끊고 표를 최상위 블록으로 분리한다.
        lines.append("")
        lines.append("| id | 역할 | status | 비고 |")
        lines.append("|---|---|---|---|")
        lines.extend(task_rows)
        lines.append("")

    refuted = [h for h in model.hypotheses if h.get("status") == "refuted"]
    lines.append("- 기각된 가설:")
    if not refuted:
        lines.append("  없음")
    else:
        for h in refuted:
            refuting = ", ".join(h.get("refuting_ids") or []) or "없음"
            lines.append(f"  - {h.get('id', '?')}: {h.get('statement', '')} (반박 증거: {refuting})")

    lines.append("- 검증 문제:")
    if not model.verify_problems:
        lines.append("  없음")
    else:
        for p in model.verify_problems:
            lines.append(f"  - {p}")

    qa_summaries = [_qa_entry_summary(e) for e in model.qa_log]
    lines.append("- QA 로그:")
    if not qa_summaries:
        lines.append("  없음")
    else:
        lines.append("  " + ", ".join(qa_summaries))

    return "\n".join(lines)
