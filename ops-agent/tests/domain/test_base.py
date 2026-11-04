from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.domain.base import StrictModel
from tests.conftest import T0


class _Sample(StrictModel):
    name: str
    enabled: bool = True


def test_모르는_키는_조용히_버려지지_않고_거부된다():
    # 오타(enabld)가 기본값을 그대로 쓰게 두면 "껐다고 믿는데 켜져 있는" 상태가 된다.
    with pytest.raises(ValidationError) as caught:
        _Sample(name="check-a", enabld=False)
    assert "enabld" in str(caught.value)


def test_선언한_키는_평소처럼_받는다():
    assert _Sample(name="check-a", enabled=False).enabled is False


# ── 시계 주입 ────────────────────────────────────────────────────────

def _is_stale(observed_at: datetime, *, clock, max_age_s: int) -> bool:
    """관측 시각이 너무 오래됐는가 — 시계를 인자로 받는 함수의 최소 예시."""
    return (clock() - observed_at).total_seconds() > max_age_s


def test_시계를_주입하면_판정이_결정론이_된다(clock):
    assert _is_stale(T0 - timedelta(seconds=61), clock=clock, max_age_s=60) is True
    assert _is_stale(T0 - timedelta(seconds=59), clock=clock, max_age_s=60) is False


def test_같은_함수를_실제_시계로_불러도_같은_모양이다():
    # 프로덕션 경로는 진짜 시계를 넘긴다 — 코드가 갈라지지 않는다는 것이 요점.
    now = datetime.now(UTC)
    assert _is_stale(now, clock=lambda: now, max_age_s=60) is False
