"""이벤트 루프 선택 — 플랫폼을 **명시적으로** 본다."""
from src.schedule.loop import install_fast_loop


def test_Windows면_asyncio다():
    """uvloop은 Windows 빌드가 없다. 예외에 맡기지 않고 먼저 플랫폼을 보는 이유:
    그러면 "Windows에서는 asyncio"가 결정으로 남고, 설치가 우연히 성공한 환경에서도
    같은 길로 간다."""
    chosen = install_fast_loop(platform="win32")

    assert "asyncio" in chosen and "Windows" in chosen


def test_무엇을_쓰는지_말해_준다():
    """조용히 정하면 "느린데 왜 느린지 모르는" 상태가 된다."""
    chosen = install_fast_loop(platform="linux")

    assert "uvloop" in chosen or "asyncio" in chosen
    assert chosen.strip()


def test_uvloop이_없어도_죽지_않는다(monkeypatch):
    """사내 PyPI에 없을 수 있다. 그때 스케줄러가 안 뜨면 안 된다."""
    import builtins

    real = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "uvloop":
            raise ImportError("no uvloop here")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)

    assert "asyncio" in install_fast_loop(platform="linux")
