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
    def __init__(self, partition, offset, value_of=None):
        self.partition, self.offset, self.timestamp = partition, offset, (_Consumer.stamp or (lambda o: 1000 + o))(offset)
        self.key, self.value = None, (value_of(partition, offset) if value_of else b'{"n": %d}' % offset)


class _Consumer:
    """`getmany`가 호출마다 **두 건만** 돌려준다 — 실제 aiokafka처럼 레코드가 조금이라도 있으면 바로 돌아온다.
    `empties_first`만큼은 빈 배치로 답한다(새 컨슈머의 첫 fetch가 늦는 것), `stall`이면 끝까지 빈 배치다."""
    calls = 0
    empties_first = 0
    stall = False
    flaky = False                    # 홀수 번째 호출마다 빈 배치 — 받는 사이사이에 비는 것
    begin = 0                        # 보존 시작(`beginning_offsets`) — message_at의 범위 검사
    holes: set = set()               # 압축·삭제로 빈 오프셋 — seek하면 다음 레코드가 온다
    value_of = None                  # (파티션, 오프셋) → 값 바이트 — 유형 세기(R3-5y ②)의 섞인 토픽
    made: list = []                  # 만든 컨슈머의 인자 — 그룹 미참여·커밋 없음을 본다
    paused: set = set()              # 멈춰 둔 파티션 — 유형 세기(R3-5z ①)가 다 센 파티션을 더 안 받는다
    stamp = None                     # 오프셋 → 메시지 시각(ms) — 시각 표본(R3-5z ②)의 `offsets_for_times`도 이것으로 답한다
    asked_at: list = []           # `offsets_for_times`에 물은 시각들

    def __init__(self, ends, **kwargs):
        self._ends, self._pos = ends, {}
        _Consumer.made.append(kwargs)

    async def start(self): pass
    async def stop(self): pass
    def assign(self, tps): pass
    async def end_offsets(self, tps): return {tp: self._ends[tp.partition] for tp in tps}
    async def beginning_offsets(self, tps): return {tp: _Consumer.begin for tp in tps}
    def seek(self, tp, offset): self._pos[tp.partition] = offset
    def pause(self, *tps): _Consumer.paused.update(tp.partition for tp in tps)
    def resume(self, *tps): _Consumer.paused.difference_update(tp.partition for tp in tps)

    async def offsets_for_times(self, wanted):
        """그 시각 이후 첫 메시지의 오프셋 — 없으면 None(aiokafka와 같다)."""
        stamp = _Consumer.stamp or (lambda o: 1000 + o)
        _Consumer.asked_at.extend(wanted.values())
        out = {}
        for tp, ts in wanted.items():
            hit = next((o for o in range(_Consumer.begin, self._ends[tp.partition]) if stamp(o) >= ts), None)
            out[tp] = None if hit is None else types.SimpleNamespace(offset=hit, timestamp=stamp(hit))
        return out

    async def getmany(self, timeout_ms, max_records):
        _Consumer.calls += 1
        if _Consumer.stall or _Consumer.calls <= _Consumer.empties_first or (_Consumer.flaky and _Consumer.calls % 2):
            return {}
        out, budget = {}, min(2, max_records)
        for p, pos in sorted(self._pos.items()):
            if p in _Consumer.paused:
                continue
            while budget and pos < self._ends[p]:
                if pos in _Consumer.holes:
                    pos += 1
                    continue
                out.setdefault(p, []).append(_Record(p, pos, _Consumer.value_of))
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
    _Consumer.calls, _Consumer.empties_first, _Consumer.stall, _Consumer.flaky = 0, 0, False, False
    _Consumer.begin, _Consumer.holes, _Consumer.value_of, _Consumer.made, _Consumer.paused = 0, set(), None, [], set()
    _Consumer.stamp, _Consumer.asked_at = None, []
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


