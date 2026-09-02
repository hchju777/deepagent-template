"""라이브러리 표면만 흉내 낸 페이크로 실구현의 API 사용을 검증한다(실 백엔드 없이).

스모크 테스트는 포트 구현 여부만 보므로 이 부류의 버그(await 누락, 메타데이터 미페치)를
못 잡는다 — 여기서는 pymongo/aiokafka의 async 표면 모양만 흉내 낸 페이크를 주입해
어댑터가 그 표면을 올바르게 호출하는지(await할 곳을 await하는지, 메타데이터를 먼저
페치하는지)를 검증한다.
"""
import asyncio
from datetime import datetime, timezone

from src.infrastructure.kafka_inspector import RealKafka
from src.infrastructure.mongo_reader import RealMongo

T = datetime(2026, 9, 2, tzinfo=timezone.utc)
CLOCK = lambda: T


class _Guards:
    timeout_s = 1
    max_rows = 10


# ---- Mongo: aggregate()는 코루틴을 반환하므로 await 없이 async for 하면 TypeError ----

class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for doc in self._docs:
            yield doc


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    async def aggregate(self, pipeline):     # pymongo AsyncCollection.aggregate처럼 코루틴
        return _FakeCursor(self._docs)


class _FakeDB:
    def __init__(self, docs):
        self._docs = docs

    def __getitem__(self, name):
        return _FakeCollection(self._docs)


async def test_mongo_aggregate는_커서를_await해서_얻는다():
    mongo = RealMongo("mongodb://localhost:1", db="test",
                      guards=_Guards(), semaphore=asyncio.Semaphore(1), clock=CLOCK)
    mongo._db = _FakeDB([{"i": 1}, {"i": 2}])   # 실 연결 없이 표면만 교체

    res = await mongo.aggregate("c", [{"$match": {}}])

    # await 누락 버그가 있으면 "어댑터 호출 예외 — TypeError: ..."로 잡혀 status=error가 된다.
    assert res.status == "ok"
    assert res.data == [{"i": 1}, {"i": 2}]


# ---- Kafka: topic을 안 넘긴 fresh consumer는 topics()로 메타데이터를 먼저 페치해야
#      partitions_for_topic이 파티션을 찾는다. 페치 전엔 None, 페치 후엔 채워진다. ----

class _FakeOffsetAndTimestamp:
    def __init__(self, offset, timestamp=None):
        self.offset = offset
        self.timestamp = timestamp


class _FakeRecord:
    def __init__(self, topic, partition, offset, timestamp, key, value):
        self.topic, self.partition, self.offset = topic, partition, offset
        self.timestamp, self.key, self.value = timestamp, key, value


class _FakeConsumer:
    def __init__(self, *, bootstrap_servers, group_id, enable_auto_commit):
        assert group_id is None, "consumer group에 참여하면 안 된다"
        assert enable_auto_commit is False, "커밋 계열 설정이 있으면 안 된다"
        self._topics_fetched = False
        self._getmany_calls = 0

    async def start(self):
        pass

    async def topics(self):
        self._topics_fetched = True   # 전체 메타데이터 페치가 일어났음을 기록
        return {"edge.raw.7"}

    def partitions_for_topic(self, topic):
        # topics()를 먼저 부르지 않으면(버그가 있으면) 여기서 항상 None을 돌려준다.
        return {0} if self._topics_fetched else None

    def assign(self, partitions):
        self._assigned = list(partitions)

    async def offsets_for_times(self, timestamps):
        return {tp: _FakeOffsetAndTimestamp(offset=0) for tp in timestamps}

    async def beginning_offsets(self, partitions):
        return dict.fromkeys(partitions, 0)

    def seek(self, tp, offset):
        pass

    async def getmany(self, *partitions, timeout_ms=0, max_records=None):
        self._getmany_calls += 1
        if self._getmany_calls == 1:
            tp = partitions[0]
            ts = int(T.timestamp() * 1000)
            rec = _FakeRecord(tp.topic, tp.partition, 0, ts, None, {"n": 1})
            return {tp: [rec]}
        return {}       # 이후 호출은 빈 배치 — 수집 루프를 종료시킨다

    async def stop(self):
        pass


async def test_kafka_read는_topics를_먼저_페치해서_파티션을_얻는다(monkeypatch):
    monkeypatch.setattr("src.infrastructure.kafka_inspector.AIOKafkaConsumer", _FakeConsumer)
    kafka = RealKafka("localhost:9092", guards=_Guards(),
                      semaphore=asyncio.Semaphore(1), clock=CLOCK)

    res = await kafka.read("edge.raw.7", start=T, end=datetime(2026, 9, 3, tzinfo=timezone.utc))

    # topics()를 안 부르면 partitions_for_topic이 항상 None이라 빈 ok로 끝난다(가짜 음성).
    assert res.status == "ok"
    assert len(res.data) == 1
    assert res.data[0]["value"] == {"n": 1}


async def test_kafka_read는_메타데이터_없는_토픽을_error로_구분한다(monkeypatch):
    class _NoSuchTopicConsumer(_FakeConsumer):
        def partitions_for_topic(self, topic):
            return None   # topics() 이후에도 계속 없음 — 진짜 존재하지 않는 토픽

    monkeypatch.setattr("src.infrastructure.kafka_inspector.AIOKafkaConsumer", _NoSuchTopicConsumer)
    kafka = RealKafka("localhost:9092", guards=_Guards(),
                      semaphore=asyncio.Semaphore(1), clock=CLOCK)

    res = await kafka.read("ghost.topic", start=T, end=datetime(2026, 9, 3, tzinfo=timezone.utc))

    assert res.status == "error"
    assert "토픽 메타데이터 없음" in res.error
