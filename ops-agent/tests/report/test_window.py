"""기간 계산 — 조용히 틀리는 것을 시끄럽게 만든다.

이 파일이 지키는 것은 넷이다:
  ① 월요일에 돌면 "어제"가 일요일이 아니라 금요일이다
  ② 날짜 필드가 문자열이라 **사전순 = 시간순**이어야 하고, 아니면 기동에서 막는다
  ③ 주말 제외는 쿼리가 아니라 코드가 한다(범위는 주말을 포함해 긁는다)
  ④ 전주 동요일이 조회 범위 **밖으로 나가지 않는다**
"""
from datetime import date, datetime, timedelta

import pytest

from src.config.schema_report import SourceSpec, WindowSpec
from src.report.window import (build_window, business_days_back, date_filter,
                               date_format_problem, format_boundary, parse_day,
                               previous_business_day, same_weekday_previous_week)

WEEKEND = frozenset({5, 6})


def source(**kw) -> SourceSpec:
    return SourceSpec(collection="alarm", date_field="occ_date", **kw)


# ── ① 어제가 어제인가 ────────────────────────────────────────────────────

@pytest.mark.parametrize("today, expected", [
    (date(2026, 9, 7), date(2026, 9, 4)),    # 월 → 금 (일·토를 건너뛴다)
    (date(2026, 9, 8), date(2026, 9, 7)),    # 화 → 월
    (date(2026, 9, 5), date(2026, 9, 4)),    # 토 → 금
    (date(2026, 9, 6), date(2026, 9, 4)),    # 일 → 금
    (date(2026, 9, 4), date(2026, 9, 3)),    # 금 → 목
])
def test_직전_평일(today, expected):
    assert previous_business_day(today, excluded=WEEKEND) == expected


def test_월요일에_돌면_어제는_금요일이다():
    window = build_window(WindowSpec(), today=date(2026, 9, 7))
    assert window.yesterday == date(2026, 9, 4)
    assert window.yesterday.weekday() == 4


def test_평일_일곱개를_주말을_건너뛰며_센다():
    days = business_days_back(date(2026, 9, 7), 7, excluded=WEEKEND)
    assert days == (date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31),
                    date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
                    date(2026, 9, 4))
    assert list(days) == sorted(days), "오름차순이어야 한다"


@pytest.mark.parametrize("offset", range(0, 400, 7))
def test_어떤_날에_돌아도_성질은_같다(offset):
    """1년 이상을 훑는다 — 월말·연말·윤년에서만 깨지는 계산을 막는다."""
    today = date(2026, 1, 1) + timedelta(days=offset)
    window = build_window(WindowSpec(business_days=7), today=today)

    assert len(window.days) == 7
    assert len(set(window.days)) == 7
    assert list(window.days) == sorted(window.days)
    assert all(d.weekday() not in WEEKEND for d in window.days)
    assert window.days[-1] < today, "오늘은 절대 포함되지 않는다(반쪽 하루)"


def test_주말_제외를_끄면_달력일_그대로다():
    spec = WindowSpec(business_days=7, exclude_weekdays=[])
    window = build_window(spec, today=date(2026, 9, 7))
    assert window.days == tuple(date(2026, 8, 31) + timedelta(days=i) for i in range(7))


# ── ② 문자열 날짜의 사전순 ────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d",
                                 "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M"])
def test_큰_자리부터_쓰는_형식은_통과한다(fmt):
    assert date_format_problem(fmt) is None


@pytest.mark.parametrize("fmt, 이유", [
    ("%d/%m/%Y", "사전순"),      # "31/12/1999" > "01/01/2000"
    ("%m/%d/%Y", "사전순"),
    ("%y-%m-%d", "사전순"),      # 세기 경계에서 뒤집힌다
    ("%Y-%m", "왕복"),           # 날짜 성분이 빠져 날짜별로 못 나눈다
    ("%H:%M:%S", "왕복"),        # 날짜가 아예 없다
])
def test_사전순이_시간순과_다른_형식은_거부된다(fmt, 이유):
    problem = date_format_problem(fmt)
    assert problem is not None, f"{fmt}가 통과해 버렸다"
    assert 이유 in problem


def test_형식_문제는_던지지_않고_메시지로_돌아온다():
    """`%-d`는 Windows의 strftime에서 ValueError다 — 리눅스에서 짜고 사내에서
    죽는 형태를 여기서 문자열로 바꾼다."""
    problem = date_format_problem("%Y-%-m-%d")
    assert isinstance(problem, str) and problem


def test_실제로_사전순_비교가_시간순과_같은지_직접_확인한다():
    """검사 함수를 믿지 않고, 통과한 형식으로 1년치를 찍어 정렬해 본다."""
    fmt = source().date_format
    days = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(400)]
    rendered = [d.strftime(fmt) for d in days]
    assert rendered == sorted(rendered)


# ── ③ 범위는 주말을 포함해 긁고, 거르기는 코드가 한다 ───────────────────

