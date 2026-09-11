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

from src.report.facts import (Facts, freshness, line_ranking, repeats,
                              scenario_lifecycle, spikes, status_breakdown)
from src.report.window import WEEKDAY_LABEL

Tone = Literal["plain", "strong", "muted", "bad", "good", "warn"]
Align = Literal["left", "right"]

EMDASH = "—"          # 값이 없음(0이 아니라 "모른다")


# ── 구조 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cell:
    text: str
    align: Align = "left"
    tone: Tone = "plain"
    chip: bool = False            # 유형 배지처럼 배경을 칠할 것인가
    hint: str | None = None       # 같은 칸의 작은 회색 글씨


@dataclass(frozen=True)
class Column:
    label: str
    align: Align = "left"


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    rows: tuple[tuple[Cell, ...], ...]
    total: tuple[Cell, ...] | None = None

    def __post_init__(self):
        # 열 수가 어긋나면 표가 조용히 밀려서 **다른 열의 숫자**로 읽힌다.
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"{index}번 행의 칸이 {len(row)}개인데 열은 {width}개다")
        if self.total is not None and len(self.total) != width:
            raise ValueError(f"합계 행의 칸이 {len(self.total)}개인데 열은 {width}개다")


@dataclass(frozen=True)
class Tile:
    label: str
    value: str
    unit: str | None = None
    note: str | None = None
    hint: str | None = None       # 라벨 옆 작은 글씨
    tone: Tone = "plain"          # 값의 색


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
    table: Table | None = None
    bullets: tuple[str, ...] = ()
    banners: tuple[Banner, ...] = ()
    footnote: str | None = None
    empty: str | None = None      # 내용이 없을 때 대신 보일 문구
    required: bool = True         # False면 내용이 없을 때 통째로 사라진다

    @property
    def has_content(self) -> bool:
        return bool(self.tiles or self.table or self.bullets or self.banners)

    @property
    def visible(self) -> bool:
        return self.required or self.has_content


# ── 서식 ────────────────────────────────────────────────────────────

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
    return f"{arrow} {abs(change):.1f}%", ("bad" if change > 0 else "good")


def day_label(value: date, *, weekday: bool = True) -> str:
    text = f"{value.month:02d}/{value.day:02d}"
    return f"{text}({WEEKDAY_LABEL[value.weekday()]})" if weekday else text


