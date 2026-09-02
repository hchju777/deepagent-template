from datetime import datetime, timezone

from src.config.schema_site import SiteConfig
from src.infrastructure.factory import StubSeeds, build_adapters
from src.infrastructure.stubs import StubMongo, StubRedis
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CLOCK = lambda: T

TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/api/v1/lines/{line}/oee"}]}},
    "derivations": {}})

SITE = SiteConfig.model_validate({
    "target": {"redis": {"url": "redis://x:6379"},
               "mongo": {"url": "mongodb://x:27017"},
               "guards": {"max_concurrent": 2}}})     # adapters 기본값 = stub


async def test_stub_모드_조립과_시드_주입():
    seeds = StubSeeds(redis_data={"plan:7": "480"},
                      mongo_collections={"twin_state": [{"line": 7}]})
    adapters = build_adapters(SITE, TOPO, clock=CLOCK, stub_seeds=seeds)
    assert isinstance(adapters.redis, StubRedis)
    assert isinstance(adapters.mongo, StubMongo)
    assert adapters.kafka is None                     # config에 kafka 없음 → None
    assert adapters.code is None                      # config에 code 없음 → None
    assert (await adapters.redis.get("plan:7")).data == "480"
    assert adapters.semaphore._value == 2             # guards.max_concurrent


async def test_rest_allowlist는_토폴로지에서_온다():
    site = SiteConfig.model_validate({
        "target": {"rest": {"base_url": "http://x"}}})
    seeds = StubSeeds(rest_responses={"/api/v1/lines/7/oee": {"oee": 0.9}})
    adapters = build_adapters(site, TOPO, clock=CLOCK, stub_seeds=seeds)
    assert (await adapters.rest.get("/api/v1/lines/7/oee")).status == "ok"
    assert (await adapters.rest.get("/admin")).status == "error"
