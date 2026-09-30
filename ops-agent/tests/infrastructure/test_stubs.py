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
