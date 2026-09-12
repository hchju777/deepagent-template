"""집계 — 숫자가 맞는가, 그리고 **같은 입력이면 같은 순서인가.**

동점을 dict 순서에 맡기면 같은 데이터로 TOP 목록의 순서가 달라지고, 읽는 사람은
"어제와 순위가 바뀌었다"고 읽는다. 리포트가 낼 수 있는 조용한 거짓말이다.
"""
from datetime import date

import pytest

from src.config.schema_report import Thresholds
from src.report.facts import (Facts, SiteOutcome, freshness, ranking_by_gbm,
                              repeats, scenario_lifecycle, spikes, status_breakdown)
from src.report.rows import normalize

from tests.support import YESTERDAY, doc, facts_from

LAST_WEEK = date(2026, 8, 28)        # 어제의 전주 동요일(금)
DAY_BEFORE = date(2026, 9, 3)        # 어제의 직전 평일(목)


def build(documents, *, source, window, thresholds=None, gbm="mx", fct="gumi"):
    rows, _ = normalize(documents, source=source, window=window, gbm=gbm, fct=fct)
    return facts_from(rows, window=window, source=source, thresholds=thresholds)


# ── 세기 ────────────────────────────────────────────────────────────

def test_어제_건수와_두_비교값(source, window):
    facts = build([doc(YESTERDAY)] * 10 + [doc(DAY_BEFORE)] * 4 + [doc(LAST_WEEK)] * 5,
                  source=source, window=window)
    change = facts.compare(YESTERDAY)
    assert change.value == 10
    assert change.vs_previous_day == (6, 2.5)
    assert change.vs_previous_week == (5, 2.0)
    assert change.previous_day_label == DAY_BEFORE
    assert change.previous_week_label == LAST_WEEK


def test_기준이_0이면_배수를_내지_않는다(source, window):
    """0 대비 5건은 무한 배다 — 그걸 실으면 "∞% 증가"가 찍힌다."""
    facts = build([doc(YESTERDAY)] * 5, source=source, window=window)
    delta, ratio = facts.compare(YESTERDAY).vs_previous_week
    assert delta == 5 and ratio == 0.0


def test_일별_추세는_0건인_날도_남긴다(source, window):
    """없는 키를 빼면 그래프에서 그날이 사라지고, 읽는 사람은 "그날이 존재하지
    않았다"고 본다."""
    facts = build([doc(YESTERDAY)], source=source, window=window)
    daily = facts.daily()
    assert list(daily) == list(window.days)
    assert daily[YESTERDAY] == 1 and daily[DAY_BEFORE] == 0


def test_미해제만_세기(source, window):
    facts = build([doc(YESTERDAY, status=0), doc(YESTERDAY, status=10),
                   doc(YESTERDAY, status=40)], source=source, window=window)
    assert facts.total(day=YESTERDAY) == 3
    assert facts.total(day=YESTERDAY, unresolved=True) == 2


def test_status_분포는_config의_이름을_쓴다(source, window):
    facts = build([doc(YESTERDAY, status=0)] * 3 + [doc(YESTERDAY, status=40)],
                  source=source, window=window)
    assert status_breakdown(facts, day=YESTERDAY) == [("발생", 3), ("조치완료(자동)", 1)]


# ── 결정론 ──────────────────────────────────────────────────────────

def test_동점이면_키_순서로_정렬된다(source, window):
    """dict 순서에 맡기면 같은 데이터로 순위가 달라진다."""
    documents = [doc(YESTERDAY, line="P333", line_name="c"),
                 doc(YESTERDAY, line="P111", line_name="a"),
                 doc(YESTERDAY, line="P222", line_name="b")]
    facts = build(documents, source=source, window=window)
    assert facts.tally(lambda r: r.line, day=YESTERDAY) == [
        (("P111", "a"), 1), (("P222", "b"), 1), (("P333", "c"), 1)]


def test_문서_순서가_달라도_결과가_같다(source, window):
    documents = [doc(YESTERDAY, line=f"P{i}", line_name=str(i)) for i in range(5)]
    first = build(documents, source=source, window=window).tally(lambda r: r.line,
                                                                day=YESTERDAY)
    second = build(list(reversed(documents)), source=source,
                   window=window).tally(lambda r: r.line, day=YESTERDAY)
    assert first == second


# ── 라인 비중 ───────────────────────────────────────────────────────

def two_gbm_facts(source, window, *, mx: int, vd: int):
    mx_rows, _ = normalize([doc(YESTERDAY, line="P111", line_name="mx라인")] * mx,
                           source=source, window=window, gbm="mx", fct="gumi")
    vd_rows, _ = normalize([doc(YESTERDAY, line="V1", line_name="vd라인")] * vd,
                           source=source, window=window, gbm="vd", fct="suwon")
    return facts_from(mx_rows + vd_rows, window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="vd", fct="suwon", status="ok")), gbms=("mx", "vd"))