async def test_tail은_첫_배치가_비어도_멈추지_않는다(monkeypatch):
    """사내 측정 #3: 파티션 하나에 `--limit 300`이 0건(8초) — 새 컨슈머의 첫 fetch는 1초를 넘기기 쉬운데 빈 배치 한 번에 멈췄다."""
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.empties_first = 2
    got = await inspector.tail("t", limit=30)
    assert len(got.data["messages"]) == 30 and got.envelope.truncated_reason == "끝에서 30건만"


async def test_tail은_빈_배치가_연속_세_번이면_멈추고_모자란다고_적는다(monkeypatch):
    """끝 오프셋은 있는데 브로커가 안 주면 시간 상한까지 매달리지 않는다 — 빈 배치 연속 셋이면 멈추고 봉투가 말한다."""
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.stall = True
    got = await inspector.tail("t", limit=30)
    assert got.status == "ok" and got.data["messages"] == [] and _Consumer.calls == 3
    assert not got.envelope.complete and "빈 응답 3번" in got.envelope.truncated_reason


async def test_tail은_받는_사이사이_빈_배치는_연속으로_세지_않는다(monkeypatch):
    """"연속 셋"이다 — 받을 때마다 센 것을 지운다. 안 지우면 드문드문 비는 브로커에서 몇 건 받다 멈춘다."""
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.flaky = True
    got = await inspector.tail("t", limit=30)
    assert len(got.data["messages"]) == 30 and got.envelope.truncated_reason == "끝에서 30건만"



# ── R3-5d ④ — 포인터의 메시지 한 건 ──

async def test_message_at은_포인터의_그_메시지_한_건만_읽고_그룹에_안_들어간다(monkeypatch):
    """측정 #13 추가분 2: 키 값의 `metadata.source`(토픽·파티션·오프셋) 한 건이 그 키를 만든 메시지다(사내 122/122키). `assign` 뒤 `seek` —
    그룹 미참여·커밋 없음. 본문은 원본에 있고 크기는 바이트다."""
    got = await _inspector(monkeypatch, {0: 100}, [0]).message_at("t", 0, 42)
    assert got.status == "ok", got.error
    assert got.data == {"topic": "t", "partition": 0, "offset": 42, "begin": 0, "end": 100, "found": True, "bytes": 9,
                        "message": {"partition": 0, "offset": 42, "timestamp": 1042, "key": None, "value": {"n": 42}}}
    assert got.envelope.complete and got.source == "kafka:message_at t[0]@42"


async def test_offsets는_파티션마다_보존_범위만_읽고_메시지는_안_가져온다(monkeypatch):
    """R3-5p ①-a(평가자 분석 10-28): 없는 키의 쓰는 클래스가 구독하는 토픽에 무엇이 남아 있는지 — `message_at`이 머리에 주는 범위만 따로. `assign`만, 그룹
    미참여, seek·fetch 없음(`getmany` 0회). 파티션을 못 찾으면 실패(토픽 없음)."""
    inspector = _inspector(monkeypatch, {0: 100, 1: 50}, [0, 1])
    _Consumer.begin = 40
    got = await inspector.offsets("t")
    assert got.status == "ok", got.error
    assert got.data == {"topic": "t", "partitions": [{"partition": 0, "begin": 40, "end": 100}, {"partition": 1, "begin": 40, "end": 50}], "messages": 70}
    assert got.source == "kafka:offsets t" and _Consumer.calls == 0 and got.envelope.complete
    none = await _inspector(monkeypatch, {}, []).offsets("t")
    assert none.status == "error" and "파티션" in none.error


async def test_message_at은_보존_범위_밖이면_없다고_하고_오류가_아니다(monkeypatch):
    """측정 #13 추가분 2: 오프셋이 보존 범위 밖인 키 2개가 둘 다 진짜 원인 자리였다 — "키를 만든 메시지가 토픽에 없다"는 사실이지 읽기 실패가 아니다.
    범위 앞(지워짐)·뒤(아직 없음) 둘 다, 읽으러 가지도 않는다."""
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.begin = 40
    for offset in (39, 100):
        got = await inspector.message_at("t", 0, offset)
        assert got.status == "ok" and got.data["found"] is False and got.data["message"] is None, offset
        assert (got.data["begin"], got.data["end"]) == (40, 100) and "보존 범위 밖" in got.data["note"]
    assert _Consumer.calls == 0


