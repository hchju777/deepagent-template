from datetime import datetime, timezone

from src.infrastructure.stubs import StubKafka, StubMongo, StubRedis, StubRest

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CLOCK = lambda: T


async def test_redis_스텁_TYPE분기와_scan_절단():
    stub = StubRedis({"plan:7": "480", "equip:7": {"state": "RUN"}},
                     ttls={"plan:7": 3600}, max_rows=1, clock=CLOCK)
    assert (await stub.get("plan:7")).data == "480"
    assert (await stub.get("equip:7")).data == {"state": "RUN"}
    assert (await stub.get("ghost")).data is None
    assert (await stub.ttl("plan:7")).data == 3600
    scan = await stub.scan("*:7")
    assert scan.envelope.complete is False and scan.envelope.truncated_reason == "max_rows"
    assert len(scan.data) == 1


async def test_mongo_스텁_필터_평가와_위험_연산_거부():
    stub = StubMongo({"twin_state": [{"line": 7, "oee": 5.12}, {"line": 6, "oee": 0.87}]},
                     max_rows=10, clock=CLOCK)
    found = await stub.find("twin_state", {"line": 7})
    assert found.status == "ok" and found.data == [{"line": 7, "oee": 5.12}]
    assert (await stub.count("twin_state", {"oee": {"$gt": 1}})).data == 1
    bad = await stub.find("twin_state", {"$where": "1"})
    assert bad.status == "error" and "$where" in bad.error


async def test_kafka_스텁_보존밖_요청은_earliest_폴백_명시():
    msgs = {"edge.raw.7": [{"ts": datetime(2026, 9, 2, tzinfo=timezone.utc), "value": {"n": 1}}]}
    stub = StubKafka(msgs, max_rows=10, clock=CLOCK)
    res = await stub.read("edge.raw.7",
                          start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                          end=T)
    assert res.status == "ok" and len(res.data) == 1
    assert res.envelope.effective_as_of == datetime(2026, 9, 2, tzinfo=timezone.utc)


async def test_rest_스텁_토폴로지_밖_끝점_거부():
    stub = StubRest({"/api/v1/lines/7/oee": {"oee": 5.12}},
                    allowed={"/api/v1/lines/{line}/oee"}, clock=CLOCK)
    assert (await stub.get("/api/v1/lines/7/oee")).data == {"oee": 5.12}
    outside = await stub.get("/admin/drop")
    assert outside.status == "error" and "토폴로지" in outside.error


async def test_mongo_스텁_regex_평가():
    stub = StubMongo({"c": [{"name": "apple"}, {"name": "banana"}]}, max_rows=10, clock=CLOCK)
    found = await stub.find("c", {"name": {"$regex": "^a"}})
    assert found.data == [{"name": "apple"}]


async def test_mongo_스텁_aggregate_절단_표시():
    stub = StubMongo({"c": [{"i": n} for n in range(5)]}, max_rows=2, clock=CLOCK)
    res = await stub.aggregate("c", [{"$match": {}}])
    assert res.envelope.complete is False and res.envelope.truncated_reason == "max_rows"


async def test_mongo_스텁_잘못된_구조는_error_결과():
    stub = StubMongo({"c": [{"a": 1}]}, max_rows=10, clock=CLOCK)
    res = await stub.find("c", {"$and": {"a": 1}})
    assert res.status == "error" and "구조 오류" in res.error


async def test_mongo_스텁_sort_필드_부재도_error_결과():
    stub = StubMongo({"c": [{"a": 1}, {"b": 2}]}, max_rows=10, clock=CLOCK)
    res = await stub.find("c", {}, sort=[("a", 1)])
    assert res.status == "error"
