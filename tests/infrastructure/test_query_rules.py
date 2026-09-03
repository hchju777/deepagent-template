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


def test_허용된_쿼리_키는_통과하고_나머지는_거부된다():
    patterns = {"/api/v1/lines/{line}/oee"}
    ok = "/api/v1/lines/L1/oee?date=2026-09-04"
    assert not endpoint_allowed(ok, patterns)                       # 기본은 쿼리 불가
    assert endpoint_allowed(ok, patterns, query_keys={"date"})
    assert not endpoint_allowed(ok, patterns, query_keys={"line"})  # 미등록 키
    assert endpoint_allowed("/api/v1/lines/L1/oee?date=x&date=y", patterns,
                            query_keys={"date"})


def test_퍼센트_인코딩은_쿼리_값에서만_허용된다():
    patterns = {"/api/v1/lines/{line}/oee"}
    # path의 %는 여전히 거부한다 — %2e%2e 경로 순회 우회로가 있다
    assert not endpoint_allowed("/api/v1/lines/%2e%2e/oee", patterns, query_keys={"date"})
    # 쿼리 값의 인코딩은 정상이다(ISO 시각의 콜론 등)
    assert endpoint_allowed("/api/v1/lines/L1/oee?date=2026-09-04T00%3A00%3A00",
                            patterns, query_keys={"date"})


def test_프래그먼트는_쿼리를_허용해도_거부된다():
    # #은 절단돼 등록되지 않은 끝점이 된다 — 쿼리 허용과 무관하게 막는다.
    assert not endpoint_allowed("/api/v1/lines/L1#/oee", {"/api/v1/lines/{line}/oee"},
                                query_keys={"date"})


def test_기존_거부는_그대로_유지된다():
    # 계획 6이 막은 우회로가 파싱 재작성 후에도 막혀 있어야 한다.
    patterns = {"/api/v1/lines/{line}/oee"}
    assert not endpoint_allowed("/api/v1/lines/L1?_method=DELETE&/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/L1;x=y/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/../oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/7/oee\n", patterns)
    assert not endpoint_allowed("http://evil/api/v1/lines/7/oee", patterns)  # 절대 URL
    assert endpoint_allowed("/api/v1/lines/7/oee", patterns)                 # 정상은 통과


def test_제어문자는_파싱_전에_거부된다():
    # urlsplit은 개행·탭을 조용히 제거한다(WHATWG URL 규약). 정규화된 것을 판정하고
    # 원본을 보내면 "판정한 것과 보내는 것이 다르다"는 원래 버그가 재발한다.
    patterns = {"/api/v1/lines/{line}/oee"}
    for bad in ("/api/v1/lines/7/oee\n", "/api/v1/lines/7\t/oee", "/api/v1/lines/7/oee\r\n"):
        assert not endpoint_allowed(bad, patterns)


def test_body는_선언된_키와_타입만_허용한다():
    from src.infrastructure.query_rules import entry_body_problems
    schema = {"part_code": "list[str]", "line_code": "str", "limit": "int"}
    assert entry_body_problems({"part_code": ["P001"], "line_code": "L1", "limit": 10},
                               schema) == []
    # 스키마 밖 키 — 해석기가 실수로 실을 수 있고, 대상이 그걸 해석하면
    # 우리가 의도하지 않은 동작이 된다
    assert any("save_as" in p for p in
               entry_body_problems({"line_code": "L1", "save_as": "x"}, schema))
    assert any("part_code" in p for p in entry_body_problems({"part_code": "P001"}, schema))
    assert any("limit" in p for p in entry_body_problems({"limit": "10"}, schema))
    assert any("part_code" in p for p in
               entry_body_problems({"part_code": ["P001", 2]}, schema))


def test_body의_누락은_문제가_아니다():
    # 어떤 필드가 필수인지는 대상 API가 정하고 우리는 모른다(계획 9의 OpenAPI가
    # 답할 문제다). 여기서 강제하면 스키마를 우리 추측으로 좁히게 된다.
    from src.infrastructure.query_rules import entry_body_problems
    assert entry_body_problems({}, {"part_code": "list[str]"}) == []


def test_bool은_int로_통과하지_않는다():
    # 파이썬에서 bool은 int의 하위 타입이라 isinstance(True, int)가 참이다.
    # 그대로 두면 {"limit": True}가 통과해 대상에 1로 나간다.
    from src.infrastructure.query_rules import entry_body_problems
    assert entry_body_problems({"limit": True}, {"limit": "int"}) != []
    assert entry_body_problems({"flag": True}, {"flag": "bool"}) == []


def test_등재_항목_증거의_출처는_보낸_body를_식별한다():
    # 같은 끝점에 다른 필터를 보낸 두 증거가 §4 표에서 구별돼야 한다.
    # 구별되지 않으면 0/0/0이 "멈췄다"인지 "잘못 물었다"인지 알 수 없다.
    from src.infrastructure.query_rules import entry_evidence_source
    a = entry_evidence_source("POST", "/summary/prod", {"part_code": ["P001"]})
    b = entry_evidence_source("POST", "/summary/prod", {"part_code": ["P002"]})
    assert a.startswith("rest:POST:/summary/prod#") and a != b
    # 키 순서가 달라도 같은 질문이면 같은 출처여야 한다(canonical_digest 규약)
    c = entry_evidence_source("POST", "/summary/prod",
                              {"part_code": ["P001"], "line_code": None})
    d = entry_evidence_source("POST", "/summary/prod",
                              {"line_code": None, "part_code": ["P001"]})
    assert c == d


def test_망가진_URL도_raise하지_않고_거부한다():
    # urlsplit은 ValueError("Invalid IPv6 URL")을 던진다. endpoint는 서브에이전트
    # LLM이 정하고, 두 어댑터의 endpoint_allowed 호출은 try/except 밖이다 —
    # 파싱 도입이 무raise 규율을 main 대비 회귀시켰다.
    assert not endpoint_allowed("//[bad/x", {"/api/{x}/oee"})
    assert not endpoint_allowed("//[::1", {"/api/{x}/oee"})
