"""보고서 렌더러 — 스펙 §5.1.

엔진은 도메인 객체(Verdict + 케이스 파일)만 내고, 5절 마크다운 조립은 여기
(프레젠테이션 층)의 책임이다. case_file은 Store에 박제된 원시 dict라서
(worker._case_file_snapshot, 계획 4b I6) 옛 스냅샷이거나 일부 키가 빠져도
render_report는 절대 raise하지 않는다 — 보고서 조립 실패가 조사 종결을
막아서는 안 된다("항상 파일 먼저" 원칙, §5.1).

"무엇을 확인 안 했나"의 명시(§5)가 신뢰의 조건이다 — 조용한 생략 금지의
보고서판. 그래서 각 하위 항목이 비면 값을 지우는 대신 "없음"을 적는다.
"""
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from src.domain.case import Verdict
from src.domain.cases import CaseRecord
from src.domain.store import EvidenceRecord

Clock = Callable[[], datetime]

_UNINVESTIGATED = {"pending", "cancelled"}   # §5: 미조사로 명시할 태스크 상태


def render_report(record: CaseRecord, *, verdict: Verdict | None,
                   evidence: list[EvidenceRecord], case_file: dict | None,
                   clock: Clock) -> str:
    """스펙 §5.1의 5절 보고서를 md로 조립한다.

    순수 함수 — case_file의 형태가 기대와 어긋나도 raise하지 않고
    "없음"으로 채운 보고서(또는 실패 시 최소 안내문)를 반환한다.
    """
    try:
        return _render(record, verdict=verdict, evidence=evidence,
                       case_file=case_file, clock=clock)
    except Exception as exc:            # noqa: BLE001 — 최후의 그물: 컨테이너 타입 가드(_as_list)를
        # 통과한 뒤에도 예상 못 한 형태가 있을 수 있다 — 그때도 조사 종결은 막지 않는다(계약)
        return (f"# 케이스 {getattr(record, 'id', '?')} 보고서\n\n"
                f"보고서 조립 실패: {type(exc).__name__}: {exc}\n")


def write_report(text: str, *, output_dir: str, case_id: str) -> str:
    """`{output_dir}/{case_id}.md`에 UTF-8로 쓰고 경로를 반환한다.

    디렉터리가 없으면 만든다. 실패(권한·경로 오류 등)는 raise 대신 빈
    문자열로 알린다 — 호출자(usecase)가 report_ready 대신 실패를 보고한다.
    """
    try:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{case_id}.md"
        path.write_text(text, encoding="utf-8")
        return str(path)
    except OSError:
        return ""


def _as_list(value: object) -> list:
    """스냅샷 필드가 리스트가 아니면 빈 리스트로 — 형태 이상에서도 5절 구조는 지킨다.

    `value or []`는 falsy만 걸러내 5 같은 truthy 비-리스트를 그대로 통과시키고,
    이후 리스트 컴프리헨션/순회에서 TypeError가 나 바깥 try/except까지 튀며
    5절 헤딩 전체가 사라진다 — 컨테이너 레벨에서 막는다.
    """
    return value if isinstance(value, list) else []


def _render(record: CaseRecord, *, verdict: Verdict | None,
           evidence: list[EvidenceRecord], case_file: dict | None, clock: Clock) -> str:
    case_file = case_file if isinstance(case_file, dict) else {}
    plan_tasks = _as_list(case_file.get("plan_tasks"))
    hypotheses = _as_list(case_file.get("hypotheses"))
    qa_log = _as_list(case_file.get("qa_log"))
    verify_problems = _as_list(case_file.get("verify_problems"))
    round_raw = case_file.get("round")
    round_no = round_raw if isinstance(round_raw, int) else None

    sections = [
        f"# 케이스 {record.id} 보고서",
        "",
        f"작성 시각: {clock().isoformat()}",
        "",
        _section1(record, verdict, plan_tasks),
        "",
        _section2(record, verdict),
        "",
        _section3(verdict),
        "",
        _section4(evidence),
        "",
        _section5(round_no, plan_tasks, hypotheses, verify_problems, qa_log),
    ]
    return "\n".join(sections) + "\n"


