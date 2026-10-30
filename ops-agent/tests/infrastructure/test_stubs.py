"""스텁 어댑터 — **모르는 것을 지어내지 않는다.**

스텁은 실어댑터의 응답 모양을 흉내내되, seed가 말해 주지 않은 구조를 만들어 넣으면
안 된다. 리드는 스텁인 줄 모르고 읽는다.
"""
from datetime import datetime

from src.infrastructure.stubs import StubKafkaInspector


def _clock():
    return datetime(2026, 9, 28, 1, 0)


async def test_group_offsets는_토픽별_lag를_낸다():
    """실어댑터는 토픽·파티션 행을 낸다. seed가 토픽별로 주면 그 모양 그대로 —
    같은 그룹을 두 서비스가 나눠 쓸 때 어느 토픽이 밀리는지가 곧 정답의 근거다."""
    stub = StubKafkaInspector({}, {"g": {"t.main": 1830, "t.raw": 0}}, clock=_clock)
    got = (await stub.group_offsets("g")).data
    assert got["total_lag"] == 1830
    assert [(r["topic"], r["lag"]) for r in got["partitions"]] == [("t.main", 1830), ("t.raw", 0)]


async def test_group_offsets는_총량만_알면_파티션을_지어내지_않는다():
    """3b 측정에서 리드가 `topic="stub"` 행을 "stub 토픽을 구독하고 있다"로 읽고 결론을
    그 위에 세웠다. 스텁이 지어낸 구조는 리드에게 증거다. 모르는 것은 비워 둔다."""
    stub = StubKafkaInspector({}, {"g": 7}, clock=_clock)
    got = (await stub.group_offsets("g")).data
    assert got["total_lag"] == 7 and got["partitions"] == []
    assert "stub" not in str(got)


async def test_group_offsets는_모르는_그룹에_lag_0을_지어내지_않는다():
    """3b off-1에서 리드가 지어낸 그룹 이름으로 물었더니 스텁이 lag 0을 돌려줘 "컨슈머 정상"으로
    읽혔다. 실어댑터는 커밋된 오프셋이 없다고 답한다 — 같은 모양으로."""
    stub = StubKafkaInspector({}, {"g": 7}, clock=_clock)
    got = (await stub.group_offsets("no-such-group")).data
    assert got["partitions"] == [] and "total_lag" not in got
    assert "커밋된 오프셋이 없다" in got["note"]


async def test_스텁_코드는_seed의_사슬만_주고_나머지는_없다고_한다():
    """`case dryrun`이 rest → code.trace → recompute 사다리를 대상 레포 없이 돌리기 위한 자리. 사슬 본문은
    사람이 seed에 적은 그대로다 — grep·read를 지어내면 리드(대본)가 그 위에 결론을 세운다."""
    from src.domain.ports import DeployedCodePort
    from src.infrastructure.stubs import StubDeployedCode

    stub = StubDeployedCode({"trace": {"/summary/badge": "api/r.py:L6 badge\n  → api/q.py:L3 Repo.recent"}},
                            clock=_clock)
    assert isinstance(stub, DeployedCodePort)
    got = await stub.trace("/summary/badge")
    assert got.status == "ok" and got.data.splitlines()[0] == "api/r.py:L6 badge"
    missing = await stub.trace("/nope")
    assert missing.status == "error" and "/nope" in missing.error
    for call in (stub.services(), stub.config("api"), stub.grep(["x"]), stub.flow("x"), stub.read("api", "a.py")):
        result = await call
        assert result.status == "error" and "스텁" in result.error
    assert (await StubDeployedCode(None, clock=_clock).trace("/summary/badge")).status == "error"


