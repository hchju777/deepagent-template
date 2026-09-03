from datetime import datetime, timezone

from src.infrastructure.query_rules import (
    aggregate_problems, endpoint_allowed, filter_problems,
    kafka_effective_start, mongo_role_problems)


def test_쓰기_스테이지와_JS_실행은_거부된다():
    assert aggregate_problems([{"$match": {"a": 1}}, {"$group": {"_id": "$b"}}]) == []
    assert any("$out" in p for p in aggregate_problems([{"$out": "evil"}]))
    assert any("$merge" in p for p in aggregate_problems([{"$merge": {"into": "evil"}}]))


def test_filter는_allowlist_연산자만():
    assert filter_problems({"line": 7, "ts": {"$lte": 5}, "$or": [{"a": 1}, {"b": {"$in": [1]}}]}) == []
    assert any("$where" in p for p in filter_problems({"$where": "sleep(1000)"}))
    assert any("$expr" in p for p in filter_problems({"x": {"$expr": {}}}))


def test_끝점은_토폴로지_패턴_전체일치만():
    patterns = {"/api/v1/lines/{line}/oee"}
    assert endpoint_allowed("/api/v1/lines/7/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/7/oee/../../admin", patterns)
    assert not endpoint_allowed("/api/v1/lines/7", patterns)


def test_kafka_보존_밖이면_earliest로_폴백하고_표시한다():
    req = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ts, fallback = kafka_effective_start(req, resolved_ts=1756701000000, earliest_ts=None)
    assert not fallback and ts == datetime.fromtimestamp(1756701000, tz=timezone.utc)
    ts2, fallback2 = kafka_effective_start(req, resolved_ts=None, earliest_ts=1756900000000)
    assert fallback2 and ts2 == datetime.fromtimestamp(1756900000, tz=timezone.utc)


def test_mongo_롤은_read계열만_허용():
    ok = {"authInfo": {"authenticatedUserRoles": [{"role": "read", "db": "twin"}]}}
    bad = {"authInfo": {"authenticatedUserRoles": [{"role": "readWrite", "db": "twin"}]}}
    anon = {"authInfo": {"authenticatedUserRoles": []}}
    assert mongo_role_problems(ok) == []
    assert any("readWrite" in p for p in mongo_role_problems(bad))
    assert mongo_role_problems(anon) == []


def test_중첩된_JS_실행_연산자도_잡는다():
    assert any("$function" in p for p in aggregate_problems(
        [{"$project": {"x": {"$function": {"body": "evil"}}}}]))
    assert any("$where" in p for p in aggregate_problems(
        [{"$match": {"$where": "sleep(1000)"}}]))
    assert any("$accumulator" in p for p in aggregate_problems(
        [{"$group": {"_id": "$a", "y": {"$accumulator": {}}}}]))


def test_끝점_메타문자와_개행_우회_차단():
    assert not endpoint_allowed("/api/v1/tags/x/PLCXLine7XValue",
                                {"/api/v1/tags/{tag}/PLC.Line7.Value"})
    assert endpoint_allowed("/api/v1/tags/x/PLC.Line7.Value",
                            {"/api/v1/tags/{tag}/PLC.Line7.Value"})
    assert not endpoint_allowed("/api/v1/lines/7/oee\n", {"/api/v1/lines/{line}/oee"})


def test_끝점_경로_순회와_퍼센트_인코딩_차단():
    patterns = {"/api/v1/lines/{line}/oee"}
    assert not endpoint_allowed("/api/v1/lines/../oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/./oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/%2e%2e/oee", patterns)


def test_끝점_쿼리_프래그먼트_매트릭스_우회_차단():
    # `{자리표시자}`가 `[^/]+`로 컴파일돼 `?`·`#`·`;`까지 삼킨다 — allowlist는
    # 통과시키는데 httpx는 등록되지 않은 `/api/v1/lines/L1`로 나간다(실증). 특히
    # `_method=DELETE`는 실재하는 메서드 오버라이드 관례라, 대상이 그걸 존중하면
    # "완전 읽기 전용"이 LLM이 쓴 문자열 하나로 깨진다.
    patterns = {"/api/v1/lines/{line}/oee"}
    assert not endpoint_allowed("/api/v1/lines/L1?_method=DELETE&/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/L1#/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/L1;x=y/oee", patterns)
