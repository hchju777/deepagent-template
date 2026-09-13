"""LLM 서술 — **코드가 센 숫자에 말을 붙인다.** 숫자를 만들지는 않는다.

## 화이트리스트가 프롬프트와 같은 곳에서 나오는 이유

"사실에 없는 숫자를 거부한다"를 구현하려면 무엇이 사실인지 정해야 한다. 허용 목록을
따로 만들면 프롬프트에 준 숫자와 어긋나서 **우리가 준 숫자를 인용했는데 거부되는**
일이 생긴다.

그래서 `facts_block`이 만든 문자열에서 숫자를 긁어 허용 목록을 만든다. 출처가
한 곳이니 어긋날 수 없고, 비율·배수·차이도 블록에 이미 계산해 넣었으므로 자동으로
허용된다. **LLM이 직접 계산할 일이 없다** — 계산은 그쪽이 틀리는 지점이다.

## 반올림은 허용한다

우리가 82.47%를 줬는데 LLM이 82.5%라고 쓰는 것은 환각이 아니다. 절댓값 0.5 또는
0.5% 안쪽이면 같은 값으로 본다. 그 관용 없이는 자연스러운 문장이 전부 거부된다.

## 무raise

LLM이 죽어도, 답이 거부돼도 리포트는 나간다. 실패는 `GbmComment.status`로 흡수하고
본문의 그 자리에 이유를 적는다 — 코멘트 하나 때문에 아침 리포트가 안 나가면 그건
훨씬 큰 손실이다.
"""
import re
from dataclasses import dataclass
from typing import Literal

from src.config.schema_report import CommentSpec
from src.domain.base import Clock
from src.domain.llm import LlmPort
from src.report.blocks import EMDASH, delta, n, pct, upper
from src.report.facts import (Facts, freshness, ranking_by_gbm, repeats,
                              scenario_lifecycle, spikes)
from src.report.window import WEEKDAY_LABEL

# 사실 블록에서 숫자를 긁는 패턴. 천 단위 쉼표와 소수점을 함께 받는다.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 답에 들어오면 안 되는 것들. 링크와 태그는 렌더러가 이스케이프하지만, 애초에
# 들어오면 프롬프트를 무시했다는 신호이므로 그 코멘트 전체를 믿지 않는다.
_FORBIDDEN = (("<", "HTML 태그"), ("http://", "링크"), ("https://", "링크"),
              ("```", "코드 블록"))


@dataclass(frozen=True)
class GbmComment:
    gbm: str
    status: Literal["ok", "rejected", "error", "skipped"]
    text: str = ""
    reason: str | None = None
    model: str | None = None

    @property
    def failed(self) -> bool:
        return self.status != "ok"


def _numbers(text: str) -> list[float]:
    found = []
    for token in _NUMBER.findall(text):
        try:
            found.append(float(token.replace(",", "")))
        except ValueError:                                  # noqa: PERF203
            continue
    return found


def allowed_numbers(block: str) -> set[float]:
    """사실 블록에 실제로 적힌 숫자들. **이것이 허용 목록의 전부다.**"""
    return set(_numbers(block))


def _known(value: float, allowed: set[float]) -> bool:
    return any(abs(value - candidate) <= max(0.5, abs(candidate) * 0.005)
               for candidate in allowed)


def problems(text: str, *, allowed: set[float], max_chars: int) -> list[str]:
    """답을 받아들일 수 없는 이유들. 빈 목록이면 받아들인다."""
    stripped = text.strip()
    if not stripped:
        return ["빈 응답"]
    if len(stripped) > max_chars:
        # 자르지 않는다 — 문장이 중간에 끊기면 "무슨 말인지 모를 코멘트"가 실린다.
        return [f"{len(stripped)}자로 상한({max_chars}자)을 넘었다"]

    found = []
    for marker, what in _FORBIDDEN:
        if marker in stripped:
            found.append(f"{what}가 들어 있다")
    unknown = sorted({value for value in _numbers(stripped)
                      if not _known(value, allowed)})
    if unknown:
        found.append("사실에 없는 숫자 — "
                     + ", ".join(f"{v:g}" for v in unknown[:5]))
    return found


