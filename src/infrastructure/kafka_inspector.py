"""Kafka 실구현 — aiokafka. group_id=None + assign()로 컨슈머 그룹 미참여, 커밋 계열 호출 없음.

read: partitions_for_topic → assign() → offsets_for_times로 시작 오프셋을 찾고,
결과가 None인 파티션은 beginning_offsets로 seek한 뒤 실제 수집된 첫 메시지 시각을
kafka_effective_start에 넘겨 폴백 여부를 봉투에 명시한다.
group_offsets: AIOKafkaAdminClient로 다른 그룹의 커밋 오프셋을 "읽기만" 하고,
end_offsets는 group_id=None 컨슈머로 조회한다 — 어느 쪽도 커밋하지 않는다.
"""
from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient

from src.domain.envelope import Envelope
from src.domain.ports import KafkaInspectorPort
from src.infrastructure.guards import guarded_call
from src.infrastructure.query_rules import kafka_effective_start

_POLL_TIMEOUT_MS = 1000
_MAX_EMPTY_POLLS = 3


class RealKafka(KafkaInspectorPort):
    def __init__(self, bootstrap, *, guards, semaphore, clock):
        self._bootstrap = bootstrap
        self._guards, self._sem, self._clock = guards, semaphore, clock

    def _call(self, op):
        return guarded_call(op, timeout_s=self._guards.timeout_s,
                            semaphore=self._sem, clock=self._clock)

    async def read(self, topic, *, start, end):
        async def op():
            consumer = AIOKafkaConsumer(bootstrap_servers=self._bootstrap,
                                        group_id=None, enable_auto_commit=False)
            await consumer.start()
            try:
                # 생성자에 topic을 안 넘긴 fresh consumer는 bootstrap이 빈 MetadataRequest를
                # 보내 per-topic 메타데이터가 없다 — topics()로 전체 클러스터 메타데이터를
                # 강제로 페치해야 partitions_for_topic이 로컬 캐시를 찾는다.
                # (생성자에 topic을 넘기면 auto-subscribe가 이후 수동 assign()과 충돌한다.)
                await consumer.topics()
                partitions = consumer.partitions_for_topic(topic)
                if not partitions:
                    raise RuntimeError(
                        f"토픽 메타데이터 없음 — {topic!r}는 브로커에 존재하지 않거나 조회할 수 없다")

                tps = sorted(TopicPartition(topic, p) for p in partitions)
                consumer.assign(tps)

                start_ms = int(start.timestamp() * 1000)
                end_ms = int(end.timestamp() * 1000)
                resolved = await consumer.offsets_for_times({tp: start_ms for tp in tps})
                fallback_tps = [tp for tp in tps if resolved.get(tp) is None]
                beginnings = (await consumer.beginning_offsets(fallback_tps)
                             if fallback_tps else {})
                for tp in tps:
                    hit = resolved.get(tp)
                    consumer.seek(tp, hit.offset if hit is not None else beginnings[tp])

                cap = self._guards.max_rows
                records, empty_polls = [], 0
                while len(records) <= cap and empty_polls < _MAX_EMPTY_POLLS:
                    batch = await consumer.getmany(
                        *tps, timeout_ms=_POLL_TIMEOUT_MS, max_records=cap + 1 - len(records))
                    fetched = [rec for recs in batch.values() for rec in recs]
                    if not fetched:
                        empty_polls += 1
                        continue
                    empty_polls = 0
                    records.extend(rec for rec in fetched
                                   if rec.timestamp is None or rec.timestamp < end_ms)

                records.sort(key=lambda r: (r.timestamp or 0, r.partition, r.offset))
                truncated = len(records) > cap
                data = [{"topic": r.topic, "partition": r.partition, "offset": r.offset,
                        "timestamp": r.timestamp, "key": r.key, "value": r.value}
                       for r in records[:cap]]

                effective_as_of = None
                if fallback_tps:
                    fallback_parts = {tp.partition for tp in fallback_tps}
                    earliest_candidates = [r["timestamp"] for r in data
                                           if r["partition"] in fallback_parts
                                           and r["timestamp"] is not None]
                    earliest_ts = max(earliest_candidates) if earliest_candidates else None
                    effective_as_of, _ = kafka_effective_start(start, None, earliest_ts)

                env = Envelope(observed_at=self._clock(), complete=not truncated,
                               truncated_reason="max_rows" if truncated else None,
                               requested_as_of=start, effective_as_of=effective_as_of)
                return data, env
            finally:
                await consumer.stop()
        return await self._call(op)

    async def group_offsets(self, group):
        async def op():
            admin = AIOKafkaAdminClient(bootstrap_servers=self._bootstrap)
            await admin.start()
            try:
                committed = await admin.list_consumer_group_offsets(group)
            finally:
                await admin.close()

            if not committed:
                return {}, Envelope(observed_at=self._clock())

            tps = list(committed.keys())
            consumer = AIOKafkaConsumer(bootstrap_servers=self._bootstrap,
                                        group_id=None, enable_auto_commit=False)
            await consumer.start()
            try:
                ends = await consumer.end_offsets(tps)
            finally:
                await consumer.stop()

            data = {}
            for tp in tps:
                committed_offset = committed[tp].offset
                end_offset = ends.get(tp, 0)
                lag = (max(end_offset - committed_offset, 0)
                      if committed_offset is not None and committed_offset >= 0 else None)
                data[f"{tp.topic}-{tp.partition}"] = {
                    "committed": committed_offset, "end": end_offset, "lag": lag}
            return data, Envelope(observed_at=self._clock())
        return await self._call(op)
