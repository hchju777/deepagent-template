"""이벤트 루프 고르기 — 리눅스면 uvloop, Windows면 asyncio.

uvloop는 Windows를 지원하지 않는다(빌드 자체가 안 된다). 그래서 `requirements.txt`가
`sys_platform != "win32"` 표시로 설치를 건너뛰고, 여기서도 플랫폼을 **먼저** 본다.

`ImportError`만으로도 같은 결과가 나오지만 플랫폼을 명시적으로 보는 이유: 그러면
"Windows에서 asyncio를 쓴다"가 **결정**이 되고, 설치가 우연히 성공한 환경에서도
같은 길로 간다. 예외에 기댄 분기는 왜 그 길로 갔는지 나중에 알 수 없다.

`set_event_loop_policy`는 파이썬 3.11의 API다(3.14에서 없어진다). 사내가 3.11이므로
지금은 이것이 맞고, 올릴 때 `asyncio.Runner(loop_factory=...)`로 바꿔야 한다.
"""
import asyncio
import sys


def install_fast_loop(*, platform: str = sys.platform) -> str:
    """무엇을 쓰기로 했는지 **한 줄로 돌려준다.** 부르는 쪽이 그것을 찍는다.

    조용히 정하면 "느린데 왜 느린지 모르는" 상태가 된다 — uvloop가 설치 안 된 것을
    눈으로 볼 수 있어야 한다.
    """
    if platform.startswith("win"):
        return "asyncio (Windows — uvloop은 Windows를 지원하지 않는다)"
    try:
        import uvloop
    except ImportError:
        return "asyncio (uvloop이 설치돼 있지 않다)"
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    return f"uvloop {getattr(uvloop, '__version__', '?')}"
