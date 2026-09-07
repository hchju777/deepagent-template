"""사이트 하나에서 표본 하나 — 기존 프로브를 그대로 쓴다(계획 16/P7).

사이트 하나의 실패는 그 사이트의 **커버리지 항목**이지 집계의 죽음이 아니다(규율 1).
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.config.schema_scenario import MetricSpec
from src.config.schema_site import SiteConfig
from src.domain.envelope import Envelope, ProbeResult
from src.fleet.collect import collect_site
from src.infrastructure.factory import StubSeeds, build_adapters
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/alarms"}]}},
    "derivations": {}})
SITE = SiteConfig.model_validate({"target": {"rest": {"base_url": "http://x"}}})


def _adapters(**seeds):
    return build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=StubSeeds(**seeds))


def _spec(**kw):
    base = dict(target="rest:/alarms", extract="body.summary.alarms", reduce="sum")
    base.update(kw)
    return MetricSpec(**base)


async def _collect(spec, adapters, **kw):
    return await collect_site(spec, gbm=kw.pop("gbm", "mx"), fct=kw.pop("fct", "gumi"),
                              adapters=adapters, clock=lambda: T, timezone_name="UTC", **kw)


async def test_성공한_사이트는_값을_싣고_covered다():
    adapters = _adapters(rest_responses={"/alarms": {"summary": {"alarms": 12}}})
    sample = await _collect(_spec(), adapters)
    assert sample.status == "covered" and sample.values == [12.0] and sample.skipped == 0
    assert sample.reason is None and (sample.gbm, sample.fct) == ("mx", "gumi")


async def test_프로브_오류는_missing과_사유다():
    sample = await _collect(_spec(), _adapters(), fct="suwon")   # 끝점 미등록
    assert sample.status == "missing" and sample.values == []
    assert sample.reason and sample.fct == "suwon"


async def test_어댑터가_던져도_raise하지_않는다():
    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("어댑터 폭발")
    sample = await _collect(_spec(), _Boom())
    assert sample.status == "missing" and "RuntimeError" in (sample.reason or "")


async def test_대상을_못_정하면_호출하지_않고_missing이다():
    sample = await _collect(_spec(target=None), _adapters())
    assert sample.status == "missing" and "프로브" in (sample.reason or "")


async def test_숫자가_아닌_항목은_건너뛴_수로_센다():
    adapters = _adapters(rest_responses={"/alarms": {"rows": [{"n": 1}, {"n": "많음"}]}})
    sample = await _collect(_spec(extract="body.rows.n"), adapters)
    assert sample.values == [1.0] and sample.skipped == 1 and sample.status == "covered"


@pytest.fixture
def fake_probe(monkeypatch):
    """프로브가 돌려주는 봉투를 직접 정하고 싶을 때 — 스텁은 effective_as_of를 안 싣는다."""
    def install(envelope, data):
        async def probe(adapters, spec, *, clock, timezone_name):
            return ProbeResult(status="ok", envelope=envelope, data=data)
        monkeypatch.setitem(__import__("src.patrol.probes", fromlist=["PROBES"]).PROBES,
                            "rest_get", probe)
    return install


async def test_요청_창보다_오래된_값은_fallback이다(fake_probe):
    # 값은 왔지만 대상이 창보다 오래된 것을 돌려줬다 — 숫자에 넣되 보고서가 적는다.
    stale = T - timedelta(hours=6)
    fake_probe(Envelope(observed_at=T, effective_as_of=stale), {"summary": {"alarms": 3}})
    sample = await _collect(_spec(extract="summary.alarms", window="1h"), _adapters())
    assert sample.status == "fallback" and sample.values == [3.0]
    assert "effective_as_of" in (sample.reason or "")


async def test_창_안의_값은_covered다(fake_probe):
    fresh = T - timedelta(minutes=10)
    fake_probe(Envelope(observed_at=T, effective_as_of=fresh), {"summary": {"alarms": 3}})
    sample = await _collect(_spec(extract="summary.alarms", window="1h"), _adapters())
    assert sample.status == "covered"


async def test_불완전한_응답도_fallback이고_사유가_붙는다(fake_probe):
    fake_probe(Envelope(observed_at=T, complete=False, truncated_reason="max_rows"),
               {"summary": {"alarms": 5}})
    sample = await _collect(_spec(extract="summary.alarms"), _adapters())
    assert sample.status == "fallback" and sample.reason == "max_rows"
