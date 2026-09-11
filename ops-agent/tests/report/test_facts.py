"""집계 — 숫자가 맞는가, 그리고 **같은 입력이면 같은 순서인가.**

동점을 dict 순서에 맡기면 같은 데이터로 TOP 목록의 순서가 달라지고, 읽는 사람은
"어제와 순위가 바뀌었다"고 읽는다. 리포트가 낼 수 있는 조용한 거짓말이다.
"""
from datetime import date

import pytest

from src.config.schema_report import Thresholds
from src.report.facts import (Facts, SiteOutcome, freshness, line_ranking, repeats,
                              scenario_lifecycle, spikes, status_breakdown)
from src.report.rows import normalize

from .conftest import YESTERDAY, doc, facts_from

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

def test_라인_비중의_분모는_그_GBM이다(source, window):
    """전사 분모로 계산하면 법인이 많은 GBM의 라인은 영원히 비중이 작게 나온다."""
    mx_rows, _ = normalize([doc(YESTERDAY, line="P111")] * 3, source=source,
                           window=window, gbm="mx", fct="gumi")
    vd_rows, _ = normalize([doc(YESTERDAY, line="V1")] * 7, source=source,
                           window=window, gbm="vd", fct="suwon")
    facts = facts_from(mx_rows + vd_rows, window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="vd", fct="suwon", status="ok")))
    shares = {s.key[0]: s for s in line_ranking(facts, day=YESTERDAY)}
    assert shares["mx"].ratio == 1.0, "mx 안에서는 그 라인이 전부다"
    assert shares["vd"].ratio == 1.0


# ── 급증 ────────────────────────────────────────────────────────────

def test_급증은_배수와_최소_건수를_둘_다_넘겨야_한다(source, window):
    thresholds = Thresholds(spike_ratio=2.0, spike_min_count=10)
    facts = build([doc(YESTERDAY, plant="gumi")] * 30 + [doc(LAST_WEEK, plant="gumi")] * 10
                  + [doc(YESTERDAY, plant="sevt")] * 3 + [doc(LAST_WEEK, plant="sevt")],
                  source=source, window=window, thresholds=thresholds)
    found = {s.key[0]: s for s in spikes(facts, lambda r: r.plant, day=YESTERDAY)}
    assert "gumi" in found and found["gumi"].ratio == 3.0
    assert "sevt" not in found, "1건 → 3건은 3배지만 급증이라 부르면 경보 피로가 된다"


def test_전주에_없던_것도_건수가_충분하면_급증이다(source, window):
    facts = build([doc(YESTERDAY, plant="gumi")] * 20, source=source, window=window)
    found = spikes(facts, lambda r: r.plant, day=YESTERDAY)
    assert len(found) == 1 and found[0].baseline == 0


def test_비교를_끄면_급증을_계산하지_않는다(source):
    """기준이 없는데 급증을 주장하면 그건 추측이다."""
    from src.config.schema_report import WindowSpec
    from src.report.window import build_window

    window = build_window(WindowSpec(compare_previous_week=False), today=date(2026, 9, 7))
    facts = build([doc(YESTERDAY)] * 50, source=source, window=window)
    assert spikes(facts, lambda r: r.plant, day=YESTERDAY) == []


# ── 반복 ────────────────────────────────────────────────────────────

def test_반복은_건수와_발생일수를_둘_다_넘겨야_한다(source, window):
    """건수만 보면 알람이 많은 큰 라인이 항상 걸려서 "큰 라인 순위"가 된다."""
    thresholds = Thresholds(repeat_min_count=5, repeat_min_days=3)
    burst = [doc(YESTERDAY, line="P111", line_name="하루짜리")] * 20
    chronic = [doc(d, line="P222", line_name="만성") for d in window.days] * 2
    facts = build(burst + chronic, source=source, window=window, thresholds=thresholds)
    found = repeats(facts)
    assert [r.line_code for r in found] == ["P222"], "하루에 몰린 20건은 만성이 아니다"
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
    assert life.appeared == [("S02", "새것")]
    assert life.vanished == [("S01", "옛것")]


def test_전주_데이터는_신규_판정에_끼어들지_않는다(source, window):
    """`wanted`에는 전주가 들어 있지만 신규·소멸은 **집계 대상 날**만 본다 —
    창이 7 평일이면 "7 평일 안에서 처음"이 이 리포트가 단정할 수 있는 전부다."""
    facts = build([doc(date(2026, 8, 20), scen="S09", scen_name="전주만"),
                   doc(YESTERDAY, scen="S09", scen_name="전주만")],
                  source=source, window=window)
    assert scenario_lifecycle(facts).appeared == [("S09", "전주만")]


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
    assert [f.site for f in stalled] == ["mx/gumi"]


def test_원래_조용한_법인은_끊긴_것이_아니다(source, window):
    """창 전체가 0건이면 "데이터가 끊겼다"가 아니라 "알람이 없다"다."""
    facts = facts_from([], window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),))
    assert [f for f in freshness(facts) if f.stalled] == []