async def test_message_at은_압축으로_빠진_오프셋의_다음_레코드를_그_메시지라_하지_않는다(monkeypatch):
    """`seek`한 오프셋이 압축·삭제로 비면 브로커는 그 다음 레코드를 준다 — 오프셋이 다르면 그 메시지는 없는 것이다."""
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.holes = {42}
    got = await inspector.message_at("t", 0, 42)
    assert got.status == "ok" and got.data["found"] is False and got.data["message"] is None and "다음 레코드는 43" in got.data["note"]


async def test_message_at은_브로커가_안_주면_빈_응답_셋_뒤_실패로_답한다(monkeypatch):
    """범위 안인데 안 오면 모르는 것이다 — 없다고 하지 않고 실패로(tail과 같은 기준: 빈 배치 연속 셋)."""
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.stall = True
    got = await inspector.message_at("t", 0, 42)
    assert got.status == "error" and "빈 응답" in got.error and _Consumer.calls == 3



# ── R3-5y ② — 섞인 토픽에서 한 유형만 센다 ──

def _kinds(partition, offset):
    """오프셋이 10의 배수면 유형 "7"(값 — 라우터가 가르는 것), 아니면 "9". 셋째마다 유형 칸이 없는 메시지, 다섯째마다 JSON이 아닌 메시지."""
    if offset % 5 == 4:
        return b"not json"
    if offset % 3 == 2:
        return b'{"meta": {}}'
    return b'{"meta": {"kind": %s}}' % (b'"7"' if offset % 10 == 0 else b"9")


async def test_count_type은_파티션마다_끝에서_N건을_세고_유형_칸의_값을_문자열로_견주며_그룹에_안_들어간다(monkeypatch):
    """측정 #32 회신의 답 2: 유형 30개가 섞인 10만 건 중 끝 몇백 건(tail)으로는 답이 안 났다. 파티션마다 끝에서 최대 N건, 값의 유형 칸(점 경로)을 문자열로
    값들과 견준다(라우터가 그 값으로 가른다 — 정수 값 9도 "9"). 칸이 없거나 JSON이 아닌 메시지는 센 범위에는 들고 유형 칸 있는 수에는 안 든다. 결과 하나에
    파티션별 센 범위(오프셋)·센 수·맞은 수, 센 창의 첫·마지막 메시지 시각, 가장 최근 맞은 메시지. 그룹 미참여·커밋 없음."""
    inspector = _inspector(monkeypatch, {0: 100, 1: 60}, [0, 1])
    _Consumer.value_of = _kinds
    got = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=30, max_seconds=8)
    assert got.status == "ok" and got.envelope.complete, got.error
    data = got.data
    assert [(r["partition"], r["spans"], r["counted"]) for r in data["partitions"]] == [(0, [[70, 100]], 30), (1, [[30, 60]], 30)]
    assert data["counted"] == 60 and data["matched"] == 4 and data["whole"] is False      # 70·90 / 30·40 — 80·50은 유형 칸이 없다 and data["stopped"] == ""
    assert data["with_field"] == sum(1 for p, (a, b) in ((0, (70, 100)), (1, (30, 60))) for o in range(a, b) if o % 5 != 4 and o % 3 != 2)
    assert data["latest"] == {"partition": 0, "offset": 90, "at": "1970-01-01T00:00:01.090000+00:00"}
    assert (data["first_at"], data["last_at"]) == ("1970-01-01T00:00:01.030000+00:00", "1970-01-01T00:00:01.099000+00:00")
    assert all(made.get("group_id") is None and made.get("enable_auto_commit") is False for made in _Consumer.made)
    nine = await _inspector(monkeypatch, {0: 20}, [0]).count_type("t", field="meta.kind", values=["9"], max_messages=50, max_seconds=8)
    assert nine.data["matched"] == 0                                    # 가짜는 값을 다시 안 심었다 — {"n": …}엔 유형 칸이 없다
    inspector = _inspector(monkeypatch, {0: 20}, [0])
    _Consumer.value_of = _kinds
    whole = await inspector.count_type("t", field="meta.kind", values=["9"], max_messages=50, max_seconds=8)
    assert whole.data["whole"] is True and whole.data["counted"] == 20 and whole.data["matched"] > 0     # 정수 9를 "9"로 견준다


