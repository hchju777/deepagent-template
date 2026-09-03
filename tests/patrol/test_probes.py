import asyncio
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


def test_경로가_아닌_target은_등재_항목_프로브로_간다():
    def _check(target):
        return CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                           "target": target, "params": {"rule": "exists"}})
    assert resolve_probe(_check("rest:/api/v1/oee")) == "rest_get"
    assert resolve_probe(_check("rest:summary_prod")) == "rest_query"


async def test_rest_query는_check의_body를_그대로_넘긴다():
    from src.config.schema_site import RestEntry
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubRest
    from src.patrol.probes import rest_query
    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"part_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /summary/prod": {"badge": [0, 0, 0]}}, set(), entries,
                             clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge", "body": {"part_code": ["P001"]}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "ok" and result.data["body"] == {"badge": [0, 0, 0]}


async def test_rest_query는_어댑터가_없어도_raise하지_않는다():
    from src.infrastructure.factory import AdapterSet
    from src.patrol.probes import rest_query
    check = CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                        "target": "rest:summary_prod",
                                        "params": {"rule": "exists"}})
    result = await rest_query(AdapterSet(semaphore=asyncio.Semaphore(1)), check,
                              clock=lambda: T)
    assert result.status == "error" and "rest" in result.error


async def test_rest_query가_해석된_값을_보낸다():
    from src.config.schema_site import RestEntry
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubMongo, StubRest
    from src.patrol.probes import rest_query
    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"line_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /summary/prod": {"badge": [1]}}, set(), entries,
                             clock=lambda: T)
    adapters.mongo = StubMongo({"lines": [{"line_code": "L1"}, {"line_code": "L2"}]},
                               max_rows=100, clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge"},
        "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                  "field": "line_code"}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "ok"
    assert result.data["request"]["params"] == {"line_code": ["L1", "L2"]}


async def test_해석_실패면_대상을_호출하지_않는다():
    from src.config.schema_site import RestEntry
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubMongo, StubRest
    from src.patrol.probes import rest_query
    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"line_code": "list[str]"})}
    called = []

    class SpyRest(StubRest):
        async def query(self, entry, params):
            called.append(params)
            return await super().query(entry, params)

    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = SpyRest({"POST /summary/prod": {"badge": [1]}}, set(), entries,
                            clock=lambda: T)
    adapters.mongo = StubMongo({"lines": []}, max_rows=100, clock=lambda: T)   # 빈 결과
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge"},
        "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                  "field": "line_code"}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "error" and "line_code" in result.error
    assert called == [], "해석에 실패했는데 대상을 호출했다"


async def test_잘라낸_표본은_불완전으로_표시된다():
    from src.config.schema_site import RestEntry
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubMongo, StubRest
    from src.patrol.probes import rest_query
    entries = {"e": RestEntry(method="POST", path="/x",
                              body_schema={"line_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /x": {"ok": 1}}, set(), entries, clock=lambda: T)
    adapters.mongo = StubMongo({"lines": [{"line_code": f"L{i}"} for i in range(10)]},
                               max_rows=100, clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body.ok"},
        "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                  "field": "line_code", "cardinality": "first:3"}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "ok"
    assert result.envelope.complete is False
    assert "10" in (result.envelope.truncated_reason or "")
