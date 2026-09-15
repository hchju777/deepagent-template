"""등재 목록 그 자체 — **목록에 없으면 문이 안 열린다**(규율 9).

산문 규율은 읽지 않으면 무력하다. `tests/domain/test_ports.py`가 포트 표면을 단정하듯,
여기는 그 포트를 **이름으로 부르는 표**를 단정한다.
"""
from datetime import datetime

import pytest

from src.domain.actions import ACTIONS, action_problem, describe, run_action
from src.domain.envelope import ProbeResult

T0 = datetime(2026, 9, 15, 9, 0, 0)
CLOCK = lambda: T0                                                  # noqa: E731


class Recorder:
    """무엇이 불렸는지만 기록한다. **안 불린 것을 확인하는 것이 요점이다.**"""

    def __init__(self, *, result=None):
        self.calls: list[tuple] = []
        self._result = result

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self._result or ProbeResult.succeeded({"ok": True},
                                                         source=f"fake:{name}", clock=CLOCK)
        return call


class Bundle:
    def __init__(self, **adapters):
        for name in ("redis", "mongo", "kafka", "rest"):
            setattr(self, name, adapters.get(name))


# ── 표 자체 ────────────────────────────────────────────────────────

def test_쓰는_메서드는_등재되지_않는다():
    """포트에 없기도 하지만, **여기에도 없다.** 두 겹으로 닫는다."""
    forbidden = ("post", "put", "patch", "delete", "insert", "update", "drop",
                 "aggregate", "eval", "flush", "write", "remove", "create")
    leaked = [a for a in ACTIONS if any(w in a.split(".")[-1] for w in forbidden)]
    assert not leaked, f"쓰기로 읽힐 수 있는 action이 등재돼 있다: {leaked}"


def test_등재_목록이_포트_표면_안에_있다():
    """표가 포트에 없는 메서드를 가리키면 런타임에 AttributeError로만 드러난다."""
    from src.domain import ports
    surfaces = {"redis": ports.RedisReaderPort, "mongo": ports.MongoReaderPort,
                "kafka": ports.KafkaInspectorPort, "rest": ports.RestProberPort}
    for action, (adapter, method, _, _) in ACTIONS.items():
        assert adapter in surfaces, f"{action}이 모르는 포트를 가리킨다 — {adapter}"
        assert hasattr(surfaces[adapter], method), \
            f"{action}이 {surfaces[adapter].__name__}에 없는 메서드를 가리킨다 — {method}"


# ── 검사 ───────────────────────────────────────────────────────────

def test_미등재_action은_거부된다():
    assert "미등재 action" in action_problem("mongo.aggregate", {})


def test_스키마_밖_인자는_거부된다():
    problem = action_problem("redis.get", {"key": "k", "pattern": "x"})
    assert "모르는 인자" in problem and "pattern" in problem


def test_필수_인자가_없으면_거부된다():
    assert "필요한 인자가 없다" in action_problem("redis.get", {})


def test_선택_인자는_없어도_된다():
    assert action_problem("mongo.find", {"collection": "c", "filter": {}}) is None


def test_무엇을_물었는지_한_줄로_남는다():
    """응답만 보관하면 "0건"이 "현장이 멈췄다"인지 "질문을 잘못 던졌다"인지 모른다."""
    assert describe("redis.get", {"key": "oee:L3"}) == "redis.get key='oee:L3'"


# ── 실행 ───────────────────────────────────────────────────────────

async def test_미등재는_포트에_닿기_전에_막힌다():
    redis, mongo = Recorder(), Recorder()
    result = await run_action(Bundle(redis=redis, mongo=mongo), "mongo.aggregate",
                              {"pipeline": []}, clock=CLOCK)
    assert result.status == "error" and "미등재" in result.error
    assert redis.calls == [] and mongo.calls == []      # **아무것도 안 불렸다**


async def test_스키마_밖_인자도_포트에_닿기_전에_막힌다():
    """어댑터가 TypeError를 던지게 두면 보고서에 "Redis 조회 실패"라고 적힌다 —
    원인이 우리라는 사실이 지워진다."""
    redis = Recorder()
    result = await run_action(Bundle(redis=redis), "redis.get",
                              {"key": "k", "pattern": "x"}, clock=CLOCK)
    assert result.status == "error"
    assert redis.calls == []


async def test_위치_인자와_키워드_인자를_포트_시그니처대로_가른다():
    mongo = Recorder()
    await run_action(Bundle(mongo=mongo), "mongo.find",
                     {"collection": "twin_state", "filter": {"line": "L3"}, "limit": 5},
                     clock=CLOCK)
    assert mongo.calls[0] == ("find", ("twin_state", {"line": "L3"}), {"limit": 5})


async def test_config가_선언하지_않은_시스템은_거부된다():
    result = await run_action(Bundle(redis=None), "redis.get", {"key": "k"}, clock=CLOCK)
    assert result.status == "error" and "어댑터가 없다" in result.error


async def test_포트가_던져도_흡수한다():
    """포트는 던지지 않기로 돼 있다. 계약을 어기는 구현 하나가 라운드를 지우면 안 된다."""
    class Throws:
        async def get(self, key):
            raise ConnectionResetError("소켓이 끊겼다")

    result = await run_action(Bundle(redis=Throws()), "redis.get", {"key": "k"},
                              clock=CLOCK)
    assert result.status == "error" and "ConnectionResetError" in result.error


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_등재된_전부에_필수_인자_검사가_붙어_있다(action):
    """인자가 하나도 필수가 아닌 action은 "아무 인자 없이 불러도 된다"는 뜻이다."""
    _, _, required, _ = ACTIONS[action]
    assert required, f"{action}에 필수 인자 선언이 없다"