async def test_count_type은_시간_상한이나_빈_응답에서_멈추면_그_까닭을_적고_봉투는_잘린_것이다(monkeypatch):
    """상한 둘 중 먼저 닿는 쪽에서 멈춘다 — N건(창)은 다 센 것이고, 시간(T초)·빈 응답은 다 못 센 것이다(봉투가 잘렸다고 말한다)."""
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.value_of = _kinds
    timed = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=50, max_seconds=0)
    assert timed.status == "ok" and timed.data["stopped"] == "시간 상한 0초" and timed.data["counted"] == 0 and not timed.envelope.complete
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.stall = True
    stalled = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=50, max_seconds=8)
    assert stalled.data["stopped"] == "빈 응답 3번" and not stalled.envelope.complete and stalled.data["whole"] is False


# ── R3-5z ① — 끝에서 조각씩 뒤로: 멈춰도 가장 최근 쪽이 남는다 ──

async def test_count_type은_끝에서_조각씩_뒤로_읽어_멈춰도_가장_최근_쪽을_덮고_센_범위를_조각_그대로_적는다(monkeypatch):
    """측정 #33 1: 끝−N으로 seek하고 앞으로 읽어, 시간 상한에서 멈춘 세기가 창의 가장 오래된 끝(부른 시각보다 14시간 전 3분치)을 셌다. 파티션마다 끝에서 조각씩
    뒤로 물러나며 읽는다(조각은 5건에서 두 배씩) — 멈추면 가장 최근 쪽이 남는다. 센 범위는 조각 그대로(`spans`, 최근 쪽부터): 다 읽은 조각은 끝에 이어 붙고, 멈춘
    조각은 읽은 데까지 따로. 세기에서만 fetch 상한을 올린다(277KB 메시지면 기본 1MB는 왕복마다 3건 남짓). 읽은 바이트·초·부른 시각을 결과에 남긴다. 다 센 파티션은
    멈춰 둔다 — 이미 센 쪽을 또 받지 않게."""
    from src.infrastructure import kafka_inspector as ki

    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.value_of = _kinds
    monkeypatch.setattr(ki, "_mono", lambda: float(_Consumer.calls))                      # getmany 한 번에 1초
    got = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=50, max_seconds=5)
    data = got.data
    assert data["stopped"] == "시간 상한 5초" and data["partitions"][0]["spans"] == [[95, 100], [85, 89]] and data["counted"] == 9
    assert data["bytes"] == sum(len(_kinds(0, o)) for o in [*range(95, 100), *range(85, 89)]) and data["seconds"] == 5.0
    assert data["called_at"] == T0.isoformat() and not got.envelope.complete
    assert _Consumer.made[0]["max_partition_fetch_bytes"] == ki.COUNT_FETCH_BYTES > 1024 * 1024
    assert all(made.get("group_id") is None and made.get("enable_auto_commit") is False for made in _Consumer.made)
    inspector = _inspector(monkeypatch, {0: 100, 1: 60}, [0, 1])
    _Consumer.value_of = _kinds
    done = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=30, max_seconds=1000)
    assert [r["spans"] for r in done.data["partitions"]] == [[[70, 100]], [[30, 60]]] and _Consumer.paused == {0, 1}   # 다 센 파티션은 멈춘다
    assert done.data["stopped"] == "" and done.envelope.complete
    # 압축 구멍: 조각 끝이 구멍이면 다음 레코드(그 뒤 오프셋)로 조각이 끝난 줄 안다. 같은 배치에 남은 옛 자리 레코드는 새 조각의 것이 아니다.
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.value_of, _Consumer.holes = _kinds, {90, 94}
    holed = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=50, max_seconds=1000)
    assert holed.data["partitions"][0]["spans"] == [[50, 100]] and holed.data["counted"] == 48 and holed.data["stopped"] == ""
    # 한 조각 안에서는 앞으로 읽는다 — 먼저 본 맞은 메시지가 가장 최근은 아니다
    inspector = _inspector(monkeypatch, {0: 100}, [0])
    _Consumer.value_of = lambda p, o: b'{"meta": {"kind": "7"}}' if o in (95, 98) else b'{"meta": {"kind": "9"}}'
    pair = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=10, max_seconds=1000)
    assert pair.data["matched"] == 2 and pair.data["latest"]["offset"] == 98


