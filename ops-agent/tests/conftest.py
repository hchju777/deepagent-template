"""테스트 전역 픽스처.

시계를 고정하는 헬퍼가 여기 있는 이유: 시간에 의존하는 판정(freshness,
retention, lease 만료)이 앞으로 계속 늘어나고, 그때마다 각자 lambda를
만들면 "어떤 값을 썼는지"가 파일마다 달라져 실패를 읽기 어려워진다.
"""
from datetime import UTC, datetime

import pytest

# 모든 테스트가 공유하는 기준 시각. 값 자체에 뜻은 없고 고정돼 있다는 것이 뜻이다.
T0 = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _git_stays_offline(monkeypatch):
    """**테스트는 git으로 네트워크를 타지 않는다.** 물리적으로 막는다.

    픽스처가 `origin`에 `https://git.example.com/...` 같은 자리표시자를 적는다 —
    `status_of`의 origin 대조가 볼 값이 필요해서다. 그 주소는 존재하지 않고
    아무도 접속하지 않지만, **"안 한다"는 주장과 "못 한다"는 사실은 다르다.**
    `GIT_ALLOW_PROTOCOL=file`이면 git이 http/https/ssh를 아예 거부하므로,
    누가 실수로 네트워크를 타는 픽스처를 넣으면 **그 자리에서 빨개진다.**

    submodule 픽스처의 url은 로컬 경로라 이 제한에 걸리지 않는다.
    """
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")


@pytest.fixture
def clock():
    """항상 T0를 돌려주는 시계."""
    return lambda: T0
