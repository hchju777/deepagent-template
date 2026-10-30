"""스케줄 루프 — **이 루프는 죽지 않는다.**

## 무raise가 여기서 제일 무겁다

리포트 한 번이 실패해서 루프가 죽으면 그 뒤로 **영원히 아무것도 안 나간다.** 그리고
그 상태는 조용하다 — 프로세스는 살아 있고(혹은 재시작됐고), 로그를 안 보면 알 수
없다. 그래서 발사 하나하나를 `try/except Exception`으로 감싸고, 실패는 `Fire`라는
**값**으로 돌려준다.

## 밀린 발사는 몰아서 쏘지 않는다

1분 간격인데 한 번이 90초 걸리면, "직전 목표 + 간격"으로 다음을 세는 구현은 이미
지난 시각을 가리켜 **즉시 다시 발사**한다. 그러면 실행이 실행을 밀며 붙어 돌고, 그
사이 메일이 연달아 나간다. 그래서 다음 시각은 항상 **지금 기준**으로 다시 계산한다 —
지나간 슬롯은 건너뛴다. 얼마나 늦게 시작했는지는 `Fire.late_seconds`에 남는다.

## 한 잡 안에서는 겹치지 않는다

잡 하나가 루프 하나이고 그 안은 순차다. 겹칠 방법이 구조적으로 없다 — 세마포어나
"실행 중" 플래그 같은 것으로 막으면 그 플래그를 지우지 못하는 경로가 생긴다.
시나리오가 여럿이면 잡이 여럿이고 그것들은 서로 독립이다.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from src.config.schema_schedule import ScheduleSpec
from src.domain.base import Clock


@dataclass(frozen=True)
class Fire:
    """발사 한 번의 기록. **실패도 기록이다.**"""
    job: str
    scheduled: datetime      # 돌기로 했던 시각
    started: datetime
    finished: datetime
    ok: bool
    detail: str

    @property
    def late_seconds(self) -> float:
        """예정보다 몇 초 늦게 시작했는가. 계속 커지면 한 번이 주기보다 오래 걸린다."""
        return (self.started - self.scheduled).total_seconds()

    @property
    def took_seconds(self) -> float:
        return (self.finished - self.started).total_seconds()


@dataclass(frozen=True)
class Job:
    name: str
    spec: ScheduleSpec
    # (성공인가, 사람이 읽을 한 줄). **던지지 않는 것이 계약**이지만 루프가 한 번 더 막는다.
    run: Callable[[], Awaitable[tuple[bool, str]]]


# (잘 시각, 멈춤 신호) → 멈추라는 신호를 받았으면 True. 테스트가 가짜를 넣는다.
Sleeper = Callable[[float, asyncio.Event], Awaitable[bool]]


async def wait_or_stop(seconds: float, stop: asyncio.Event) -> bool:
    """자되 멈춤 신호가 오면 즉시 깬다.

    `asyncio.sleep`으로 자면 Ctrl+C나 SIGTERM을 받고도 **다음 발사까지** 안 깬다.
    매일 8시라면 최대 24시간이다 — 컨테이너 종료 유예 시간(보통 30초) 안에 못 끝나
    강제 종료된다.
    """
    if seconds <= 0:
        return stop.is_set()
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def run_job(job: Job, *, clock: Clock, stop: asyncio.Event,
                  on_fire: Callable[[Fire], None],
                  sleeper: Sleeper = wait_or_stop,
                  max_fires: int | None = None) -> list[Fire]:
    """한 잡을 스케줄대로 돌린다. `stop`이 설 때까지, 또는 `max_fires`번."""
    anchor = clock()
    fires: list[Fire] = []

    if job.spec.run_on_start and not stop.is_set():
        fires.append(await _fire(job, anchor, clock=clock, on_fire=on_fire))

    while not stop.is_set() and (max_fires is None or len(fires) < max_fires):
        now = clock()
        target = job.spec.next_after(now, anchor=anchor)
        if await sleeper((target - now).total_seconds(), stop):
            break
        fires.append(await _fire(job, target, clock=clock, on_fire=on_fire))
    return fires


async def _fire(job: Job, scheduled: datetime, *, clock: Clock,
                on_fire: Callable[[Fire], None]) -> Fire:
    started = clock()
    try:
        ok, detail = await job.run()
    except Exception as exc:                                       # noqa: BLE001
        # 최후의 방어선. 잡이 계약을 어기고 던져도 **루프는 계속 돈다.**
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    fire = Fire(job=job.name, scheduled=scheduled, started=started,
                finished=clock(), ok=ok, detail=detail)
    try:
        on_fire(fire)
    except Exception:                                              # noqa: BLE001
        # 찍다가 죽는 것(닫힌 파이프 등)이 스케줄을 멈추게 두지 않는다.
        pass
    return fire


async def run_all(jobs: list[Job], *, clock: Clock, stop: asyncio.Event,
                  on_fire: Callable[[Fire], None],
                  sleeper: Sleeper = wait_or_stop,
                  max_fires: int | None = None) -> list[Fire]:
    """잡 여럿을 동시에. 하나가 죽어도 나머지는 돈다(`return_exceptions=True`)."""
    if not jobs:
        return []
    results = await asyncio.gather(
        *(run_job(job, clock=clock, stop=stop, on_fire=on_fire,
                  sleeper=sleeper, max_fires=max_fires) for job in jobs),
        return_exceptions=True)
    fires: list[Fire] = []
    for result in results:
        if isinstance(result, list):
            fires.extend(result)
    return sorted(fires, key=lambda f: f.scheduled)