# ── R3-5z ② — 시각 표본: 끝 창이 0건이면 부른 시각 기준 여러 시점에서 파티션당 m건 ──

T0_MS = int(T0.timestamp() * 1000)


def _minutely(end):
    """끝 오프셋이 부른 시각 1분 전 — 메시지가 1분에 한 건."""
    return lambda o: T0_MS - (end - o) * 60_000


async def test_count_type은_끝_창이_0건이면_부른_시각_기준_상대_시점마다_파티션당_m건을_세고_시각은_결과에만_담는다(monkeypatch):
    """측정 #33 회신의 답 1(가)(나): 정답이 "그 유형이 3주 넘게 안 온다"인 케이스는 끝의 연속 창 하나로 답할 수 없다. 끝 창이 0건이고 전부가 아니면, 부른 시각 기준
    상대 초 목록의 시점(−1시간 … −21일)과 보존 범위 처음에서 파티션당 m건씩 센다(`offsets_for_times` — 읽기 전용, 그룹·커밋 없음). 인자는 **상대 초**다 — 절대 시각을
    인자에 넣으면 녹화 때와 재생 때 시계가 달라 재생이 매번 녹화 밖이 된다. 절대 시각은 결과에만. 끝 창 안의 시점, 보존 처음보다 앞선 시점(보존 처음 창이 센다),
    앞 시점과 같은 자리는 건너뛴다. 표본에만 쓰는 시간 상한이 따로다."""
    from src.infrastructure import kafka_inspector as ki

    inspector = _inspector(monkeypatch, {0: 3000}, [0])
    _Consumer.stamp = _minutely(3000)                                                     # 3000분(약 2일) — 3일 전은 보존 처음보다 앞선다
    _Consumer.value_of = lambda p, o: b'{"meta": {"kind": "7"}}' if o in (3, 1562) else b'{"meta": {"kind": "9"}}'
    ages = [3600, 21600, 86400, 259200]
    got = await inspector.count_type("t", field="meta.kind", values=["8"], max_messages=10, max_seconds=1000,
                                     sample_ages=ages, sample_messages=5, sample_seconds=1000)
    data = got.data
    assert data["matched"] == 0 and data["partitions"][0]["spans"] == [[2990, 3000]]                              # 끝 창은 0건
    assert (data["sample_ages"], data["sample_messages"], data["sample_seconds"]) == (ages, 5, 1000.0)              # 인자는 상대 초 그대로
    assert [(w["age"], w["partitions"][0]["spans"], w["counted"], w["matched"]) for w in data["samples"]] == [
        (3600, [[2940, 2945]], 5, 0), (21600, [[2640, 2645]], 5, 0), (86400, [[1560, 1565]], 5, 0), (None, [[0, 5]], 5, 0)]
    assert data["samples_skipped"] == [{"age": 259200, "why": "보존 처음보다 앞선다"}] and data["sample_stopped"] == ""
    assert data["samples"][0]["at"] == "2026-03-01T08:00:00+00:00" and data["samples"][3]["at"] is None           # 절대 시각은 결과에만
    assert _Consumer.asked_at == [T0_MS - a * 1000 for a in ages] and data["sample_bytes"] > 0
    # 본 창에서 멈춘다 — 그 창이 "마지막으로 본 창"이고 더 오래된 창은 답을 안 바꾼다
    inspector = _inspector(monkeypatch, {0: 3000}, [0])
    _Consumer.stamp = _minutely(3000)
    _Consumer.value_of = lambda p, o: b'{"meta": {"kind": "7"}}' if o in (3, 1562) else b'{"meta": {"kind": "9"}}'
    hit = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=10, max_seconds=1000,
                                     sample_ages=ages, sample_messages=5, sample_seconds=1000)
    assert [(w["age"], w["matched"]) for w in hit.data["samples"]] == [(3600, 0), (21600, 0), (86400, 1)]
    assert hit.data["samples"][2]["latest"]["offset"] == 1562
    # 끝 창이 그 시점을 이미 덮었으면 건너뛴다
    inspector = _inspector(monkeypatch, {0: 3000}, [0])
    _Consumer.stamp, _Consumer.value_of = _minutely(3000), (lambda p, o: b'{"meta": {"kind": "9"}}')
    covered = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=100, max_seconds=1000,
                                         sample_ages=[3600], sample_messages=5, sample_seconds=1000)
    assert covered.data["samples_skipped"] == [{"age": 3600, "why": "끝 창 안"}] and [w["age"] for w in covered.data["samples"]] == [None]
    # 끝 창에서 보면 표본을 안 센다 · 표본 상한
    inspector = _inspector(monkeypatch, {0: 3000}, [0])
    _Consumer.stamp, _Consumer.value_of = _minutely(3000), (lambda p, o: b'{"meta": {"kind": "7"}}')
    seen = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=10, max_seconds=1000,
                                      sample_ages=ages, sample_messages=5, sample_seconds=1000)
    assert seen.data["matched"] == 10 and seen.data["samples"] == [] and _Consumer.asked_at == []
    inspector = _inspector(monkeypatch, {0: 3000}, [0])
    _Consumer.stamp, _Consumer.value_of = _minutely(3000), (lambda p, o: b'{"meta": {"kind": "9"}}')
    monkeypatch.setattr(ki, "_mono", lambda: float(_Consumer.calls))
    cut = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=10, max_seconds=100,
                                     sample_ages=ages, sample_messages=5, sample_seconds=4)
    assert cut.data["sample_stopped"] == "표본 시간 상한 4초" and len(cut.data["samples"]) < 4 and not cut.envelope.complete
    assert cut.data["samples"][-1]["counted"] == 2                                          # 창을 읽다 멈추면 읽은 데까지
    # 창 사이에서 상한에 닿으면 다음 시점은 묻지도 않는다 — 빈 창을 지어 붙이지 않게
    inspector = _inspector(monkeypatch, {0: 3000}, [0])
    _Consumer.stamp, _Consumer.value_of = _minutely(3000), (lambda p, o: b'{"meta": {"kind": "9"}}')
    edge = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=10, max_seconds=100,
                                      sample_ages=ages, sample_messages=5, sample_seconds=3)
    assert [w["age"] for w in edge.data["samples"]] == [3600] and len(_Consumer.asked_at) == 1
    # 메시지가 없는 동안의 두 시점은 같은 자리다 — 한 번만 센다
    inspector = _inspector(monkeypatch, {0: 3000}, [0])
    monkeypatch.setattr(ki, "_mono", lambda: 0.0)
    _Consumer.stamp = lambda o: T0_MS - (3000 - o) * 60_000 if o >= 1560 else T0_MS - 5 * 86_400_000 + o
    _Consumer.value_of = lambda p, o: b'{"meta": {"kind": "9"}}'
    gap = await inspector.count_type("t", field="meta.kind", values=["7"], max_messages=10, max_seconds=100,
                                     sample_ages=[86400, 259200], sample_messages=5, sample_seconds=100)
    assert [w["age"] for w in gap.data["samples"]] == [86400, None] and gap.data["samples_skipped"] == [{"age": 259200, "why": "앞 시점과 같은 자리"}]
