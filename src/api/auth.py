"""토큰 → 주체 역조회. 인증은 표 하나다 (계획 13 설계 결정 ③).

토큰 발급·회전·세션·OAuth는 만들지 않는다 — 리버스 프록시가 그것을 하고 우리는
주체만 받는 배치가 표준이다. 여기는 `Authorization: Bearer <토큰>`을 `access.subjects`
표에서 역조회할 뿐이다.

**틀린 토큰은 익명이 아니라 거부다.** 틀린 토큰을 익명으로 떨어뜨리면 `access.allow`가
비어 있는 설치에서 아무 문자열이나 통과한다 — "토큰을 냈는데 틀렸다"는 "안 냈다"와
다른 사실이다. 익명(헤더 없음) 자체는 거부가 아니고, 거부는 `access.allow`가
정한다(계획 12 `can_access`).
"""
import hmac

from pydantic import SecretStr


class AuthError(Exception):
    """틀리거나 형식이 어긋난 자격 증명. 핸들러가 401로 옮긴다."""


def subject_of(authorization: str | None, subjects: dict[str, SecretStr]) -> str | None:
    """헤더에서 주체를 얻는다. 표가 비어 있으면 무조건 익명(None)."""
    if not subjects:
        return None
    if authorization is None:
        return None
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1]:
        raise AuthError("Authorization 헤더는 'Bearer <토큰>' 형식이어야 한다")
    presented = parts[1].encode()
    # `==`는 첫 불일치 바이트에서 멈춘다 — 타이밍으로 토큰을 한 바이트씩 캘 수 있다.
    # 모든 주체를 끝까지 돈다(일치를 찾아도 멈추지 않는다) — 주체 수도 새지 않게.
    matched: str | None = None
    for subject, token in subjects.items():
        if hmac.compare_digest(presented, token.get_secret_value().encode()):
            matched = subject
    if matched is None:
        raise AuthError("토큰이 어느 주체와도 맞지 않는다")
    return matched
