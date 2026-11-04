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
import time
from datetime import datetime, timezone
from typing import Any

from src.config.schema_site import KafkaConsumerConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.jsonpath import select
from src.domain.ports import KafkaInspectorPort

DEFAULT_TAIL = 10
# tail이 레코드를 모으는 데 쓸 시간. `getmany`는 레코드가 조금이라도 오면 바로 돌아오므로(한 번에 limit을 채우지 않는다 —
# 사내 `--limit 300`이 2건을 돌려줬다) 다 채우거나 더 안 올 때까지 돈다. 이 상한은 브로커가 느릴 때 명령이 매달리지 않게.
TAIL_WAIT_S = 15.0
# 빈 배치를 몇 번 연속으로 받으면 그만 기다리나. 한 번으로는 안 된다 — 새 컨슈머의 첫 fetch는 1초를 넘기기 쉬워 사내 측정 #3에서
# 파티션 하나에 0건이 나왔다. 셋이면 3초다(받을 것이 끝에 닿으면 그 전에 끝난다).
EMPTY_POLLS = 3


# 유형 세기에서만 올리는 파티션당 fetch 상한(R3-5z ① — 측정 #33: 메시지 중간값 277KB라 기본 1MB는 왕복마다 3건 남짓). 조각 끝을 넘어 받는 양도 이만큼까지라
# 무작정 키우지 않는다.
COUNT_FETCH_BYTES = 4 * 1024 * 1024
# 끝에서 뒤로 물러나며 읽는 첫 조각(파티션마다, 그 뒤 두 배씩) — 작게 시작해야 멈춰도 가장 최근 쪽이 남는다(측정 #33: 277KB 메시지 8초에 60건 남짓).
COUNT_FIRST_CHUNK = 5
_mono = time.monotonic                 # 세기의 시간 상한만 이것으로 잰다 — 테스트가 갈아 끼우는 자리(이벤트 루프의 시계는 건드리지 않게)


