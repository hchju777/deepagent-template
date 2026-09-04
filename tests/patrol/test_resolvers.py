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


async def test_중복은_제거되고_결과는_정렬된다():
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": [{"line_code": "L2"}, {"line_code": "L1"},
                                          {"line_code": "L2"}]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=adapters, clock=lambda: T)
    assert out.params["line"] == ["L1", "L2"]   # 정렬 — 문서 순서에 안 흔들린다


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
    assert out.params == {}                       # 하나라도 실패하면 전부 버린다


async def test_시계_해석기는_사이트_시간대로_날짜를_만든다():
    # 스케줄러는 app.timezone(예: Asia/Seoul)으로 도는데 clock은 UTC다.
    # 그대로 두면 00:00~09:00 KST 사이에 today가 항상 어제가 된다 —
    # cron "0 8 * * *"로 등재하면 매일 100% 전날 날짜로 대상을 호출한다.
    kst_morning = datetime(2026, 9, 3, 23, 30, tzinfo=timezone.utc)   # 09-04 08:30 KST
    out = await resolve_params(_specs(d={"from": "clock", "expr": "today"}),
                               adapters=_adapters(), clock=lambda: kst_morning,
                               timezone_name="Asia/Seoul")
    assert out.params == {"d": "2026-09-04"}
    utc = await resolve_params(_specs(d={"from": "clock", "expr": "today"}),
                               adapters=_adapters(), clock=lambda: kst_morning,
                               timezone_name="UTC")
    assert utc.params == {"d": "2026-09-03"}


async def test_알_수_없는_시간대는_문제로_보고하고_raise하지_않는다():
    out = await resolve_params(_specs(d={"from": "clock", "expr": "today"}),
                               adapters=_adapters(), clock=lambda: T,
                               timezone_name="없는/지역")
    assert out.params == {} and out.problems


async def test_소스가_max_rows로_잘렸으면_그_사실이_전파된다():
    # guards.max_rows가 5,000개를 1,000개로 자른 사실을 버리면, 잘린 표본으로
    # 물어본 결과가 "완전한 증거"로 박제된다 — 이 계획이 막으려던 바로 그 형태다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": [{"line_code": f"L{i}"} for i in range(10)]},
                               max_rows=3, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=adapters, clock=lambda: T)
    assert out.params["line"] == ["L0", "L1", "L2"]
    assert any("max_rows" in t for t in out.truncated), out.truncated


async def test_해석_실패가_있으면_부분_결과를_돌려주지_않는다():
    # problems가 있는데 params가 채워져 있으면, 새 호출부가 그것을 그대로 쓸 수 있다.
    # 전부-또는-전무를 호출자 규율이 아니라 반환 타입이 지키게 한다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": []}, max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(a={"from": "mongo", "collection": "lines", "field": "x"},
               c={"from": "clock", "expr": "today"}),
        adapters=adapters, clock=lambda: T)
    assert out.problems and out.params == {}


async def test_0과_False는_다른_값으로_유지된다():
    # 집합 멤버십으로 중복을 제거하면 0과 False, 1과 True가 병합된다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"v": [{"x": 0}, {"x": False}, {"x": 1}, {"x": True}]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(k={"from": "mongo", "collection": "v", "field": "x"}),
        adapters=adapters, clock=lambda: T)
    # bool과 int가 각자 살아남는다(정렬은 타입명 우선이라 bool이 앞선다)
    assert sorted(map(repr, out.params["k"])) == ["0", "1", "False", "True"]


async def test_해석_결과는_문서_순서에_흔들리지_않는다():
    # Mongo find는 자연 순서(불안정)다. 그대로 물려받으면 매 실행 다른 표본을
    # 점검하고, 같은 질문이 여러 증거로 흩어진다(출처 digest가 달라진다).
    a, b = _adapters(), _adapters()
    a.mongo = StubMongo({"lines": [{"c": "L1"}, {"c": "L2"}, {"c": "L3"}]},
                        max_rows=100, clock=lambda: T)
    b.mongo = StubMongo({"lines": [{"c": "L3"}, {"c": "L1"}, {"c": "L2"}]},
                        max_rows=100, clock=lambda: T)
    spec = _specs(k={"from": "mongo", "collection": "lines", "field": "c",
                     "cardinality": "first:2"})
    out_a = await resolve_params(spec, adapters=a, clock=lambda: T)
    out_b = await resolve_params(spec, adapters=b, clock=lambda: T)
    assert out_a.params["k"] == out_b.params["k"] == ["L1", "L2"]


async def test_숫자는_숫자로_정렬된다():
    # 결정론을 얻으려고 str(v)로 정렬하면 [2,10,1]이 [1,10,2]가 된다 —
    # "매번 같지만 틀린 표본"이고, 스키마 검증도 어댑터도 이걸 못 막는다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"v": [{"x": n} for n in [2, 10, 1, 20, 3]]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(k={"from": "mongo", "collection": "v", "field": "x",
                  "cardinality": "first:3"}),
        adapters=adapters, clock=lambda: T)
    assert out.params["k"] == [1, 2, 3]


async def test_타입이_섞여도_정렬이_raise하지_않는다():
    # 타입명으로 먼저 그룹을 가르면 그룹 안은 동종이라 자연 비교가 안전하다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"v": [{"x": 2}, {"x": "L1"}, {"x": 1}, {"x": "L0"}]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(_specs(k={"from": "mongo", "collection": "v", "field": "x"}),
                               adapters=adapters, clock=lambda: T)
    assert out.params["k"] == [1, 2, "L0", "L1"]


async def test_필드가_없는_행은_조용히_버리지_않는다():
    # "5,000개 중 50개만 봤다"를 truncated로 남기는 규율이, "500행 중 3행에만 그
    # 필드가 있었다"에는 안 걸리면 반쪽이다 — 뽑은 값 3개가 완전한 목록처럼 보인다.
    adapters = _adapters()
    adapters.mongo = StubMongo({"lines": [{"line_code": "L1"}, {"other": 1}, {}]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=adapters, clock=lambda: T)
    assert out.params == {"line": ["L1"]}
    assert any("3행 중 1행" in t and "line_code" in t for t in out.truncated), out.truncated


async def test_카디널리티_한도를_소스에_밀지_않는다():
    # 밀면 대상 부하는 줄지만 전체 개수를 잃어 "10개 중 3개만 봤다"를 못 적는다.
    # 정직성이 부하보다 비싸다는 판단을 배선으로 고정한다.
    seen = {}
    adapters = _adapters()
    stub = StubMongo({"lines": [{"line_code": f"L{i}"} for i in range(10)]},
                     max_rows=100, clock=lambda: T)
    original = stub.find

    async def spy(collection, filter, *, sort=None, limit=None):
        seen["limit"] = limit
        return await original(collection, filter, sort=sort, limit=limit)

    stub.find = spy
    adapters.mongo = stub
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code",
                     "cardinality": "first:3"}),
        adapters=adapters, clock=lambda: T)
    assert seen["limit"] is None
    assert any("10개 중 3개" in t for t in out.truncated), out.truncated