def test_조회_범위는_주말을_포함한다():
    window = build_window(WindowSpec(), today=date(2026, 9, 7))
    weekend = date(2026, 8, 29)                    # 토요일
    assert weekend.weekday() in WEEKEND
    assert window.covers(weekend), "쿼리는 주말도 긁는다(요일 필터가 서버에 없다)"
    assert weekend not in window.selected, "집계에서는 빠진다"


def test_몽고_필터는_기간_전체를_한_번에_긁는다():
    window = build_window(WindowSpec(), today=date(2026, 9, 7))
    assert date_filter(source(), window) == {
        "occ_date": {"$gte": "2026-08-20 00:00:00", "$lt": "2026-09-05 00:00:00"}}


def test_상한은_어제_끝까지_포함하고_오늘은_뺀다():
    """Mongo가 문자열 필드에 하는 것과 같은 비교를 그대로 해 본다."""
    window = build_window(WindowSpec(), today=date(2026, 9, 7))
    bounds = date_filter(source(), window)["occ_date"]
    lo, hi = bounds["$gte"], bounds["$lt"]

    def selected(raw: str) -> bool:
        return lo <= raw < hi

    assert selected("2026-09-04 23:59:59"), "어제 마지막 1초가 빠지면 안 된다"
    assert selected("2026-09-04 00:00:00")
    assert selected("2026-08-20 00:00:00"), "하한은 포함이다"
    assert not selected("2026-09-05 00:00:00"), "오늘은 반쪽 하루라 뺀다"
    assert not selected("2026-08-19 23:59:59")


def test_날짜만_있는_형식에서도_경계가_맞는다():
    """형식이 `%Y-%m-%d`라도 접두사 비교라 그대로 성립한다."""
    spec = source(date_format="%Y-%m-%d")
    window = build_window(WindowSpec(), today=date(2026, 9, 7))
    bounds = date_filter(spec, window)["occ_date"]
    assert bounds == {"$gte": "2026-08-20", "$lt": "2026-09-05"}
    assert bounds["$gte"] <= "2026-09-04 23:59:59" < bounds["$lt"]
    assert not (bounds["$gte"] <= "2026-09-05 00:00:00" < bounds["$lt"])


# ── ④ 전주 동요일이 범위 밖으로 나가지 않는다 ───────────────────────────

def test_전주_동요일은_요일이_같고_정확히_7일_전이다():
    for day in build_window(WindowSpec(), today=date(2026, 9, 7)).days:
        partner = same_weekday_previous_week(day)
        assert partner.weekday() == day.weekday()
        assert (day - partner).days == 7


@pytest.mark.parametrize("offset", range(0, 100, 3))
def test_전주_비교값이_조회_범위_안에_있다(offset):
    """이게 깨지면 비교 칸이 **항상** 비어서 "데이터가 없네"로 오인된다."""
    today = date(2026, 3, 2) + timedelta(days=offset)
    window = build_window(WindowSpec(), today=today)
    for day, partner in zip(window.days, window.previous_week):
        assert window.covers(partner), f"{partner}가 조회 범위 밖이다"
        assert window.previous_of(day) == partner


def test_비교를_끄면_범위가_줄어든다():
    on = build_window(WindowSpec(compare_previous_week=True), today=date(2026, 9, 7))
    off = build_window(WindowSpec(compare_previous_week=False), today=date(2026, 9, 7))
    assert off.previous_week == ()
    assert off.query_from == off.days[0]
    assert on.query_from < off.query_from, "비교를 켜면 그만큼 더 긁는다"
    assert off.previous_of(off.days[0]) is None


# ── 문서 한 건을 날짜로 되읽기 ──────────────────────────────────────────

def test_문서의_날짜를_되읽는다():
    assert parse_day(source(), "2026-08-21 00:00:00") == date(2026, 8, 21)
    assert parse_day(source(), datetime(2026, 8, 21, 13, 0)) == date(2026, 8, 21)
    assert parse_day(source(), date(2026, 8, 21)) == date(2026, 8, 21)


@pytest.mark.parametrize("raw", ["", "2026-08-21", "어제", None, 20260821, [], {"a": 1}])
def test_못_읽는_값은_None이지_예외가_아니다(raw):
    """문서 한 건의 형식이 어긋났다고 리포트 전체가 죽으면 안 된다."""
    assert parse_day(source(), raw) is None


def test_경계값은_문서에_저장된_형식_그대로다():
    assert format_boundary(source(), date(2026, 9, 5)) == "2026-09-05 00:00:00"
    assert format_boundary(source(date_format="%Y%m%d"), date(2026, 9, 5)) == "20260905"


def test_같은_날이면_같은_결과다():
    """결정론 — 리포트를 두 번 돌려 숫자가 다르면 아무도 안 믿는다."""
    first = build_window(WindowSpec(), today=date(2026, 9, 7))
    second = build_window(WindowSpec(), today=date(2026, 9, 7))
    assert first == second
