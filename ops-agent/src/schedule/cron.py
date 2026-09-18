"""cron 식을 읽고 **다음 발사 시각**을 계산한다. 순수 함수뿐이다.

## 왜 라이브러리를 안 쓰는가

`croniter`가 표준이지만 사내 PyPI에 없을 수 있다(7단계에서 langchain으로 이미
겪었고, 9d에서 matplotlib 때문에 PNG 인코더를 직접 썼다). 리포트 하나 때문에 배포가
막히면 안 된다. 그리고 여기서 필요한 문법은 좁다 — 아래 표가 전부다.

대신 **직접 쓴 만큼 테스트가 촘촘해야 한다.** 이 계산이 틀리면 증상은 "리포트가 안
온다"이고, 그때는 이미 며칠 지난 뒤다.

## 읽는 문법

| | 뜻 |
|---|---|
| `*` | 전부 |
| `5` | 그 값 |
| `1-5` | 범위(양끝 포함) |
| `*/15` | 0부터 15씩 |
| `1-5/2` | 범위 안에서 2씩 |
| `0,30` | 목록 |

다섯 칸: `분 시 일 월 요일`. 요일은 0~6(0=일요일)이고 7도 일요일로 받는다 —
`0 8 * * 1-5`가 월~금이다.

## 일(日)과 요일이 **둘 다** 제한되면 OR다

cron의 유명한 비직관이고, 우리가 지어낸 규칙이 아니라 **원래 cron이 그렇다**.
`0 8 13 * 5`는 "13일 **또는** 금요일"이지 "13일의 금요일"이 아니다. 한쪽만
제한되면 그쪽만 본다.

여기서 이것을 흉내내는 이유: 사람이 cron 식을 다른 데서 복사해 오기 때문이다.
우리만 AND로 해석하면 같은 글자가 다른 날에 도는데, 그 차이는 **안 도는 날에만**
드러난다.

## 시간대

`after`가 들고 온 시간대를 그대로 쓴다. "매일 8시"는 그 시간대의 8시다 —
기계의 시스템 TZ가 아니라 `app.json`의 `timezone`이 정한다(`_clock` 참고).
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# 4년. 2월 29일만 도는 식(`0 0 29 2 *`)도 반드시 한 번은 걸린다.
_MAX_DAYS = 4 * 366

_FIELD = re.compile(r"^(?:\*|\d+)(?:-\d+)?(?:/\d+)?$")


class CronError(ValueError):
    """cron **식**이 잘못됐다. 데이터 이상이 아니라 설정 오류다.

    `KnownRuleError`와 같은 성격이라 예외가 맞다 — finding으로 삼키면 설정 실수가
    매일 "안 돌았다"로 둔갑하고, 그 증상으로는 원인을 못 찾는다.
    """


@dataclass(frozen=True)
class Cron:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: tuple[int, ...]
    months: tuple[int, ...]
    weekdays: tuple[int, ...]        # 0=일요일
    day_restricted: bool             # 일(日) 칸이 `*`가 아닌가
    weekday_restricted: bool


_RANGES = {"minute": (0, 59), "hour": (0, 23), "day": (1, 31),
           "month": (1, 12), "weekday": (0, 7)}


def _values(field: str, name: str) -> tuple[int, ...]:
    low, high = _RANGES[name]
    found: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part or not _FIELD.match(part):
            raise CronError(f"{name} 칸을 읽을 수 없다 — {part!r}")
        body, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if step < 1:
            raise CronError(f"{name} 칸의 간격이 0 이하다 — {part!r}")
        if body == "*":
            start, end = low, high
        elif "-" in body:
            start, end = (int(v) for v in body.split("-", 1))
        else:
            start = int(body)
            # `5/10`은 "5부터 10씩"이다(cron의 관례). `5`만 있으면 그 값 하나.
            end = high if step_text else start
        if not (low <= start <= high and low <= end <= high):
            raise CronError(f"{name} 칸이 범위({low}~{high})를 벗어났다 — {part!r}")
        if start > end:
            raise CronError(f"{name} 칸의 범위가 거꾸로다 — {part!r}")
        found.update(range(start, end + 1, step))
    return tuple(sorted(found))


def parse_cron(expression: str) -> Cron:
    """읽을 수 없으면 `CronError`. **기동에서 죽이려고** 있는 함수다."""
    fields = expression.split()
    if len(fields) != 5:
        raise CronError(f"cron은 다섯 칸이다(분 시 일 월 요일) — "
                        f"{len(fields)}칸을 받았다: {expression!r}")
    minute, hour, day, month, weekday = fields
    # 7을 0(일요일)으로 접는다. 두 표기가 다 쓰이고, 사람은 복사해 온다.
    weekdays = tuple(sorted({0 if v == 7 else v
                             for v in _values(weekday, "weekday")}))
    return Cron(minutes=_values(minute, "minute"), hours=_values(hour, "hour"),
                days=_values(day, "day"), months=_values(month, "month"),
                weekdays=weekdays,
                day_restricted=day.strip() != "*",
                weekday_restricted=weekday.strip() != "*")


def _day_matches(cron: Cron, day: date) -> bool:
    if day.month not in cron.months:
        return False
    # Python의 weekday()는 월=0, cron은 일=0.
    dow = (day.weekday() + 1) % 7
    in_day, in_dow = day.day in cron.days, dow in cron.weekdays
    if cron.day_restricted and cron.weekday_restricted:
        return in_day or in_dow            # 위 독스트링의 OR 규칙
    if cron.day_restricted:
        return in_day
    if cron.weekday_restricted:
        return in_dow
    return True


def next_fire(expression: str, after: datetime) -> datetime:
    """`after`**보다 뒤**의 첫 발사 시각. 같은 순간은 돌려주지 않는다.

    같은 순간을 돌려주면 발사 직후 다시 계산할 때 같은 시각이 나와서 **무한히
    즉시 발사**한다. 실제로 그렇게 만들기 쉬운 자리다.
    """
    cron = parse_cron(expression)
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()
    for _ in range(_MAX_DAYS):
        if _day_matches(cron, day):
            for hour in cron.hours:
                if day == start.date() and hour < start.hour:
                    continue
                for minute in cron.minutes:
                    if (day == start.date() and hour == start.hour
                            and minute < start.minute):
                        continue
                    return datetime.combine(day, time(hour, minute),
                                            tzinfo=after.tzinfo)
        day += timedelta(days=1)
    # 여기 오는 식은 영원히 안 도는 식이다(예: `0 0 30 2 *` — 2월 30일).
    raise CronError(f"{_MAX_DAYS}일 안에 도는 날이 없다 — {expression!r}")
