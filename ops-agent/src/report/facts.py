"""집계 — 리포트의 **모든 숫자**가 여기서 나온다. 순수 함수이고 I/O가 없다.

## 왜 LLM이 여기 들어오지 않는가

"어제 알람 1,204건"은 사실이고, 사실은 셀 수 있는 것이 세야 한다. LLM이 세면
같은 데이터로 두 번 돌릴 때 숫자가 달라질 수 있고, 그러면 그 리포트는 단 한 번의
오차로 신뢰를 전부 잃는다. LLM은 9e에서 **이미 나온 숫자에 대한 서술**만 붙인다.

## 왜 Facts 하나에 행을 다 들고 있는가

블록(섹션)을 추가하기 쉬워야 한다는 요구 때문이다. 섹션마다 전용 쿼리를 날리는
구조면 섹션 하나 추가할 때 대상 Mongo 왕복이 하나 늘고, 섹션들이 서로 다른
순간의 데이터를 보게 된다(하나는 08:00, 다른 하나는 08:02). 한 번 긁어서 한
덩어리로 들고 있으면 **모든 섹션이 같은 스냅샷을 본다**, 그리고 새 섹션은
`tally()` 한 줄이다.

대가는 메모리와 선형 탐색이다. 법인당 상한이 5만 행이고 섹션이 수십 개니
수백만 번의 순회인데, 하루 한 번 도는 잡에서 몇 초다 — 대상 시스템에 왕복을
더하는 것보다 우리 CPU를 쓰는 편이 낫다.

## 정렬은 항상 결정론이다

`tally`는 건수 내림차순, **동점이면 키 오름차순**으로 정렬한다. 동점을 dict
순서에 맡기면 같은 데이터로 TOP 목록의 순서가 달라지고, 읽는 사람은 "어제와
순위가 바뀌었다"고 읽는다.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Hashable, Literal, TypeVar

from src.config.schema_report import SourceSpec, Thresholds
from src.report.rows import AlarmRow, RowProblems
from src.report.window import ReportWindow, same_weekday_previous_week

K = TypeVar("K", bound=Hashable)


@dataclass(frozen=True)
class SiteOutcome:
    """법인 하나를 읽은 결과. **실패도 결과다** — 리포트 하단에 이름으로 남는다.

    읽지 못한 법인을 조용히 빼면 "전사 합계"가 말 없이 줄어든다. 관리자는 그걸
    "알람이 줄었다"로 읽는다.
    """
    gbm: str
    fct: str
    status: Literal["ok", "error", "skipped"]
    fetched: int = 0                      # 대상에서 받아 온 문서 수
    kept: int = 0                         # 집계에 쓴 행 수
    complete: bool = True                 # 표본이 잘리지 않았는가
    truncated_reason: str | None = None
    error: str | None = None
    reason: str | None = None             # skipped인 이유
    source: str = ""                      # 무엇을 물었는가
    problems: RowProblems = field(default_factory=RowProblems)

    @property
    def site(self) -> str:
        return f"{self.gbm}/{self.fct}"


@dataclass(frozen=True)
class Facts:
    window: ReportWindow
    source: SourceSpec
    thresholds: Thresholds
    rows: tuple[AlarmRow, ...]
    sites: tuple[SiteOutcome, ...]

    # ── 상태 ────────────────────────────────────────────────────────

    @property
    def complete(self) -> bool:
        """표본이 하나라도 잘렸으면 False. **이때 모든 건수는 하한일 뿐이다.**"""
        return all(s.complete for s in self.sites if s.status == "ok")

    @property
    def ok_sites(self) -> tuple[SiteOutcome, ...]:
        return tuple(s for s in self.sites if s.status == "ok")

    @property
    def unavailable(self) -> tuple[SiteOutcome, ...]:
        """읽지 못한 법인 — 방화벽·타임아웃·설정 없음. 리포트 하단에 실린다."""
        return tuple(s for s in self.sites if s.status != "ok")

    @property
    def problems(self) -> RowProblems:
        merged = RowProblems()
        for site in self.sites:
            merged = merged.merge(site.problems)
        return merged

    # ── 기본 연산 ────────────────────────────────────────────────────

    def select(self, *, day: date | None = None, days: frozenset[date] | None = None,
               gbm: str | None = None, site: str | None = None,
               unresolved: bool | None = None) -> list[AlarmRow]:
        return [r for r in self.rows
                if (day is None or r.day == day)
                and (days is None or r.day in days)
                and (gbm is None or r.gbm == gbm)
                and (site is None or f"{r.gbm}/{r.fct}" == site)
                and (unresolved is None or r.unresolved == unresolved)]

    def total(self, **filters) -> int:
        return len(self.select(**filters))

    def tally(self, key: Callable[[AlarmRow], K], *, limit: int | None = None,
              **filters) -> list[tuple[K, int]]:
        """키별 건수. 내림차순, 동점은 키 오름차순."""
        counts = Counter(key(row) for row in self.select(**filters))
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], _sortable(kv[0])))
        return ranked[:limit] if limit else ranked

    def daily(self, **filters) -> dict[date, int]:
        """집계 대상 날짜 전부를 키로 갖는다 — **0건인 날도 0으로 들어간다.**

        없는 키를 빼면 추세 그래프에서 그날이 사라지고, 읽는 사람은 "그날은
        리포트에 없네"가 아니라 "그날이 존재하지 않았네"로 본다.
        """
        counts = Counter(r.day for r in self.select(**filters))
        return {day: counts.get(day, 0) for day in self.window.days}

    # ── 비교 ─────────────────────────────────────────────────────────

    def baseline(self, day: date, **filters) -> float | None:
        """`day`를 **제외한** 집계 대상 날들의 평균. "평소 수준"이 이것이다.

        제외하는 이유: 검사 대상인 날을 기준에 넣으면 그날이 튈수록 기준도 같이
        올라가서 신호가 둔해진다(어제 3배가 터졌는데 기준이 1.3배 올라가 버린다).

        전주 동요일 **한 날**과 비교하는 것보다 이쪽이 기준으로 더 안전하다 —
        전주 그 하루가 마침 이상했으면 비교가 통째로 거짓이 된다. 전주 동요일은
        비교 값으로 **함께 보여 주되** 급증 판정의 기준으로는 쓰지 않는다.
        """
        others = [d for d in self.window.days if d != day]
        if not others:
            return None
        return sum(self.total(day=d, **filters) for d in others) / len(others)

    def compare(self, day: date, **filters) -> "Change":
        """어제 대비 / 전주 동요일 대비. 비교 대상이 조회 범위 밖이면 None."""
        previous_day = next((d for d in reversed(self.window.days) if d < day), None)
        partner = self.window.previous_of(day)
        return Change(
            value=self.total(day=day, **filters),
            previous_day=self.total(day=previous_day, **filters) if previous_day else None,
            previous_week=self.total(day=partner, **filters) if partner else None,
            previous_day_label=previous_day,
            previous_week_label=partner)


@dataclass(frozen=True)
class Change:
    value: int
    previous_day: int | None
    previous_week: int | None
    previous_day_label: date | None
    previous_week_label: date | None

    @staticmethod
    def _delta(now: int, before: int | None) -> tuple[int, float] | None:
        """(차이, 배수). 기준이 0이면 배수를 계산하지 않는다 — 0 대비 5건은
        무한 배이고, 그 숫자를 리포트에 실으면 "∞% 증가"가 찍힌다."""
        if before is None:
            return None
        return (now - before, (now / before) if before else 0.0)

    @property
    def vs_previous_day(self) -> tuple[int, float] | None:
        return self._delta(self.value, self.previous_day)

    @property
    def vs_previous_week(self) -> tuple[int, float] | None:
        return self._delta(self.value, self.previous_week)


def _sortable(key):
    """정렬 키를 문자열로 평탄화한다. 튜플 키와 단일 키가 섞여도 비교 가능해야 한다."""
    if isinstance(key, tuple):
        return tuple(str(part) for part in key)
    return (str(key),)


# ── 섹션용 유도 ─────────────────────────────────────────────────────
# 전부 Facts를 받는 **모듈 함수**다. Facts의 메서드로 넣지 않는 이유: 섹션이
# 늘어날 때마다 Facts가 비대해지고, 섹션 하나를 빼면 쓰이지 않는 메서드가 남는다.


@dataclass(frozen=True)
class Share:
    key: tuple[str, ...]
    count: int
    parent: str          # 비중의 분모가 무엇인가(예: GBM 이름)
    parent_total: int

    @property
    def ratio(self) -> float:
        return self.count / self.parent_total if self.parent_total else 0.0


def line_ranking(facts: Facts, *, day: date, limit: int | None = None) -> list[Share]:
    """라인 TOP. 비중의 분모는 **그 라인이 속한 GBM**이다(전사가 아니다).

    전사 분모로 계산하면 법인이 많은 GBM의 라인은 영원히 비중이 작게 나와서,
    "그 GBM 안에서 이 라인이 문제다"라는 신호가 사라진다.
    """
    per_gbm = Counter(r.gbm for r in facts.select(day=day))
    counts: Counter = Counter()
    for row in facts.select(day=day):
        counts[(row.gbm, row.plant, row.line_code, row.line_name)] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], _sortable(kv[0])))
    shares = [Share(key=key, count=count, parent=key[0], parent_total=per_gbm[key[0]])
              for key, count in ranked]
    return shares[:limit] if limit else shares


@dataclass(frozen=True)
class Spike:
    key: tuple[str, ...]
    count: int
    baseline: float          # 직전 평일 평균이므로 정수가 아니다
    baseline_days: int

    @property
    def ratio(self) -> float:
        """기준이 0이면 배수를 내지 않는다 — 0 대비 20건은 무한 배다."""
        return self.count / self.baseline if self.baseline else 0.0

    @property
    def brand_new(self) -> bool:
        """기준 구간에 아예 없던 것. "몇 배"가 아니라 "신규"로 표시해야 한다."""
        return self.baseline == 0


def spikes(facts: Facts, key: Callable[[AlarmRow], K], *, day: date) -> list[Spike]:
    """**직전 평일 평균 대비** 급증. 임계값은 config(`thresholds`)가 정한다.

    기준이 전주 동요일 하루가 아닌 이유는 `Facts.baseline`에 적혀 있다 — 그 하루가
    마침 이상했으면 비교가 통째로 거짓이 된다.

    건수 하한(`spike_min_count`)이 있는 이유: 1건 → 3건은 3배지만 그걸 급증이라
    부르면 리포트가 매일 급증으로 가득 차고, 사람은 그 섹션을 안 보게 된다.
    """
    others = [d for d in facts.window.days if d != day]
    if not others:
        return []
    now = Counter(key(r) for r in facts.select(day=day))
    before = Counter(key(r) for r in facts.select(days=frozenset(others)))
    found = []
    for item, count in now.items():
        mean = before.get(item, 0) / len(others)
        if count < facts.thresholds.spike_min_count:
            continue
        if count < mean * facts.thresholds.spike_ratio:
            continue
        found.append(Spike(key=_as_tuple(item), count=count, baseline=mean,
                           baseline_days=len(others)))
    return sorted(found, key=lambda s: (-s.ratio, -s.count, s.key))


@dataclass(frozen=True)
class Repeat:
    plant: str
    line_code: str
    line_name: str
    scenario_name: str
    count: int
    days: int            # 며칠에 걸쳐 있었는가


def repeats(facts: Facts, *, limit: int | None = None) -> list[Repeat]:
    """같은 (법인·라인·알람항목)이 기간 내 반복된 것.

    건수와 **발생일수**를 둘 다 넘겨야 한다. 건수만 보면 알람이 많은 큰 라인이
    항상 걸려서 이 목록이 "큰 라인 순위"가 되고, 정작 찾아야 할 만성 문제가
    묻힌다(하루에 20번 터진 것은 순간 장애, 7일 내내 매일 터진 것이 만성 문제다).
    """
    grouped: dict[tuple, list[date]] = defaultdict(list)
    for row in facts.select(days=facts.window.selected):
        grouped[(row.plant, row.line_code, row.line_name, row.scenario_name)].append(row.day)
    found = [Repeat(plant=k[0], line_code=k[1], line_name=k[2], scenario_name=k[3],
                    count=len(days), days=len(set(days)))
             for k, days in grouped.items()
             if len(days) >= facts.thresholds.repeat_min_count
             and len(set(days)) >= facts.thresholds.repeat_min_days]
    # 발생일수가 1급 정렬 키다 — "매일 나는 것"이 위로 와야 만성 문제가 보인다.
    ranked = sorted(found, key=lambda r: (-r.days, -r.count, r.plant, r.line_code))
    return ranked[:limit] if limit else ranked


@dataclass(frozen=True)
class Lifecycle:
    appeared: list[tuple[str, str]]      # 어제 처음 나타난 알람 항목
    vanished: list[tuple[str, str]]      # 그 전에는 있었는데 어제 없는 항목


def scenario_lifecycle(facts: Facts) -> Lifecycle:
    """알람 항목의 신규·소멸.

    "신규"는 **창 안에서** 처음이라는 뜻일 뿐 영구적 신규가 아니다. 과거 데이터는
    보존 기간(TTL) 밖이면 없으므로, 이 시스템이 단정할 수 있는 것은 창 안의 사실뿐이다.
    """
    yesterday = facts.window.yesterday
    earlier = frozenset(d for d in facts.window.selected if d < yesterday)
    today_set = {r.scenario for r in facts.select(day=yesterday)}
    earlier_set = {r.scenario for r in facts.select(days=earlier)} if earlier else set()
    return Lifecycle(appeared=sorted(today_set - earlier_set),
                     vanished=sorted(earlier_set - today_set))


@dataclass(frozen=True)
class Freshness:
    site: str
    last_seen: datetime | None
    yesterday_count: int
    window_count: int

    @property
    def stalled(self) -> bool:
        """창의 다른 날에는 있었는데 어제는 0건 — 데이터가 끊겼다는 신호다.

        "원래 알람이 없는 조용한 법인"과 구별하려고 창 전체 건수를 함께 본다.
        임계 시각 같은 설정을 두지 않은 이유: 알람이 드문 법인에서는 마지막
        알람이 오전 10시인 게 정상이라 그 기준이 거짓 경보만 만든다.
        """
        return self.yesterday_count == 0 and self.window_count > 0


def freshness(facts: Facts) -> list[Freshness]:
    """법인별 최신성. 어제 데이터가 끊긴 곳을 찾는다."""
    result = []
    for outcome in facts.ok_sites:
        site_rows = facts.select(site=outcome.site)
        moments = [r.occurred_at for r in site_rows]
        result.append(Freshness(
            site=outcome.site,
            last_seen=max(moments) if moments else None,
            yesterday_count=facts.total(site=outcome.site, day=facts.window.yesterday),
            window_count=len([r for r in site_rows if r.day in facts.window.selected])))
    return sorted(result, key=lambda f: f.site)


def status_breakdown(facts: Facts, *, day: date) -> list[tuple[str, int]]:
    """status 분포. 이름은 config(`status_labels`)가 정한다."""
    counts = Counter(r.status for r in facts.select(day=day))
    labelled = Counter()
    for value, count in counts.items():
        labelled[facts.source.status_label(value)] += count
    return sorted(labelled.items(), key=lambda kv: (-kv[1], kv[0]))


def _as_tuple(key) -> tuple[str, ...]:
    return tuple(str(p) for p in key) if isinstance(key, tuple) else (str(key),)


# `same_weekday_previous_week`를 여기서 다시 내보낸다 — 섹션 코드가 window를
# 따로 import하지 않게 해서, 기간 규칙이 한 곳에만 있다는 것을 분명히 한다.
__all__ = ["Facts", "SiteOutcome", "Change", "Share", "Spike", "Repeat", "Lifecycle",
           "Freshness", "line_ranking", "spikes", "repeats", "scenario_lifecycle",
           "freshness", "status_breakdown", "same_weekday_previous_week"]
