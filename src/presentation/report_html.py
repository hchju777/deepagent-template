"""HTML 보고서 렌더러 — 스펙 §4.1.

ReportModel만 보고 문자열을 만든다(report.py의 마크다운 렌더러와 같은 소스).
템플릿 엔진을 쓰지 않는 이유는 스펙 §6이 YAGNI로 기각했기 때문이다 — 내장
템플릿 하나면 충분하고, 사용자 템플릿은 v2 범위 밖이다.

이스케이프는 선택이 아니다: 증상·narrative·component·오류 메시지에는 LLM이 쓴
텍스트와 대상 시스템의 응답이 섞여 들어온다. 그대로 넣으면 보고서를 여는 것만으로
임의 마크업이 실행된다.
"""
from html import escape

_MARKS = {"ok": "✅", "fail": "❌", "warn": "⚠", "skip": "⬜"}

_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 60rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; }
th { background: #f5f5f5; }
h2 { border-bottom: 1px solid #eee; padding-bottom: 0.3rem; margin-top: 2rem; }
.partial { background: #fff8e1; border-left: 4px solid #ffb300; padding: 0.6rem 1rem; }
"""


def _e(value) -> str:
    return escape(str(value), quote=True)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """셀은 호출부가 이미 이스케이프한 것으로 본다(기호·표식을 넣어야 하므로)."""
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _bullets(items: list[str]) -> str:
    if not items:
        return "<p>없음</p>"
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _headline(record, verdict) -> str:
    """마크다운의 _verdict_headline과 같은 규약 — narrative 첫 줄까지 싣는다.

    HTML이 기본 산출물이므로 여기서 narrative를 빠뜨리면 리드의 서술이 사람에게
    닿는 유일한 경로가 사라진다.
    """
    if verdict is None:
        return f"판정 없음(종결 사유: {record.closed_reason or '미상'})"
    first_line = verdict.narrative.splitlines()[0] if verdict.narrative else ""
    return f"{verdict.verdict_type} — {first_line}"


def render_html(model) -> str:
    """ReportModel에서 HTML 보고서를 조립한다. 절대 raise하지 않는다."""
    try:
        return _render(model)
    except Exception as exc:            # noqa: BLE001 — 마크다운 렌더러와 같은 계약
        return ("<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
                "<title>보고서 조립 실패</title></head><body>"
                f"<h1>보고서 조립 실패</h1><p>{_e(f'{type(exc).__name__}: {exc}')}</p>"
                "</body></html>")


from src.presentation.report import _digests   # 두 렌더러가 같은 표기를 쓴다


def _render(model) -> str:
    record = model.record
    verdict = model.verdict

    summary_rows = [
        ["케이스 id", _e(record.id)],
        ["스코프", _e(f"{record.gbm}/{record.fct}")],
        ["개설 경로", _e(record.origin)],
        ["증상", _e(record.symptom)],
        ["T0", _e(record.t0.isoformat())],
        ["판정", _e(_headline(record, verdict))],
        ["신뢰도", _e(verdict.confidence if verdict else "없음")],
        ["태스크 에러율", _e(model.task_error_rate)],
        ["지식 digest", _e(_digests(model.knowledge_digests))],
    ]
    stage_rows = [[_e(s.label), _MARKS.get(s.mark, "?"), _e(s.note or "없음")]
                  for s in model.stages]

    cause_items = []
    if verdict is not None and verdict.root_cause is not None:
        ids = ", ".join(verdict.root_cause.evidence_ids) or "없음"
        cause_items.append(f"근본 원인: {_e(verdict.root_cause.component)} (증거: {_e(ids)})")
    elif verdict is not None:
        # 항목을 지우면 "확인 안 했다"와 "없다"를 구별할 수 없다(마크다운과 같은 규약).
        cause_items.append("근본 원인: 없음")
    for c in (verdict.contributing if verdict else []):
        ids = ", ".join(c.evidence_ids) or "없음"
        relation = f" — {_e(c.relation)}" if c.relation else ""
        cause_items.append(f"기여 요인: {_e(c.component)} (증거: {_e(ids)}){relation}")

    evidence_rows = [[_e(ev.id), _e(ev.source),
                      _e(ev.as_of.isoformat() if ev.as_of else "-"),
                      ("완전" if ev.complete
                       else _e(f"⚠ 불완전({ev.truncated_reason})") if ev.truncated_reason
                       else "⚠ 불완전"),
                      _e(ev.effective_as_of.isoformat() if ev.effective_as_of else "-"),
                      _e((model.evidence_summaries or {}).get(ev.id,
                                                              (ev.body_digest or "")[:12]))]
                     for ev in model.evidence]

    task_rows = [[_e(t.get("id", "?")), _e(t.get("role", "?")), _e(t.get("status", "?")),
                  _e("미조사" if t.get("status") in ("pending", "cancelled")
                     else (t.get("error") or ""))]
                 for t in model.plan_tasks]

    refuted = [f"{_e(h.get('id', '?'))}: {_e(h.get('statement', ''))} "
               f"(반박 증거: {_e(', '.join(h.get('refuting_ids') or []) or '없음')})"
               for h in model.hypotheses if h.get("status") == "refuted"]

    qa_items = [_e(e.get("kind", e)) if isinstance(e, dict) else _e(e) for e in model.qa_log]

    partial_note = ""
    if model.partial:
        extra = (f"<br>조사 흔적 구제 실패: {_e(model.salvage_error)}"
                 if model.salvage_error else "")
        partial_note = f"<p class='partial'>실패 시점 부분 스냅샷(조사 미완){extra}</p>"

    # evidence_summaries가 없으면 실제로 보여주는 것은 digest다 — 열 이름으로
    # 정직하게 표기한다(§4 I4, 마크다운 렌더러와 같은 규약).
    column = "요지" if model.evidence_summaries is not None else "본문 digest"
    evidence_block = (_table(["id", "출처", "as_of", "완전성", "effective_as_of", column],
                             evidence_rows) if evidence_rows else "<p>없음</p>")
    task_block = (_table(["id", "역할", "status", "비고"], task_rows)
                  if task_rows else "<p>없음</p>")

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>케이스 {_e(record.id)} 보고서</title><style>{_STYLE}</style></head><body>
<h1>케이스 {_e(record.id)} 보고서</h1>
<p>작성 시각: {_e(model.generated_at.isoformat())}</p>
<h2>1. 요약</h2>
{_table(["항목", "값"], summary_rows)}
<h3>조사 단계</h3>
{_table(["단계", "상태", "비고"], stage_rows)}
<h2>2. 판정</h2>
{_bullets(cause_items)}
<h3>caveat</h3>
{_bullets([_e(c) for c in (verdict.caveats if verdict else [])])}
<h2>3. 조치 권고</h2>
{_bullets([_e(r) for r in (verdict.recommendations if verdict else [])])}
<h2>4. 증거</h2>
{evidence_block}
<h2>5. 조사 경위</h2>
{partial_note}
<p>라운드: {_e(model.round_no if model.round_no is not None else "없음")}</p>
{task_block}
<h3>기각된 가설</h3>
{_bullets(refuted)}
<h3>검증 문제</h3>
{_bullets([_e(p) for p in model.verify_problems])}
<h3>QA 로그</h3>
{_bullets(qa_items)}
</body></html>
"""
