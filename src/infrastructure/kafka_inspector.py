"""Kafka 실구현 — aiokafka. group_id=None + assign()로 컨슈머 그룹 미참여, 커밋 계열 호출 없음.

read: partitions_for_topic → assign() → 모든 파티션에 대해 beginning_offsets와
offsets_for_times를 함께 조회한다. aiokafka의 offsets_for_times는 "start 이후
메시지가 없을 때"(빈 파티션·미래 시각)만 None을 준다 — start가 보존 범위보다
오래됐으면 None이 아니라 earliest 오프셋과 그 오프셋의(요청보다 나중인) 실제
타임스탬프를 정상 반환한다. 그래서 폴백 여부는 None 체크가 아니라
`resolved[tp].offset == beginning_offset(tp) and resolved[tp].timestamp > start_ms`
로 판정하고, 봉투 effective_as_of는 폴백 파티션들의 resolved 타임스탬프 중
최댓값(가장 늦은 달성-시작)을 kafka_effective_start에 넘겨 명시한다(조용한 폴백
금지, §4.2) — 파티션마다 폴백 폭이 다르면 전체 결과 집합이 빠짐없이 완전한
시점은 그 중 가장 늦은 파티션이 시작되는 때부터다. min을 쓰면 아직 데이터가
없는 파티션의 구간을 "이미 완전하다"고 축소 보고하게 된다(재리뷰에서 2-파티션
픽스처로 실증).
수집 루프는 파티션별로 end 이후 레코드를 처음 본 순간 그 파티션을 완료 처리해
폴링 대상에서 뺀다 — 그래야 라이브 토픽에서도(계속 새 레코드가 들어와
empty_polls가 리셋되는 상황) 전 파티션이 끝났을 때 확정적으로 종료한다.
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

                # 폴백 판정에 전 파티션의 beginning_offsets가 필요하다 — offsets_for_times가
                # None을 주지 않고도(보존 밖) earliest로 조용히 폴백할 수 있어서(위 독스트링).
                beginnings = await consumer.beginning_offsets(tps)
                resolved = await consumer.offsets_for_times({tp: start_ms for tp in tps})

                fallback_tps = [
                    tp for tp in tps
                    if resolved.get(tp) is not None
                    and resolved[tp].offset == beginnings.get(tp)
                    and resolved[tp].timestamp is not None
                    and resolved[tp].timestamp > start_ms]

                for tp in tps:
                    hit = resolved.get(tp)
                    consumer.seek(tp, hit.offset if hit is not None else beginnings[tp])

                cap = self._guards.max_rows
                records, empty_polls = [], 0
                tps_pending = set(tps)          # end 이후를 본 파티션은 여기서 빠진다
                while tps_pending and len(records) <= cap and empty_polls < _MAX_EMPTY_POLLS:
                    batch = await consumer.getmany(
                        *tps_pending, timeout_ms=_POLL_TIMEOUT_MS,
                        max_records=cap + 1 - len(records))
                    fetched = [rec for recs in batch.values() for rec in recs]
                    if not fetched:
                        empty_polls += 1
                        continue
                    empty_polls = 0
                    for tp, recs in batch.items():
                        for rec in recs:
                            if rec.timestamp is None or rec.timestamp < end_ms:
                                records.append(rec)
                            else:
                                # end 이후 레코드를 이 파티션에서 처음 본 순간 완료 —
                                # 계속 폴링하면 라이브 토픽에서 empty_polls가 매번
                                # 리셋되어 수집 루프가 끝나지 않는다.
                                tps_pending.discard(tp)
                                break
                    if len(records) > cap:      # max_rows 도달 시 즉시 종료
                        break

                records.sort(key=lambda r: (r.timestamp or 0, r.partition, r.offset))
                truncated = len(records) > cap
                data = [{"topic": r.topic, "partition": r.partition, "offset": r.offset,
                        "timestamp": r.timestamp, "key": r.key, "value": r.value}
                       for r in records[:cap]]

                effective_as_of = None
                if fallback_tps:
                    # 파티션별 폴백 시작이 다를 수 있어 max — 결과 집합 전체가 빠짐없이
                    # 완전한 시점은 가장 늦게 시작되는 파티션부터다(위 독스트링).
                    coverage_start_ts = max(resolved[tp].timestamp for tp in fallback_tps)
                    effective_as_of, _ = kafka_effective_start(start, None, coverage_start_ts)

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
