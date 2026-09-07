import functools
import asyncio
from datetime import datetime, timezone

from src.config.schema_site import CheckConfig, RestEntry, SiteConfig
from src.infrastructure.factory import AdapterSet, StubSeeds, build_adapters
from src.infrastructure.stubs import StubMongo, StubRest
from src.knowledge.topology import Topology
from src.patrol.probes import PROBES as _PROBES, resolve_probe

# timezone_name은 키워드 필수 — 그 기본값이 배선 누락을 조용히 가렸다.
PROBES = {name: functools.partial(fn, timezone_name="UTC") for name, fn in _PROBES.items()}
rest_query = PROBES["rest_query"]

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


async def test_대상이_알려준_절단_이유를_덮어쓰지_않는다():
    # 우리가 표본을 자른 사실을 적으면서 대상이 알려준 절단을 지우면, 두 절단 중
    # 하나가 증거에서 사라진다 — 조용한 생략이다.
    from src.domain.envelope import Envelope, ProbeResult
    from src.config.schema_site import RestEntry

    class _Rest:
        async def query(self, entry, params):
            return ProbeResult(status="ok", data={"body": [], "request": {}},
                               envelope=Envelope(observed_at=T, complete=False,
                                                 truncated_reason="서버가 1페이지만 줬다"))

    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = _Rest()
    adapters.mongo = StubMongo({"lines": [{"c": f"L{i}"} for i in range(10)]},
                               max_rows=100, clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body"},
        "resolve": {"line": {"from": "mongo", "collection": "lines", "field": "c",
                             "cardinality": "first:3"}}})
    out = await PROBES["rest_query"](adapters, check, clock=lambda: T)
    reason = out.envelope.truncated_reason or ""
    assert "서버가 1페이지만 줬다" in reason and "10개 중 3개" in reason, reason


async def test_의도한_전체조회는_증거에_남는다():
    # unfiltered의 존재 이유는 "해석 실패로 우연히 전체를 본 것"과 "일부러 전체를
    # 본 것"을 코드가 구별하는 것이다. 증거에 안 남으면 런타임에는 그 구별이 없다.
    entries = {"e": RestEntry(method="POST", path="/x", body_schema={"line": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /x": {"ok": 1}}, set(), entries, clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body"},
        "resolve": {"line": {"from": "unfiltered"}}})
    out = await PROBES["rest_query"](adapters, check, clock=lambda: T)
    assert out.status == "ok"
    assert out.data["request"].get("unfiltered") == ["line"], out.data["request"]


# ---- mongo_find: config가 특정 collection에 특정 find 질의를 낸다 ------------------------
def _mongo_adapters(docs):
    return build_adapters(SITE, TOPO, clock=lambda: T,
                          stub_seeds=StubSeeds(mongo_collections={"twin_state": docs}))


def _find_check(**params):
    return CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state", "params": {"rule": "exists", "field": "0.line", **params}})


async def test_mongo_find는_params의_필터로_문서를_좁힌다():
    docs = [{"line": 7, "state": "RUN"}, {"line": 8, "state": "STOP"}, {"line": 9, "state": "RUN"}]
    result = await PROBES["mongo_find"](_mongo_adapters(docs), _find_check(filter={"state": "STOP"}),
                                        clock=lambda: T)
    assert result.status == "ok" and [d["line"] for d in result.data] == [8]


async def test_mongo_find는_정렬과_표본_상한을_따른다():
    docs = [{"line": i, "ts": i} for i in range(5)]
    check = _find_check(filter={}, sort=[["ts", -1]])
    check = check.model_copy(update={"sample": 2})
    result = await PROBES["mongo_find"](_mongo_adapters(docs), check, clock=lambda: T)
    assert [d["line"] for d in result.data] == [4, 3]


async def test_mongo_find의_필터는_연산자_허용_목록을_넘지_못한다():
    # `$where`는 서버측 JS 실행이다 — 읽기 전용이 메커니즘으로 남으려면 표현 불가능해야
    # 한다(규율 9). 어댑터와 스텁이 같은 판정 함수(filter_problems)를 쓴다.
    result = await PROBES["mongo_find"](_mongo_adapters([]),
                                        _find_check(filter={"$where": "sleep(1000)"}),
                                        clock=lambda: T)
    assert result.status == "error" and "$where" in (result.error or "")


