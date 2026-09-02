"""프로브 레지스트리 — 점검(CheckConfig)을 어댑터 호출로 잇는다.

프로브 계약(스펙 §4.6-9): 프로브는 절대 raise하지 않는다. 어댑터 미설정,
target 형식 오류, params 부재 등은 전부 error ProbeResult로 돌려준다 —
최외곽 try/except가 예상 밖 예외까지 마지막 방어선으로 잡는다.
"""
from typing import Any, Awaitable, Callable

from src.config.schema_site import CheckConfig
from src.domain.envelope import Envelope, ProbeResult
from src.infrastructure.factory import AdapterSet

ProbeFn = Callable[..., Awaitable[ProbeResult]]

_TARGET_PREFIX_TO_PROBE = {
    "rest": "rest_get",
    "redis": "redis_get",
    "mongo": "mongo_recent",
    "kafka": "kafka_lag",
}


def _error(msg: str, clock) -> ProbeResult:
    return ProbeResult(status="error", envelope=Envelope(observed_at=clock()), error=msg)


def _split_target(target: str | None) -> tuple[str, str] | None:
    """"kind:나머지" 형식을 분리한다. 콜론이 없거나 target이 없으면 None."""
    if not target or ":" not in target:
        return None
    kind, _, rest = target.partition(":")
    return kind, rest


async def rest_get(adapters: AdapterSet, check: CheckConfig, *, clock) -> ProbeResult:
    """target "rest:/path" → adapters.rest.get(path)."""
    try:
        if adapters.rest is None:
            return _error("어댑터 미설정: rest", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "rest":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, path = parts
        return await adapters.rest.get(path)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


async def redis_get(adapters: AdapterSet, check: CheckConfig, *, clock) -> ProbeResult:
    """target "redis:key" → adapters.redis.get(key)."""
    try:
        if adapters.redis is None:
            return _error("어댑터 미설정: redis", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "redis":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, key = parts
        return await adapters.redis.get(key)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


async def mongo_recent(adapters: AdapterSet, check: CheckConfig, *, clock) -> ProbeResult:
    """target "mongo:coll" → 최근 문서 find(sort=ts_field desc, limit=sample or 20)."""
    try:
        if adapters.mongo is None:
            return _error("어댑터 미설정: mongo", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "mongo":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, coll = parts
        ts_field = check.params.get("ts_field", "ts")
        limit = check.sample or 20
        return await adapters.mongo.find(coll, {}, sort=[(ts_field, -1)], limit=limit)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


async def kafka_lag(adapters: AdapterSet, check: CheckConfig, *, clock) -> ProbeResult:
    """params["group"] → adapters.kafka.group_offsets(group)."""
    try:
        if adapters.kafka is None:
            return _error("어댑터 미설정: kafka", clock)
        group = check.params.get("group")
        if not group:
            return _error("params에 group이 없다", clock)
        return await adapters.kafka.group_offsets(group)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


PROBES: dict[str, ProbeFn] = {
    "rest_get": rest_get,
    "redis_get": redis_get,
    "mongo_recent": mongo_recent,
    "kafka_lag": kafka_lag,
}


def resolve_probe(check: CheckConfig) -> str | None:
    """check.probe가 있으면 그것, 없으면 target 접두사로 기본 프로브를 고른다."""
    if check.probe is not None:
        return check.probe
    parts = _split_target(check.target)
    if parts is None:
        return None
    kind, _ = parts
    return _TARGET_PREFIX_TO_PROBE.get(kind)
