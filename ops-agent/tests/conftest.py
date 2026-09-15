"""테스트 전역 픽스처.

시계를 고정하는 헬퍼가 여기 있는 이유: 시간에 의존하는 판정(freshness,
retention, lease 만료)이 앞으로 계속 늘어나고, 그때마다 각자 lambda를
만들면 "어떤 값을 썼는지"가 파일마다 달라져 실패를 읽기 어려워진다.
"""
from datetime import UTC, datetime

import pytest

# 모든 테스트가 공유하는 기준 시각. 값 자체에 뜻은 없고 고정돼 있다는 것이 뜻이다.
T0 = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock():
    """항상 T0를 돌려주는 시계."""
    return lambda: T0