async def test_스텁_코드는_seed의_callers와_uses도_그대로_준다():
    """6d — 사다리의 넷째·다섯째 칸(uses → callers)을 대본으로 밟기 위해. 역시 적힌 것만 돌려준다."""
    from src.infrastructure.stubs import StubDeployedCode

    stub = StubDeployedCode({"uses": {"alarm_events": "alarm_events [collection]\n  쓰기 1:\n    sink.writer.run"},
                             "callers": {"sink.writer.run": "대상 sink.writer.run\n부르는 곳이 없다"}}, clock=_clock)
    assert (await stub.uses("alarm_events")).data.startswith("alarm_events [collection]")
    assert (await stub.callers("sink.writer.run")).data.startswith("대상 sink.writer.run")
    for result in (await stub.uses("nope"), await stub.callers("nope"), await stub.trace("/x")):
        assert result.status == "error" and "스텁" in result.error


async def test_stub_redis_get은_JSON_경로로_고른_부분만_준다():
    """사내 실측: 요약 키 값이 커서 첫 키에서 잘렸다. 경로를 주면 그 부분을 **통째로**, 없는 경로면 있는 키를 말한다."""
    import json

    from src.infrastructure.stubs import StubRedisReader

    redis = StubRedisReader({"k": json.dumps({"record": [{"data": {"x": 1}}], "metadata": {"status": "ok"}})}, clock=_clock)
    got = await redis.get("k", path="record[0].data")
    assert got.status == "ok" and got.data == {"type": "string", "path": "record[0].data", "value": {"x": 1}}
    missing = await redis.get("k", path="record[0].nope")
    assert missing.status == "error" and "nope" in missing.error and "data" in missing.error
    plain = await redis.get("k")
    assert plain.data["value"].startswith("{") and "path" not in plain.data       # 경로 없으면 전처럼 원문
    absent = await redis.get("none", path="a")
    assert absent.status == "ok" and absent.data is None                           # 키 없음은 사실이지 오류가 아니다


async def test_스텁_코드는_seed의_resources도_그대로_준다():
    from src.infrastructure.stubs import StubDeployedCode

    stub = StubDeployedCode({"resources": {"SummaryRefresh": "SummaryRefresh (batch/job.py:L1)\n  읽기 1:\n    alarm:source:{line} [rediskey]"}}, clock=_clock)
    assert (await stub.resources("SummaryRefresh")).data.startswith("SummaryRefresh (batch/job.py:L1)")
    missing = await stub.resources("nope")
    assert missing.status == "error" and "스텁" in missing.error



async def test_stub_redis는_오류_신호_시드를_읽기_실패로_돌려준다():
    """측정판의 읽기 실패(R3-5b ②) — 키 없음(None, 성공)과 다른 사실이다. 시드 값이 `{"__error__": …}` 하나면 실패로."""
    from datetime import datetime

    from src.infrastructure.stubs import StubRedisReader
    redis = StubRedisReader({"k": {"__error__": "WRONGTYPE"}, "v": "1"}, clock=lambda: datetime(2026, 10, 16))
    bad, gone, ok = await redis.get("k"), await redis.get("없음"), await redis.get("v")
    assert bad.status == "error" and "WRONGTYPE" in (bad.error or "")
    assert gone.status == "ok" and gone.data is None and ok.status == "ok"



