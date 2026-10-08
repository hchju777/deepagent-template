"""LLM 호출의 **속도** — 429(분당 할당량) 뒤 기다리기와 호출 간 최소 간격.

사내 10-08: 리드가 빨라지자 분당 할당량에 걸렸고, 어댑터는 0초 만에 재시도해 두 번 다 날렸다. 할당량은 모델을 가리지 않고
**게이트웨이 단위**로 공유된다 — 리드 모델이 쓴 몫만큼 판정 모델도 막힌다. 그래서 간격은 어댑터가 아니라 base_url이 쥔다.

429 뒤에는 응답 본문의 `nextAccessTime`(보통 다음 분)까지 기다렸다 한 번만 다시 묻고, 그 대기는 실패로 세지 않는다. 모양을
확인 못 했으니 ISO·naive(now의 시간대)·epoch 초·밀리초를 다 받고, 없으면 `Retry-After`, 그것도 없으면 60초 — 어느 쪽이든
`rate_wait_max_s`가 자른다(한 호출이 분 단위로 매달리지 않게).
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable

_RATE_FALLBACK_S = 60.0
_KEY = "nextAccessTime"


def next_access_wait(body: Any, *, now: datetime) -> float | None:
    """본문 어딘가의 `nextAccessTime`까지 몇 초인가(과거면 0). 없거나 못 읽으면 None."""
    raw = _find(body, _KEY)
    if raw is None:
        return None
    when = _parse_when(raw, tz=now.tzinfo or timezone.utc)
    if when is None:
        return None
    return max(0.0, round((when - now).total_seconds(), 3))


def quota_wait(body: Any, headers: Any, *, now: datetime, cap: float) -> float:
    """429 뒤 기다릴 초 — `nextAccessTime` → `Retry-After` → 기본 60초, 전부 `cap` 안에서."""
    wait = next_access_wait(body, now=now)
    if wait is None:
        retry_after = _header(headers, "retry-after")
        try:
            wait = float(retry_after) if retry_after is not None else None
        except ValueError:
            wait = None
    if wait is None:
        wait = _RATE_FALLBACK_S
    return min(max(0.0, wait), cap)


def _find(node: Any, key: str):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find(value, key)
            if found is not None:
                return found
    return None


def _parse_when(raw: Any, *, tz) -> datetime | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        seconds = raw / 1000.0 if raw > 1e11 else float(raw)        # 밀리초면 1e11을 넘는다(1973년 이후)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        text = raw.strip()
        if text.replace(".", "", 1).isdigit():
            return _parse_when(float(text), tz=tz)
        try:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return when if when.tzinfo else when.replace(tzinfo=tz)
    return None


def _header(headers: Any, name: str):
    if headers is None:
        return None
    try:
        return headers.get(name) or headers.get(name.title()) or headers.get(name.upper())
    except AttributeError:
        return None


class Pacer:
    """게이트웨이 하나의 호출 시계. `wait_turn(interval)`은 직전 호출에서 `interval`초가 지날 때까지 기다린 뒤 지금을 적는다."""

    def __init__(self, *, ticker: Callable[[], float] = time.monotonic):
        self._ticker = ticker
        self._last: float | None = None

    async def wait_turn(self, interval_s: float, *, sleep=asyncio.sleep) -> float:
        waited = 0.0
        if self._last is not None and interval_s > 0:
            remaining = self._last + interval_s - self._ticker()
            if remaining > 0:
                await sleep(remaining)
                waited = remaining
        self._last = self._ticker()
        return waited


_PACERS: dict[str, Pacer] = {}


def pacer_for(base_url: str) -> Pacer:
    """같은 게이트웨이(base_url, 끝 `/` 무시)면 같은 pacer — 리드와 판정 어댑터가 하나의 할당량을 나눠 쓴다."""
    key = (base_url or "").rstrip("/")
    if key not in _PACERS:
        _PACERS[key] = Pacer()
    return _PACERS[key]
