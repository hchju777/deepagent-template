"""어댑터 공통 가드 — 스펙 §4.1 원칙 ③ "아픈 시스템을 더 아프게 하지 않는다".

- 타임아웃: 호출 단위 상한. 아픈 대상에 매달리지 않는다.
- 세마포어: 사이트별 동시 요청 상한 (팩토리가 사이트당 하나 생성).
- raise 금지: 모든 실패는 error ProbeResult로 — 그래프 superstep을 죽이지 않는다(§5.4).
"""
import asyncio

from src.domain.envelope import Envelope, ProbeResult


async def guarded_call(op, *, timeout_s, semaphore, clock) -> ProbeResult:
    try:
        async with semaphore:
            data, envelope = await asyncio.wait_for(op(), timeout=timeout_s)
        return ProbeResult(status="ok", envelope=envelope, data=data)
    except asyncio.TimeoutError:
        return ProbeResult(
            status="error", envelope=Envelope(observed_at=clock()),
            error=f"타임아웃({timeout_s}s 초과)")
    except Exception as exc:   # 어댑터 계약: 어떤 실패도 그래프 안으로 raise하지 않는다
        return ProbeResult(
            status="error", envelope=Envelope(observed_at=clock()),
            error=f"{type(exc).__name__}: {exc}")