async def test_stub_kafka는_포인터의_메시지_한_건을_주고_보존_범위_밖이면_없다고_한다():
    """R3-5d ④ — 실어댑터와 같은 모양. 토픽 시드는 메시지 목록(오프셋 = 차례, 파티션 0 하나)이거나 `{"begin": n, "messages": […]}`(보존이 n부터 —
    측정판이 "키를 만든 메시지가 토픽에 없다"를 심는 자리)."""
    stub = StubKafkaInspector({"t": [{"a": 1}, {"a": 2}], "old": {"begin": 100, "messages": [{"b": 1}]}}, {}, clock=_clock)
    got = await stub.message_at("t", 0, 1)
    assert got.data == {"topic": "t", "partition": 0, "offset": 1, "begin": 0, "end": 2, "found": True, "bytes": 8,
                        "message": {"partition": 0, "offset": 1, "timestamp": None, "key": None, "value": {"a": 2}}}
    gone = await stub.message_at("t", 0, 2)
    assert gone.status == "ok" and gone.data["found"] is False and gone.data["message"] is None and "보존 범위 밖" in gone.data["note"]
    kept = await stub.message_at("old", 0, 100)
    assert kept.data["found"] is True and kept.data["message"]["value"] == {"b": 1} and (kept.data["begin"], kept.data["end"]) == (100, 101)
    assert (await stub.message_at("old", 0, 3)).data["found"] is False                 # 보존 시작 앞 — 지워진 것
    assert (await stub.tail("old", limit=5)).data["messages"] == [{"b": 1}]            # 목록 시드와 같이 읽힌다
    assert (await stub.list_topics()).data == ["old", "t"]
    # 보존 범위만(R3-5p ①-a) — 파티션 0 하나, 남은 메시지 수
    assert (await stub.offsets("t")).data == {"topic": "t", "partitions": [{"partition": 0, "begin": 0, "end": 2}], "messages": 2}
    assert (await stub.offsets("old")).data == {"topic": "old", "partitions": [{"partition": 0, "begin": 100, "end": 101}], "messages": 1}
    assert (await stub.offsets("none")).status == "error"
    for topic, partition in (("none", 0), ("t", 1)):
        bad = await stub.message_at(topic, partition, 0)
        assert bad.status == "error", (topic, partition)


# ── R3-5v ① — 재생: Kafka의 실제 begin/end와 띄엄 오프셋 ──

async def test_kafka_시드가_begin_end와_띄엄_오프셋을_주면_그대로_답하고_안_뜬_오프셋과_꼬리는_실패다():
    """평가자 회신(11-03): 재생 스냅샷의 토픽은 실제 begin/end와 뜬 메시지 몇 건(tail 덤프·값의 포인터 메시지)뿐이다 — 오프셋이 0부터의 차례가 아니다. 범위 안인데
    안 뜬 오프셋을 "보존 범위 밖"(없음)으로 답하면 틀린 사실이다 — 실패로. 범위 밖은 진짜 사실이라 전처럼. 옛 두 꼴(목록, begin + 차례 목록)은 그대로."""
    seed = {"begin": 1200, "end": 1210, "messages": [
        {"offset": 1203, "value": {"rows": [1]}, "timestamp": 1700000000000},
        {"offset": 1208, "value": {"rows": [2]}}, {"offset": 1209, "value": {"rows": [3]}}]}
    stub = StubKafkaInspector({"t.main": seed}, clock=_clock)
    got = (await stub.offsets("t.main")).data
    assert got["partitions"] == [{"partition": 0, "begin": 1200, "end": 1210}] and got["messages"] == 10
    hit = (await stub.message_at("t.main", 0, 1203)).data
    assert hit["found"] is True and hit["message"]["value"] == {"rows": [1]} and hit["message"]["timestamp"] == 1700000000000
    gap = await stub.message_at("t.main", 0, 1205)
    assert gap.status == "error" and gap.error.startswith("스냅샷 밖 — t.main[0]@1205는 안 떴다")
    before = (await stub.message_at("t.main", 0, 1199)).data
    assert before["found"] is False and "보존 범위 밖" in before["note"]
    assert (await stub.tail("t.main", limit=2)).data["messages"] == [{"rows": [2]}, {"rows": [3]}]
    short = await stub.tail("t.main", limit=3)
    assert short.status == "error" and short.error.startswith("스냅샷 밖 — t.main의 끝 3건 중 1건이 안 떴다")
    old = StubKafkaInspector({"a": [{"x": 1}, {"x": 2}], "b": {"begin": 5, "messages": [{"y": 1}]}}, clock=_clock)
    assert (await old.tail("a", limit=5)).data["messages"] == [{"x": 1}, {"x": 2}]
    assert (await old.message_at("b", 0, 5)).data["found"] is True and (await old.offsets("b")).data["partitions"][0]["end"] == 6
    assert (await old.list_topics()).data == ["a", "b"]
