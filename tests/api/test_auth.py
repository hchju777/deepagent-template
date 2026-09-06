"""토큰 → 주체 역조회 — 인증은 표 하나다 (계획 13 설계 결정 ③)."""
import inspect

import pytest
from pydantic import SecretStr

from src.api import auth
from src.api.auth import AuthError, subject_of

SUBJECTS = {"alice": SecretStr("tok-a"), "bob": SecretStr("tok-b")}


def test_토큰이_주체로_역조회된다():
    assert subject_of("Bearer tok-b", SUBJECTS) == "bob"


def test_표가_비어_있으면_익명이다():
    # 단일 팀 설치에 토큰을 강요하지 않는다 — 계획 12의 access.allow가 빈 것과 짝이다.
    assert subject_of("Bearer 아무거나", {}) is None
    assert subject_of(None, {}) is None


def test_표가_있는데_헤더가_없으면_익명이다():
    # 익명 자체는 거부가 아니다 — 거부는 access.allow가 정한다(계획 12 can_access).
    assert subject_of(None, SUBJECTS) is None


def test_틀린_토큰은_익명이_아니라_거부다():
    # 틀린 토큰을 익명으로 떨어뜨리면 allow가 비어 있는 설치에서 아무 문자열이나
    # 통과한다 — "토큰을 냈는데 틀렸다"는 "안 냈다"와 다르다.
    with pytest.raises(AuthError):
        subject_of("Bearer 틀림", SUBJECTS)


def test_형식이_틀린_헤더는_거부다():
    for bad in ("tok-a", "Basic tok-a", "Bearer", "Bearer  ", "bearer tok-a tok-b"):
        with pytest.raises(AuthError):
            subject_of(bad, SUBJECTS)


def test_비교는_상수_시간이다():
    # 문자열 == 은 첫 불일치 바이트에서 멈춘다 — 타이밍으로 토큰을 한 바이트씩 캘 수 있다.
    assert "compare_digest" in inspect.getsource(auth)
