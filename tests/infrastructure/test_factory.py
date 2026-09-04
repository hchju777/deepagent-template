from datetime import datetime, timezone

from src.config.schema_site import SiteConfig
from src.infrastructure.factory import StubSeeds, build_adapters
from src.infrastructure.stubs import StubMongo, StubRedis
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CLOCK = lambda: T

TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/api/v1/lines/{line}/oee"}]}},
    "derivations": {}})

SITE = SiteConfig.model_validate({
    "target": {"redis": {"url": "redis://x:6379"},
               "mongo": {"url": "mongodb://x:27017"},
               "guards": {"max_concurrent": 2}}})     # adapters 기본값 = stub


async def test_stub_모드_조립과_시드_주입():
    seeds = StubSeeds(redis_data={"plan:7": "480"},
                      mongo_collections={"twin_state": [{"line": 7}]})
    adapters = build_adapters(SITE, TOPO, clock=CLOCK, stub_seeds=seeds)
    assert isinstance(adapters.redis, StubRedis)
    assert isinstance(adapters.mongo, StubMongo)
    assert adapters.kafka is None                     # config에 kafka 없음 → None
    assert adapters.code is None                      # config에 code 없음 → None
    assert (await adapters.redis.get("plan:7")).data == "480"
    assert adapters.semaphore._value == 2             # guards.max_concurrent


async def test_rest_allowlist는_토폴로지에서_온다():
    site = SiteConfig.model_validate({
        "target": {"rest": {"base_url": "http://x"}}})
    seeds = StubSeeds(rest_responses={"/api/v1/lines/7/oee": {"oee": 0.9}})
    adapters = build_adapters(site, TOPO, clock=CLOCK, stub_seeds=seeds)
    assert (await adapters.rest.get("/api/v1/lines/7/oee")).status == "ok"
    assert (await adapters.rest.get("/admin")).status == "error"


def test_시드_파일의_알_수_없는_키는_예외가_아니라_문제가_된다(tmp_path):
    # load_stub_seeds가 StubSeeds(**spec)로 잇는다 — 이름으로만 묶여 있어서 오타
    # 하나가 TypeError로 조립 도중에 튀면 BootError가 아니라 스택트레이스로 죽는다.
    import json
    from src.patrol.daemon import load_stub_seeds
    path = tmp_path / "seeds.json"
    path.write_text(json.dumps({"mx/gumi": {"rest_responses": {}, "오타": 1}}),
                    encoding="utf-8")
    seeds, problems = load_stub_seeds(path)
    assert seeds == {} and any("오타" in p for p in problems)


def test_시드_파일이_깨져도_raise하지_않는다(tmp_path):
    from src.patrol.daemon import load_stub_seeds
    for content in ("{ 망가진 json", "[]", '{"mx/gumi": 3}'):
        path = tmp_path / "seeds.json"
        path.write_text(content, encoding="utf-8")
        seeds, problems = load_stub_seeds(path)
        assert seeds == {} and problems
    seeds, problems = load_stub_seeds(tmp_path / "없는파일.json")
    assert seeds == {} and problems


def test_config의_명세_경로가_어댑터까지_간다():
    # 스키마 검증자와 어댑터 저장은 각각 테스트가 있는데 둘을 잇는 줄만 없었다 —
    # "함수는 되는데 호출부가 안 넘긴다"의 교과서적 형태.
    from src.config.schema_site import SiteConfig
    cfg = SiteConfig.model_validate({"target": {
        "adapters": "real",
        "rest": {"base_url": "http://x", "openapi_path": "/v3/api-docs"}}})
    adapters = build_adapters(cfg, TOPO, clock=CLOCK)
    assert adapters.rest._openapi_path == "/v3/api-docs"
