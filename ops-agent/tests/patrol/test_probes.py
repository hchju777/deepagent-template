"""점검이 선언한 프로브를 읽는다 — **판정은 안 한다**(5단계)."""
from datetime import datetime

import pytest

from src.config.schema_patrol import CheckConfig
from src.domain.envelope import ProbeResult
from src.patrol.probes import run_probes

T0 = datetime(2026, 9, 15, 9, 0, 0)
CLOCK = lambda: T0                                                  # noqa: E731


class Rest:
    def __init__(self, replies=None, *, fail=None):
        self._replies = replies or {}
        self._fail = fail
        self.asked: list[str] = []

    async def query(self, entry, params):
        self.asked.append(entry)
        if self._fail and entry in self._fail:
            return ProbeResult.failed(self._fail[entry], source=f"rest:{entry}", clock=CLOCK)
        return ProbeResult.succeeded(self._replies.get(entry), source=f"rest:{entry}",
                                     clock=CLOCK)


class Bundle:
    def __init__(self, rest=None):
        self.redis = self.mongo = self.kafka = None
        self.rest = rest


def check(**overrides) -> CheckConfig:
    body = {"concern": "operation", "probes": {
        "badge":  {"action": "rest.query",
                   "params": {"entry": "summary_badge", "params": {}}},
        "status": {"action": "rest.query",
                   "params": {"entry": "prod_status", "params": {}}}},
        "rule": "items_all_zero",
        "params": {"items": {"probe": "badge", "path": "response"},
                   "identity": ["group", "title"],
                   "counts": ["alarm", "caution", "normal"],
                   "only_when": {"probe": "status", "path": "response.status",
                                 "equals": "In Production"}}}
    body.update(overrides)
    return CheckConfig.model_validate(body)


async def test_선언한_프로브를_전부_읽는다():
    rest = Rest({"summary_badge": [{"title": "Target Rate"}],
                 "prod_status": {"status": "In Production"}})
    probes = await run_probes("badge_all_zero", check(), adapters=Bundle(rest),
                              site="mx/gumi", clock=CLOCK)
    assert probes.status == "ok"
    assert sorted(probes.results) == ["badge", "status"]
    assert sorted(rest.asked) == ["prod_status", "summary_badge"]


async def test_이름으로_결과를_찾는다():
    """rule이 `"items": "badge"`처럼 **이름으로** 가리킨다.

    순서나 인덱스로 가리키면 프로브를 하나 끼워 넣을 때 조용히 어긋나고, 그
    어긋남은 "판정이 이상하다"로만 드러난다.
    """
    rest = Rest({"summary_badge": ["B"], "prod_status": {"status": "In Production"}})
    probes = await run_probes("c", check(), adapters=Bundle(rest), site="s", clock=CLOCK)
    assert probes.results["badge"].data == ["B"]
    assert probes.results["status"].data == {"status": "In Production"}


async def test_하나라도_못_읽으면_unreachable이다():
    """전부-또는-전무. "생산 중인지 모르는데 0/0/0"은 이상인지 **알 수 없다** —
    `ok`로 접으면 놓치고 `finding`이면 거짓 알람이다."""
    rest = Rest({"summary_badge": ["B"]}, fail={"prod_status": "ConnectTimeout"})
    probes = await run_probes("c", check(), adapters=Bundle(rest), site="s", clock=CLOCK)
    assert probes.status == "unreachable"
    assert probes.failed == ["status"]
    assert "ConnectTimeout" in probes.reason()


async def test_한_프로브가_실패해도_나머지는_읽힌다():
    """실패한 것 때문에 성공한 것까지 버리면 다음 라운드에 다시 읽어야 한다."""
    rest = Rest({"summary_badge": ["B"]}, fail={"prod_status": "503"})
    probes = await run_probes("c", check(), adapters=Bundle(rest), site="s", clock=CLOCK)
    assert probes.results["badge"].status == "ok"
    assert probes.results["badge"].data == ["B"]


async def test_어댑터가_던져도_흡수한다():
    """순찰 잡 하나가 raise하면 스케줄러가 그 잡을 조용히 빼버릴 수 있다 —
    그러면 순찰이 죽어도 아무도 모른다."""
    class Explodes:
        async def query(self, entry, params):
            raise RuntimeError("어댑터가 계약을 어겼다")

    probes = await run_probes("c", check(), adapters=Bundle(Explodes()),
                              site="s", clock=CLOCK)
    assert probes.status == "unreachable"
    assert probes.failed == ["badge", "status"]


async def test_동시에_읽는다():
    """as_of 정렬이다. badge와 status를 읽는 시점이 벌어지면 "생산 중이었는데 그
    사이에 멈춤"이 "생산 중인데 0/0/0"으로 보인다.

    **타임라인의 모양을 본다.** 처음엔 "둘 다 시작됐고 이름이 다르다"만 봤는데,
    그건 순차 실행도 통과한다(끝나고 나면 둘 다 시작돼 있으니까). 동시라는 것은
    **둘째가 시작될 때 첫째가 아직 안 끝났다**는 뜻이고, 그게 보이는 유일한 자리가
    시작·끝이 섞이는 순서다.
    """
    import asyncio

    timeline: list[str] = []

    class Slow:
        async def query(self, entry, params):
            timeline.append(f"start:{entry}")
            await asyncio.sleep(0.01)
            timeline.append(f"end:{entry}")
            return ProbeResult.succeeded(None, source=entry, clock=CLOCK)

    await run_probes("c", check(), adapters=Bundle(Slow()), site="s", clock=CLOCK)
    kinds = [item.split(":")[0] for item in timeline]
    # 동시: start start end end   /   순차: start end start end
    assert kinds == ["start", "start", "end", "end"], timeline


def test_활성_점검만_돈다():
    from src.config.schema_patrol import PatrolConfig
    base = check().model_dump()
    cfg = PatrolConfig.model_validate({"checks": {
        "on": base, "off": {**base, "enabled": False}}})
    assert sorted(cfg.active()) == ["on"]
