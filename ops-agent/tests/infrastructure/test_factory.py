"""어댑터 조립 — 스텁↔실구현 전환이 여기 하나뿐인가."""
from src.config.schema_site import SiteConfig
from src.infrastructure.factory import build_adapters

SITE = SiteConfig.model_validate({
    "site": {"gbm": "mx", "fct": "gumi"},
    "infra": {
        "redis": {"url": "redis://h:6379"},
        "mongodb": {"url": "mongodb://h:27017", "database": "data"},
        "kafka": {"consumer": {"bootstrap_server": ["h:9092"], "group_ids": ["g"],
                               "topic": {"topic1": "GUMI_TOPIC"}}},
        "rest": {"base_url": "http://h:8080",
                 "entries": {"lines": {"method": "GET", "path": "/lines"}}},
    }})


def test_seeds가_있으면_스텁을_조립한다(clock):
    adapters = build_adapters(SITE, clock=clock, seeds={})
    assert type(adapters.redis).__name__ == "StubRedisReader"
    assert adapters.available() == ["redis", "mongo", "kafka", "rest"]


def test_seeds가_없으면_실구현을_조립한다(clock):
    # 만들기만 해서는 소켓이 열리지 않는다(지연 연결) — 그래서 이 테스트가 네트워크를 안 탄다.
    adapters = build_adapters(SITE, clock=clock)
    assert type(adapters.redis).__name__ == "RealRedisReader"
    assert type(adapters.rest).__name__ == "RealRestProber"


def test_설정에_없는_시스템은_None이다(clock):
    only_redis = SiteConfig.model_validate({"site": {"gbm": "mx", "fct": "gumi"},
                                            "infra": {"redis": {"url": "redis://h:6379"}}})
    adapters = build_adapters(only_redis, clock=clock, seeds={})
    assert adapters.available() == ["redis"]
    assert adapters.mongo is None and adapters.kafka is None


async def test_스텁도_같은_계약을_지킨다_없는_키는_오류가_아니다(clock):
    adapters = build_adapters(SITE, clock=clock, seeds={"redis": {"oee:L3": "87.2"}})
    found = await adapters.redis.get("oee:L3")
    missing = await adapters.redis.get("없는키")
    assert found.status == "ok" and found.data["value"] == "87.2"
    assert missing.status == "ok" and missing.data is None, (
        "키가 없는 것은 사실이지 오류가 아니다 — 실구현과 같은 계약이어야 한다")


async def test_스텁_rest도_등재제를_지킨다(clock):
    adapters = build_adapters(SITE, clock=clock, seeds={"rest": {"lines": {"lines": ["L3"]}}})
    ok = await adapters.rest.query("lines", {})
    rejected = await adapters.rest.query("delete_line", {})
    assert ok.status == "ok" and ok.data["response"] == {"lines": ["L3"]}
    assert rejected.status == "error" and "등재되지 않은" in rejected.error