def test_비중의_분모는_그_GBM이다(source, window):
    """전사 분모로 계산하면 법인이 많은 GBM의 라인은 영원히 비중이 작게 나온다."""
    facts = two_gbm_facts(source, window, mx=3, vd=7)
    groups = {g.gbm: g for g in ranking_by_gbm(
        facts, lambda r: (r.plant, r.line_code, r.line_name), day=YESTERDAY, limit=5)}
    assert groups["mx"].items[0].ratio == 1.0, "mx 안에서는 그 라인이 전부다"
    assert groups["vd"].items[0].ratio == 1.0


def test_알람이_많은_GBM이_목록을_독차지하지_않는다(source, window):
    """전사 TOP N이었을 때 상위 10칸이 전부 MX였고 DA의 법인은 한 줄도 못 들어왔다."""
    facts = two_gbm_facts(source, window, mx=100, vd=2)
    groups = ranking_by_gbm(facts, lambda r: r.plant, day=YESTERDAY, limit=5)
    assert [g.gbm for g in groups] == ["mx", "vd"], "적은 GBM도 자기 묶음을 갖는다"
    assert groups[1].items[0].count == 2


def test_GBM_순서는_config가_정한다(source, window):
    """건수 순으로 정렬하면 색과 위치가 매일 바뀌어 위치로 기억할 수 없다."""
    facts = two_gbm_facts(source, window, mx=2, vd=50)
    assert facts.gbm_order() == ("mx", "vd"), "건수가 적은 mx가 먼저 선언됐으므로 먼저다"
    assert [g.gbm for g in ranking_by_gbm(facts, lambda r: r.plant,
                                          day=YESTERDAY, limit=5)] == ["mx", "vd"]
    assert facts.series_of("mx") == 0 and facts.series_of("vd") == 1


def test_config에_없는_GBM도_버리지_않는다(source, window):
    """설정 누락을 데이터 누락으로 바꾸지 않는다."""
    rows, _ = normalize([doc(YESTERDAY)], source=source, window=window,
                        gbm="zz", fct="unknown")
    facts = facts_from(rows, window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),), gbms=("mx",))
    assert facts.gbm_order() == ("mx", "zz")
    assert facts.series_of("zz") == 1


# ── 급증 ────────────────────────────────────────────────────────────

def test_기준은_어제를_제외한_평일_평균이다(source, window):
    """검사 대상인 날을 기준에 넣으면 그날이 튈수록 기준도 올라가 신호가 둔해진다."""
    earlier = [d for d in window.days if d != YESTERDAY]          # 6일
    facts = build([doc(YESTERDAY)] * 60 + [doc(d) for d in earlier for _ in range(10)],
                  source=source, window=window)
    assert facts.baseline(YESTERDAY) == 10.0, "어제 60건이 기준을 끌어올리면 안 된다"
    assert facts.total(day=YESTERDAY) == 60


def test_급증은_배수와_최소_건수를_둘_다_넘겨야_한다(source, window):
    thresholds = Thresholds(spike_ratio=2.0, spike_min_count=10)
    earlier = [d for d in window.days if d != YESTERDAY]
    documents = ([doc(YESTERDAY, plant="gumi")] * 30
                 + [doc(d, plant="gumi") for d in earlier for _ in range(10)]
                 + [doc(YESTERDAY, plant="sevt")] * 3
                 + [doc(earlier[0], plant="sevt")])
    facts = build(documents, source=source, window=window, thresholds=thresholds)
    found = {s.key[0]: s for s in spikes(facts, lambda r: r.plant, day=YESTERDAY)}
    assert "gumi" in found and found["gumi"].ratio == 3.0, "30건 vs 평균 10건"
    assert "sevt" not in found, "1건 → 3건은 3배지만 급증이라 부르면 경보 피로가 된다"


def test_기준_구간에_없던_것은_신규로_표시된다(source, window):
    """0 대비 20건은 무한 배다 — 배수 대신 "신규"로 말해야 한다."""
    facts = build([doc(YESTERDAY, plant="gumi")] * 20, source=source, window=window)
    found = spikes(facts, lambda r: r.plant, day=YESTERDAY)
    assert len(found) == 1
    assert found[0].baseline == 0.0 and found[0].brand_new is True
    assert found[0].ratio == 0.0, "무한 배를 숫자로 내놓지 않는다"


def test_급증_기준은_전주_비교_설정과_무관하다(source):
    """기준이 전주 동요일 하루가 아니라 창 안의 평균이므로, 전주 비교를 꺼도
    급증은 계산된다 — 둘은 다른 질문에 답한다."""
    from src.config.schema_report import WindowSpec
    from src.report.window import build_window

    off = build_window(WindowSpec(compare_previous_week=False), today=date(2026, 9, 7))
    earlier = [d for d in off.days if d != YESTERDAY]
    facts = build([doc(YESTERDAY)] * 50 + [doc(d) for d in earlier for _ in range(5)],
                  source=source, window=off)
    found = spikes(facts, lambda r: r.plant, day=YESTERDAY)
    assert len(found) == 1 and found[0].baseline == 5.0


# ── 반복 ────────────────────────────────────────────────────────────

