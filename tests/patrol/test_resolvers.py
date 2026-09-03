import asyncio
from datetime import datetime, timezone

from src.config.schema_site import CheckConfig
from src.infrastructure.factory import AdapterSet
from src.infrastructure.stubs import StubMongo, StubRedis
from src.patrol.resolvers import resolve_params

T = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)


def _specs(**raw):
    return CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                       "resolve": raw}).resolve


def _adapters():
    return AdapterSet(semaphore=asyncio.Semaphore(1))


async def test_clock_해석기는_주입된_시계를_쓴다():
    out = await resolve_params(_specs(d={"from": "clock", "expr": "today"}),
                               adapters=_adapters(), clock=lambda: T)
    assert out.problems == [] and out.params == {"d": "2026-09-04"}


async def test_unfiltered는_키를_아예_생략한다():
    # 빈 리스트를 보내는 것과 키를 안 보내는 것은 대상에서 다르게 동작한다.
    out = await resolve_params(_specs(g={"from": "unfiltered"}),
                               adapters=_adapters(), clock=lambda: T)
    assert out.params == {} and out.omitted == ["g"] and out.problems == []


async def test_해석기가_비면_문제로_보고한다():
    # 빈 값을 그대로 보내면 대상에 따라 0/0/0(거짓 경보) 또는 전체 조회(거짓 안심)가
    # 된다. 어느 쪽인지 알 방법이 없으므로 구별이 필요 없는 규율을 세운다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": []}, max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=adapters, clock=lambda: T)
    assert out.params == {} and any("line" in p for p in out.problems)


async def test_어댑터가_없으면_문제로_보고하고_raise하지_않는다():
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=_adapters(), clock=lambda: T)
    assert out.params == {} and out.problems


async def test_카디널리티는_잘라내고_그_사실을_남긴다():
    # "5,000개 중 50개만 봤다"를 안 적으면 조용한 생략이다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": [{"line_code": f"L{i}"} for i in range(10)]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code",
                     "cardinality": "first:3"}),
        adapters=adapters, clock=lambda: T)
    assert out.params["line"] == ["L0", "L1", "L2"]
    assert any("line" in t and "10" in t for t in out.truncated)


async def test_중복은_등장_순서를_지키며_제거된다():
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": [{"line_code": "L2"}, {"line_code": "L1"},
                                          {"line_code": "L2"}]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=adapters, clock=lambda: T)
    assert out.params["line"] == ["L2", "L1"]


async def test_redis_해석기는_키_목록을_돌려준다():
    adapters = _adapters()
    adapters.redis = StubRedis({"plan:1": "a", "plan:2": "b"}, ttls={},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(_specs(k={"from": "redis", "pattern": "plan:*"}),
                               adapters=adapters, clock=lambda: T)
    assert sorted(out.params["k"]) == ["plan:1", "plan:2"]


async def test_한_해석기가_실패해도_나머지를_계속_보고_전부를_문제로_모은다():
    # 첫 실패에서 멈추면 사람이 한 번에 하나씩만 고치게 된다(기동 거부 철학과 같다).
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": []}, max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(a={"from": "mongo", "collection": "lines", "field": "x"},
               b={"from": "redis", "pattern": "p:*"},
               c={"from": "clock", "expr": "today"}),
        adapters=adapters, clock=lambda: T)
    assert len(out.problems) == 2                 # a는 빈 결과, b는 어댑터 없음
    assert out.params == {"c": "2026-09-04"}      # 성공한 것은 남는다(호출은 안 한다)