class TypeTally:
    """`count_type`의 셈 — 실어댑터(받는 대로)와 스텁(시드에서)이 같은 규칙·같은 모양을 내게 한 곳에 둔다(R3-5y ②). 유형 칸이 있고 그 값이 스칼라인 메시지만
    "유형 칸이 든" 것이다 — 선언한 칸 이름이 틀렸을 때 "0건"이 "그 유형이 없다"로 읽히지 않게 그 수를 따로 센다. 센 범위는 파티션마다 다 읽은 오프셋 범위들
    (`spans`, 최근 쪽부터 — R3-5z ①): 메시지 오프셋이 아니라 읽은 자리로 적는다(압축 구멍은 빈 것이 아니다)."""

    def __init__(self, topic: str, *, field: str, values, max_messages: int, max_seconds: float, tz, called_at: str | None = None):
        self.head = {"topic": topic, "field": field, "values": sorted({str(v) for v in values}),
                     "max_messages": int(max_messages), "max_seconds": float(max_seconds)}
        self._wanted = set(self.head["values"])
        self._tz = tz or timezone.utc
        self.rows: dict[int, dict] = {}
        self._first = self._last = None
        self._latest: tuple | None = None
        self.bytes = 0
        self.called_at = called_at

    def track(self, partition: int, begin: int, end: int) -> None:
        self.rows[partition] = {"partition": partition, "begin": begin, "end": end, "spans": [], "counted": 0, "with_field": 0, "matched": 0}

    def tail_start(self, begin: int, end: int) -> int:
        """끝에서 최대 N건의 첫 오프셋."""
        return max(begin, end - max(self.head["max_messages"], 0))

    def covered(self, partition: int) -> int:
        return sum(hi - lo for lo, hi in self.rows[partition]["spans"])

    def cover(self, partition: int, lo: int, hi: int) -> None:
        """[lo, hi)를 다 읽었다 — 맞닿은 범위는 하나로, 최근 쪽부터."""
        if hi <= lo:
            return
        merged: list[list[int]] = []
        for a, b in sorted([*self.rows[partition]["spans"], [lo, hi]]):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self.rows[partition]["spans"] = sorted(merged, key=lambda span: -span[0])

    def add(self, partition: int, offset: int, timestamp, value, size: int = 0) -> None:
        row = self.rows.get(partition)
        if row is None:
            return
        row["counted"] += 1
        self.bytes += max(int(size), 0)
        if isinstance(timestamp, (int, float)):
            self._first = timestamp if self._first is None else min(self._first, timestamp)
            self._last = timestamp if self._last is None else max(self._last, timestamp)
        ok, kind = select(value, self.head["field"]) if isinstance(value, (dict, str)) else (False, None)
        if not ok or isinstance(kind, (dict, list)) or kind is None:
            return
        row["with_field"] += 1
        if str(kind) in self._wanted:
            row["matched"] += 1
            key = (timestamp if isinstance(timestamp, (int, float)) else -1, partition, offset)
            if self._latest is None or key > self._latest:
                self._latest = key

    def _at(self, ms) -> str | None:
        return datetime.fromtimestamp(ms / 1000, tz=self._tz).isoformat() if isinstance(ms, (int, float)) else None

    def result(self, stopped: str = "", seconds: float = 0.0) -> dict:
        rows = [self.rows[p] for p in sorted(self.rows)]
        latest = None
        if self._latest is not None:
            ts, partition, offset = self._latest
            latest = {"partition": partition, "offset": offset, "at": self._at(ts) if ts >= 0 else None}
        whole = not stopped and all(r["begin"] >= r["end"] or r["spans"] == [[r["begin"], r["end"]]] for r in rows)
        return {**self.head, "partitions": rows, "counted": sum(r["counted"] for r in rows), "with_field": sum(r["with_field"] for r in rows),
                "matched": sum(r["matched"] for r in rows), "first_at": self._at(self._first), "last_at": self._at(self._last), "latest": latest,
                "whole": whole, "stopped": stopped, "bytes": self.bytes, "seconds": round(float(seconds), 2), "called_at": self.called_at}


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

    async def list_topics(self) -> ProbeResult:
        """브로커의 토픽 이름들. **AdminClient에게 묻는다.**

        `consumer.topics()`를 쓰지 않는 이유는 `_partitions_of`와 같다 — 컨슈머
        캐시는 구독/assign 전에 비어 있고, 그 메서드가 캐시를 갱신하지도 않는다.
        내부 토픽(`__consumer_offsets` 등)은 뺀다: 우리 관심사가 아니고, 목록에
        섞이면 리드가 그걸 데이터 토픽으로 착각한다.
        """
        source = "kafka:topics"
        admin = None
        try:
            from aiokafka.admin import AIOKafkaAdminClient

            admin = AIOKafkaAdminClient(bootstrap_servers=self._brokers)
            await admin.start()
            names = await admin.list_topics()
            return ProbeResult.succeeded(
                sorted(n for n in names if not n.startswith("__")),
                source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)
        finally:
            if admin is not None:
                try:
                    await admin.close()
                except Exception:                                  # noqa: BLE001
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

            # 끝에서 뒤로 간 자리부터 끝까지가 받을 수 있는 전부다 — 그만큼 모이거나, 더 안 오거나, 시간이 다할 때까지.
            wanted = min(limit, sum(ends[tp] - max(begins[tp], ends[tp] - per_partition) for tp in tps))
            # `wanted`를 다 받았다 = 모든 파티션이 끝 오프셋에 닿았다(끝에서 뒤로 간 자리부터 차례로 읽으므로).
            records: list = []
            empties = 0
            deadline = time.monotonic() + TAIL_WAIT_S
            while len(records) < wanted and time.monotonic() < deadline:
                batches = await consumer.getmany(timeout_ms=1000, max_records=wanted - len(records))
                got = [record for rs in batches.values() for record in rs]
                if not got:
                    empties += 1
                    if empties >= EMPTY_POLLS:
                        break
                    continue
                empties = 0
                records.extend(got)
            messages = [_render(record) for record in records]
            messages.sort(key=lambda m: (m["partition"], m["offset"]))
            short = len(messages) < wanted
            why = f"빈 응답 {EMPTY_POLLS}번" if empties >= EMPTY_POLLS else f"{TAIL_WAIT_S:.0f}초"
            return ProbeResult.succeeded(
                {"topic": actual, "messages": messages[:limit]},
                source=source, clock=self._clock,
                truncated_reason=(f"{why} 뒤 {len(messages)}건만(받을 수 있던 것 {wanted}건) — 더 있을 수 있다" if short
                                  else f"끝에서 {limit}건만" if len(messages) >= limit else None))
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:                                  # noqa: BLE001
                    pass

    async def message_at(self, topic: str, partition: int, offset: int) -> ProbeResult:
        """포인터가 가리키는 메시지 한 건 — `assign` 뒤 `seek`. 그룹 미참여, 오프셋 커밋 없음. 범위 밖·압축 구멍은 `found=False`(오류 아님)."""
        actual = self.resolve_topic(topic)
        source = f"kafka:message_at {actual}[{partition}]@{offset}"
        consumer = None
        try:
            from aiokafka import AIOKafkaConsumer, TopicPartition

            partition, offset = int(partition), int(offset)
            tp = TopicPartition(actual, partition)
            consumer = AIOKafkaConsumer(bootstrap_servers=self._brokers,
                                        group_id=None,              # ← 그룹에 안 들어간다
                                        enable_auto_commit=False)   # ← 커밋도 안 한다
            await consumer.start()
            consumer.assign([tp])                                   # ← subscribe가 아니다
            begin = (await consumer.beginning_offsets([tp]))[tp]
            end = (await consumer.end_offsets([tp]))[tp]
            head = {"topic": actual, "partition": partition, "offset": offset, "begin": begin, "end": end}
            if not begin <= offset < end:
                # 범위 밖은 읽으러 가지 않는다 — 앞은 지워진 것, 뒤는 아직 없는 것. 둘 다 "키를 만든 메시지가 토픽에 없다"는 사실이다.
                return ProbeResult.succeeded({**head, "found": False, "message": None, "bytes": 0,
                                              "note": f"보존 범위 밖 — 남은 것은 {begin}부터 {end} 전까지"},
                                             source=source, clock=self._clock)
            consumer.seek(tp, offset)
            record, empties = None, 0
            deadline = time.monotonic() + TAIL_WAIT_S
            while record is None and time.monotonic() < deadline:
                batches = await consumer.getmany(timeout_ms=1000, max_records=1)
                got = [r for rs in batches.values() for r in rs]
                if not got:
                    empties += 1
                    if empties >= EMPTY_POLLS:          # 한 건도 안 오면 tail과 같은 기준으로 그만둔다
                        break
                    continue
                record = got[0]
            if record is None:
                return ProbeResult.failed(f"오프셋 {offset}은 보존 범위({begin}~{end}) 안인데 브로커가 빈 응답 {EMPTY_POLLS}번 — 모른다",
                                          source=source, clock=self._clock)
            if record.offset != offset:
                # 압축·삭제로 빈 오프셋에 seek하면 브로커는 그 다음 레코드를 준다 — 그건 그 메시지가 아니다.
                return ProbeResult.succeeded({**head, "found": False, "message": None, "bytes": 0,
                                              "note": f"그 오프셋의 레코드가 없다 — 다음 레코드는 {record.offset}(압축·삭제)"},
                                             source=source, clock=self._clock)
            return ProbeResult.succeeded({**head, "found": True, "message": _render(record), "bytes": len(record.value or b"")},
                                         source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:                                  # noqa: BLE001
                    pass

    async def count_type(self, topic: str, *, field: str, values: list[str], max_messages: int, max_seconds: float) -> ProbeResult:
        """섞인 토픽에서 한 유형만 센다(포트 docstring). `tail`과 같은 연결·같은 읽기 전용(assign·seek, 그룹 미참여·커밋 없음) — 다만 끝에서 N건을 센다.
        파티션마다 **끝에서 조각씩 뒤로** 물러나며 읽는다(R3-5z ① — 측정 #33: 끝−N에서 앞으로 읽어, 시간 상한에서 멈춘 세기가 창의 가장 오래된 끝만 셌다). 조각은
        작게 시작해 두 배씩 — 멈추면 가장 최근 쪽이 남고, 멈춘 조각은 읽은 데까지 따로 적는다. 다 센 파티션은 멈춰 둔다(이미 센 쪽을 또 받지 않게)."""
        actual = self.resolve_topic(topic)
        source = f"kafka:count_type {actual} {field}"
        consumer = None
        try:
            from aiokafka import AIOKafkaConsumer, TopicPartition

            partitions = await self._partitions_of(actual)
            if not partitions:
                return ProbeResult.failed(f"토픽이 없거나 파티션을 못 찾았다 — {actual}", source=source, clock=self._clock)
            consumer = AIOKafkaConsumer(bootstrap_servers=self._brokers,
                                        group_id=None,              # ← 그룹에 안 들어간다
                                        enable_auto_commit=False,   # ← 커밋도 안 한다
                                        auto_offset_reset="earliest",
                                        max_partition_fetch_bytes=COUNT_FETCH_BYTES)
            await consumer.start()
            tps = [TopicPartition(actual, p) for p in partitions]
            consumer.assign(tps)                                    # ← subscribe가 아니다
            ends = await consumer.end_offsets(tps)
            begins = await consumer.beginning_offsets(tps)
            called = self._clock()
            tally = TypeTally(actual, field=field, values=values, max_messages=max_messages, max_seconds=max_seconds,
                              tz=called.tzinfo, called_at=called.isoformat())
            limit = max(int(max_messages), 0)
            by_partition = {tp.partition: tp for tp in tps}
            reading: dict[int, list[int]] = {}                      # 파티션 → [조각 시작, 조각 끝, 읽은 데까지, 조각 크기]
            for tp in tps:
                begin, end = begins[tp], ends[tp]
                tally.track(tp.partition, begin, end)
                lo = max(begin, end - min(COUNT_FIRST_CHUNK, limit))
                if lo < end:
                    reading[tp.partition] = [lo, end, lo, end - lo]
                    consumer.seek(tp, lo)
            idle = [tp for tp in tps if tp.partition not in reading]
            if idle:
                consumer.pause(*idle)                               # seek 안 한 파티션은 처음부터 온다 — 셀 것이 없으면 받지 않는다

            def step(partition: int) -> None:
                """조각 하나를 다 읽었다 — 끝에 이어 붙이고, 남았으면 그 앞 조각으로(두 배), 다 셌으면 멈춰 둔다."""
                lo, hi, _, size = reading[partition]
                tally.cover(partition, lo, hi)
                begin, left = tally.rows[partition]["begin"], limit - tally.covered(partition)
                if lo <= begin or left <= 0:
                    del reading[partition]
                    consumer.pause(by_partition[partition])
                    return
                size = min(size * 2, left)
                start = max(begin, lo - size)
                reading[partition] = [start, lo, start, size]
                consumer.seek(by_partition[partition], start)

            stopped, empties = "", 0
            started = _mono()
            deadline = started + float(max_seconds)
            while reading:
                if _mono() >= deadline:
                    stopped = f"시간 상한 {float(max_seconds):g}초"
                    break
                batches = await consumer.getmany(timeout_ms=1000, max_records=5000)
                got = [record for rs in batches.values() for record in rs]
                if not got:
                    empties += 1
                    if empties >= EMPTY_POLLS:
                        stopped = f"빈 응답 {EMPTY_POLLS}번"
                        break
                    continue
                empties = 0
                moved: set[int] = set()                             # 이 배치에서 다음 조각으로 옮긴 파티션 — 남은 레코드는 옛 자리의 것이다
                for record in got:
                    p = record.partition
                    if p in moved or p not in reading:
                        continue
                    lo, hi = reading[p][0], reading[p][1]
                    if record.offset < lo:
                        continue
                    if record.offset >= hi:                         # 조각 끝이 압축 구멍이었다 — 다 읽은 것이다
                        step(p)
                        moved.add(p)
                        continue
                    tally.add(p, record.offset, record.timestamp, _decode(record.value), size=len(record.value or b""))
                    reading[p][2] = record.offset + 1
                    if record.offset + 1 >= hi:
                        step(p)
                        moved.add(p)
            for p, (lo, _, upto, _) in reading.items():
                tally.cover(p, lo, upto)                            # 멈춘 조각은 읽은 데까지 따로
            return ProbeResult.succeeded(tally.result(stopped, seconds=_mono() - started), source=source, clock=self._clock,
                                         truncated_reason=(f"{stopped}에서 멈췄다 — 다 못 셌다" if stopped else None))
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}", source=source, clock=self._clock)
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:                                  # noqa: BLE001
                    pass

    async def offsets(self, topic: str) -> ProbeResult:
        """토픽의 보존 범위만 — 파티션마다 `beginning_offsets`·`end_offsets`(R3-5p ①-a). `assign`만, 그룹 미참여·커밋 없음, seek·fetch 없음(브로커 왕복 둘).
        파티션은 `message_at`과 달리 모른 채 오므로 AdminClient에게 묻는다(`_partitions_of`) — 없으면 토픽이 없는 것."""
        actual = self.resolve_topic(topic)
        source = f"kafka:offsets {actual}"
        consumer = None
        try:
            from aiokafka import AIOKafkaConsumer, TopicPartition

            partitions = await self._partitions_of(actual)
            if not partitions:
                return ProbeResult.failed(f"토픽이 없거나 파티션을 못 찾았다 — {actual}", source=source, clock=self._clock)
            tps = [TopicPartition(actual, p) for p in partitions]
            consumer = AIOKafkaConsumer(bootstrap_servers=self._brokers, group_id=None, enable_auto_commit=False)
            await consumer.start()
            consumer.assign(tps)
            begins = await consumer.beginning_offsets(tps)
            ends = await consumer.end_offsets(tps)
            rows = [{"partition": tp.partition, "begin": begins[tp], "end": ends[tp]} for tp in tps]
            return ProbeResult.succeeded({"topic": actual, "partitions": rows, "messages": sum(r["end"] - r["begin"] for r in rows)},
                                         source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}", source=source, clock=self._clock)
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:                                  # noqa: BLE001
                    pass


def _decode(raw):
    import json

    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)      # JSON이면 파싱해서 읽기 좋게
    except ValueError:
        return text                  # 아니면 원문 그대로 — 무엇이 왔는지 보여야 한다


def _render(record: Any) -> dict:
    return {"partition": record.partition, "offset": record.offset,
            "timestamp": record.timestamp, "key": _decode(record.key),
            "value": _decode(record.value)}
