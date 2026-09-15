"""Kafka 관찰 어댑터. **컨슈머 그룹에 참여하지 않는다.**

## 이 파일에서 제일 중요한 문장

`subscribe(group_id=...)`를 쓰지 않는다. 그룹에 참여하면 브로커가 리밸런스를
돌리고 `__consumer_offsets`에 커밋이 기록된다. 그건 읽기가 아니라 **쓰기**이고,
같은 group_id를 쓰는 실제 서비스가 있으면 우리가 그 서비스의 **파티션을 빼앗는다.**
모니터링하러 들어가서 대상을 멈추는 셈이다.

대신:
- lag은 `AIOKafkaAdminClient`로 **밖에서** 조회한다(그룹에 안 들어간다)
- 메시지는 `assign()`으로 파티션을 직접 지정해 읽는다(`group_id=None`,
  `enable_auto_commit=False` — 커밋할 그룹 자체가 없다)

## 빈 메타데이터 함정 (실제로 여기서 한 번 틀렸다)

`assign()`에 넘길 파티션 목록을 알아야 하는데, 컨슈머의 `partitions_for_topic()`은
**로컬 메타데이터 캐시**를 읽는다. 구독도 assign도 안 한 새 컨슈머는 그 캐시가
비어 있어 `None`을 돌려준다.

흔한 처방인 `await consumer.topics()`는 **듣지 않는다** — 그 메서드는 별도의
cluster 객체를 만들어 돌려줄 뿐 컨슈머 내부 캐시를 갱신하지 않는다. 처음에
그렇게 짰다가 실제 브로커에 붙여 보고 나서야 알았다(토픽에 메시지가 5건
있는데도 "토픽을 못 찾았다"가 나왔다).

그래서 파티션 목록은 **AdminClient의 `describe_topics`**로 얻는다. 공개 API이고,
"관리자에게 토폴로지를 묻고, 컨슈머는 지정된 파티션만 읽는다"는 역할 분리도 명확하다.
"""
from typing import Any

from src.config.schema_site import KafkaConsumerConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import KafkaInspectorPort

DEFAULT_TAIL = 10


