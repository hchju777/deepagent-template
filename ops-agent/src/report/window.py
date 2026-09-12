"""리포트가 볼 기간 — "어제부터 과거 N 평일"이 **정확히 어느 날짜들**인가.

## 왜 이게 집계 코드에서 분리돼 있는가

기간 계산은 집계보다 **조용히 틀리기 쉽다.** 월요일 아침에 도는 잡이 "어제"를
일요일로 잡으면 그날 리포트의 모든 숫자가 0이 되는데, 코드는 아무 오류도 내지
않는다. 분리해 두면 이 계산만 시계를 고정해서 단독으로 검사할 수 있고,
`report window` 명령으로 **사람이 눈으로** 날짜 목록을 볼 수 있다.

## occ_date가 문자열이라는 것의 무게

대상 컬렉션의 날짜 필드는 `datetime`이 아니라 `"2026-08-21 00:00:00"` 같은
**문자열**이다. Mongo의 `$gte`/`$lt`는 문자열에 대해 사전순(바이트 순)으로
비교하므로, 형식이 "고정폭 + 큰 자리 우선"이 아니면 범위 쿼리가 **조용히 다른
구간**을 읽는다. `%d/%m/%Y`라면 `"31/12/1999" < "01/01/2000"`이 거짓이라
연말 데이터가 통째로 빠진다. 그래서 `date_format_problem`이 기동에서 형식을
거부한다 — 런타임에 "숫자가 좀 이상한데" 형태로 발견되면 아무도 원인을 못 찾는다.

## 주말은 쿼리로 뺄 수 없다

문자열 날짜에서 요일을 뽑는 서버 측 연산이 없다(`$dayOfWeek`는 date 타입에만
동작한다). 그리고 **뺄 수 있어도 빼지 않는다** — 대상 시스템에 계산을 미루는
만큼 그쪽 부하가 된다. 범위 하나로 긁어 오고 요일 거르기는 우리 쪽에서 한다.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from src.config.schema_report import SourceSpec, WindowSpec

_DAY = timedelta(days=1)
_WEEK = timedelta(days=7)

# 요일 이름은 사람이 읽는 출력에만 쓴다(locale에 따라 달라지는 %a를 피한다).
WEEKDAY_LABEL = ("월", "화", "수", "목", "금", "토", "일")

# 형식 검사용 표본. 아무 날짜나 쓰면 안 된다 — 아래 성질들을 각각 깨뜨리도록
# 고른 것이다:
#   · 세기 경계(1999→2000): `%y`(두 자리 연도)는 "99" > "00"이라 사전순이 뒤집힌다
#   · 한 자리/두 자리 월·일 혼재: 고정폭이 아닌 형식(`%-m`)을 드러낸다
#   · 월·일이 서로 큰 쪽/작은 쪽으로 엇갈리는 조합: 일이 연보다 앞서는
#     형식(`%d/%m/%Y`)을 드러낸다
# 표본은 전부 **서로 다른 날**이다 — 그래야 "날짜 성분이 빠진 형식"(`%Y-%m`)이
# 같은 문자열로 뭉개지면서 걸린다.
_SAMPLES = (
    datetime(1999, 12, 31, 23, 59, 59),
    datetime(2000, 1, 1, 0, 0, 0),
    datetime(2025, 1, 2, 3, 4, 5),
    datetime(2025, 1, 10, 0, 0, 0),
    datetime(2025, 2, 1, 12, 0, 0),
    datetime(2025, 9, 9, 9, 9, 9),
    datetime(2025, 10, 1, 0, 0, 0),
    datetime(2026, 8, 21, 0, 0, 0),
)


def date_format_problem(fmt: str) -> str | None:
    """이 형식으로 문자열 범위 비교를 해도 되는가. 문제가 없으면 None.

    무raise: 형식이 이 플랫폼에서 아예 못 쓰는 것이어도 메시지로 돌려준다
    (`%-d`는 Windows에서 `ValueError`다 — 리눅스에서 짜고 사내 Windows에서
    죽는 전형적인 형태라 여기서 잡아 준다).
    """
    rendered: list[str] = []
    for sample in _SAMPLES:
        try:
            text = sample.strftime(fmt)
        except (ValueError, TypeError) as exc:            # noqa: PERF203
            return f"이 플랫폼에서 쓸 수 없는 날짜 형식이다 — {fmt!r}: {exc}"
        rendered.append(text)

    for sample, text in zip(_SAMPLES, rendered):
        try:
            back = datetime.strptime(text, fmt)
        except ValueError as exc:
            return (f"{fmt!r}로 찍은 {text!r}를 같은 형식으로 되읽을 수 없다: {exc}. "
                    f"되읽기가 안 되면 문서의 날짜를 날짜별로 나눌 수 없다")
        if back.date() != sample.date():
            return (f"{fmt!r}는 왕복이 안 된다 — {sample.date()}를 {text!r}로 찍었는데 "
                    f"되읽으면 {back.date()}가 된다")

    widths = {len(text) for text in rendered}
    if len(widths) != 1:
        return (f"{fmt!r}는 길이가 고정이 아니다(관측된 길이 {sorted(widths)}). "
                f"길이가 다르면 사전순 비교가 시간순과 어긋난다 — "
                f"자리수를 채우는 형식을 써라(%m, %d)")

    if rendered != sorted(rendered):
        first = next(i for i in range(1, len(rendered)) if rendered[i] < rendered[i - 1])
        return (f"{fmt!r}는 사전순이 시간순과 다르다 — "
                f"{_SAMPLES[first - 1].date()}는 {rendered[first - 1]!r}, "
                f"{_SAMPLES[first].date()}는 {rendered[first]!r}로 찍혀 "
                f"나중 날짜가 사전순으로 앞선다. 날짜 필드가 문자열이라 "
                f"범위 쿼리가 통째로 틀린 구간을 읽는다. 큰 자리부터 쓰는 "
                f"형식(%Y-%m-%d ...)을 써라")
    return None


def previous_business_day(day: date, *, excluded: frozenset[int] | set[int]) -> date:
    """`day` **직전**의 평일. `day` 자신은 평일이어도 포함하지 않는다."""
    cursor = day - _DAY
    for _ in range(7):
        if cursor.weekday() not in excluded:
            return cursor
        cursor -= _DAY
    # config로는 도달할 수 없다(`WindowSpec`이 7요일 전부 제외를 막는다).
    # 직접 호출하는 코드가 무한 루프에 빠지지 않도록 남겨 둔 계약 위반 신호다.
    raise ValueError("모든 요일이 제외돼 있어 직전 평일이 존재하지 않는다")


def business_days_back(anchor: date, count: int,
                       *, excluded: frozenset[int] | set[int]) -> tuple[date, ...]:
    """`anchor` 직전 평일부터 과거로 `count`개. **오름차순**으로 돌려준다.

    `anchor`는 보통 "오늘"이다. 그래서 마지막 원소가 "어제"(직전 평일)이고,
    월요일에 돌면 그것이 일요일이 아니라 **금요일**이다.
    """
    days: list[date] = []
    cursor = anchor
    for _ in range(count):
        cursor = previous_business_day(cursor, excluded=excluded)
        days.append(cursor)
    return tuple(reversed(days))


def same_weekday_previous_week(day: date) -> date:
    """전주 동요일. 평일 세기가 아니라 **정확히 7일 전**이다.

    평일로 세면 공휴일이 낀 주에 요일이 어긋나 "화요일 대 월요일"을 비교하게
    된다. 비교의 의미가 "같은 요일의 평소 수준"이므로 요일이 우선이다.
    """
    return day - _WEEK


@dataclass(frozen=True)
class ReportWindow:
    anchor: date                        # 리포트를 낸 날(보통 오늘)
    days: tuple[date, ...]              # 오름차순. `days[-1]`이 "어제"(직전 평일)
    previous_week: tuple[date, ...]     # `days`와 같은 길이·순서. 비교를 끄면 빈 튜플
    query_from: date                    # 조회 하한(포함)
    query_to: date                      # 조회 상한(**제외**)

    @property
    def yesterday(self) -> date:
        return self.days[-1]

    @property
    def selected(self) -> frozenset[date]:
        """집계에 실제로 쓸 날들. 범위로 긁어 온 문서를 이걸로 거른다."""
        return frozenset(self.days)

    @property
    def wanted(self) -> frozenset[date]:
        """버리지 않을 날들 — 집계 대상 **더하기** 전주 비교용.

        `selected`만으로 거르면 전주 비교값이 통째로 버려진다. 둘은 다른
        질문에 답한다: `selected`는 "리포트에 그릴 날", `wanted`는 "들고 있을 날".
        """
        return frozenset(self.days) | frozenset(self.previous_week)

    def covers(self, day: date) -> bool:
        return self.query_from <= day < self.query_to

    def previous_of(self, day: date) -> date | None:
        """`day`의 전주 동요일. 비교가 꺼져 있거나 조회 범위 밖이면 None."""
        if not self.previous_week:
            return None
        partner = same_weekday_previous_week(day)
        return partner if self.covers(partner) else None


def build_window(spec: WindowSpec, *, today: date) -> ReportWindow:
    """`today`에 도는 리포트가 볼 기간.

    상한이 `today`가 아니라 **어제까지**인 이유: 아침 8시에 도는 잡이 오늘을
    포함하면 "오늘"만 8시간짜리 반쪽 막대가 돼서, 전날 대비 그래프가 매일
    급감한 것처럼 보인다.

    하한은 `days[0]`이 아니라 **그 전주 동요일**까지 내려간다. 전주 비교값을
    별도 쿼리로 또 나가면 대상 Mongo에 왕복이 두 배가 된다 — 어차피 한 번
    긁는 김에 범위만 늘린다. 보존 기간(TTL) 밖이라 그 구간이 비어 있는 것은
    정상이고, 그 경우 비교 칸만 비운다.
    """
    excluded = spec.excluded()
    days = business_days_back(today, spec.business_days, excluded=excluded)
    previous = (tuple(same_weekday_previous_week(d) for d in days)
                if spec.compare_previous_week else ())
    return ReportWindow(
        anchor=today,
        days=days,
        previous_week=previous,
        query_from=min(days + previous),
        query_to=days[-1] + _DAY,
    )


def format_boundary(source: SourceSpec, day: date) -> str:
    """`day` 00:00:00을 경계 형식(`date_format`)으로 찍는다. `parse_formats`는 쓰지 않는다.

    ## 경계가 하루 단위라서 안전한 것

    사내 실데이터의 `occ_date`에는 소수점 초가 붙어 있고 자릿수도 섞여 있다
    (`.000`과 `.545776`). 한 건은 분이 한 자리(`00:0:00`)였다. 경계값에는 소수점이
    없는데도 비교가 맞는 이유는 **`YYYY-MM-DD`가 고정폭**이라서다 — 날짜가 다르면
    10번째 글자 안에서 승부가 나고, 날짜가 같으면 경계(`... 00:00:00`)가 그날의
    어떤 시각보다 작거나 같다.

    ## 그래서 하루보다 짧은 경계로 바꾸면 깨진다

    "최근 24시간"처럼 **시각을 경계로** 자르게 되면 `00:0:00`(한 자리 분)이
    `00:00:00`보다 사전순으로 **크다**(`:`(0x3A) > `0`(0x30)). 그 순간 경계가 조용히
    틀린다. 하루 경계를 벗어나려면 그 전에 저장 형식을 고정폭으로 정규화해야 한다.
    """
    return datetime.combine(day, time.min).strftime(source.date_format)


def date_filter(source: SourceSpec, window: ReportWindow) -> dict:
    """대상 Mongo에 그대로 나가는 필터 문서.

    `$lt`인 이유: `$lte`로 상한 날짜의 00:00:00을 쓰면 그날 00시 정각 1건만
    들어오고 나머지가 빠진다. 상한을 **다음 날 00:00:00 미만**으로 잡으면
    형식에 시각이 있든 없든(`%Y-%m-%d`처럼 날짜만이어도 접두사 비교라)
    경계가 정확히 맞는다.
    """
    return {source.date_field: {"$gte": format_boundary(source, window.query_from),
                                "$lt": format_boundary(source, window.query_to)}}


def parse_moment(source: SourceSpec, raw) -> datetime | None:
    """문서의 날짜 필드 값을 시각으로. 못 읽으면 None — **던지지 않는다.**

    문서 한 건의 형식이 어긋났다고 리포트 전체가 죽으면 안 된다. 못 읽은 건수는
    호출부가 세어서 데이터 품질 이슈로 올린다.

    날짜가 아니라 **시각**까지 보존하는 이유: "어제 마지막 알람이 몇 시인가"가
    데이터가 끊겼는지 보는 신호다. 날짜로 잘라 버리면 그 신호가 사라진다.
    """
    if isinstance(raw, datetime):       # 나중에 date 타입으로 바뀌어도 그대로 돈다
        return raw
    if isinstance(raw, date):
        return datetime.combine(raw, time.min)
    if not isinstance(raw, str):
        return None
    for fmt in source.formats():
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


def parse_day(source: SourceSpec, raw) -> date | None:
    moment = parse_moment(source, raw)
    return moment.date() if moment is not None else None


def describe(window: ReportWindow, source: SourceSpec) -> dict:
    """사람이 눈으로 검토할 수 있는 형태. `report window` 명령이 이걸 찍는다."""
    def label(day: date) -> str:
        return f"{day.isoformat()}({WEEKDAY_LABEL[day.weekday()]})"

    return {
        "기준일(오늘)": label(window.anchor),
        "어제(직전 평일)": label(window.yesterday),
        "집계 대상": [label(d) for d in window.days],
        "전주 동요일": [label(d) for d in window.previous_week],
        "조회 범위": {
            "하한(포함)": format_boundary(source, window.query_from),
            "상한(제외)": format_boundary(source, window.query_to),
            "달력일수": (window.query_to - window.query_from).days,
        },
        "몽고 필터": date_filter(source, window),
        "형식 문제": date_format_problem(source.date_format),
    }
