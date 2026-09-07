"""집계 리포트 — 커버리지가 **숫자보다 앞에** 온다(계획 16/P7, 방향 문서 §4.2)."""
from datetime import datetime, timedelta, timezone

from src.domain.rollup import FleetReport, MetricRollup, SiteCoverage
from src.presentation.fleet_report import render_fleet_html, render_fleet_md

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _report(**kw):
    base = dict(
        scenario="alarm_trend", title="알람 추세", concern="operation",
        scenario_digest="d1a2b3", window_from=T, window_to=T + timedelta(minutes=40),
        coverage=[SiteCoverage(gbm="mx", fct="gumi", status="covered"),
                  SiteCoverage(gbm="mx", fct="suwon", status="missing", reason="REST 타임아웃",
                               last_success_at=T - timedelta(days=3)),
                  SiteCoverage(gbm="ds", fct="xian", status="fallback",
                               reason="effective_as_of가 요청보다 6시간 이전")],
        rollups=[MetricRollup(metric="alarms", value=12.0, reduce="sum", expected_sites=3,
                              covered_sites=2, complete=False, coverage_note="1개 사이트 미확인",
                              unit="건")],
        generated_at=T)
    base.update(kw)
    return FleetReport(**base)


def test_커버리지가_지표보다_먼저_나온다():
    # 숫자를 먼저 보여주고 아래에 각주로 커버리지를 다는 것이 "12% 감소"가 실은
    # "3개 법인 누락"인 사고의 형태다. 순서 자체가 방어선이다.
    md = render_fleet_md(_report())
    assert md.index("## 커버리지") < md.index("## 지표") < md.index("alarms")
    html = render_fleet_html(_report())
    assert html.index("<h2>커버리지</h2>") < html.index("<h2>지표</h2>") < html.index("alarms")


def test_미확인_사이트가_사유와_마지막_성공과_함께_나온다():
    md = render_fleet_md(_report())
    assert "mx/suwon" in md and "REST 타임아웃" in md and "2026-08-31" in md
    assert "ds/xian" in md and "6시간 이전" in md
    assert "2 / 3" in md or "2/3" in md          # 커버 수를 먼저 말한다


def test_불완전한_지표는_표식과_사유를_단다():
    md = render_fleet_md(_report())
    assert "1개 사이트 미확인" in md and "⚠" in md


def test_값이_없으면_대시이지_0이_아니다():
    rollup = MetricRollup(metric="alarms", value=None, reduce="sum", expected_sites=3,
                          covered_sites=0, complete=False, coverage_note="전부 실패")
    md = render_fleet_md(_report(rollups=[rollup]))
    assert "| — |" in md and "| 0 |" not in md and "0.0" not in md


def test_헤더에_창과_digest가_있다():
    md = render_fleet_md(_report())
    assert "2026-09-03T08:00:00+00:00" in md and "08:40" in md
    assert "d1a2b3" in md and "40분" in md        # 최대 편차를 사람이 읽는 단위로


def test_추세는_digest가_다르면_문장으로_대체된다():
    caveat = "이전 실행의 scenario_digest가 다르다(옛d → d1a2b3) — 추세 비교 불가"
    md = render_fleet_md(_report(trend={"alarms": 3.0}, trend_caveat=caveat))
    assert caveat in md and "+3" not in md
    with_trend = render_fleet_md(_report(trend={"alarms": 3.0}, previous_digest="d1a2b3"))
    assert "+3" in with_trend


def test_HTML은_대상_문자열을_이스케이프한다():
    html = render_fleet_html(_report(coverage=[
        SiteCoverage(gbm="mx", fct="gumi", status="missing", reason="<script>x</script>")]))
    assert "&lt;script&gt;" in html and "<script>x" not in html
    full = render_fleet_html(_report())
    assert "2026-08-31" in full          # 미확인 사이트의 마지막 성공 시각도 HTML에 있다


def test_축_분해가_있으면_표로_나온다():
    groups = {"mx": [MetricRollup(metric="alarms", value=5.0, reduce="sum", expected_sites=2,
                                  covered_sites=2, complete=True)],
              "ds": [MetricRollup(metric="alarms", value=7.0, reduce="sum", expected_sites=1,
                                  covered_sites=1, complete=True)]}
    md = render_fleet_md(_report(groups=groups))
    assert "축 분해" in md and "mx" in md and "5" in md and "7" in md


def test_렌더는_형태가_망가진_보고서에도_raise하지_않는다():
    broken = _report().model_copy(update={"rollups": ["이상한 값"]})
    assert "알람 추세" in render_fleet_md(broken) or "실패" in render_fleet_md(broken)
    assert render_fleet_html(broken)