def test_반복은_건수와_발생일수를_둘_다_넘겨야_한다(source, window):
    """건수만 보면 알람이 많은 큰 라인이 항상 걸려서 "큰 라인 순위"가 된다."""
    thresholds = Thresholds(repeat_min_count=5, repeat_min_days=3)
    burst = [doc(YESTERDAY, line="P111", line_name="하루짜리")] * 20
    chronic = [doc(d, line="P222", line_name="만성") for d in window.days] * 2
    facts = build(burst + chronic, source=source, window=window, thresholds=thresholds)
    found = repeats(facts)
    assert [r.line_code for r in found] == ["P222"], "하루에 몰린 20건은 만성이 아니다"
    assert found[0].gbm == "mx", "이슈 표의 GBM 열을 채우려면 필요하다"
    assert found[0].days == 7 and found[0].count == 14


def test_반복_목록은_발생일수_순이다(source, window):
    thresholds = Thresholds(repeat_min_count=5, repeat_min_days=2)
    many_one_day = [doc(YESTERDAY, line="A", line_name="a")] * 6 + [
        doc(DAY_BEFORE, line="A", line_name="a")]
    fewer_many_days = [doc(d, line="B", line_name="b") for d in window.days]
    facts = build(many_one_day + fewer_many_days, source=source, window=window,
                  thresholds=thresholds)
    assert [r.line_code for r in repeats(facts)] == ["B", "A"]


# ── 신규·소멸 ───────────────────────────────────────────────────────

def test_어제_처음_나타난_항목이_신규다(source, window):
    facts = build([doc(DAY_BEFORE, scen="S01", scen_name="옛것"),
                   doc(YESTERDAY, scen="S02", scen_name="새것")],
                  source=source, window=window)
    life = scenario_lifecycle(facts)
    assert [(i.gbm, i.name, i.count) for i in life.appeared] == [("mx", "새것", 1)]
    assert [(i.gbm, i.name) for i in life.vanished] == [("mx", "옛것")]
    assert life.appeared[0].plants == ("gumi",)


def test_신규는_GBM별로_본다(source, window):
    """전사로 보면 "DA에서 처음 나타난 항목"이 MX에 이미 있으면 안 잡힌다 —
    그런데 조치는 GBM 단위로 이뤄지므로 그게 더 중요한 신호다."""
    mx_rows, _ = normalize([doc(DAY_BEFORE, scen="S07", scen_name="공통항목"),
                            doc(YESTERDAY, scen="S07", scen_name="공통항목")],
                           source=source, window=window, gbm="mx", fct="gumi")
    da_rows, _ = normalize([doc(YESTERDAY, scen="S07", scen_name="공통항목")],
                           source=source, window=window, gbm="da", fct="gwangju")
    facts = facts_from(mx_rows + da_rows, window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="da", fct="gwangju", status="ok")), gbms=("mx", "da"))
    appeared = scenario_lifecycle(facts).appeared
    assert [(i.gbm, i.name) for i in appeared] == [("da", "공통항목")], \
        "mx에는 이미 있었지만 da에는 어제 처음이다"


def test_전주_데이터는_신규_판정에_끼어들지_않는다(source, window):
    """`wanted`에는 전주가 들어 있지만 신규·소멸은 **집계 대상 날**만 본다 —
    창이 7 평일이면 "7 평일 안에서 처음"이 이 리포트가 단정할 수 있는 전부다."""
    facts = build([doc(date(2026, 8, 20), scen="S09", scen_name="전주만"),
                   doc(YESTERDAY, scen="S09", scen_name="전주만")],
                  source=source, window=window)
    assert [i.name for i in scenario_lifecycle(facts).appeared] == ["전주만"]


# ── 표본과 가용성 ───────────────────────────────────────────────────

def test_한_법인이라도_잘리면_전체가_불완전하다(source, window):
    facts = facts_from([], window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok", complete=True),
        SiteOutcome(gbm="mx", fct="sevt", status="ok", complete=False,
                    truncated_reason="limit=10에 걸림")))
    assert facts.complete is False, "잘린 표본으로 낸 건수는 하한일 뿐이다"


def test_읽지_못한_법인은_결과에_남는다(source, window):
    facts = facts_from([], window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="mx", fct="sevt", status="error", error="타임아웃")))
    assert [s.site for s in facts.unavailable] == ["mx/sevt"]
    assert [s.site for s in facts.ok_sites] == ["mx/gumi"]


def test_어제만_데이터가_끊긴_법인을_찾는다(source, window):
    rows, _ = normalize([doc(DAY_BEFORE)] * 5, source=source, window=window,
                        gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),))
    stalled = [f for f in freshness(facts) if f.stalled]
    assert [(f.gbm, f.fct) for f in stalled] == [("mx", "gumi")]
    assert stalled[0].site == "mx/gumi"


def test_원래_조용한_법인은_끊긴_것이_아니다(source, window):
    """창 전체가 0건이면 "데이터가 끊겼다"가 아니라 "알람이 없다"다."""
    facts = facts_from([], window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),))
    assert [f for f in freshness(facts) if f.stalled] == []
