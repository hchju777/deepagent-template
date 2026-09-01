import asyncio
from datetime import datetime

from src.domain.envelope import Envelope
from src.infrastructure.guards import guarded_call

T = datetime(2026, 9, 3, 8, 0, 0)
CLOCK = lambda: T


async def test_정상_호출은_ok로_감싼다():
    async def op():
        return {"v": 1}, Envelope(observed_at=T)
    sem = asyncio.Semaphore(1)
    result = await guarded_call(op, timeout_s=1, semaphore=sem, clock=CLOCK)
    assert result.status == "ok" and result.data == {"v": 1}


async def test_타임아웃은_raise가_아니라_error_결과다():
    async def slow():
        await asyncio.sleep(0.2)
        return None, Envelope(observed_at=T)
    sem = asyncio.Semaphore(1)
    result = await guarded_call(slow, timeout_s=0.01, semaphore=sem, clock=CLOCK)
    assert result.status == "error" and "타임아웃" in result.error


async def test_예외도_error_결과로_변환된다():
    async def boom():
        raise ConnectionError("connection refused")
    sem = asyncio.Semaphore(1)
    result = await guarded_call(boom, timeout_s=1, semaphore=sem, clock=CLOCK)
    assert result.status == "error" and "connection refused" in result.error


async def test_세마포어가_동시_실행을_제한한다():
    running, peak = 0, 0

    async def op():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return None, Envelope(observed_at=T)

    sem = asyncio.Semaphore(2)
    await asyncio.gather(*[
        guarded_call(op, timeout_s=1, semaphore=sem, clock=CLOCK) for _ in range(6)])
    assert peak <= 2
