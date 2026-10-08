"""Kafka 리더 — 소켓 없이 가짜 `aiokafka`로 **tail의 모으는 규칙**만 본다. 실물 접속은 `tests/live`."""
import sys
import types
from datetime import datetime, timezone

import pytest

from src.config.schema_site import KafkaConsumerConfig
from src.infrastructure.kafka_inspector import RealKafkaInspector

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


class _TP:
    def __init__(self, topic, partition):
        self.topic, self.partition = topic, partition

    def __hash__(self):
        return hash((self.topic, self.partition))

    def __eq__(self, other):
        return (self.topic, self.partition) == (other.topic, other.partition)


class _Record:
    def __init__(self, partition, offset):
        self.partition, self.offset, self.timestamp = partition, offset, 1000 + offset
        self.key, self.value = None, b'{"n": %d}' % offset


class _Consumer:
    """`getmany`가 호출마다 **두 건만** 돌려준다 — 실제 aiokafka처럼 레코드가 조금이라도 있으면 바로 돌아온다."""
    calls = 0

    def __init__(self, ends, **kwargs):
        self._ends, self._pos = ends, {}

    async def start(self): pass
    async def stop(self): pass
    def assign(self, tps): pass
    async def end_offsets(self, tps): return {tp: self._ends[tp.partition] for tp in tps}
    async def beginning_offsets(self, tps): return {tp: 0 for tp in tps}
    def seek(self, tp, offset): self._pos[tp.partition] = offset

    async def getmany(self, timeout_ms, max_records):
        _Consumer.calls += 1
        out, budget = {}, min(2, max_records)
        for p, pos in sorted(self._pos.items()):
            while budget and pos < self._ends[p]:
                out.setdefault(p, []).append(_Record(p, pos))
                pos += 1
                budget -= 1
            self._pos[p] = pos
        return out


def _inspector(monkeypatch, ends: dict, partitions: list):
    fake = types.ModuleType("aiokafka")
    fake.TopicPartition = _TP
    fake.AIOKafkaConsumer = lambda **kw: _Consumer(ends, **kw)
    monkeypatch.setitem(sys.modules, "aiokafka", fake)
    inspector = RealKafkaInspector(KafkaConsumerConfig(bootstrap_server=["broker.example:9092"], topic={"t": "t"}), clock=lambda: T0)

    async def partitions_of(topic):
        return list(partitions)
    monkeypatch.setattr(inspector, "_partitions_of", partitions_of)
    _Consumer.calls = 0
    return inspector


async def test_tail은_limit을_채울_때까지_getmany를_돈다(monkeypatch):
    """사내 10-08: `--limit 300`이 2건만 돌려줬다. `getmany` 한 번은 첫 배치일 뿐이다."""
    got = await _inspector(monkeypatch, {0: 100, 1: 100}, [0, 1]).tail("t", limit=50)
    assert got.status == "ok", got.error
    assert len(got.data["messages"]) == 50 and _Consumer.calls > 1
    offsets = [(m["partition"], m["offset"]) for m in got.data["messages"]]
    assert offsets == sorted(offsets) and offsets[0] == (0, 75) and offsets[-1] == (1, 99)   # 끝에서 25건씩
    assert got.envelope.truncated_reason == "끝에서 50건만"


async def test_tail은_더_안_오면_멈추고_모자라면_그렇다고_적지_않는다(monkeypatch):
    """토픽에 3건뿐이면 3건이 전부다 — 완전한 결과다. 시간이 다해 모자란 것과 다르다."""
    got = await _inspector(monkeypatch, {0: 2, 1: 1}, [0, 1]).tail("t", limit=50)
    assert [m["offset"] for m in got.data["messages"]] == [0, 1, 0] and got.envelope.complete
