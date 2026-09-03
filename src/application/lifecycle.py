"""케이스 수명주기 순수 함수 — 스펙 §계획 4b.

전이·임차(lease)·타임아웃 판정을 여기서만 다룬다. 모든 함수는 시계를
`clock` 인자로 주입받아 순수하게 유지한다(테스트 용이성·재현성) —
`datetime.now()`를 직접 부르지 않는다. 예외는 LifecycleError 하나로
통일한다: 호출부가 "허용되지 않은 전이"와 "권한 없는 lease 해제"를
같은 방식으로 처리할 수 있게 하기 위해서다.
"""
from datetime import datetime, timedelta
from typing import Callable

from src.domain.cases import CaseRecord, CaseStatus

ENGINE_SCHEMA_VERSION = 1

Clock = Callable[[], datetime]


class LifecycleError(Exception):
    """허용되지 않은 전이 또는 권한 없는 lease 조작."""


# 상태 전이표 — 각 상태에서 갈 수 있는 다음 상태의 집합.
ALLOWED: dict[CaseStatus, set[CaseStatus]] = {
    "open": {"investigating", "closed"},
    "investigating": {"awaiting_human", "closed"},
    "awaiting_human": {"investigating", "closed"},
    "closed": set(),
}


def transition(record: CaseRecord, to: CaseStatus, *, clock: Clock,
               reason: str | None = None) -> CaseRecord:
    """record.status → to로 전이한다. 허용표에 없으면 LifecycleError.

    status_since를 함께 스탬프한다(계획 4b I2) — updated_at은 게이트의 attach처럼
    status와 무관한 갱신에도 움직이므로, "이 상태로 얼마나 오래 있었나"를 재는
    is_timed_out·retention ④는 status_since를 봐야 첨부가 타임아웃을 리셋하지
    않는다. transition은 항상 실제 상태 변화만 표현하므로(전이표에 자기 자신으로의
    전이가 없다) 호출될 때마다 status_since를 새로 찍어도 안전하다.

    종결(to=="closed")이면 closed_reason을 남기고 lease를 해제한다
    (소유자가 더 이상 종결된 케이스를 붙들고 있지 않도록).
    """
    allowed = ALLOWED.get(record.status, set())
    if to not in allowed:
        raise LifecycleError(f"{record.status!r} → {to!r} 전이는 허용되지 않는다")
    now = clock()
    update: dict[str, object] = {"status": to, "updated_at": now, "status_since": now}
    if to == "closed":
        update["closed_reason"] = reason
        update["owner"] = None
        update["lease_until"] = None
    return record.model_copy(update=update)


def acquire_lease(record: CaseRecord, owner: str, *, clock: Clock,
                   ttl_s: float) -> CaseRecord | None:
    """owner가 lease를 획득(또는 갱신)할 수 있으면 갱신된 레코드를, 아니면 None을 반환한다.

    획득 가능 조건: 현재 lease가 없거나(owner is None), 이미 같은 owner가
    쥐고 있거나(갱신), 기존 lease가 만료됐을 때(lease_until < clock()).
    """
    now = clock()
    can_acquire = (record.owner is None
                  or record.owner == owner
                  or (record.lease_until is not None and record.lease_until < now))
    if not can_acquire:
        return None
    return record.model_copy(update={"owner": owner, "lease_until": now + timedelta(seconds=ttl_s)})


def release_lease(record: CaseRecord, owner: str, *, clock: Clock) -> CaseRecord:
    """같은 owner만 lease를 해제할 수 있다. 아니면 LifecycleError."""
    if record.owner != owner:
        raise LifecycleError(f"{owner!r}는 이 케이스의 lease 소유자가 아니다")
    return record.model_copy(update={"owner": None, "lease_until": None})


def is_timed_out(record: CaseRecord, *, clock: Clock, timeout_h: int) -> bool:
    """awaiting_human 상태로 timeout_h 시간을 초과했는지. 다른 상태는 항상 False.

    status_since(없으면 updated_at) 기준 — 게이트의 finding 첨부처럼 status와
    무관한 updated_at 갱신이 타임아웃을 무한히 리셋하지 않게 한다(I2).
    """
    if record.status != "awaiting_human":
        return False
    since = record.status_since or record.updated_at
    return clock() - since > timedelta(hours=timeout_h)