async def test_mongo_find의_필터가_dict가_아니면_error다():
    result = await PROBES["mongo_find"](_mongo_adapters([]), _find_check(filter=[{"a": 1}]),
                                        clock=lambda: T)
    assert result.status == "error" and "filter" in (result.error or "")


async def test_mongo_find는_해석기_값을_필터에_합친다():
    # 값을 config에 적으면 즉시 썩는다(사업부마다 다르고 매일 바뀐다) — 값이 아니라
    # 값이 어디서 오는지를 선언한다. 리스트는 $in, 단일 값은 동등 비교다.
    docs = [{"part": "A", "n": 1}, {"part": "B", "n": 2}, {"part": "C", "n": 3}]
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state",
        "params": {"rule": "exists", "field": "0.part", "filter": {"n": {"$gt": 0}}},
        "resolve": {"part": {"from": "mongo", "collection": "parts", "field": "code"}}})
    adapters = build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=StubSeeds(
        mongo_collections={"twin_state": docs, "parts": [{"code": "A"}, {"code": "C"}]}))
    result = await PROBES["mongo_find"](adapters, check, clock=lambda: T)
    assert result.status == "ok" and [d["part"] for d in result.data] == ["A", "C"]


async def test_해석기가_값을_못_내면_질의하지_않는다():
    # 전부-또는-전무(§2-N3): 빈 필터로 전체 조회하면 "거짓 안심"이 된다.
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state", "params": {"rule": "exists", "field": "0.part"},
        "resolve": {"part": {"from": "mongo", "collection": "없는컬렉션", "field": "code"}}})
    result = await PROBES["mongo_find"](_mongo_adapters([{"part": "A"}]), check, clock=lambda: T)
    assert result.status == "error"


async def test_해석기_키가_필터_키와_겹치면_error다():
    # 어느 쪽이 이기는지 config만 봐서 알 수 없으면 사람이 값을 고쳤는데 안 바뀐다.
    # 해석기 자체는 **성공**해야 이 가드를 실제로 지난다(안 그러면 전부-또는-전무가 먼저 잡는다).
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state",
        "params": {"rule": "exists", "field": "0.part", "filter": {"part": "Z"}},
        "resolve": {"part": {"from": "mongo", "collection": "parts", "field": "code"}}})
    adapters = build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=StubSeeds(
        mongo_collections={"twin_state": [{"part": "A"}], "parts": [{"code": "A"}]}))
    result = await PROBES["mongo_find"](adapters, check, clock=lambda: T)
    assert result.status == "error" and "part" in (result.error or "")


async def test_정렬_형식이_틀리면_질의하지_않는다():
    result = await PROBES["mongo_find"](_mongo_adapters([{"line": 1}]),
                                        _find_check(sort=[["ts", "내림차순"]]), clock=lambda: T)
    assert result.status == "error" and "sort" in (result.error or "")


async def test_mongo_기본_프로브는_그대로_recent다():
    # 기존 점검은 한 글자도 안 바뀐다 — mongo_find는 probe로 명시할 때만 쓰인다.
    assert resolve_probe(CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "mongo:twin_state",
        "params": {"rule": "exists", "field": "0.line"}})) == "mongo_recent"


async def test_해석기_표본이_잘리면_증거가_불완전이다():
    # 검증 리뷰 B1: 잘린 표본으로 좁힌 질의의 결과가 "완전한 증거"로 박제되면
    # verify의 "불완전 증거로 부정 결론 금지" 가드가 통째로 비껴간다.
    site = SiteConfig.model_validate({"target": {
        "mongo": {"url": "mongodb://x:27017"}, "guards": {"max_rows": 2}}})
    adapters = build_adapters(site, TOPO, clock=lambda: T, stub_seeds=StubSeeds(
        mongo_collections={"twin_state": [{"part": "A"}],
                           "parts": [{"code": c} for c in "ABCDE"]}))
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state", "params": {"rule": "exists", "field": "0.part"},
        "resolve": {"part": {"from": "mongo", "collection": "parts", "field": "code"}}})
    result = await PROBES["mongo_find"](adapters, check, clock=lambda: T)
    assert result.status == "ok"
    assert result.envelope.complete is False and result.envelope.truncated_reason


