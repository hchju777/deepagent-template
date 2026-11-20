"""프록시 예비 접속(R3-5z ㉔ — 사람 요청 11-18). 사이트 config의 접속마다 `proxy_` 예비 주소를 둔다 — 원 주소가 먼저, 원 주소가 **접속 단계에서** 실패하면
프록시, 프록시도 실패하면 실패.

## 왜 접속 단계 실패만 대체하나

키 없음·404·인증 실패·권한 없음은 대상이 **답한** 것이다. 다른 길로 같은 문을 다시 두드리면 같은 답이거나, 답이 갈려 사실이 흐려진다. 연결 거부·접속 타임아웃·
이름 해석 실패만이 "이 길로는 못 닿았다"이다. 어느 예외가 그것인지는 라이브러리마다 달라 어댑터가 판정 함수를 댄다.

## 왜 (사이트, 자원)을 프로세스 안에서 고정하나

원 주소가 접속 타임아웃으로 죽어 있으면 읽기마다 그 타임아웃을 문다 — 걷기 30초 상한 안에 원 → 프록시가 끝나야 한다. 어댑터는 케이스마다 다시 만들어지므로
고정은 어댑터가 아니라 프로세스에 둔다.

## 경로 표시가 대체가 걸린 접속에서만인 이유

증거의 `source` 글자는 증거와 보고에 실리고, 레포 밖 재생 도구가 옛 녹화와 맞춘다. 프록시 키가 없거나 스위치가 꺼졌으면 글자가 지금과 같아야 한다.
"""
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

SWITCH = "AGENT_PROXY_FALLBACK"
# 원 주소의 접속 타임아웃 — 대체가 걸린 접속에서만 쓴다(프록시 쪽과 대체가 없는 접속은 지금 값 그대로).
ORIGIN_CONNECT_S = 3.0
_OFF = frozenset({"0", "false", "off", "no"})
PINNED: set[tuple[str, str]] = set()

T = TypeVar("T")


def fallback_on(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(SWITCH, "")).strip().lower() not in _OFF


def switch_line(environ: Mapping[str, str] | None = None) -> str:
    return f"프록시 예비 접속: {'켬' if fallback_on(environ) else '끔'} ({SWITCH} — 0·false·off·no면 끈다)"


def _text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class ProxyAlsoFailed(Exception):
    """원이 접속 단계에서 실패했고 프록시도 실패했다 — 두 오류를 다 든다(하나만 남기면 사람이 원 주소 문제인지 프록시 문제인지 못 가른다)."""

    def __init__(self, origin: BaseException, proxy: BaseException):
        self.origin, self.proxy = origin, proxy
        super().__init__(f"원 {_text(origin)} → 프록시 {_text(proxy)}")


class Route:
    """(사이트, 자원) 하나의 경로. `proxy`가 비면(None·빈 문자열·빈 리스트) 대체가 없다."""

    def __init__(self, site: str, resource: str, proxy: Any, *, enabled: bool = True, pinned: set[tuple[str, str]] | None = None):
        self.site, self.resource, self.proxy = site, resource, proxy
        self.enabled = enabled
        self._pinned = PINNED if pinned is None else pinned

    @property
    def armed(self) -> bool:
        return bool(self.proxy) and self.enabled

    @property
    def on_proxy(self) -> bool:
        return self.armed and (self.site, self.resource) in self._pinned

    def mark(self, proxy: bool) -> str:
        """`source` 끝 토막 — 대체가 걸린 접속에서만."""
        if not self.armed:
            return ""
        return " · 경로=프록시" if proxy else " · 경로=원"

    async def attempt(self, run: Callable[[bool], Awaitable[T]], *, connect_failure: Callable[[BaseException], bool]) -> tuple[T, bool]:
        """`run(프록시인가)` → (결과, 프록시로 읽었나). 원의 접속 단계 실패만 고정하고 프록시로 한 번 더. 그 밖의 예외는 그대로 올린다 — 어댑터의 최외곽이
        `ProbeResult.failed`로 바꾼다."""
        if self.on_proxy:
            return await run(True), True
        try:
            return await run(False), False
        except Exception as exc:
            if not self.armed or not connect_failure(exc):
                raise
            self._pinned.add((self.site, self.resource))
            try:
                return await run(True), True
            except Exception as again:
                raise ProxyAlsoFailed(exc, again) from again
