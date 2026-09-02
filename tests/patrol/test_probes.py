from datetime import datetime, timezone

from src.config.schema_site import CheckConfig, SiteConfig
from src.infrastructure.factory import StubSeeds, build_adapters
from src.knowledge.topology import Topology
from src.patrol.probes import PROBES, resolve_probe

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {}})
SITE = SiteConfig.model_validate({"target": {
    "rest": {"base_url": "http://x"}, "redis": {"url": "redis://x"},
    "mongo": {"url": "mongodb://x:27017"}}})
SITE_KAFKA = SiteConfig.model_validate({"target": {"kafka": {"bootstrap": "kafka:9092"}}})


def _adapters(seeds):
    return build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=seeds)


def _check(**kw):
    base = {"judge": "rule", "schedule": {"interval": "5m"}}
    base.update(kw)
    return CheckConfig.model_validate(base)


def test_target_kind로_기본_프로브가_정해지고_명시가_우선():
    assert resolve_probe(_check(target="rest:/oee")) == "rest_get"
    assert resolve_probe(_check(target="mongo:twin_state")) == "mongo_recent"
    assert resolve_probe(_check(target="rest:/oee", probe="kafka_lag")) == "kafka_lag"
    assert resolve_probe(_check()) is None


async def test_rest_get_프로브는_봉투와_본문을_돌려준다():
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 5.12}}))
    result = await PROBES["rest_get"](adapters, _check(target="rest:/oee"), clock=lambda: T)
    assert result.status == "ok" and result.data["body"] == {"oee": 5.12}
    assert result.envelope.observed_at == T


async def test_미설정_어댑터와_잘못된_target은_error_결과():
    adapters = _adapters(StubSeeds())
    kafka = await PROBES["kafka_lag"](adapters, _check(params={"group": "g"}), clock=lambda: T)
    assert kafka.status == "error" and "어댑터" in kafka.error
    nogroup = await PROBES["kafka_lag"](adapters, _check(), clock=lambda: T)
    assert nogroup.status == "error"


async def test_kafka_어댑터는_있어도_group_없으면_error():
    # 위 테스트의 nogroup은 kafka 어댑터 자체가 미설정이라 "어댑터 미설정"
    # 분기에서 이미 걸린다 — 여기서는 kafka 타깃이 있는 사이트로 "group 부재"
    # 분기를 독립적으로 덮는다.
    adapters = build_adapters(SITE_KAFKA, TOPO, clock=lambda: T, stub_seeds=StubSeeds())
    result = await PROBES["kafka_lag"](adapters, _check(), clock=lambda: T)
    assert result.status == "error" and "group" in result.error
