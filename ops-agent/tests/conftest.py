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
    **`GIT_ALLOW_PROTOCOL=file`을 쓰지 않는다.** 그건 차단 목록이 아니라
    허용 목록이라, `file`을 적는 순간 `protocol.file.allow=always`와 같은 뜻이
    된다 — 처음에 그렇게 썼다가 "픽스처가 file 허락을 안 탄다"는 자물쇠를
    **내가 조용히 풀어 버렸다**(RED 스윕이 잡았다).

    그래서 네트워크 프로토콜만 콕 집어 막는다. submodule 픽스처의 url은 로컬
    경로라 영향이 없고, file 허락은 필요한 한 곳에서만 따로 건다.
    """
    from tests.support import add_git_config

    for scheme in ("http", "https", "ssh", "git"):
        add_git_config(monkeypatch, f"protocol.{scheme}.allow", "never")


@pytest.fixture
def clock():
    """항상 T0를 돌려주는 시계."""
    return lambda: T0
