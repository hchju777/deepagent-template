"""Fleet 집계 리포트 렌더러 — 커버리지가 **숫자보다 앞에** 온다(계획 16/P7).

순서 자체가 방어선이다: 숫자를 먼저 보여주고 아래에 각주로 커버리지를 다는 것이
"알람 12% 감소"가 실은 "3개 법인 데이터 누락"인 사고의 형태다. 읽는 사람은 첫 화면을
읽고 결론을 내린다.

두 렌더러가 같은 데이터(`FleetReport`)에서 나온다 — 계획 7의 2단 원칙과 같다.
절대 raise하지 않는다: 리포트 조립 실패가 발송 자체를 삼키면 안 된다.
"""
from html import escape
from typing import Any

from src.domain.rollup import FleetReport

_MARK_INCOMPLETE = "⚠"


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _num(value: float | None) -> str:
    """값이 없으면 대시다 — **0이 아니다.** 0은 "전부 0이었다"는 다른 주장이다."""
    if value is None:
        return "—"
    return f"{value:g}"


def _delta(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+g}"


def _window_span(report: FleetReport) -> str:
    seconds = max(0.0, (report.window_to - report.window_from).total_seconds())
    if seconds < 60:
        return f"{seconds:.0f}초"
    return f"{seconds / 60:.0f}분"


def _coverage_counts(report: FleetReport) -> tuple[int, int]:
    covered = sum(1 for c in report.coverage if c.status == "covered")
    return covered, len(report.coverage)


def render_fleet_md(report: FleetReport) -> str:
    try:
        return _md(report)
    except Exception as exc:                    # noqa: BLE001 — 최후의 그물(계약)
        return (f"# {getattr(report, 'title', '집계')} 리포트\n\n"
                f"리포트 조립 실패: {type(exc).__name__}: {exc}\n")


def _md(report: FleetReport) -> str:
    covered, total = _coverage_counts(report)
    lines = [
        f"# {report.title}",
        "",
        f"- 시나리오: {report.scenario} (concern: {report.concern})",
        f"- 실행 창: {report.window_from.isoformat()} ~ {report.window_to.isoformat()}"
        f" (최대 편차 {_window_span(report)})",
        f"- 시나리오 digest: {report.scenario_digest}",
        "",
        "## 커버리지",                     # 숫자보다 **먼저**
        "",
        f"{covered} / {total} 사이트 (필수 지표 기준)",
        "",
    ]
    gaps = [c for c in report.coverage if c.status != "covered"]
    if gaps:
        lines += ["| 사이트 | 상태 | 사유 | 마지막 성공 |", "|---|---|---|---|"]
        for c in gaps:
            last = c.last_success_at.isoformat() if c.last_success_at else "—"
            lines.append(f"| {c.gbm}/{c.fct} | {c.status} | {c.reason or ''} | {last} |")
        lines.append("")
    else:
        lines += ["전 사이트 확인됨", ""]

    lines += ["## 지표", "", "| metric | value | reduce | 커버리지 | 완전성 |", "|---|---|---|---|---|"]
    for r in report.rollups:
        mark = "완전" if r.complete else f"{_MARK_INCOMPLETE} {r.coverage_note or '불완전'}"
        unit = f" {r.unit}" if r.unit else ""
        lines.append(f"| {r.metric} | {_num(r.value)}{unit} | {r.reduce} "
                     f"| {r.covered_sites}/{r.expected_sites} | {mark} |")
    lines.append("")

    if report.groups:
        lines += ["## 축 분해", "", "| 축 | metric | value | 커버리지 |", "|---|---|---|---|"]
        for axis, rollups in report.groups.items():
            for r in rollups:
                lines.append(f"| {axis} | {r.metric} | {_num(r.value)} "
                             f"| {r.covered_sites}/{r.expected_sites} |")
        lines.append("")

    lines += ["## 추세", ""]
    if report.trend_caveat:
        lines.append(report.trend_caveat)
    elif report.trend:
        lines += ["| metric | 전회 대비 |", "|---|---|"]
        lines += [f"| {k} | {_delta(v)} |" for k, v in report.trend.items()]
    else:
        lines.append("이전 실행 없음")
    return "\n".join(lines) + "\n"


def render_fleet_html(report: FleetReport) -> str:
    try:
        return _html(report)
    except Exception as exc:                    # noqa: BLE001
        return ("<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
                "<title>집계 리포트 조립 실패</title></head><body>"
                f"<p>{_e(f'{type(exc).__name__}: {exc}')}</p></body></html>")


def _rows(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _html(report: FleetReport) -> str:
    covered, total = _coverage_counts(report)
    gaps = [[_e(f"{c.gbm}/{c.fct}"), _e(c.status), _e(c.reason or ""),
             _e(c.last_success_at.isoformat() if c.last_success_at else "—")]
            for c in report.coverage if c.status != "covered"]
    coverage_block = (_rows(["사이트", "상태", "사유", "마지막 성공"], gaps) if gaps
                      else "<p>전 사이트 확인됨</p>")
    metric_rows = [[_e(r.metric), _e(_num(r.value) + (f" {r.unit}" if r.unit else "")),
                    _e(r.reduce), _e(f"{r.covered_sites}/{r.expected_sites}"),
                    _e("완전" if r.complete else f"{_MARK_INCOMPLETE} {r.coverage_note or '불완전'}")]
                   for r in report.rollups]
    group_block = ""
    if report.groups:
        group_rows = [[_e(axis), _e(r.metric), _e(_num(r.value)),
                       _e(f"{r.covered_sites}/{r.expected_sites}")]
                      for axis, rollups in report.groups.items() for r in rollups]
        group_block = ("<h2>축 분해</h2>"
                       + _rows(["축", "metric", "value", "커버리지"], group_rows))
    if report.trend_caveat:
        trend_block = f"<p>{_e(report.trend_caveat)}</p>"
    elif report.trend:
        trend_block = _rows(["metric", "전회 대비"],
                            [[_e(k), _e(_delta(v))] for k, v in report.trend.items()])
    else:
        trend_block = "<p>이전 실행 없음</p>"
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>{_e(report.title)}</title></head><body>
<h1>{_e(report.title)}</h1>
<p>시나리오: {_e(report.scenario)} (concern: {_e(report.concern)})</p>
<p>실행 창: {_e(report.window_from.isoformat())} ~ {_e(report.window_to.isoformat())}
 (최대 편차 {_e(_window_span(report))})</p>
<p>시나리오 digest: {_e(report.scenario_digest)}</p>
<h2>커버리지</h2>
<p>{covered} / {total} 사이트 (필수 지표 기준)</p>
{coverage_block}
<h2>지표</h2>
{_rows(["metric", "value", "reduce", "커버리지", "완전성"], metric_rows)}
{group_block}
<h2>추세</h2>
{trend_block}
</body></html>
"""
