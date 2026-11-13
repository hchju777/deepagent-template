"""**실제 시스템에 붙는** 테스트 — 기본 실행에서는 빠진다.

```bash
# 사내에서
export OPS_TEST_REDIS_URL=redis://gumi-redis:6379
export OPS_TEST_REDIS_PASSWORD=...
pytest tests/live -m live -v
```

오프라인 테스트(`tests/infrastructure/`)가 잠그는 것은 **로직**이다 — 필터를
막는가, 잘림을 아는가, 파라미터를 거부하는가. 여기 있는 것은 그것으로 절대 알 수
없는 것들이다: 진짜로 붙는가, 인증이 통하는가, 그리고 **우리가 읽은 것이 실제로
거기 있는 값인가.**

세 번째가 제일 중요하다. 스텁은 우리가 넣은 것을 우리가 꺼내므로 항상 맞는다.
"""
import json
import os

import pytest

from src.config.schema_site import KafkaConsumerConfig, MongoConfig, RedisConfig
from src.infrastructure.kafka_inspector import RealKafkaInspector
from src.infrastructure.mongo_reader import RealMongoReader
from src.infrastructure.redis_reader import RealRedisReader

pytestmark = pytest.mark.live


# ── Redis ────────────────────────────────────────────────────────────

@pytest.fixture
def redis_reader(clock):
    url = os.environ.get("OPS_TEST_REDIS_URL")
    if not url:
        pytest.skip("OPS_TEST_REDIS_URL이 없다")
    cfg = RedisConfig(url=url, db=int(os.environ.get("OPS_TEST_REDIS_DB", 0)),
                      password=os.environ.get("OPS_TEST_REDIS_PASSWORD") or None)
    return RealRedisReader(cfg, clock=clock)


async def test_redis에_실제로_붙는다(redis_reader):
    result = await redis_reader.scan("*")
    assert result.status == "ok", result.error
    await redis_reader.close()


async def test_없는_키는_오류가_아니라_None이다(redis_reader):
    # 스텁과 같은 계약인지 실물로 확인한다. 여기가 갈라지면
    # "테스트는 통과하는데 사내에서 깨진다"가 된다.
    result = await redis_reader.get("ops-agent:존재하지-않는-키")
    assert result.status == "ok" and result.data is None
    await redis_reader.close()


async def test_없는_키의_ttl은_minus_2다(redis_reader):
    result = await redis_reader.ttl("ops-agent:존재하지-않는-키")
    assert result.data == -2, "Redis 규약(-2=키 없음)을 정규화하면 사실이 하나 사라진다"
    await redis_reader.close()


# ── Mongo ────────────────────────────────────────────────────────────

@pytest.fixture
def mongo_reader(clock):
    url = os.environ.get("OPS_TEST_MONGO_URL")
    if not url:
        pytest.skip("OPS_TEST_MONGO_URL이 없다")
    cfg = MongoConfig(url=url, database=os.environ.get("OPS_TEST_MONGO_DB", "data"),
                      user=os.environ.get("OPS_TEST_MONGO_USER") or None,
                      password=os.environ.get("OPS_TEST_MONGO_PASSWORD") or None,
                      auth_source=os.environ.get("OPS_TEST_MONGO_AUTH_SOURCE", "admin"))
    return RealMongoReader(cfg, clock=clock)


async def test_mongo에_인증하고_붙는다(mongo_reader):
    result = await mongo_reader.count(os.environ.get("OPS_TEST_MONGO_COLLECTION", "__none__"), {})
    assert result.status == "ok", result.error
    await mongo_reader.close()


async def test_읽기_전용_계정이면_쓰기가_거부된다(mongo_reader):
    """이 테스트는 우리 코드가 아니라 **계정 권한**을 확인한다.

    포트에 쓰기 메서드가 없으니 코드로는 못 쓴다. 그래도 계정 자체가 읽기
    전용인지는 별개의 방어층이고, 그건 우리가 아니라 DBA가 정한 것이므로
    실물로 확인할 가치가 있다.
    """
    collection = os.environ.get("OPS_TEST_MONGO_COLLECTION")
    if not collection:
        pytest.skip("OPS_TEST_MONGO_COLLECTION이 없다")
    db = mongo_reader._database()                      # noqa: SLF001 — 의도적 침범
    with pytest.raises(Exception) as caught:
        await db[collection].insert_one({"_ops_agent_write_probe": True})
    assert "not authorized" in str(caught.value).lower() or "unauthorized" in str(caught.value).lower()
    await mongo_reader.close()


# ── Kafka ────────────────────────────────────────────────────────────

@pytest.fixture
def kafka_inspector(clock):
    brokers = os.environ.get("OPS_TEST_KAFKA_BROKERS")
    topic = os.environ.get("OPS_TEST_KAFKA_TOPIC")
    if not (brokers and topic):
        pytest.skip("OPS_TEST_KAFKA_BROKERS / OPS_TEST_KAFKA_TOPIC이 없다")
    # `.get(key, "{}") or 기본값`으로 쓰면 "{}"가 truthy라 기본값이 안 먹는다 —
    # 실제로 여기서 한 번 틀렸다.
    topics = json.loads(os.environ.get("OPS_TEST_KAFKA_TOPICS") or json.dumps({"topic1": topic}))
    cfg = KafkaConsumerConfig(
        bootstrap_server=brokers.split(","),
        group_ids=[g for g in os.environ.get("OPS_TEST_KAFKA_GROUP", "").split(",") if g],
        topic=topics)
    return RealKafkaInspector(cfg, clock=clock)


async def test_토픽_끝에서_읽는다(kafka_inspector):
    topic = os.environ["OPS_TEST_KAFKA_TOPIC"]
    result = await kafka_inspector.tail(topic, limit=5)
    assert result.status == "ok", result.error
    assert "messages" in result.data


async def test_그룹_오프셋을_밖에서_조회한다(kafka_inspector):
    result = await kafka_inspector.group_offsets(
        os.environ.get("OPS_TEST_KAFKA_GROUP", "ops-agent-none"))
    assert result.status == "ok", result.error


async def test_읽어도_컨슈머_그룹이_생기지_않는다(kafka_inspector):
    """이 시스템의 제일 중요한 약속을 실물로 확인한다.

    `assign()`으로 읽으면 그룹이 만들어지지 않는다. `subscribe(group_id=...)`였다면
    여기서 우리 그룹이 목록에 나타나고, 그건 대상 클러스터에 대한 쓰기다.
    """
    from aiokafka.admin import AIOKafkaAdminClient

    topic = os.environ["OPS_TEST_KAFKA_TOPIC"]
    admin = AIOKafkaAdminClient(bootstrap_servers=kafka_inspector._brokers)   # noqa: SLF001
    await admin.start()
    try:
        before = {g[0] for g in await admin.list_consumer_groups()}
        await kafka_inspector.tail(topic, limit=5)
        after = {g[0] for g in await admin.list_consumer_groups()}
    finally:
        await admin.close()
    assert after == before, f"읽기만 했는데 컨슈머 그룹이 생겼다 — {after - before}"
