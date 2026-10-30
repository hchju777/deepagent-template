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


def test_seeds에_code_절이_있을_때만_스텁_코드를_조립하고_만들어진_것이_있으면_그것이_이긴다(clock):
    """대본 실행에 코드 자리를 주되, 달라고 하지 않은 대본에 `code`가 생기면 안 된다 — 목록에 없는
    시스템이 어댑터 묶음에 뜨는 것은 config가 아닌 곳에서 허용 범위가 넓어지는 일이다."""
    assert build_adapters(SITE, clock=clock, seeds={}).code is None
    stubbed = build_adapters(SITE, clock=clock, seeds={"code": {"trace": {"/lines": "x"}}})
    assert type(stubbed.code).__name__ == "StubDeployedCode" and "code" in stubbed.available()
    marker = object()
    assert build_adapters(SITE, clock=clock, seeds={"code": {}}, code=marker).code is marker


# ── R3-5v ① — 재생 스냅샷 ──

SNAP = {"id": "s-1", "captured_at": "2026-11-01T09:00:00+09:00"}


async def test_재생_스냅샷이면_스냅샷_밖_읽기는_없음이_아니라_실패이고_관측_시각은_뜬_시각이다(clock):
    """평가자 회신(11-03): R3는 그날 뜬 스냅샷의 재생 판으로 닫는다. 안 뜬 것을 "없음"으로 답하면 `키가 없다`·빈 토픽 같은 틀린 사실이 된다 — 실패로. `complete`에
    든 시스템(전부 뜬 것 — Redis 전체)은 없는 키가 진짜 없음이다. TTL은 안 떴다 — 있는 키의 TTL을 "만료 없음(-1)"으로 답하지 않는다. 관측 시각은 뜬 시각."""
    from datetime import datetime, timedelta, timezone
    at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    seeds = {"snapshot": {**SNAP, "complete": ["redis"]}, "redis": {"k:1": "v"},
             "kafka": {"t.main": {"begin": 10, "end": 12, "messages": [{"offset": 11, "value": {"a": 1}}]}}}
    a = build_adapters(SITE, clock=clock, seeds=seeds)
    missing = await a.redis.get("k:2")
    assert missing.status == "ok" and missing.data is None and missing.envelope.observed_at == at
    assert (await a.redis.ttl("k:2")).data == -2
    ttl = await a.redis.ttl("k:1")
    assert ttl.status == "error" and ttl.error.startswith("스냅샷에 TTL이 없다")
    for got in (await a.mongo.find("alarm_events", {}), await a.mongo.count("alarm_events", {}), await a.mongo.list_collections(),
                await a.kafka.tail("t.other"), await a.kafka.list_topics(), await a.kafka.group_offsets("g"),
                await a.rest.query("lines", {})):
        assert got.status == "error" and got.error.startswith("스냅샷 밖 — "), got.error
        assert got.envelope.observed_at == at
    assert (await a.kafka.offsets("t.main")).data["partitions"][0] == {"partition": 0, "begin": 10, "end": 12}
    partial = build_adapters(SITE, clock=clock, seeds={"snapshot": SNAP, "redis": {"k:1": "v"}, "mongo": {"alarm_events": [{"x": 1}]}})
    for got in (await partial.redis.get("k:2"), await partial.redis.scan("k:*"), await partial.redis.ttl("k:2")):
        assert got.status == "error" and got.error.startswith("스냅샷 밖 — "), got.error
    assert (await partial.redis.get("k:1")).data["value"] == "v" and (await partial.mongo.count("alarm_events", {})).data == 1
    plain = build_adapters(SITE, clock=clock, seeds={"redis": {}})                     # 재생이 아니면 전처럼
    assert (await plain.redis.get("k:2")).data is None and (await plain.mongo.find("alarm_events", {})).data == []
    assert (await plain.kafka.tail("t.other")).data["messages"] == [] and (await plain.redis.ttl("k:2")).data == -2
    assert (await plain.mongo.list_collections()).data == [] and (await plain.kafka.list_topics()).data == []


async def test_재생_REST는_인자별로_뜬_응답만_답하고_인자_없는_응답은_재생에서_안_쓴다(clock):
    """REST는 인자마다 다른 사실이다 — 인자를 안 보고 한 응답을 돌려주면 다른 라인을 물은 질의에 이 라인의 행이 간다. `captures`(인자와 응답)는 인자를 대조하고,
    재생에서는 인자 없는 응답 꼴을 안 쓴다(뜬 쪽이 인자를 적어야 한다). 재생이 아니면 옛 꼴 그대로."""
    site = SiteConfig.model_validate({"site": {"gbm": "mx", "fct": "gumi"}, "infra": {"rest": {
        "base_url": "http://h:8080", "entries": {"lines": {"method": "GET", "path": "/lines", "params": {"line": {"type": "str"}}}}}}})
    caught = {"lines": {"captures": [{"params": {"line": "L1"}, "response": [{"line": "L1", "n": 3}]}]}}
    a = build_adapters(site, clock=clock, seeds={"snapshot": SNAP, "rest": caught})
    assert (await a.rest.query("lines", {"line": "L1"})).data["response"] == [{"line": "L1", "n": 3}]
    other = await a.rest.query("lines", {"line": "L2"})
    assert other.status == "error" and other.error.startswith("스냅샷 밖 — lines는 이 인자로 안 떴다")
    loose = build_adapters(site, clock=clock, seeds={"snapshot": SNAP, "rest": {"lines": [{"line": "L1"}]}})
    bare = await loose.rest.query("lines", {"line": "L1"})
    assert bare.status == "error" and bare.error.startswith("스냅샷 형식 — lines의 응답에 인자가 없다")
    old = build_adapters(site, clock=clock, seeds={"rest": {"lines": [{"line": "L1"}]}})
    assert (await old.rest.query("lines", {"line": "L2"})).data["response"] == [{"line": "L1"}]
    assert (await build_adapters(site, clock=clock, seeds={"rest": caught}).rest.query("lines", {"line": "L2"})).status == "error"