class RealKafkaInspector(KafkaInspectorPort):
    def __init__(self, cfg: KafkaConsumerConfig, *, clock: Clock):
        self._cfg = cfg
        self._clock = clock

    @property
    def _brokers(self) -> list[str]:
        return list(self._cfg.bootstrap_server)

    def resolve_topic(self, name: str) -> str:
        """config의 논리 이름("topic1")을 실제 토픽 이름("GUMI_TOPIC")으로.

        이름이 목록에 없으면 그대로 쓴다 — 실제 토픽 이름을 직접 준 경우다.
        """
        return self._cfg.topic.get(name, name)

    async def group_offsets(self, group: str) -> ProbeResult:
        """**다른** 그룹의 커밋 오프셋과 lag. 우리가 그 그룹에 들어가는 게 아니다."""
        source = f"kafka:group_offsets {group}"
        admin = consumer = None
        try:
            from aiokafka.admin import AIOKafkaAdminClient
            from aiokafka import AIOKafkaConsumer

            admin = AIOKafkaAdminClient(bootstrap_servers=self._brokers)
            await admin.start()
            committed = await admin.list_consumer_group_offsets(group)
            if not committed:
                return ProbeResult.succeeded(
                    {"group": group, "partitions": [],
                     "note": "커밋된 오프셋이 없다 — 그룹이 없거나 아직 아무것도 안 읽었다"},
                    source=source, clock=self._clock)

            # end offset은 Admin이 아니라 컨슈머가 안다. group_id=None이라 그룹 밖이다.
            consumer = AIOKafkaConsumer(bootstrap_servers=self._brokers, group_id=None,
                                        enable_auto_commit=False)
            await consumer.start()
            ends = await consumer.end_offsets(list(committed))

            rows = []
            for tp, meta in sorted(committed.items(), key=lambda kv: (kv[0].topic, kv[0].partition)):
                end = ends.get(tp)
                rows.append({"topic": tp.topic, "partition": tp.partition,
                             "committed": meta.offset, "end": end,
                             "lag": (end - meta.offset) if end is not None else None})
            total = sum(r["lag"] for r in rows if r["lag"] is not None)
            return ProbeResult.succeeded({"group": group, "total_lag": total, "partitions": rows},
                                         source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)
        finally:
            for client in (consumer, admin):
                if client is None:
                    continue
                try:
                    await client.close() if client is admin else await client.stop()
                except Exception:                                  # noqa: BLE001
                    pass                        # 정리 실패가 관측 결과를 덮으면 안 된다

    async def _partitions_of(self, topic: str) -> list[int]:
        """토픽의 파티션 번호들. 컨슈머 캐시가 아니라 AdminClient에게 묻는다.

        `consumer.partitions_for_topic()`은 구독/assign 전에는 캐시가 비어 None이고,
        `await consumer.topics()`는 그 캐시를 갱신하지 않는다(별도 객체를 돌려준다).
        """
        from aiokafka.admin import AIOKafkaAdminClient

        admin = AIOKafkaAdminClient(bootstrap_servers=self._brokers)
        await admin.start()
        try:
            described = await admin.describe_topics([topic])
            if not described or described[0].get("error_code"):
                return []
            return sorted(p["partition"] for p in described[0]["partitions"])
        finally:
            try:
                await admin.close()
            except Exception:                                      # noqa: BLE001
                pass

    async def tail(self, topic: str, *, limit: int = DEFAULT_TAIL) -> ProbeResult:
        """토픽 끝에서 최근 메시지를 읽는다. 그룹 미참여, 오프셋 커밋 없음."""
        actual = self.resolve_topic(topic)
        source = f"kafka:tail {actual} limit={limit}"
        consumer = None
        try:
            from aiokafka import AIOKafkaConsumer, TopicPartition

            partitions = await self._partitions_of(actual)
            if not partitions:
                return ProbeResult.failed(f"토픽이 없거나 파티션을 못 찾았다 — {actual}",
                                          source=source, clock=self._clock)

            consumer = AIOKafkaConsumer(bootstrap_servers=self._brokers,
                                        group_id=None,              # ← 그룹에 안 들어간다
                                        enable_auto_commit=False,   # ← 커밋도 안 한다
                                        auto_offset_reset="earliest")
            await consumer.start()

            tps = [TopicPartition(actual, p) for p in partitions]
            consumer.assign(tps)                                    # ← subscribe가 아니다
            ends = await consumer.end_offsets(tps)
            begins = await consumer.beginning_offsets(tps)

            per_partition = max(1, limit // len(tps))
            empty = True
            for tp in tps:
                start = max(begins[tp], ends[tp] - per_partition)
                if ends[tp] > begins[tp]:
                    empty = False
                consumer.seek(tp, start)
            if empty:
                return ProbeResult.succeeded(
                    {"topic": actual, "messages": [], "note": "토픽이 비어 있다"},
                    source=source, clock=self._clock)

            batches = await consumer.getmany(timeout_ms=3000, max_records=limit)
            messages = [_render(record)
                        for records in batches.values() for record in records]
            messages.sort(key=lambda m: (m["partition"], m["offset"]))
            return ProbeResult.succeeded(
                {"topic": actual, "messages": messages[:limit]},
                source=source, clock=self._clock,
                truncated_reason=(f"끝에서 {limit}건만" if len(messages) >= limit else None))
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:                                  # noqa: BLE001
                    pass


def _render(record: Any) -> dict:
    import json

    def decode(raw):
        if raw is None:
            return None
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)      # JSON이면 파싱해서 읽기 좋게
        except ValueError:
            return text                  # 아니면 원문 그대로 — 무엇이 왔는지 보여야 한다
    return {"partition": record.partition, "offset": record.offset,
            "timestamp": record.timestamp, "key": decode(record.key),
            "value": decode(record.value)}
