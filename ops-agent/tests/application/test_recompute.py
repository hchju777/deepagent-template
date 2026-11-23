"""원천 재집계(11b 3b) — 리드가 세운 기대 중 **세거나 더하면 확인되는 것**을 코드가 원천에서 다시 만들어
앞선 증거의 값과 대조한다. 로직을 실행하지 않는다. 기대값이 없거나 숫자가 아니면 불일치가 아니라 error다."""
from src.application.recompute import Recomputer, pluck
from src.domain.envelope import ProbeResult
from src.infrastructure.stubs import StubMongoReader

from tests.application.conftest import T0

DOCS = [{"line": "L1", "status": "alarm", "n": 3}, {"line": "L1", "status": "alarm", "n": 4},
        {"line": "L2", "status": "alarm", "n": 5}, {"line": "L1", "status": "normal", "n": 9}]


def _recomputer(raw: dict) -> Recomputer:
    mongo = StubMongoReader({"alarm_events": DOCS}, clock=lambda: T0)
    return Recomputer(mongo, raw.get, clock=lambda: T0)


async def test_count는_원천에서_세어_인용_증거의_값과_대조한다():
    r = _recomputer({"t-1.e1": {"badge": {"alarm": 2, "caution": 0}}})
    got = await r.count("alarm_events", {"status": "alarm", "line": "L1"}, {"evidence": "t-1.e1", "path": "badge.alarm"})
    assert got.status == "ok" and got.envelope.complete
    assert got.data == {"recomputed": 2, "expected": 2, "match": True, "evidence": "t-1.e1", "path": "badge.alarm"}
    wide = await r.count("alarm_events", {"status": "alarm"}, {"evidence": "t-1.e1", "path": "badge.alarm"})
    assert wide.data["recomputed"] == 3 and wide.data["match"] is False
    assert got.source.startswith("recompute.count ") and "alarm_events" in got.source


async def test_sum은_한_필드를_더한다():
    r = _recomputer({"t-1.e1": {"total": 12}})
    got = await r.sum("alarm_events", {"status": "alarm"}, "n", {"evidence": "t-1.e1", "path": "total"})
    assert got.status == "ok" and got.data["recomputed"] == 12 and got.data["match"] is True


async def test_기대값을_못_찾으면_불일치가_아니라_error다():
    r = _recomputer({"t-1.e1": {"badge": {"alarm": "many"}}})
    missing = await r.count("alarm_events", {}, {"evidence": "t-9.e1", "path": "x"})
    assert missing.status == "error" and "t-9.e1" in missing.error and "다시" in missing.error
    nopath = await r.count("alarm_events", {}, {"evidence": "t-1.e1", "path": "badge.caution"})
    assert nopath.status == "error" and "badge.caution" in nopath.error
    text = await r.count("alarm_events", {}, {"evidence": "t-1.e1", "path": "badge.alarm"})
    assert text.status == "error" and "숫자" in text.error
    shape = await r.count("alarm_events", {}, {"path": "x"})
    assert shape.status == "error" and "expect" in shape.error


def test_path는_점과_색인으로_증거_안을_가리킨다():
    data = {"items": [{"alarm": 7}, {"alarm": 8}], "n": 1}
    assert pluck(data, "items[1].alarm") == 8 and pluck(data, "n") == 1
    assert pluck([{"a": 2}], "[0].a") == 2
    assert pluck(data, "items[5].alarm") is None and pluck(data, "nope") is None and pluck(data, "") is None


async def test_표본이_잘리면_합계를_안_낸다():
    class Cut:
        async def find(self, collection, filter, *, sort=None, limit=None, projection=None):
            return ProbeResult.succeeded([{"n": 1}], source="cut", clock=lambda: T0, truncated_reason="limit 1")

    r = Recomputer(Cut(), {"t-1.e1": {"total": 1}}.get, clock=lambda: T0)
    got = await r.sum("c", {}, "n", {"evidence": "t-1.e1", "path": "total"})
    assert got.status == "error" and "잘려" in got.error


async def test_원천_읽기가_실패하면_그대로_실패다():
    r = _recomputer({"t-1.e1": {"total": 1}})
    got = await r.count("alarm_events", {"n": {"$regex": "x"}}, {"evidence": "t-1.e1", "path": "total"})
    assert got.status == "error"