def _line_label(code: str, name: str) -> str:
    return f"{code} {name}" if code != name else code


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
        names = ", ".join(s.site for s in missing)
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
                 f"({', '.join(s.site for s in truncated)}). 아래 건수는 "
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

    issue_count = (len(spikes(facts, lambda r: r.scenario, day=yesterday))
                   + len(scenario_lifecycle(facts).appeared)
                   + len(repeats(facts))
                   + len([f for f in freshness(facts) if f.stalled]))
    ok = len(facts.ok_sites)
    return Block(key="tiles", tiles=(
        Tile(label="총 알람", hint="어제", value=n(total), unit="건",
             note=f"직전 {len(facts.window.days) - 1} 평일 평균 {n(base)}건 · {change}",
             tone=tone),
        Tile(label="미해제", hint=f"status {'·'.join(str(v) for v in facts.source.unresolved_status)}",
             value=n(unresolved), unit="건",
             note=f"미해제율 {pct(unresolved, total)}",
             tone="bad" if unresolved else "plain"),
        Tile(label="최다 발생 GBM", value=str(top_gbm).upper(),
             note=f"{n(top_count)}건 · 전체의 {pct(top_count, total)}"),
        Tile(label="이슈 감지", value=n(issue_count), unit="건",
             note="급증·신규·반복·데이터 합계",
             tone="warn" if issue_count else "plain"),
        Tile(label="분석 범위", value=n(len({r.gbm for r in facts.rows})),
             unit="GBM", note=f"법인 {ok} / {len(facts.sites)}",
             tone="bad" if ok < len(facts.sites) else "plain"),
        Tile(label="조회 기간", value=n(len(facts.window.days)), unit="평일",
             note=f"{day_label(facts.window.days[0], weekday=False)} – "
                  f"{day_label(facts.window.yesterday, weekday=False)} (주말 제외)"),
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
    for gbm, count in facts.tally(lambda r: r.gbm, day=yesterday):
        unresolved = facts.total(day=yesterday, gbm=gbm, unresolved=True)
        base = facts.baseline(yesterday, gbm=gbm)
        change, tone = delta(count, base)
        partner = facts.window.previous_of(yesterday)
        week = facts.total(day=partner, gbm=gbm) if partner else None
        week_text, week_tone = delta(count, week)
        missing = [s for s in facts.unavailable if s.gbm == gbm]
        rows.append((
            Cell(str(gbm).upper(), tone="strong",
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


def _daily_trend(facts: Facts) -> Block:
    """일별 추이 — **표로도** 남긴다.

    9d에서 차트 이미지가 붙지만 이 표는 남는다. 메일 클라이언트가 이미지를 막는
    일이 흔하고, 그때 그림만 있으면 읽을 것이 없어진다.
    """
    gbms = sorted({r.gbm for r in facts.rows})
    columns = (Column("날짜"), *(Column(g.upper(), "right") for g in gbms),
               Column("합계", "right"))
    rows = []
    for day in facts.window.days:
        counts = [facts.total(day=day, gbm=g) for g in gbms]
        emphasis = "strong" if day == facts.window.yesterday else "plain"
        rows.append((Cell(day_label(day), tone=emphasis),
                     *(Cell(n(c), "right", emphasis) for c in counts),
                     Cell(n(sum(counts)), "right", "strong")))
    return Block(key="trend", title="일별 알람 추이", hint="평일만",
                 table=Table(columns=columns, rows=tuple(rows)) if gbms else None,
                 empty="기간 안에 알람이 없습니다.")


def _issues(facts: Facts) -> Block:
    """급증·신규·소멸·반복·데이터를 **한 표에** 모은다.

    종류별로 표를 쪼개면 각 표가 비어 있기 쉽고, 읽는 사람은 다섯 군데를 확인해야
    한다. "오늘 뭐가 이상한가"는 한 군데서 답이 나와야 한다.
    """
    yesterday = facts.window.yesterday
    columns = (Column("유형"), Column("대상"), Column("알람 항목"),
               Column("어제", "right"), Column("평균", "right"), Column("증감", "right"))
    rows: list[tuple[Cell, ...]] = []

    for stalled in (f for f in freshness(facts) if f.stalled):
        last = stalled.last_seen.strftime("%m/%d %H:%M") if stalled.last_seen else EMDASH
        rows.append((Cell("데이터", tone="bad", chip=True),
                     Cell(stalled.site, tone="strong"),
                     Cell(f"어제 0건 — 마지막 알람 {last}"),
                     Cell("0", "right"), Cell(n(stalled.window_count / max(
                         len(facts.window.days) - 1, 1)), "right", "muted"),
                     Cell("▼ 100.0%", "right", "bad")))

    for spike in spikes(facts, lambda r: r.scenario, day=yesterday):
        change, tone = delta(spike.count, spike.baseline)
        rows.append((Cell("급증", tone="bad", chip=True),
                     Cell(EMDASH, tone="muted"), Cell(spike.key[1]),
                     Cell(n(spike.count), "right", "strong"),
                     Cell(n(spike.baseline), "right", "muted"),
                     Cell(change, "right", tone)))

    lifecycle = scenario_lifecycle(facts)
    for scenario_id, name in lifecycle.appeared:
        appeared = len([r for r in facts.select(day=yesterday)
                        if r.scenario == (scenario_id, name)])
        rows.append((Cell("신규", tone="warn", chip=True),
                     Cell(EMDASH, tone="muted"), Cell(name),
                     Cell(n(appeared), "right", "strong"),
                     Cell(EMDASH, "right", "muted"), Cell("신규", "right", "bad")))
    for scenario_id, name in lifecycle.vanished:
        rows.append((Cell("소멸", tone="good", chip=True),
                     Cell(EMDASH, tone="muted"), Cell(name),
                     Cell("0", "right"), Cell(EMDASH, "right", "muted"),
                     Cell("소멸", "right", "good")))

    for repeat in repeats(facts, limit=facts.thresholds.top_n):
        rows.append((Cell("반복", tone="muted", chip=True),
                     Cell(f"{repeat.plant} · {_line_label(repeat.line_code, repeat.line_name)}"),
                     Cell(repeat.scenario_name,
                          hint=f"{repeat.days} 평일에 걸쳐"),
                     Cell(n(repeat.count), "right", "strong"),
                     Cell(EMDASH, "right", "muted"), Cell(EMDASH, "right", "muted")))

    t = facts.thresholds
    footnote = (f"급증 어제가 직전 평일 평균의 {t.spike_ratio}배 이상 & "
                f"{t.spike_min_count}건 이상 · 신규 집계 구간 안에서 어제 처음 · "
                f"소멸 이전에는 있었는데 어제 0건 · "
                f"반복 같은 라인·항목이 {t.repeat_min_count}건 이상 & "
                f"{t.repeat_min_days} 평일 이상 · 데이터 기간 중에는 있었는데 어제 0건 "
                f"— 임계값 전부 시나리오 config")
    return Block(key="issues", title="이슈 감지",
                 hint=f"{len(rows)}건" if rows else None,
                 table=Table(columns=columns, rows=tuple(rows)) if rows else None,
                 footnote=footnote,
                 empty="임계값을 넘은 이슈가 없습니다.")


def _plant_top(facts: Facts) -> Block:
    yesterday = facts.window.yesterday
    total = facts.total(day=yesterday)
    rows = []
    for (gbm, plant), count in facts.tally(lambda r: (r.gbm, r.plant), day=yesterday,
                                           limit=facts.thresholds.top_n):
        rows.append((Cell(str(gbm).upper(), tone="strong"), Cell(plant),
                     Cell(n(count), "right", "strong"),
                     Cell(pct(count, total), "right", "muted")))
    return Block(key="plant", title="법인 TOP",
                 hint=f"상위 {facts.thresholds.top_n}",
                 table=Table(columns=(Column("GBM"), Column("법인"),
                                      Column("어제 건수", "right"),
                                      Column("전체 비중", "right")),
                             rows=tuple(rows)) if rows else None,
                 empty="어제 알람이 없습니다.")


def _scenario_top(facts: Facts) -> Block:
    yesterday = facts.window.yesterday
    total = facts.total(day=yesterday)
    rows = []
    for (scenario_id, name), count in facts.tally(lambda r: r.scenario, day=yesterday,
                                                  limit=facts.thresholds.top_n):
        unresolved = len([r for r in facts.select(day=yesterday, unresolved=True)
                          if r.scenario == (scenario_id, name)])
        rows.append((Cell(name, tone="strong", hint=str(scenario_id)),
                     Cell(n(count), "right", "strong"),
                     Cell(pct(count, total), "right", "muted"),
                     Cell(n(unresolved), "right")))
    return Block(key="scenario", title="알람 항목 TOP",
                 hint=f"상위 {facts.thresholds.top_n}",
                 table=Table(columns=(Column("알람 항목"), Column("어제 건수", "right"),
                                      Column("전체 비중", "right"),
                                      Column("미해제", "right")),
                             rows=tuple(rows)) if rows else None,
                 empty="어제 알람이 없습니다.")


def _line_top(facts: Facts) -> Block:
    rows = []
    for share in line_ranking(facts, day=facts.window.yesterday,
                              limit=facts.thresholds.top_n):
        gbm, plant, code, name = share.key
        rows.append((Cell(str(gbm).upper(), tone="strong"), Cell(plant),
                     Cell(_line_label(code, name)),
                     Cell(n(share.count), "right", "strong"),
                     Cell(f"{share.ratio * 100:.1f}%", "right", "muted")))
    return Block(key="line", title="라인 TOP", hint="비중은 그 GBM 안에서",
                 table=Table(columns=(Column("GBM"), Column("법인"), Column("라인"),
                                      Column("어제 건수", "right"),
                                      Column("GBM 내 비중", "right")),
                             rows=tuple(rows)) if rows else None,
                 empty="어제 알람이 없습니다.")


def _status_mix(facts: Facts) -> Block:
    yesterday = facts.window.yesterday
    total = facts.total(day=yesterday)
    rows = [(Cell(label), Cell(n(count), "right", "strong"),
             Cell(pct(count, total), "right", "muted"))
            for label, count in status_breakdown(facts, day=yesterday)]
    return Block(key="status", title="처리 상태 분포", hint="어제",
                 table=Table(columns=(Column("상태"), Column("건수", "right"),
                                      Column("비중", "right")),
                             rows=tuple(rows)) if rows else None,
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
        rows.append((Cell(outcome.site, tone="strong"),
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
    _issues, _plant_top, _scenario_top, _line_top, _status_mix, _coverage_detail,
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
