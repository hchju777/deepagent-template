"""실패가 예외가 아니라 값으로 표현되는지, 그리고 모순된 상태를 만들 수 없는지."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.domain.envelope import Envelope, ProbeResult
from tests.conftest import T0


# ── 봉투: "잘렸다"를 조용히 넘기지 않는다 ──────────────────────────────

def test_잘린_표본은_이유를_반드시_말해야_한다():
    # 상한에 걸려 100건만 읽은 것과 실제로 100건뿐인 것은 완전히 다른 사실이다.
    with pytest.raises(ValidationError, match="truncated_reason이 필요하다"):
        Envelope(observed_at=T0, complete=False)


def test_안_잘렸는데_이유가_있으면_모순이라_거부한다():
    with pytest.raises(ValidationError, match="둘 중 하나가 틀렸다"):
        Envelope(observed_at=T0, complete=True, truncated_reason="limit 100")


def test_잘린_사실은_이유와_함께_남는다():
    env = Envelope(observed_at=T0, complete=False, truncated_reason="limit=100에 걸림")
    assert env.complete is False
    assert "100" in env.truncated_reason


# ── 결과: 성공과 실패가 한 타입 ────────────────────────────────────────

def test_실패에_원인이_없으면_거부한다():
    with pytest.raises(ValidationError, match="error 원인이 필요하다"):
        ProbeResult(status="error", source="redis:oee", envelope=Envelope(observed_at=T0))


def test_성공인데_에러가_실려_있으면_거부한다():
    with pytest.raises(ValidationError, match="error가 없어야 한다"):
        ProbeResult(status="ok", source="redis:oee", error="timeout",
                    envelope=Envelope(observed_at=T0))


# ── 생성 헬퍼: 올바른 길이 제일 쉬운 길이어야 한다 ──────────────────────

def test_성공_헬퍼가_주입된_시계로_관측시각을_찍는다(clock):
    result = ProbeResult.succeeded({"oee": 87.2}, source="redis:mx/gumi:oee:L3", clock=clock)
    assert result.status == "ok"
    assert result.data == {"oee": 87.2}
    assert result.envelope.observed_at == T0        # datetime.now()가 아니라 주입된 시계
    assert result.envelope.complete is True


def test_잘림_이유를_주면_자동으로_불완전_봉투가_된다(clock):
    result = ProbeResult.succeeded([1, 2, 3], source="mongo:data.defects",
                                   clock=clock, truncated_reason="limit=3")
    assert result.envelope.complete is False
    assert result.envelope.truncated_reason == "limit=3"


def test_실패_헬퍼는_던지지_않고_돌려준다(clock):
    result = ProbeResult.failed("ConnectionError: timeout", source="mongo:data.oee", clock=clock)
    assert result.status == "error"
    assert "timeout" in result.error
    assert result.data is None
    assert result.envelope.observed_at == T0        # 실패에도 "언제 실패했나"가 남는다


# ── 호출자가 try/except 없이 분기할 수 있는가 ──────────────────────────

async def _read_oee(adapter, *, clock) -> str:
    """어댑터가 죽어 있어도 이 함수는 정상 종료한다 — 무raise 규율의 사용 예."""
    result = await adapter.get("oee:L3")
    if result.status == "error":
        return f"읽기 실패: {result.error}"
    return f"OEE={result.data}"


class _DeadRedis:
    """붙지 않는 Redis를 흉내낸다. 던지지 않고 error를 돌려준다."""

    def __init__(self, clock):
        self._clock = clock

    async def get(self, key):
        return ProbeResult.failed("ConnectionRefusedError", source=f"redis:{key}", clock=self._clock)


async def test_어댑터가_죽어도_호출자는_try_except_없이_계속_간다(clock):
    message = await _read_oee(_DeadRedis(clock), clock=clock)
    assert message == "읽기 실패: ConnectionRefusedError"


def test_관측시각은_타임존을_가진다(clock):
    result = ProbeResult.succeeded(1, source="redis:x", clock=clock)
    assert result.envelope.observed_at.tzinfo is not None, (
        "naive datetime은 나중에 다른 타임존 값과 비교할 때 TypeError로 터진다")
    assert result.envelope.observed_at == datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