def _verdict_headline(record: CaseRecord, verdict: Verdict | None) -> str:
    if verdict is None:
        reason = record.closed_reason or "미상"
        return f"판정 없음(종결 사유: {reason})"
    first_line = verdict.narrative.splitlines()[0] if verdict.narrative else ""
    return f"{verdict.verdict_type} — {first_line}"


def _task_error_rate(plan_tasks: list) -> str:
    if not plan_tasks:
        return "없음"
    total = len(plan_tasks)
    errors = sum(1 for t in plan_tasks if isinstance(t, dict) and t.get("status") == "error")
    return f"{errors}/{total}"


def _section1(record: CaseRecord, verdict: Verdict | None, plan_tasks: list) -> str:
    confidence = verdict.confidence if verdict is not None else "없음"
    rows = [
        f"- 케이스 id: {record.id}",
        f"- 스코프: {record.gbm}/{record.fct}",
        f"- 개설 경로: {record.origin}",
        f"- 증상: {record.symptom}",
        f"- T0: {record.t0.isoformat()}",
        f"- 판정: {_verdict_headline(record, verdict)}",
        f"- 신뢰도: {confidence}",
        f"- 태스크 에러율: {_task_error_rate(plan_tasks)}",
    ]
    return "## 1. 요약\n" + "\n".join(rows)


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


def _section4(evidence: list[EvidenceRecord]) -> str:
    if not evidence:
        return "## 4. 증거\n없음"
    lines = ["## 4. 증거",
             "| id | 출처 | as_of | 완전성 | effective_as_of | 요지 |",
             "|---|---|---|---|---|---|"]
    for ev in evidence:
        as_of = ev.as_of.isoformat() if ev.as_of else "-"
        eff = ev.effective_as_of.isoformat() if ev.effective_as_of else "-"
        complete = "완전" if ev.complete else "⚠ 불완전"
        digest = (ev.body_digest or "")[:12]
        lines.append(f"| {ev.id} | {ev.source} | {as_of} | {complete} | {eff} | {digest} |")
    return "\n".join(lines)


def _qa_entry_summary(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("kind", entry))
    return str(entry)


def _section5(round_no: int | None, plan_tasks: list, hypotheses: list,
             verify_problems: list, qa_log: list) -> str:
    lines = ["## 5. 조사 경위",
             f"- 라운드: {round_no if round_no is not None else '없음'}",
             "- 태스크 현황:"]

    task_rows = []
    for t in plan_tasks:
        if not isinstance(t, dict):
            continue
        status = t.get("status", "?")
        note = "미조사" if status in _UNINVESTIGATED else (t.get("error") or "")
        task_rows.append(f"  | {t.get('id', '?')} | {t.get('role', '?')} | {status} | {note} |")
    if not task_rows:
        lines.append("  없음")
    else:
        lines.append("  | id | 역할 | status | 비고 |")
        lines.append("  |---|---|---|---|")
        lines.extend(task_rows)

    refuted = [h for h in hypotheses if isinstance(h, dict) and h.get("status") == "refuted"]
    lines.append("- 기각된 가설:")
    if not refuted:
        lines.append("  없음")
    else:
        for h in refuted:
            refuting = ", ".join(h.get("refuting_ids") or []) or "없음"
            lines.append(f"  - {h.get('id', '?')}: {h.get('statement', '')} (반박 증거: {refuting})")

    lines.append("- 검증 문제:")
    if not verify_problems:
        lines.append("  없음")
    else:
        for p in verify_problems:
            lines.append(f"  - {p}")

    qa_summaries = [_qa_entry_summary(e) for e in qa_log]
    lines.append("- QA 로그:")
    if not qa_summaries:
        lines.append("  없음")
    else:
        lines.append("  " + ", ".join(qa_summaries))

    return "\n".join(lines)
