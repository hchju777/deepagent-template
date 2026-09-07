"""시나리오 하나를 사이트에 팬아웃해 `FleetReport`를 만든다(계획 16/P7).

**상한은 코드가 쥔다(규율 6).** 기존 세마포어(`target.guards.max_concurrent`)는 사이트당
하나뿐이라 30 사이트 팬아웃이면 최대 120 in-flight가 조사 워커 트래픽과 동시에 대상
시스템으로 나간다. 여기서 사이트를 가로지르는 전역 세마포어를 하나 더 건다.

`window_from`/`window_to`를 찍는 이유: 40분에 걸쳐 모은 표본으로 만든 숫자와 1분 창의
숫자는 **다른 주장**이다. 사이트 간 클럭 스큐는 보정하지 않고 **드러낸다**.
"""
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Callable

from src.domain.rollup import FleetReport, MetricRollup, SiteCoverage, fold_complete
from src.fleet.collect import collect_site
from src.fleet.reduce import reduce_values

# digest가 반응해야 하는 것은 **무엇을 어떻게 재는가**뿐이다. title·enabled 같은 표현
# 필드가 섞이면 제목만 고쳐도 어제 숫자와 비교 불가가 돼 추세가 무의미해진다.
_DIGEST_FIELDS = ("kind", "concern", "metrics", "group_by", "scope")


def scenario_digest(scenario) -> str:
    payload = {f: scenario.model_dump(mode="json")[f] for f in _DIGEST_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _in_scope(scenario, sites: list[tuple[str, str]]) -> list[tuple[str, str]]:
    wanted = scenario.scope.sites
    excluded = set(scenario.scope.exclude)
    keys = [(g, f) for g, f in sites if f"{g}/{f}" not in excluded]
    if wanted == "all":
        return keys
    allow = set(wanted)
    return [(g, f) for g, f in keys if f"{g}/{f}" in allow]


async def run_scenario(name: str, scenario, *, sites: list[tuple[str, str]],
                       adapters_for_site: Callable[[str, str], Any],
                       clock: Callable[[], datetime], timezone_name: str,
                       digests=None, collect=collect_site) -> FleetReport:
    """팬아웃 → 커버리지 → 롤업 → 추세. 절대 raise하지 않는다."""
    scope = _in_scope(scenario, sites)
    digest = scenario_digest(scenario)
    started = clock()
    samples: dict[str, list] = {metric: [] for metric in scenario.metrics}
    semaphore = asyncio.Semaphore(scenario.scope.max_parallel_sites)

    async def one_site(gbm: str, fct: str):
        async with semaphore:      # 사이트를 가로지르는 전역 상한 — config가 값을 주고 코드가 건다
            adapters = adapters_for_site(gbm, fct)
            for metric, spec in scenario.metrics.items():
                samples[metric].append(await collect(spec, gbm=gbm, fct=fct, adapters=adapters,
                                                     clock=clock, timezone_name=timezone_name))

    await asyncio.gather(*(one_site(g, f) for g, f in scope), return_exceptions=True)
    finished = clock()

    coverage = _coverage(samples, scope)
    rollups = [_rollup(metric, scenario.metrics[metric], samples[metric], len(scope))
               for metric in scenario.metrics]
    groups = _groups(scenario, samples, scope)
    previous = digests.latest(name) if digests is not None else None
    trend, caveat = _trend(rollups, previous, digest)
    return FleetReport(scenario=name, title=scenario.title, concern=scenario.concern,
                       scenario_digest=digest, window_from=started, window_to=finished,
                       coverage=coverage, rollups=rollups, groups=groups,
                       previous_digest=previous.scenario_digest if previous else None,
                       trend=trend, trend_caveat=caveat, generated_at=finished)


def _coverage(samples: dict[str, list], scope) -> list[SiteCoverage]:
    """사이트별로 **가장 나쁜** 상태를 싣는다 — 지표 하나가 실패한 사이트를 covered로
    적으면 커버리지 블록이 거짓말한다."""
    rank = {"covered": 0, "fallback": 1, "missing": 2}
    worst: dict[tuple[str, str], Any] = {}
    for rows in samples.values():
        for sample in rows:
            key = (sample.gbm, sample.fct)
            current = worst.get(key)
            if current is None or rank[sample.status] > rank[current.status]:
                worst[key] = sample
    out = []
    for gbm, fct in scope:
        sample = worst.get((gbm, fct))
        if sample is None:
            out.append(SiteCoverage(gbm=gbm, fct=fct, status="missing",
                                    reason="표본이 수집되지 않았다"))
            continue
        out.append(SiteCoverage(gbm=gbm, fct=fct, status=sample.status, reason=sample.reason,
                                last_success_at=sample.effective_as_of))
    return out


def _rollup(metric: str, spec, rows: list, expected: int) -> MetricRollup:
    usable = [s for s in rows if s.status != "missing"]
    values = [v for s in usable for v in s.values]
    covered = len(usable)
    value = reduce_values(values, spec.reduce) if covered else None
    gaps = [s for s in rows if s.status != "covered"]
    skipped = sum(s.skipped for s in rows)
    complete = fold_complete([s.status == "covered" for s in rows]) and skipped == 0 \
        and covered == expected
    note = None
    if not complete:
        parts = []
        if covered < expected:
            parts.append(f"{expected - covered}개 사이트 미확인")
        if any(s.status == "fallback" for s in gaps):
            parts.append("폴백 표본 포함")
        if skipped:
            parts.append(f"숫자가 아닌 항목 {skipped}건 제외")
        note = " · ".join(parts) or "표본 없음"
    return MetricRollup(metric=metric, value=value, reduce=spec.reduce, expected_sites=expected,
                        covered_sites=covered, complete=complete, coverage_note=note,
                        unit=spec.unit)


def _groups(scenario, samples: dict[str, list], scope) -> dict[str, list[MetricRollup]]:
    if not scenario.group_by or scenario.group_by != ["gbm"]:
        # 축은 지금 gbm 하나다. 다른 축을 늘릴 때 스펙을 먼저 고친다(규율 6).
        return {}
    axes: dict[str, list[tuple[str, str]]] = {}
    for gbm, fct in scope:
        axes.setdefault(gbm, []).append((gbm, fct))
    out: dict[str, list[MetricRollup]] = {}
    for axis, members in axes.items():
        keys = set(members)
        out[axis] = [_rollup(metric, scenario.metrics[metric],
                             [s for s in samples[metric] if (s.gbm, s.fct) in keys], len(members))
                     for metric in scenario.metrics]
    return out


def _trend(rollups, previous, digest) -> tuple[dict, str | None]:
    if previous is None:
        return {}, None
    if previous.scenario_digest != digest:
        # 시나리오의 extract/reduce가 바뀐 뒤 어제 숫자와 나란히 그리면 추세가 거짓이다.
        return {}, (f"이전 실행의 scenario_digest가 다르다({previous.scenario_digest} → {digest})"
                    " — 추세 비교 불가")
    before = {r.metric: r.value for r in previous.rollups}
    trend = {}
    for rollup in rollups:
        old, new = before.get(rollup.metric), rollup.value
        trend[rollup.metric] = None if old is None or new is None else new - old
    return trend, None
