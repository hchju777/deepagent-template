"""블록 — 리포트의 한 섹션. **숫자를 사람이 읽는 텍스트로 바꾸는 곳이다.**

## 왜 HTML을 모르는가

여기가 `<td>`를 찍기 시작하면 "숫자가 틀렸다"와 "표가 깨졌다"를 같은 파일에서
찾아야 한다. 블록은 텍스트와 구조만 만들고, HTML은 `presentation/report_html.py`가
만든다. 그래서 숫자 서식은 HTML 없이 단독으로 검사할 수 있다.

## 섹션 하나 추가하기

함수 하나를 쓰고 `BLOCKS`에 넣는다. 그게 전부다 —

```python
def _my_section(facts: Facts) -> Block:
    return Block(key="내_섹션", title="내 섹션",
                 table=Table(columns=(...), rows=(...)))
BLOCKS = (..., _my_section)
```

렌더러는 `BLOCKS`를 순회할 뿐이라 블록을 더하거나 빼도 나머지는 모른다.
`BLOCKS`가 **단일 진실 소스**다.

## 비어 있는 섹션은 사라지지 않는다

데이터가 0건이라고 섹션을 감추면 읽는 사람은 "그 항목은 원래 없는 리포트"라고
읽는다. `required=True`인 블록은 내용이 없어도 `empty` 문구로 남는다. 사라질 수
있는 것은 경고 배너처럼 **있을 때만 의미가 있는 것**뿐이다.

## 색에 의미를 싣지 않는다

증감은 `▲`/`▼`와 부호가 말하고 색은 보조다. 메일 클라이언트의 다크모드는 우리가
켜는 게 아니라 클라이언트가 **강제로 색을 반전**시키는 것이라(Outlook 데스크톱은
CSS로 막을 수 없다), 빨강이 초록처럼 보일 수 있다. 화살표는 반전돼도 화살표다.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Literal

from src.report.facts import (Facts, freshness, ranking_by_gbm, repeats,
                              scenario_lifecycle, spikes)
from src.report.window import WEEKDAY_LABEL

Tone = Literal["plain", "strong", "muted", "bad", "good", "warn"]
Align = Literal["left", "right"]

EMDASH = "—"          # 값이 없음(0이 아니라 "모른다")

# 줄바꿈 금지 공백(U+00A0). `&nbsp;` 엔티티가 아니라 **문자**를 쓰는 이유: 텍스트가
# `html.escape`를 지나므로 엔티티를 미리 넣으면 `&amp;nbsp;`가 된다. 문자는 그대로 통과한다.
#
# 왜 필요한가: 한국어는 단어 사이 공백이 있어도 브라우저가 **음절 단위로** 끊는다.
# 그래서 "7 평일에 걸쳐"가 "7 평" / "일에 걸쳐"로 갈라진다. CSS(`word-break:keep-all`)로도
# 되지만 Outlook의 Word 엔진이 무시할 수 있어서, 끊기면 안 되는 짧은 구절은
# **문자 수준에서** 붙여 둔다.
NBSP = "\u00a0"


# ── 구조 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cell:
    text: str
    align: Align = "left"
    tone: Tone = "plain"
    chip: bool = False            # 유형 배지처럼 배경을 칠할 것인가
    hint: str | None = None       # 같은 칸의 작은 회색 글씨
    # 계열색 번호. GBM 이름처럼 **정체성**을 나타내는 글씨에만 쓴다.
    # 색 값 자체는 렌더러가 안다 — 여기에 hex를 쓰면 블록이 HTML을 알게 된다.
    # `tone`과 겹치면 series가 이긴다(정체성이 의미보다 앞서는 칸이기 때문이다).
    series: int | None = None


@dataclass(frozen=True)
class Column:
    label: str
    align: Align = "left"
    series: int | None = None


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    rows: tuple[tuple[Cell, ...], ...]
    total: tuple[Cell, ...] | None = None
    # 이 행부터 새 묶음이 시작된다(GBM이 바뀌는 자리). 렌더러가 윗선을 굵게 그어
    # **묶음 경계를 눈에 보이게** 한다 — 그러지 않으면 GBM별 TOP 표가 한 덩어리로
    # 읽혀서 "MX 5개 + DA 5개"가 "10개 순위"로 보인다.
    group_starts: frozenset[int] = frozenset()

    def __post_init__(self):
        # 열 수가 어긋나면 표가 조용히 밀려서 **다른 열의 숫자**로 읽힌다.
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"{index}번 행의 칸이 {len(row)}개인데 열은 {width}개다")
        if self.total is not None and len(self.total) != width:
            raise ValueError(f"합계 행의 칸이 {len(self.total)}개인데 열은 {width}개다")


@dataclass(frozen=True)
class ChartBar:
    label: str            # 계열 이름(GBM)
    value: int
    series: int | None    # 계열색 번호. None이면 중립색


@dataclass(frozen=True)
class ChartColumn:
    label: str            # x축 — 날짜
    bars: tuple[ChartBar, ...]
    emphasis: bool = False


@dataclass(frozen=True)
class ChartPanel:
    """세로 막대 한 판. x축은 시간, y축은 건수.

    `scale`(y축 최댓값)을 **판마다** 갖는 이유: GBM 간 건수 차가 20배쯤 되면
    (MX 177 · NW 8) 공유 y축에서는 작은 GBM이 전부 바닥에 깔려 추세가 보이지
    않는다. 판마다 자기 최댓값을 쓰면 모든 GBM의 모양이 읽힌다.

    그 대가로 **판 사이의 높이를 비교할 수 없다.** 그래서 판마다 최댓값을 적고
    차트 전체에 경고를 붙인다 — 안 적으면 읽는 사람이 높이로 비교한다.
    """
    columns: tuple[ChartColumn, ...]
    scale: int
    title: str | None = None          # small multiples면 GBM 이름
    series: int | None = None         # 제목 색
    height: int = 92                  # 플롯 표시 높이(px). PNG는 2배로 그린다


@dataclass(frozen=True)
class Chart:
    """세로 막대 차트. **이미지가 아니라 표다.**

    왜 이미지가 아닌가: 인라인 SVG는 Outlook(Word 엔진)에서 아예 안 보이고, PNG를
    만들려면 차트 라이브러리와 **한글 폰트**가 필요하다 — 개발(Linux)에 그 폰트가
    없으면 플랫폼마다 다른 그림이 나오고 테스트가 그걸 못 잡는다. 배경색을 칠한
    `<td>`는 어디서나 칠해진다.
    """
    panels: tuple[ChartPanel, ...]
    axis: tuple[str, ...] = ()        # x축 라벨 — 판들이 공유한다
    legend: tuple[ChartBar, ...] = ()
    warning: str | None = None        # "판 사이 높이를 비교하지 말라"

    @property
    def has_panels(self) -> bool:
        return any(panel.columns for panel in self.panels)


@dataclass(frozen=True)
class Tile:
    label: str
    value: str
    unit: str | None = None
    note: str | None = None
    hint: str | None = None       # 라벨 옆 작은 글씨
    tone: Tone = "plain"          # 값의 색
    series: int | None = None


@dataclass(frozen=True)
class Banner:
    tone: Literal["bad", "warn", "good"]
    text: str


@dataclass(frozen=True)
class Block:
    key: str
    title: str | None = None      # None이면 제목 줄 없이 내용만(헤더·배너)
    hint: str | None = None       # 제목 옆 작은 글씨
    lead: str | None = None       # 제목 아래 한 줄 설명
    tiles: tuple[Tile, ...] = ()
    chart: Chart | None = None
    table: Table | None = None
    bullets: tuple[str, ...] = ()
    banners: tuple[Banner, ...] = ()
    footnote: str | None = None
    empty: str | None = None      # 내용이 없을 때 대신 보일 문구
    required: bool = True         # False면 내용이 없을 때 통째로 사라진다

    @property
    def has_content(self) -> bool:
        return bool(self.tiles or self.chart or self.table or self.bullets
                    or self.banners)

    @property
    def visible(self) -> bool:
        return self.required or self.has_content


# ── 서식 ────────────────────────────────────────────────────────────

def tight(text: str) -> str:
    """이 구절은 통째로 한 줄에 — 공백을 줄바꿈 금지 공백으로 바꾼다.

    짧고 **쪼개지면 뜻이 흐려지는** 것에만 쓴다(`▲ 29.3%`, `7 평일에 걸쳐`).
    긴 문장에 쓰면 칸을 넘쳐서 표가 밀린다.
    """
    return text.replace(" ", NBSP)


def upper(name: str) -> str:
    """법인·GBM 이름은 대문자로.

    문서의 값은 `gumi`처럼 소문자로 들어오는데 GBM은 `MX`로 쓰므로, 한 표 안에서
    두 층의 표기가 어긋난다. 한국어 법인명에는 `upper()`가 아무 영향이 없어서
    영문·한글이 섞여 있어도 안전하다.
    """
    return str(name).upper()


def n(value: int | float | None) -> str:
    """천 단위 구분. None은 `—`(0과 구별해야 한다)."""
    if value is None:
        return EMDASH
    if isinstance(value, float) and value != int(value):
        return f"{value:,.1f}"
    return f"{int(value):,}"


def pct(part: int, whole: int | None) -> str:
    if not whole:
        return EMDASH
    return f"{part / whole * 100:.1f}%"


def delta(now: int, before: int | float | None) -> tuple[str, Tone]:
    """증감을 `▲ 29.3%` 꼴로. **화살표가 방향을 말하고 색은 보조다.**

    기준이 0이면 배수가 무한이므로 "신규"라고 쓴다 — `+∞%`를 리포트에 실을 수는 없다.
    """
    if before is None:
        return EMDASH, "muted"
    if before == 0:
        return ("신규", "bad") if now else (EMDASH, "muted")
    change = (now - before) / before * 100
    if abs(change) < 0.05:
        return "0.0%", "muted"
    arrow = "▲" if change > 0 else "▼"
    # 화살표와 숫자가 줄바꿈으로 갈라지면 방향을 읽을 수 없다 — 색이 반전되는
    # 다크모드에서는 그 화살표가 유일한 신호다.
    return tight(f"{arrow} {abs(change):.1f}%"), ("bad" if change > 0 else "good")


def day_label(value: date, *, weekday: bool = True) -> str:
    text = f"{value.month:02d}/{value.day:02d}"
    return f"{text}({WEEKDAY_LABEL[value.weekday()]})" if weekday else text


def _line_label(code: str, name: str) -> str:
    return f"{code} {name}" if code != name else code


def _gbm_cell(facts: Facts, gbm: str, *, blank: bool = False, hint: str | None = None) -> Cell:
    """GBM 이름 칸. **색은 이 한 곳에서만 붙는다.**

    `blank`는 같은 묶음의 둘째 행부터다 — 같은 이름을 다섯 번 반복하면 눈이
    그걸 읽느라 정작 다른 열의 차이를 못 본다. 묶음 경계는 `group_starts`가
    윗선으로 표시한다.
    """
    if blank:
        return Cell("", series=facts.series_of(gbm))
    return Cell(upper(gbm), tone="strong", series=facts.series_of(gbm),
                hint=tight(hint) if hint else None)


def _plants_label(plants: tuple[str, ...], *, limit: int = 2) -> str:
    """법인이 여럿이면 앞 몇 개만. 전부 적으면 한 칸이 줄을 여러 개 먹는다."""
    if not plants:
        return EMDASH
    names = [upper(p) for p in plants]
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])}{NBSP}외{NBSP}{len(names) - limit}"


# ── 블록들 ──────────────────────────────────────────────────────────

def _header(facts: Facts) -> Block:
    window = facts.window
    span = f"{day_label(window.days[0])}–{day_label(window.yesterday)}"
    return Block(
        key="header",
        lead=f"기준일 {window.yesterday.isoformat()} "
             f"({WEEKDAY_LABEL[window.yesterday.weekday()]}) · "
             f"비교 구간 {span} 중 평일 {len(window.days)}일")


def _coverage(facts: Facts) -> Block:
    """읽지 못한 법인이 있을 때만 나타난다 — 있을 때만 의미가 있는 경고다.

    맨 위에 두는 이유: 아래 모든 숫자가 **일부 법인만의 합**이라는 사실을 숫자보다
    먼저 봐야 한다. 맨 아래에 적으면 그때는 이미 숫자를 다 읽은 뒤다.
    """
    banners: list[Banner] = []
    missing = facts.unavailable
    if missing:
        total = len(facts.sites)
        names = ", ".join(upper(s.site) for s in missing)
        banners.append(Banner(
            tone="bad",
            text=f"법인 {len(missing)}곳의 데이터를 읽지 못했습니다. 아래 숫자는 "
                 f"{total - len(missing)} / {total} 법인 기준입니다 — "
                 f"누락 법인({names})의 알람은 포함되지 않았습니다."))
    truncated = [s for s in facts.ok_sites if not s.complete]
    if truncated:
        banners.append(Banner(
            tone="warn",
            text=f"법인 {len(truncated)}곳에서 표본 상한에 걸렸습니다 "
                 f"({', '.join(upper(s.site) for s in truncated)}). 아래 건수는 "
                 f"실제보다 **작습니다** — 하한으로 읽어야 합니다."))
    return Block(key="coverage", banners=tuple(banners), required=False)


def _summary_tiles(facts: Facts) -> Block:
    yesterday = facts.window.yesterday
    total = facts.total(day=yesterday)
    unresolved = facts.total(day=yesterday, unresolved=True)
    base = facts.baseline(yesterday)
    change, tone = delta(total, base)

    gbm_ranking = facts.tally(lambda r: r.gbm, day=yesterday, limit=1)
    top_gbm, top_count = gbm_ranking[0] if gbm_ranking else (EMDASH, 0)

    issue_count = (len(_spike_rows(facts, yesterday))
                   + len(scenario_lifecycle(facts).appeared)
                   + len(repeats(facts))
                   + len([f for f in freshness(facts) if f.stalled]))
    ok = len(facts.ok_sites)
    return Block(key="tiles", tiles=(
        Tile(label="총 알람", hint="어제", value=n(total), unit="건",
             note=f"{tight(f'직전 {len(facts.window.days) - 1} 평일 평균')} "
                  f"{tight(n(base) + '건')} · {change}",
             tone=tone),
        Tile(label="미해제", hint=f"status {'·'.join(str(v) for v in facts.source.unresolved_status)}",
             value=n(unresolved), unit="건",
             note=tight(f"미해제율 {pct(unresolved, total)}"),
             tone="bad" if unresolved else "plain"),
        Tile(label="최다 발생 GBM", value=upper(top_gbm),
             note=f"{tight(n(top_count) + '건')} · "
                  f"{tight('전체의 ' + pct(top_count, total))}",
             series=facts.series_of(top_gbm) if gbm_ranking else None),
        Tile(label="이슈 감지", value=n(issue_count), unit="건",
             note="급증·신규·반복·데이터 합계",
             tone="warn" if issue_count else "plain"),
        Tile(label="분석 범위", value=n(len({r.gbm for r in facts.rows})),
             unit="GBM", note=f"법인 {ok} / {len(facts.sites)}",
             tone="bad" if ok < len(facts.sites) else "plain"),
        Tile(label="조회 기간", value=n(len(facts.window.days)), unit="평일",
             note=tight(f"{day_label(facts.window.days[0], weekday=False)} – "
                        f"{day_label(facts.window.yesterday, weekday=False)}")
                  + f" {tight('(주말 제외)')}"),
    ))


def _llm_comment(facts: Facts) -> Block:
    """9e에서 LLM 서술이 들어올 자리. 지금은 자리만 잡아 둔다.

    비어 있어도 섹션을 남기는 이유: 9e를 붙였을 때 "여기에 들어간다"가 이미
    정해져 있어야 렌더러를 다시 고치지 않는다.
    """
    return Block(key="comment", title="주요 이슈 분석",
                 lead="숫자는 집계 코드가 확정했고, 서술은 그 숫자만 보고 작성됩니다.",
                 empty="(LLM 코멘트는 아직 붙지 않았습니다 — 9e)")


def _gbm_summary(facts: Facts) -> Block:
    yesterday = facts.window.yesterday
    columns = (Column("GBM"), Column("어제 건수", "right"), Column("미해제", "right"),
               Column("미해제율", "right"),
               Column(f"직전 {len(facts.window.days) - 1} 평일 평균", "right"),
               Column("증감", "right"), Column("전주 동요일", "right"))
    rows = []
    # **config 순서**로 늘어놓는다. 건수 순으로 정렬하면 색과 위치가 매일 바뀌어서
    # "어제는 두 번째였는데"라는 혼동이 생긴다 — 누가 제일 많은지는 건수 열이 말한다.
    for gbm in facts.gbm_order():
        count = facts.total(day=yesterday, gbm=gbm)
        if not count and not any(s.gbm == gbm for s in facts.sites):
            continue
        unresolved = facts.total(day=yesterday, gbm=gbm, unresolved=True)
        base = facts.baseline(yesterday, gbm=gbm)
        change, tone = delta(count, base)
        partner = facts.window.previous_of(yesterday)
        week = facts.total(day=partner, gbm=gbm) if partner else None
        week_text, week_tone = delta(count, week)
        missing = [s for s in facts.unavailable if s.gbm == gbm]
        rows.append((
            _gbm_cell(facts, gbm,
                      hint=f"{len(missing)}곳 누락" if missing else None),
            Cell(n(count), "right", "strong"), Cell(n(unresolved), "right"),
            Cell(pct(unresolved, count), "right"),
            Cell(n(base), "right", "muted"),
            Cell(change, "right", tone), Cell(week_text, "right", week_tone)))

    total = facts.total(day=yesterday)
    unresolved = facts.total(day=yesterday, unresolved=True)
    base = facts.baseline(yesterday)
    change, tone = delta(total, base)
    partner = facts.window.previous_of(yesterday)
    week = facts.total(day=partner) if partner else None
    week_text, week_tone = delta(total, week)
    return Block(key="gbm", title="GBM별 요약", table=Table(
        columns=columns, rows=tuple(rows),
        total=(Cell("합계", tone="strong"), Cell(n(total), "right", "strong"),
               Cell(n(unresolved), "right", "strong"),
               Cell(pct(unresolved, total), "right", "strong"),
               Cell(n(base), "right", "muted"), Cell(change, "right", tone),
               Cell(week_text, "right", week_tone))),
        empty="어제 알람이 한 건도 없습니다.")


def _trend_chart(facts: Facts, gbms: list[str], daily: dict) -> Chart:
    """GBM마다 꺾은선 한 판. 판마다 자기 y축을 쓴다.

    왜 한 판에 GBM을 겹치지 않는가: 건수 차가 20배쯤 되면(MX 177 · NW 8) 공유
    y축에서 작은 GBM이 전부 바닥에 깔려 **추세가 사라진다.** 추세를 보는 것이
    이 차트의 목적이므로 판을 나눈다.

    전사 합계 판은 두지 않는다 — 바로 아래 표의 `합계` 열이 같은 수열을 보여 주고,
    어제 총계는 KPI 타일이 말한다. 판을 하나 더 두면 "GBM별 추세"라는 이 절의
    초점이 흐려진다.
    """
    yesterday = facts.window.yesterday
    panels = tuple(
        ChartPanel(
            title=upper(gbm),
            series=facts.series_of(gbm),
            columns=tuple(ChartColumn(label=day_label(day, weekday=False),
                                      bars=(ChartBar(label=upper(gbm),
                                                     value=counts[index],
                                                     series=facts.series_of(gbm)),),
                                      emphasis=day == yesterday)
                          for day, counts in daily.items()),
            scale=max((counts[index] for counts in daily.values()), default=0))
        for index, gbm in enumerate(gbms))

    return Chart(panels=panels,
                 axis=tuple(day_label(day, weekday=False) for day in facts.window.days),
                 warning=tight("판마다 y축이 다르다(오른쪽 최댓값 참고) — ")
                         + "줄 사이의 높이를 비교하면 안 된다. "
                         + tight("GBM 간 크기 비교는 아래 표로."))


def _daily_trend(facts: Facts) -> Block:
    """일별 추이 — 차트와 표를 **둘 다** 낸다.

    둘은 다른 질문에 답하므로 중복이 아니다: 차트는 **모양**(어느 날 솟았나,
    GBM마다 어떻게 움직였나)을, 표는 **정확한 값**을 말한다. 차트만 두면 GBM별
    숫자를 읽을 수 없고, 표만 두면 일곱 줄 숫자에서 추세를 눈으로 못 잡는다.
    """
    gbms = [g for g in facts.gbm_order() if any(r.gbm == g for r in facts.rows)]
    if not gbms:
        return Block(key="trend", title="일별 알람 추이", hint="평일만",
                     empty="기간 안에 알람이 없습니다.")

    daily = {day: [facts.total(day=day, gbm=g) for g in gbms]
             for day in facts.window.days}

    columns = (Column("날짜"),
               *(Column(upper(g), "right", series=facts.series_of(g)) for g in gbms),
               Column("합계", "right"))
    rows = tuple(
        (Cell(day_label(day),
              tone="strong" if day == facts.window.yesterday else "plain"),
         *(Cell(n(c), "right",
                "strong" if day == facts.window.yesterday else "plain")
           for c in counts),
         Cell(n(sum(counts)), "right", "strong"))
        for day, counts in daily.items())

    return Block(key="trend", title="일별 알람 추이", hint="평일만",
                 chart=_trend_chart(facts, gbms, daily),
                 table=Table(columns=columns, rows=rows),
                 empty="기간 안에 알람이 없습니다.")


def _spike_rows(facts: Facts, day):
    """급증을 **(GBM·법인·항목)** 단위로 본다.

    항목만으로 보면 "설비 신호 끊김이 늘었다"까지만 알고 어디를 봐야 하는지 모른다.
    이슈 표가 GBM·법인 열을 채울 수 있어야 그 표가 조치의 출발점이 된다.
    """
    # 항목 **id까지** 키에 넣는다. 이름만으로 묶으면 id가 다른 두 항목이 같은 이름을
    # 쓸 때 한 줄로 합쳐지고, 리포트를 받은 사람이 조회할 키가 사라진다.
    return spikes(facts, lambda r: (r.gbm, r.plant, r.scenario_id, r.scenario_name),
                  day=day)


def _grouped_table(facts: Facts, groups, *, columns, make_row) -> Table | None:
    """GBM별 묶음을 한 표로. 묶음이 바뀌는 행을 `group_starts`에 적어 둔다.

    묶음 경계를 표시하지 않으면 "MX 5개 + DA 5개"가 "10개 순위"로 읽힌다.
    """
    rows: list[tuple[Cell, ...]] = []
    starts: set[int] = set()
    for group in groups:
        for index, share in enumerate(group.items):
            if index == 0:
                starts.add(len(rows))
            rows.append(make_row(group, share, index == 0))
    if not rows:
        return None
    return Table(columns=columns, rows=tuple(rows), group_starts=frozenset(starts))


def _unresolved_in(facts: Facts, *, gbm: str, match) -> int:
    """그 GBM의 어제 미해제 중 `match`를 만족하는 건수."""
    return len([r for r in facts.select(day=facts.window.yesterday, gbm=gbm,
                                        unresolved=True) if match(r)])


def _issues(facts: Facts) -> Block:
    """급증·신규·소멸·반복·데이터를 **한 표에** 모은다.

    종류별로 표를 쪼개면 각 표가 비어 있기 쉽고, 읽는 사람은 다섯 군데를 확인해야
    한다. "오늘 뭐가 이상한가"는 한 군데서 답이 나와야 한다.

    GBM·법인을 **별개 열**로 두는 이유: 한 칸에 "gumi · P222 조립2라인"처럼 합쳐
    넣으면 GBM이 어디인지 아예 안 보이고, 눈으로 훑을 때 비교가 안 된다.
    """
    yesterday = facts.window.yesterday
    earlier_days = max(len(facts.window.days) - 1, 1)
    cap = facts.thresholds.top_n
    columns = (Column("유형"), Column("GBM"), Column("법인"), Column("대상"),
               Column("어제", "right"), Column("평균", "right"), Column("증감", "right"))
    rows: list[tuple[Cell, ...]] = []
    # 유형별로 몇 건을 생략했는가. **0이 아니면 제목 아래에 적는다** — 조용히
    # 자르면 "이슈가 이것뿐"이라는 거짓이 된다.
    omitted: dict[str, int] = {}

    for stalled in (f for f in freshness(facts) if f.stalled):
        last = stalled.last_seen.strftime("%m/%d %H:%M") if stalled.last_seen else EMDASH
        rows.append((Cell("데이터", tone="bad", chip=True),
                     _gbm_cell(facts, stalled.gbm), Cell(upper(stalled.fct)),
                     Cell(f"{tight('어제 0건')} — "
                          f"{tight('마지막 알람')} {tight(last)}"),
                     Cell("0", "right"),
                     Cell(n(stalled.window_count / earlier_days), "right", "muted"),
                     Cell("▼ 100.0%", "right", "bad")))

    found_spikes = _spike_rows(facts, yesterday)
    omitted["급증"] = max(len(found_spikes) - cap, 0)
    for spike in found_spikes[:cap]:
        gbm, plant, scenario_id, name = spike.key
        change, tone = delta(spike.count, spike.baseline)
        rows.append((Cell("급증", tone="bad", chip=True),
                     _gbm_cell(facts, gbm), Cell(upper(plant)),
                     Cell(name, hint=scenario_id),
                     Cell(n(spike.count), "right", "strong"),
                     Cell(n(spike.baseline), "right", "muted"),
                     Cell(change, "right", tone)))

    lifecycle = scenario_lifecycle(facts)
    omitted["신규"] = max(len(lifecycle.appeared) - cap, 0)
    omitted["소멸"] = max(len(lifecycle.vanished) - cap, 0)
    for item in lifecycle.appeared[:cap]:
        rows.append((Cell("신규", tone="warn", chip=True),
                     _gbm_cell(facts, item.gbm), Cell(_plants_label(item.plants)),
                     Cell(item.name, hint=item.scenario_id),
                     Cell(n(item.count), "right", "strong"),
                     Cell(EMDASH, "right", "muted"), Cell("신규", "right", "bad")))
    for item in lifecycle.vanished[:cap]:
        rows.append((Cell("소멸", tone="good", chip=True),
                     _gbm_cell(facts, item.gbm), Cell(_plants_label(item.plants)),
                     Cell(item.name, hint=item.scenario_id),
                     Cell("0", "right"),
                     Cell(n(item.count / earlier_days), "right", "muted"),
                     Cell("소멸", "right", "good")))

    found_repeats = repeats(facts)
    omitted["반복"] = max(len(found_repeats) - cap, 0)
    for repeat in found_repeats[:cap]:
        rows.append((Cell("반복", tone="muted", chip=True),
                     _gbm_cell(facts, repeat.gbm), Cell(upper(repeat.plant)),
                     Cell(f"{_line_label(repeat.line_code, repeat.line_name)} · "
                          f"{repeat.scenario_name}",
                          # 항목 id를 먼저 — 모든 행의 hint가 같은 모양으로 시작해야
                          # 눈이 그 자리를 학습한다.
                          hint=f"{repeat.scenario_id} · "
                               f"{tight(f'{repeat.days} 평일에 걸쳐')}"),
                     Cell(n(repeat.count), "right", "strong"),
                     Cell(EMDASH, "right", "muted"), Cell(EMDASH, "right", "muted")))

    hidden = {kind: count for kind, count in omitted.items() if count}
    lead = None
    if hidden:
        detail = " · ".join(f"{kind} {count}건" for kind, count in hidden.items())
        lead = (f"유형별 상위 {cap}건만 보입니다 — {detail}을 생략했습니다. "
                f"전체 목록은 report aggregate의 팩트시트에 있습니다.")

    t = facts.thresholds
    footnote = (f"급증 어제가 직전 평일 평균의 {t.spike_ratio}배 이상 & "
                f"{t.spike_min_count}건 이상(GBM·법인·항목 단위) · "
                f"신규 집계 구간 안에서 그 GBM에 어제 처음 · "
                f"소멸 이전에는 있었는데 어제 0건 · "
                f"반복 같은 라인·항목이 {t.repeat_min_count}건 이상 & "
                f"{t.repeat_min_days} 평일 이상 · 데이터 기간 중에는 있었는데 어제 0건 "
                f"— 임계값 전부 시나리오 config")
    total_found = len(rows) + sum(hidden.values())
    return Block(key="issues", title="이슈 감지",
                 hint=f"{total_found}건" if rows else None,
                 lead=lead,
                 table=Table(columns=columns, rows=tuple(rows)) if rows else None,
                 footnote=footnote,
                 empty="임계값을 넘은 이슈가 없습니다.")


def _plant_top(facts: Facts) -> Block:
    """법인 TOP — **GBM별로** 상위 N개."""
    groups = ranking_by_gbm(facts, lambda r: r.plant, day=facts.window.yesterday,
                            limit=facts.thresholds.top_per_gbm)

    def row(group, share, first):
        plant = share.key[0]
        return (_gbm_cell(facts, group.gbm, blank=not first),
                Cell(upper(plant)),
                Cell(n(share.count), "right", "strong"),
                Cell(f"{share.ratio * 100:.1f}%", "right", "muted"),
                Cell(n(_unresolved_in(facts, gbm=group.gbm,
                                      match=lambda r: r.plant == plant)), "right"))

    return Block(key="plant", title="법인 TOP",
                 hint=f"GBM별 상위 {facts.thresholds.top_per_gbm}",
                 table=_grouped_table(facts, groups, columns=(
                     Column("GBM"), Column("법인"), Column("어제 건수", "right"),
                     Column("GBM 내 비중", "right"), Column("미해제", "right")),
                     make_row=row),
                 empty="어제 알람이 없습니다.")


def _scenario_top(facts: Facts) -> Block:
    """알람 항목 TOP — **GBM별로** 상위 N개.

    전사 하나로 줄을 세우면 알람이 많은 GBM의 항목이 목록을 차지하고, 다른 GBM에서
    무엇이 문제인지는 영원히 안 보인다.
    """
    groups = ranking_by_gbm(facts, lambda r: r.scenario, day=facts.window.yesterday,
                            limit=facts.thresholds.top_per_gbm)

    def row(group, share, first):
        scenario = (share.key[0], share.key[1])
        return (_gbm_cell(facts, group.gbm, blank=not first),
                Cell(share.key[1], hint=share.key[0]),
                Cell(n(share.count), "right", "strong"),
                Cell(f"{share.ratio * 100:.1f}%", "right", "muted"),
                Cell(n(_unresolved_in(facts, gbm=group.gbm,
                                      match=lambda r: r.scenario == scenario)), "right"))

    return Block(key="scenario", title="알람 항목 TOP",
                 hint=f"GBM별 상위 {facts.thresholds.top_per_gbm}",
                 table=_grouped_table(facts, groups, columns=(
                     Column("GBM"), Column("알람 항목"), Column("어제 건수", "right"),
                     Column("GBM 내 비중", "right"), Column("미해제", "right")),
                     make_row=row),
                 empty="어제 알람이 없습니다.")


def _line_top(facts: Facts) -> Block:
    """라인 TOP — **GBM별로** 상위 N개.

    전사 TOP N이었을 때 상위 10칸이 전부 한 GBM으로 채워져 다른 GBM의 법인은 한
    줄도 들어오지 못했다. 그게 이 표를 GBM별로 바꾼 이유다.
    """
    groups = ranking_by_gbm(facts, lambda r: (r.plant, r.line_code, r.line_name),
                            day=facts.window.yesterday,
                            limit=facts.thresholds.top_per_gbm)
    return Block(key="line", title="라인 TOP",
                 hint=f"GBM별 상위 {facts.thresholds.top_per_gbm} · 비중은 그 GBM 안에서",
                 table=_grouped_table(facts, groups, columns=(
                     Column("GBM"), Column("법인"), Column("라인"),
                     Column("어제 건수", "right"), Column("GBM 내 비중", "right")),
                     make_row=lambda group, share, first: (
                         _gbm_cell(facts, group.gbm, blank=not first),
                         Cell(upper(share.key[0])),
                         Cell(_line_label(share.key[1], share.key[2])),
                         Cell(n(share.count), "right", "strong"),
                         Cell(f"{share.ratio * 100:.1f}%", "right", "muted"))),
                 empty="어제 알람이 없습니다.")


def _coverage_detail(facts: Facts) -> Block:
    """맨 아래 — 무엇을 읽었고 무엇을 못 읽었는가.

    `required=True`인 이유: 전부 정상이어도 "12 / 12 법인을 읽었다"가 찍혀야
    그 리포트가 완전하다는 근거가 된다. 이상할 때만 나타나는 표는, 없을 때
    "괜찮다"인지 "확인을 안 했다"인지 구별해 주지 않는다.
    """
    rows = []
    for outcome in sorted(facts.sites, key=lambda s: s.site):
        if outcome.status == "ok":
            note = "정상" if outcome.complete else f"표본 잘림 — {outcome.truncated_reason}"
            tone: Tone = "plain" if outcome.complete else "warn"
        else:
            note = outcome.error or outcome.reason or "원인 불명"
            tone = "bad"
        rows.append((Cell(upper(outcome.site), tone="strong"),
                     Cell({"ok": "읽음", "error": "실패", "skipped": "제외"}[outcome.status],
                          tone=tone, chip=outcome.status != "ok"),
                     Cell(n(outcome.fetched) if outcome.status == "ok" else EMDASH, "right"),
                     Cell(n(outcome.kept) if outcome.status == "ok" else EMDASH, "right"),
                     Cell(note, tone="muted")))
    quality = facts.problems.describe()
    return Block(key="sites", title="조회 범위", hint=f"{len(facts.ok_sites)} / {len(facts.sites)} 법인",
                 table=Table(columns=(Column("법인"), Column("상태"),
                                      Column("받은 문서", "right"),
                                      Column("집계에 쓴 행", "right"), Column("비고")),
                             rows=tuple(rows)) if rows else None,
                 bullets=tuple(quality),
                 footnote="데이터 품질 항목이 있으면 그만큼의 문서가 집계에서 빠졌습니다.",
                 empty="대상 법인이 없습니다.")


# **단일 진실 소스.** 순서가 곧 리포트의 순서다.
BLOCKS: tuple[Callable[[Facts], Block], ...] = (
    _header, _coverage, _summary_tiles, _llm_comment, _gbm_summary, _daily_trend,
    _issues, _plant_top, _scenario_top, _line_top, _coverage_detail,
)


def build_blocks(facts: Facts) -> list[Block]:
    """전부 만들어서 보일 것만 돌려준다. 키가 겹치면 바로 실패한다 —
    겹친 키는 렌더러가 둘 중 하나만 그리거나 두 번 그리게 만든다."""
    blocks = [builder(facts) for builder in BLOCKS]
    keys = [b.key for b in blocks]
    duplicated = {k for k in keys if keys.count(k) > 1}
    if duplicated:
        raise ValueError(f"블록 키가 겹친다 — {', '.join(sorted(duplicated))}")
    return [b for b in blocks if b.visible]