async def test_증거_출처가_무엇을_물었는지_담는다():
    # §2-N4: 응답만 보관하면 "0건"이 "현장이 멈췄다"인지 "질문을 잘못했다"인지 모른다.
    # 같은 컬렉션에 다른 필터를 내는 두 점검이 같은 출처를 가지면 안 된다.
    a = await PROBES["mongo_find"](_mongo_adapters([]), _find_check(filter={"state": "STOP"}),
                                   clock=lambda: T)
    b = await PROBES["mongo_find"](_mongo_adapters([]), _find_check(filter={"state": "RUN"}),
                                   clock=lambda: T)
    assert a.source and a.source.startswith("mongo:twin_state#") and a.source != b.source
    same = await PROBES["mongo_find"](_mongo_adapters([]), _find_check(filter={"state": "STOP"}),
                                      clock=lambda: T)
    assert a.source == same.source          # 같은 질문이면 같은 출처로 모인다


async def test_일부러_전체를_본_것은_출처에_남는다():
    # "해석이 실패해 우연히 전체를 봤다"와 "일부러 전체를 봤다"를 코드가 구별한다.
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state", "params": {"rule": "exists", "field": "0.part"},
        "resolve": {"part": {"from": "unfiltered"}}})
    result = await PROBES["mongo_find"](_mongo_adapters([{"part": "A"}]), check, clock=lambda: T)
    assert result.status == "ok" and "unfiltered:part" in (result.source or "")


async def test_정렬_방향은_정수_1_또는_마이너스1이어야_한다():
    # 검증 리뷰 M3: `-1.0`은 JSON에 자연스럽고 파이썬 동등 비교를 통과하지만
    # pymongo가 TypeError를 낸다 — 기동은 통과하고 매 순찰이 실패한다.
    result = await PROBES["mongo_find"](_mongo_adapters([]), _find_check(sort=[["ts", -1.0]]),
                                        clock=lambda: T)
    assert result.status == "error" and "sort" in (result.error or "")
    from src.patrol.probes import mongo_find_problems
    assert mongo_find_problems({"sort": [["ts", True]]}, {})


async def test_겹침_판정은_선언된_해석기_전체를_본다():
    # 검증 리뷰 N7: `resolved.params`로 보면 unfiltered 키가 빠져 기동 검증과 갈린다.
    from src.patrol.probes import mongo_find_problems
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state",
        "params": {"rule": "exists", "field": "0.part", "filter": {"part": "Z"}},
        "resolve": {"part": {"from": "unfiltered"}}})
    result = await PROBES["mongo_find"](_mongo_adapters([]), check, clock=lambda: T)
    assert result.status == "error" and "part" in (result.error or "")
    # 기동 검증도 같은 판정을 한다 — 갈리면 "기동은 통과했는데 매 순찰 error"가 된다.
    assert any("part" in p for p in mongo_find_problems(check.params, check.resolve))


async def test_두_절단이_함께_나면_이유가_둘_다_남는다():
    # 검증 리뷰 N12: 덮어쓰면 두 절단 중 하나가 증거에서 사라진다(rest_query와 같은 규약).
    from src.domain.envelope import Envelope, ProbeResult
    adapters = build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=StubSeeds(
        mongo_collections={"parts": [{"code": "A"}, {"code": "B"}]}))
    real_find = adapters.mongo.find

    async def truncated_find(collection, filter, *, sort=None, limit=None):
        if collection == "parts":
            return await real_find(collection, filter, sort=sort, limit=limit)
        return ProbeResult(status="ok", data=[],
                           envelope=Envelope(observed_at=T, complete=False,
                                             truncated_reason="max_rows"))
    adapters.mongo.find = truncated_find
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "probe": "mongo_find",
        "target": "mongo:twin_state", "params": {"rule": "exists", "field": "0.part"},
        "resolve": {"part": {"from": "mongo", "collection": "parts", "field": "code",
                             "cardinality": "first:1"}}})
    result = await PROBES["mongo_find"](adapters, check, clock=lambda: T)
    reason = result.envelope.truncated_reason or ""
    assert "max_rows" in reason and "part" in reason
