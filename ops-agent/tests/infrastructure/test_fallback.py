"""프록시 예비 접속의 공용 경로(R3-5z ㉔) — 스위치, (사이트, 자원) 고정, 원 → 프록시 시도. 어댑터마다의 접속은 각 어댑터 테스트가 본다."""
import pytest

from src.infrastructure.fallback import SWITCH, ProxyAlsoFailed, Route, fallback_on, switch_line


class _Refused(Exception):
    pass


class _Answered(Exception):
    pass


def _refused(exc):
    return isinstance(exc, _Refused)


def _runner(origin, proxy="proxy-ok"):
    """`run(proxy)` — 원·프록시마다 값이나 예외. 부른 차례를 남긴다."""
    calls = []

    async def run(on_proxy):
        calls.append("프록시" if on_proxy else "원")
        out = proxy if on_proxy else origin
        if isinstance(out, BaseException):
            raise out
        return out
    return run, calls


def test_스위치는_기본_켬이고_0_false_off_no면_끈다():
    assert fallback_on({}) and fallback_on({SWITCH: ""}) and fallback_on({SWITCH: "1"}) and fallback_on({SWITCH: "on"})
    for off in ("0", "false", "OFF", " no "):
        assert not fallback_on({SWITCH: off}), off
    assert switch_line({}) == "프록시 예비 접속: 켬 (AGENT_PROXY_FALLBACK — 0·false·off·no면 끈다)"
    assert switch_line({SWITCH: "0"}) == "프록시 예비 접속: 끔 (AGENT_PROXY_FALLBACK — 0·false·off·no면 끈다)"


async def test_원이_되면_원이고_원이_접속_단계에서_실패하면_그_사이트_자원을_고정하고_프록시로():
    pins = set()
    route = Route("mx/gumi", "redis", "redis://p:1", pinned=pins)
    run, calls = _runner("origin-ok")
    assert await route.attempt(run, connect_failure=_refused) == ("origin-ok", False) and calls == ["원"]
    assert route.mark(False) == " · 경로=원" and route.mark(True) == " · 경로=프록시"
    run, calls = _runner(_Refused("refused"))
    assert await route.attempt(run, connect_failure=_refused) == ("proxy-ok", True) and calls == ["원", "프록시"]
    assert pins == {("mx/gumi", "redis")} and route.on_proxy


async def test_고정되면_다음_시도는_원을_다시_안_치고_같은_사이트_자원의_새_경로도_그렇다():
    pins = set()
    first = Route("mx/gumi", "redis", "redis://p:1", pinned=pins)
    run, _ = _runner(_Refused("refused"))
    await first.attempt(run, connect_failure=_refused)
    run, calls = _runner("origin-ok")
    assert await first.attempt(run, connect_failure=_refused) == ("proxy-ok", True) and calls == ["프록시"]
    again = Route("mx/gumi", "redis", "redis://p:1", pinned=pins)                    # 어댑터를 다시 만들어도(케이스마다)
    run, calls = _runner("origin-ok")
    assert await again.attempt(run, connect_failure=_refused) == ("proxy-ok", True) and calls == ["프록시"]
    for other in (Route("mx/sevt", "redis", "redis://p:1", pinned=pins), Route("mx/gumi", "mongo", "mongodb://p:1", pinned=pins)):
        run, calls = _runner("origin-ok")
        assert await other.attempt(run, connect_failure=_refused) == ("origin-ok", False) and calls == ["원"]


async def test_둘_다_실패하면_두_오류를_다_든_실패이고_답한_실패는_대체하지_않는다():
    pins = set()
    route = Route("mx/gumi", "redis", "redis://p:1", pinned=pins)
    run, calls = _runner(_Refused("origin refused"), _Refused("proxy refused"))
    with pytest.raises(ProxyAlsoFailed) as both:
        await route.attempt(run, connect_failure=_refused)
    assert str(both.value) == "원 _Refused: origin refused → 프록시 _Refused: proxy refused" and calls == ["원", "프록시"]
    assert route.on_proxy                                                           # 원은 접속 단계에서 죽었다 — 다음은 프록시만
    fresh = Route("mx/sevt", "redis", "redis://p:1", pinned=pins)
    run, calls = _runner(_Answered("NOAUTH"))
    with pytest.raises(_Answered):
        await fresh.attempt(run, connect_failure=_refused)
    assert calls == ["원"] and not fresh.on_proxy                                   # 인증 실패·키 없음 같은 대상의 답은 그대로


async def test_프록시가_없거나_빈_값이거나_스위치가_꺼지면_지금과_같다():
    pins = {("mx/gumi", "redis")}                                                  # 고정돼 있어도
    for route in (Route("mx/gumi", "redis", None, pinned=pins), Route("mx/gumi", "redis", "", pinned=pins),
                  Route("mx/gumi", "redis", [], pinned=pins), Route("mx/gumi", "redis", "redis://p:1", enabled=False, pinned=pins)):
        run, calls = _runner(_Refused("refused"))
        with pytest.raises(_Refused):
            await route.attempt(run, connect_failure=_refused)
        assert calls == ["원"] and route.mark(False) == "" and route.mark(True) == "" and not route.on_proxy
    assert pins == {("mx/gumi", "redis")}