def facts_block(facts: Facts, gbm: str) -> str:
    """LLM에게 줄 사실. **이 함수가 허용 목록도 정한다**(위 독스트링 참고).

    비율·배수·차이를 미리 계산해 넣는 이유: 넣지 않으면 LLM이 직접 계산하고, 그
    계산이 틀려도 우리는 "사실에 없는 숫자"로만 알 수 있다. 미리 주면 인용이 된다.
    """
    yesterday = facts.window.yesterday
    total = facts.total(day=yesterday, gbm=gbm)
    unresolved = facts.total(day=yesterday, gbm=gbm, unresolved=True)
    base = facts.baseline(yesterday, gbm=gbm)
    change, _ = delta(total, base)
    partner = facts.window.previous_of(yesterday)
    week = facts.total(day=partner, gbm=gbm) if partner else None
    week_change, _ = delta(total, week)
    earlier = len(facts.window.days) - 1

    lines = [
        f"GBM: {upper(gbm)}",
        f"어제({yesterday.isoformat()}, "
        f"{WEEKDAY_LABEL[yesterday.weekday()]}요일) 알람: {n(total)}건",
        f"미해제: {n(unresolved)}건 (미해제율 {pct(unresolved, total)})",
        f"직전 {earlier} 평일 평균: {n(base)}건 · 어제는 그 대비 {change}",
    ]
    if partner:
        lines.append(f"전주 동요일({partner.isoformat()}): {n(week)}건 · "
                     f"어제는 그 대비 {week_change}")

    daily = " / ".join(f"{day.month:02d}-{day.day:02d} {n(facts.total(day=day, gbm=gbm))}"
                       for day in facts.window.days)
    lines.append(f"일별 건수: {daily}")

    for label, key, formatter in (
            ("상위 법인", lambda r: r.plant, lambda share: upper(share.key[0])),
            ("상위 알람 항목", lambda r: r.scenario,
             lambda share: f"{share.key[1]}({share.key[0]})"),
            ("상위 라인", lambda r: (r.plant, r.line_code, r.line_name),
             lambda share: f"{upper(share.key[0])} {share.key[1]} {share.key[2]}")):
        groups = [g for g in ranking_by_gbm(facts, key, day=yesterday,
                                            limit=facts.thresholds.top_per_gbm)
                  if g.gbm == gbm]
        if groups:
            items = " / ".join(
                f"{formatter(share)} {n(share.count)}건"
                f"({share.ratio * 100:.1f}%)" for share in groups[0].items)
            lines.append(f"{label}: {items}")

    found = [s for s in spikes(facts, lambda r: (r.gbm, r.plant, r.scenario_id,
                                                 r.scenario_name), day=yesterday)
             if s.key[0] == gbm]
    if found:
        lines.append("급증(직전 평일 평균 대비): " + " / ".join(
            f"{upper(s.key[1])} {s.key[3]}({s.key[2]}) {n(s.count)}건, "
            f"평균 {n(s.baseline)}건의 {s.ratio:.1f}배" for s in found[:5]))

    chronic = [r for r in repeats(facts) if r.gbm == gbm]
    if chronic:
        lines.append("반복: " + " / ".join(
            f"{upper(r.plant)} {r.line_code} {r.line_name} — {r.scenario_name}"
            f"({r.scenario_id}) {n(r.count)}건, {r.days} 평일에 걸쳐"
            for r in chronic[:5]))

    lifecycle = scenario_lifecycle(facts)
    fresh = [i for i in lifecycle.appeared if i.gbm == gbm]
    if fresh:
        lines.append("어제 처음 나타난 항목: " + " / ".join(
            f"{i.name}({i.scenario_id}) {n(i.count)}건, {', '.join(upper(p) for p in i.plants)}"
            for i in fresh[:5]))
    gone = [i for i in lifecycle.vanished if i.gbm == gbm]
    if gone:
        lines.append("어제 사라진 항목: " + " / ".join(
            f"{i.name}({i.scenario_id}) — 그전 {n(i.count)}건" for i in gone[:5]))

    stalled = [f for f in freshness(facts) if f.stalled and f.gbm == gbm]
    if stalled:
        lines.append("어제 데이터가 없는 법인: "
                     + ", ".join(upper(f.fct) for f in stalled))

    missing = [s for s in facts.unavailable if s.gbm == gbm]
    if missing:
        # 못 읽은 법인이 있으면 **그 사실을 LLM에게도 알려야** 한다. 모르면
        # "줄었다"고 쓰고, 실제로는 안 읽은 것이다.
        lines.append(f"읽지 못한 법인 {len(missing)}곳 — 위 숫자에 포함되지 않았다: "
                     + ", ".join(upper(s.fct) for s in missing))
    if not facts.complete:
        lines.append("표본이 잘린 법인이 있어 위 건수는 하한이다")
    return "\n".join(lines)


def build_prompt(template: str, facts: Facts, gbm: str) -> str:
    """템플릿의 `{facts}`·`{gbm}`만 채운다.

    `str.format`을 쓰지 않는 이유: 프롬프트에 `{`가 들어 있으면(JSON 예시 등)
    KeyError로 죽는다. 치환은 두 자리뿐이므로 replace가 맞다.
    """
    return (template.replace("{facts}", facts_block(facts, gbm))
            .replace("{gbm}", upper(gbm)))


async def comment_on(facts: Facts, *, llm: LlmPort | None, spec: CommentSpec,
                     template: str, clock: Clock) -> tuple[GbmComment, ...]:
    """GBM별로 한 번씩 묻는다. **무엇이 실패해도 리포트는 나간다.**"""
    if not spec.enabled:
        return ()
    if llm is None:
        return tuple(GbmComment(gbm=gbm, status="skipped",
                                reason="LLM이 설정되지 않았다")
                     for gbm in facts.gbm_order())

    results = []
    for gbm in facts.gbm_order():
        if not facts.total(gbm=gbm):
            results.append(GbmComment(gbm=gbm, status="skipped",
                                      reason="기간 내 알람이 없다"))
            continue
        block = facts_block(facts, gbm)
        try:
            reply = await llm.ask(build_prompt(template, facts, gbm))
        except Exception as exc:                                   # noqa: BLE001
            # 최외곽 방어선. 어댑터가 던지면 그 GBM만 비고 나머지는 계속 간다.
            results.append(GbmComment(gbm=gbm, status="error",
                                      reason=f"{type(exc).__name__}: {exc}"))
            continue
        if reply.status == "error":
            results.append(GbmComment(gbm=gbm, status="error", reason=reply.error,
                                      model=reply.model))
            continue
        found = problems(reply.text or "", allowed=allowed_numbers(block),
                         max_chars=spec.max_chars)
        if found:
            results.append(GbmComment(gbm=gbm, status="rejected",
                                      reason="; ".join(found), model=reply.model,
                                      text=(reply.text or "")[:200]))
            continue
        results.append(GbmComment(gbm=gbm, status="ok",
                                  text=(reply.text or "").strip(),
                                  model=reply.model))
    return tuple(results)
