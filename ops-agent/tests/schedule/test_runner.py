"""스케줄 루프 — **죽지 않는가, 그리고 몰아서 쏘지 않는가.**

진짜로 자지 않는다. `sleeper`를 주입받으므로 가짜 시계를 앞으로 돌려서 며칠치를
한순간에 돌린다 — 그러지 않으면 "1분 간격" 하나를 확인하는 데 1분이 든다.
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.config.schema_schedule import ScheduleSpec
from src.schedule.runner import Fire, Job, run_all, run_job, wait_or_stop

KST = ZoneInfo("Asia/Seoul")
START = datetime(2026, 9, 14, 7, 0, tzinfo=KST)      # 월요일 아침 7시


class FakeTime:
    """앞으로만 가는 가짜 시계. `sleeper`가 이걸 민다."""

    def __init__(self, now: datetime = START):
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    async def sleeper(self, seconds: float, stop: asyncio.Event) -> bool:
        self.slept.append(seconds)
        self.advance(max(0.0, seconds))
        return stop.is_set()


def job_that(fake, *, name="job", spec=None, result=(True, "됐다"),
             takes=0.0, raises=None, log=None):
    async def run():
        if log is not None:
            log.append(fake.now)
        fake.advance(takes)                       # 일하는 동안 시계가 흐른다
        if raises is not None:
            raise raises
        return result() if callable(result) else result

    return Job(name=name, spec=spec or ScheduleSpec(kind="interval",
                                                    interval_seconds=60), run=run)


def drive(job, fake, *, max_fires=3) -> list[Fire]:
    stop = asyncio.Event()
    return asyncio.run(run_job(job, clock=fake, stop=stop, on_fire=lambda _: None,
                               sleeper=fake.sleeper, max_fires=max_fires))


# ── 루프는 죽지 않는다 ────────────────────────────────────────────────

def test_한_번_실패해도_계속_돈다():
    """실패로 루프가 죽으면 그 뒤로 **영원히 아무것도 안 나간다.** 그리고 조용하다."""
    fake = FakeTime()
    results = iter([(False, "메일 실패"), (True, "됐다"), (True, "됐다")])
    fires = drive(job_that(fake, result=lambda: next(results)), fake)

    assert [f.ok for f in fires] == [False, True, True]
    assert fires[0].detail == "메일 실패"


def test_잡이_던져도_계속_돈다():
    """잡은 던지지 않는 것이 계약이지만, 루프가 한 겹 더 막는다 —
    라이브러리가 우리 손을 거치지 않고 던지는 경우가 있다."""
    fake = FakeTime()
    fires = drive(job_that(fake, raises=RuntimeError("드라이버가 죽었다")), fake)

    assert len(fires) == 3
    assert all(not f.ok for f in fires)
    assert "RuntimeError: 드라이버가 죽었다" in fires[0].detail


def test_찍다가_죽어도_계속_돈다():
    """로그 파이프가 닫히는 일이 있다. 그것이 스케줄을 멈추게 두지 않는다."""
    fake = FakeTime()
    stop = asyncio.Event()

    def broken(fire):
        raise BrokenPipeError("닫힌 파이프")

    fires = asyncio.run(run_job(job_that(fake), clock=fake, stop=stop,
                                on_fire=broken, sleeper=fake.sleeper, max_fires=2))
    assert len(fires) == 2


# ── 밀린 발사를 몰아 쏘지 않는다 ──────────────────────────────────────

def test_한_번이_주기보다_오래_걸려도_붙어_돌지_않는다():
    """1분 간격인데 한 번이 90초 걸리는 상황이다.

    "직전 목표 + 간격"으로 다음을 세면 이미 지난 시각이 나와서 **즉시 재발사**하고,
    실행이 실행을 밀며 붙어 돈다 — 그 사이 메일이 연달아 나간다.
    """
    fake = FakeTime()
    log: list[datetime] = []
    fires = drive(job_that(fake, takes=90, log=log,
                           spec=ScheduleSpec(kind="interval", interval_seconds=60)),
                  fake, max_fires=3)

    gaps = [(b - a).total_seconds() for a, b in zip(log, log[1:])]
    assert gaps == [120, 120], f"슬롯을 건너뛰지 않았다 — {gaps}"
    # 잠을 0초로 재우지 않았다 = 즉시 재발사가 아니다.
    assert all(seconds > 0 for seconds in fake.slept), fake.slept
    assert all(f.late_seconds >= 0 for f in fires)


def test_박자는_기준점에_묶인다():
    """"직전 실행 + 간격"이면 매번 실행 시간만큼 밀려서 며칠 뒤엔 다른 시각에 돈다."""
    fake = FakeTime()
    log: list[datetime] = []
    drive(job_that(fake, takes=7, log=log,
                   spec=ScheduleSpec(kind="interval", interval_seconds=60)),
          fake, max_fires=3)

    assert [(t - START).total_seconds() for t in log] == [60, 120, 180]


def test_cron도_같은_규칙으로_돈다():
    fake = FakeTime()
    log: list[datetime] = []
    drive(job_that(fake, log=log,
                   spec=ScheduleSpec(kind="cron", cron="0 8 * * *")),
          fake, max_fires=2)

    assert [t.strftime("%m-%d %H:%M") for t in log] == ["09-14 08:00", "09-15 08:00"]


# ── 시작과 멈춤 ───────────────────────────────────────────────────────

def test_run_on_start면_뜨자마자_한_번_돈다():
    fake = FakeTime()
    log: list[datetime] = []
    drive(job_that(fake, log=log,
                   spec=ScheduleSpec(kind="interval", interval_seconds=60,
                                     run_on_start=True)), fake, max_fires=2)

    assert log[0] == START, "기다렸다 — run_on_start가 안 먹혔다"


def test_멈춤_신호를_받으면_더_안_돈다():
    fake = FakeTime()
    stop = asyncio.Event()
    log: list[datetime] = []

    async def stopping(seconds, event):
        fake.advance(seconds)
        event.set()                      # 자는 동안 SIGTERM이 왔다
        return True

    fires = asyncio.run(run_job(job_that(fake, log=log), clock=fake, stop=stop,
                                on_fire=lambda _: None, sleeper=stopping))
    assert fires == [] and log == []


async def test_자는_중에_멈춤_신호를_받으면_즉시_깬다():
    """매일 8시라면 자는 시간이 최대 24시간이다. 그동안 안 깨면 컨테이너 종료
    유예 시간(보통 30초)을 넘겨 강제 종료된다."""
    stop = asyncio.Event()

    async def signal_soon():
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.create_task(signal_soon())
    stopped = await asyncio.wait_for(wait_or_stop(30.0, stop), timeout=2.0)

    assert stopped is True


async def test_잘_시간이_없으면_그냥_돈다():
    assert await wait_or_stop(0, asyncio.Event()) is False
    assert await wait_or_stop(-5, asyncio.Event()) is False


# ── 여러 잡 ───────────────────────────────────────────────────────────

def test_잡이_여럿이면_각자_돈다():
    fake = FakeTime()
    stop = asyncio.Event()
    jobs = [job_that(fake, name="일간",
                     spec=ScheduleSpec(kind="interval", interval_seconds=60)),
            job_that(fake, name="주간",
                     spec=ScheduleSpec(kind="interval", interval_seconds=60))]

    fires = asyncio.run(run_all(jobs, clock=fake, stop=stop,
                                on_fire=lambda _: None, sleeper=fake.sleeper,
                                max_fires=2))

    assert sorted(f.job for f in fires) == ["일간", "일간", "주간", "주간"]


def test_잡이_없으면_조용히_끝난다():
    fake = FakeTime()
    assert asyncio.run(run_all([], clock=fake, stop=asyncio.Event(),
                               on_fire=lambda _: None)) == []


# ── 기록 ──────────────────────────────────────────────────────────────

def test_얼마나_걸렸는지_기록에_남는다():
    """계속 커지면 한 번이 주기보다 오래 걸린다는 뜻이다 — 사람이 봐야 한다."""
    fake = FakeTime()
    fires = drive(job_that(fake, takes=90,
                           spec=ScheduleSpec(kind="interval", interval_seconds=60)),
                  fake, max_fires=2)

    assert [f.took_seconds for f in fires] == [90, 90]


def test_늦게_깨워지면_그것도_기록에_남는다():
    """슬롯은 항상 미래로 잡으므로 **시계가 완벽하면 지각은 0이다.**

    실제로 값이 생기는 자리는 OS가 늦게 깨울 때다(GC 멈춤, 부하, 컨테이너 스로틀).
    계속 커지면 이 프로세스가 제때 못 깨고 있다는 뜻이라 사람이 봐야 한다.
    """
    fake = FakeTime()

    async def oversleeps(seconds, stop):
        fake.advance(seconds + 5)          # 5초 늦게 깬다
        return stop.is_set()

    fires = asyncio.run(run_job(job_that(fake), clock=fake, stop=asyncio.Event(),
                                on_fire=lambda _: None, sleeper=oversleeps,
                                max_fires=2))

    assert [f.late_seconds for f in fires] == [5, 5]
