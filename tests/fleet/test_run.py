"""팬아웃과 롤업 조립 — 상한은 코드가 쥔다(계획 16/P7, 규율 6)."""
import asyncio
from datetime import datetime, timedelta, timezone

from src.config.schema_scenario import ScenarioConfig
from src.domain.rollup import InMemoryDigestStore
from src.fleet.collect import SiteSample
from src.fleet.run import run_scenario, scenario_digest

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
_SPEC = {"kind": "aggregate", "concern": "operation", "title": "알람 추세",
         "schedule": {"cron": "0 7 * * *"},
         "metrics": {"alarms": {"target": "rest:/alarms", "extract": "body.n", "reduce": "sum"}}}


def _scenario(**kw):
    return ScenarioConfig.model_validate({**_SPEC, **kw})


def _sampler(results, *, seen=None, delay=0.0):
    """사이트→SiteSample 매핑을 collect_site 자리에 꽂는다."""
    async def collect(spec, *, gbm, fct, adapters, clock, timezone_name):
        if seen is not None:
            seen.append(("enter", f"{gbm}/{fct}"))
        if delay:
            await asyncio.sleep(delay)
        if seen is not None:
            seen.append(("exit", f"{gbm}/{fct}"))
        return results[f"{gbm}/{fct}"]
    return collect


def _covered(gbm, fct, values):
    return SiteSample(gbm=gbm, fct=fct, values=values, status="covered")


def _missing(gbm, fct, reason="REST 타임아웃"):
    return SiteSample(gbm=gbm, fct=fct, status="missing", reason=reason)


SITES = [("mx", "gumi"), ("mx", "suwon"), ("ds", "xian")]


async def _run(scenario, results, **kw):
    return await run_scenario("alarm_trend", scenario, sites=SITES,
                              adapters_for_site=lambda g, f: object(), clock=lambda: T,
                              timezone_name="UTC", collect=_sampler(results, **kw.pop("sampler", {})),
                              **kw)


async def test_동시_사이트_수가_상한을_넘지_않는다():
    # 규율 6의 핵심: 30 사이트 팬아웃이 조사 워커 트래픽과 함께 대상 시스템으로 나간다.
    seen = []
    results = {f"{g}/{f}": _covered(g, f, [1.0]) for g, f in SITES}
    await _run(_scenario(scope={"max_parallel_sites": 2}), results,
               sampler={"seen": seen, "delay": 0.01})
    peak, live = 0, 0
    for kind, _ in seen:
        live += 1 if kind == "enter" else -1
        peak = max(peak, live)
    assert peak == 2


async def test_사이트_하나가_죽어도_나머지가_집계된다():
    results = {"mx/gumi": _covered("mx", "gumi", [2.0]), "mx/suwon": _covered("mx", "suwon", [3.0]),
               "ds/xian": _missing("ds", "xian")}
    report = await _run(_scenario(), results)
    rollup = report.rollups[0]
    assert rollup.value == 5.0 and rollup.expected_sites == 3 and rollup.covered_sites == 2
    assert rollup.complete is False and rollup.coverage_note
    assert [c.status for c in report.coverage].count("missing") == 1
    assert any("타임아웃" in (c.reason or "") for c in report.coverage)


async def test_전부_실패하면_값이_없다():
    # 0이 아니다 — "알람이 없었다"로 읽히면 안 된다.
    results = {f"{g}/{f}": _missing(g, f) for g, f in SITES}
    report = await _run(_scenario(), results)
    assert report.rollups[0].value is None and report.rollups[0].covered_sites == 0


async def test_scope가_사이트를_좁히고_expected가_그것을_따른다():
    results = {"mx/gumi": _covered("mx", "gumi", [4.0])}
    report = await _run(_scenario(scope={"sites": ["mx/gumi"]}), results)
    assert report.rollups[0].expected_sites == 1 and report.rollups[0].value == 4.0
    results2 = {"mx/gumi": _covered("mx", "gumi", [4.0]), "mx/suwon": _covered("mx", "suwon", [1.0])}
    report2 = await _run(_scenario(scope={"exclude": ["ds/xian"]}), results2)
    assert report2.rollups[0].expected_sites == 2


