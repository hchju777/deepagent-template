from datetime import datetime, timedelta, timezone

import pytest
from src.application.lifecycle import (ENGINE_SCHEMA_VERSION, LifecycleError, acquire_lease,
                                       is_timed_out, release_lease, transition)
from src.domain.cases import CaseRecord

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _rec(**kw):
    base = dict(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                created_at=T, updated_at=T)
    base.update(kw)
    return CaseRecord(**base)


def test_허용_전이만_통과하고_종결은_사유와_lease_해제를_남긴다():
    r = transition(_rec(), "investigating", clock=lambda: T)
    assert r.status == "investigating"
    r = transition(r, "awaiting_human", clock=lambda: T + timedelta(minutes=1))
    assert r.updated_at == T + timedelta(minutes=1)
    r = acquire_lease(r, "worker-a", clock=lambda: T, ttl_s=60)
    closed = transition(r, "closed", clock=lambda: T, reason="타임아웃")
    assert closed.closed_reason == "타임아웃" and closed.owner is None and closed.lease_until is None
    with pytest.raises(LifecycleError):
        transition(closed, "investigating", clock=lambda: T)
    # open↔awaiting_human은 접수 되묻기 전용으로 열려 있다(계획 12) — 그래프
    # 파킹은 여전히 investigating에서만 오고, 재개는 awaiting_human→investigating이다.
    parked = transition(_rec(), "awaiting_human", clock=lambda: T)
    assert transition(parked, "open", clock=lambda: T).status == "open"
    with pytest.raises(LifecycleError):
        transition(_rec(), "open", clock=lambda: T)                # open→open 없음


def test_lease는_타인의_유효_lease를_존중하고_만료면_빼앗는다():
    r = acquire_lease(_rec(), "a", clock=lambda: T, ttl_s=60)
    assert r.owner == "a" and r.lease_until == T + timedelta(seconds=60)
    assert acquire_lease(r, "b", clock=lambda: T + timedelta(seconds=30), ttl_s=60) is None
    taken = acquire_lease(r, "b", clock=lambda: T + timedelta(seconds=61), ttl_s=60)
    assert taken.owner == "b"
    same = acquire_lease(r, "a", clock=lambda: T + timedelta(seconds=30), ttl_s=60)
    assert same.owner == "a" and same.lease_until == T + timedelta(seconds=90)   # 갱신
    with pytest.raises(LifecycleError):
        release_lease(r, "b", clock=lambda: T)
    assert release_lease(r, "a", clock=lambda: T).owner is None


def test_타임아웃은_awaiting_human에만_적용():
    waiting = transition(transition(_rec(), "investigating", clock=lambda: T),
                         "awaiting_human", clock=lambda: T)
    assert not is_timed_out(waiting, clock=lambda: T + timedelta(hours=71), timeout_h=72)
    assert is_timed_out(waiting, clock=lambda: T + timedelta(hours=73), timeout_h=72)
    assert not is_timed_out(_rec(), clock=lambda: T + timedelta(hours=100), timeout_h=72)
    assert ENGINE_SCHEMA_VERSION == 1


def test_첨부가_status_since를_건드리지_않으면_타임아웃이_리셋되지_않는다():
    # I2: 게이트의 finding 첨부는 updated_at만 갱신하고 status_since는 그대로 둔다
    # (model_copy 직접 사용 — transition을 거치지 않는다. gate.py와 동일한 패턴).
    # T+100h에 첨부(updated_at만 T+100h로 갱신)해도, status_since는 여전히 T다 —
    # updated_at 기준이었다면 T+101h 시점엔 "1시간 전 갱신"이라 타임아웃이 아니어야
    # 하겠지만, status_since(T) 기준이므로 101시간 경과로 여전히 타임아웃이다.
    waiting = transition(transition(_rec(), "investigating", clock=lambda: T),
                         "awaiting_human", clock=lambda: T)
    assert waiting.status_since == T
    attached = waiting.model_copy(update={"updated_at": T + timedelta(hours=100)})
    assert attached.status_since == T                     # status_since는 안 움직인다
    assert is_timed_out(attached, clock=lambda: T + timedelta(hours=101), timeout_h=72)