async def test_fallback은_숫자에_들어가되_커버리지에_드러난다():
    results = {"mx/gumi": _covered("mx", "gumi", [2.0]),
               "mx/suwon": SiteSample(gbm="mx", fct="suwon", values=[1.0], status="fallback",
                                      reason="effective_as_of가 요청보다 6시간 이전"),
               "ds/xian": _covered("ds", "xian", [3.0])}
    report = await _run(_scenario(), results)
    assert report.rollups[0].value == 6.0 and report.rollups[0].complete is False
    assert [c.status for c in report.coverage].count("fallback") == 1


async def test_축_분해는_group_by를_따른다():
    results = {"mx/gumi": _covered("mx", "gumi", [2.0]), "mx/suwon": _covered("mx", "suwon", [3.0]),
               "ds/xian": _covered("ds", "xian", [4.0])}
    report = await _run(_scenario(group_by=["gbm"]), results)
    assert {k: [r.value for r in v] for k, v in report.groups.items()} == {"mx": [5.0], "ds": [4.0]}


async def test_창은_첫_표본과_마지막_표본의_시각이다():
    ticks = iter([T, T + timedelta(minutes=40)])
    results = {f"{g}/{f}": _covered(g, f, [1.0]) for g, f in SITES}
    report = await run_scenario("alarm_trend", _scenario(), sites=SITES,
                                adapters_for_site=lambda g, f: object(),
                                clock=lambda: next(ticks, T + timedelta(minutes=40)),
                                timezone_name="UTC", collect=_sampler(results))
    assert report.window_from == T and report.window_to == T + timedelta(minutes=40)


def test_digest는_내용에_반응하고_표현에는_반응하지_않는다():
    base = _scenario()
    assert scenario_digest(base) == scenario_digest(_scenario(title="다른 제목"))
    assert scenario_digest(base) == scenario_digest(_scenario(enabled=False))
    changed = _scenario(metrics={"alarms": {"target": "rest:/alarms", "extract": "body.m",
                                            "reduce": "sum"}})
    assert scenario_digest(base) != scenario_digest(changed)
    assert scenario_digest(base) != scenario_digest(_scenario(group_by=["gbm"]))


async def test_digest가_다르면_추세는_비교_불가다():
    # extract/reduce를 바꾼 뒤 어제 숫자와 나란히 그리면 추세가 거짓이 된다.
    digests = InMemoryDigestStore()
    results = {f"{g}/{f}": _covered(g, f, [1.0]) for g, f in SITES}
    first = await _run(_scenario(), results, digests=digests)
    digests.put(first.model_copy(update={"scenario_digest": "옛날digest"}))
    second = await _run(_scenario(), results, digests=digests)
    assert second.trend == {} and "digest" in (second.trend_caveat or "")


async def test_같은_digest면_전회_대비를_낸다():
    digests = InMemoryDigestStore()
    results = {f"{g}/{f}": _covered(g, f, [1.0]) for g, f in SITES}
    first = await _run(_scenario(), results, digests=digests)
    digests.put(first)
    results2 = {f"{g}/{f}": _covered(g, f, [2.0]) for g, f in SITES}
    second = await _run(_scenario(), results2, digests=digests)
    assert second.trend == {"alarms": 3.0} and second.trend_caveat is None


async def test_지표_하나가_실패한_사이트는_covered로_적히지_않는다():
    # 커버리지 블록 자신이 거짓말하면 이 기능의 존재 이유가 무너진다.
    scenario = _scenario(metrics={
        "alarms": {"target": "rest:/alarms", "extract": "body.n", "reduce": "sum"},
        "downtime": {"target": "rest:/down", "extract": "body.n", "reduce": "sum"}})
    calls = {"n": 0}

    async def collect(spec, *, gbm, fct, adapters, clock, timezone_name):
        calls["n"] += 1
        if (gbm, fct) == ("mx", "suwon") and spec.target == "rest:/down":
            return _missing(gbm, fct, "다운타임 끝점 타임아웃")
        return _covered(gbm, fct, [1.0])

    report = await run_scenario("alarm_trend", scenario, sites=SITES,
                                adapters_for_site=lambda g, f: object(), clock=lambda: T,
                                timezone_name="UTC", collect=collect)
    suwon = next(c for c in report.coverage if c.fct == "suwon")
    assert suwon.status == "missing" and "다운타임" in (suwon.reason or "")
    assert next(c for c in report.coverage if c.fct == "gumi").status == "covered"
