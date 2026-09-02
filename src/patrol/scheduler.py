"""순찰 스케줄러 — 사이트 config의 점검 정의를 APScheduler 잡으로 등록한다.

여기서는 스케줄러를 "만들기"만 한다 — start()는 호출하지 않는다(데몬 기동은
계획 4b의 몫). 이벤트 루프 없이도 add_job/get_jobs/get_job이 바로 동작해야
테스트가 동기 함수로 검증할 수 있다 — AsyncIOScheduler는 생성·잡 등록 단계에서
이벤트 루프를 요구하지 않는다(3.11.3 기준, 잡 소비/트리거 계산은 start() 이후).

점검마다 max_instances=1, coalesce=True를 강제한다 — 이전 실행이 아직 끝나지
않았으면 겹쳐 돌리지 않고(순찰이 밀려도 동일 점검 두 개가 동시에 스크래치
케이스에 쓰는 경합을 막는다), 밀린 실행은 쌓아 재생하지 않고 합쳐 하나로
넘긴다. 잡 id는 "gbm/fct/check명" — 사이트·점검 단위로 유일하다.

모든 add_job에 misfire_grace_time=None을 강제한다 — APScheduler 기본값(유한
초)은 그 창을 넘겨 늦게 깨어난 틱을 misfire로 조용히 버린다(로그도 콜백 호출도
없다). 순찰은 "이번 틱을 놓쳤다"는 사실 자체가 하트비트·레저에 남아야 할
신호이므로 조용한 드롭을 허용하지 않는다 — None이면 늦어도 반드시 한 번은
돌린다(coalesce=True라 밀린 여러 틱은 하나로 합쳐진다). 그래도 못 돌린 틱
(예: max_instances 초과로 겹쳐 스킵된 경우)은 on_missed 콜백으로 건져
4b 데몬이 레저에 skipped를 남길 수 있게 한다.
"""
import asyncio
import inspect
from typing import Awaitable, Callable

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config.schema_site import Schedule, SiteConfig

_UNITS = {"s": 1, "m": 60, "h": 3600}


def interval_seconds(spec: str) -> int:
    """"30s"/"5m"/"1h" 형식의 간격 문자열을 초 단위 정수로 바꾼다.

    형식 검증은 Schedule 모델의 정규식이 이미 맡고 있으므로 여기서는
    마지막 글자를 단위로, 나머지를 숫자로 그대로 파싱한다.
    """
    unit = spec[-1]
    value = int(spec[:-1])
    return value * _UNITS[unit]


def build_trigger(schedule: Schedule, timezone: str | None = None):
    """Schedule(정확히 interval/cron 중 하나)로부터 APScheduler 트리거를 만든다.

    timezone은 cron 트리거에만 의미가 있다 — interval은 경과 시간 기준이라
    시간대가 필요 없다. build_scheduler가 사이트 순회 중 이 값을 넘긴다.
    """
    if schedule.interval is not None:
        return IntervalTrigger(seconds=interval_seconds(schedule.interval))
    return CronTrigger.from_crontab(schedule.cron, timezone=timezone)


def build_scheduler(
    sites: list[tuple[str, str, SiteConfig]], *,
    run_one: Callable, heartbeat: Callable,
    heartbeat_seconds: int = 60, timezone: str,
    on_missed: Callable[[str], "Awaitable | None"] | None = None,
) -> AsyncIOScheduler:
    """사이트별 점검마다 잡을 등록하고 하트비트 잡을 붙인 스케줄러를 돌려준다.

    start()는 호출하지 않는다 — 데몬 기동(4b)이 시점을 결정한다. run_one은
    (gbm, fct, check명, CheckConfig)를 인자로 받는 async 콜러블, heartbeat는
    인자 없는 async 콜러블이다.

    on_missed가 주어지면 EVENT_JOB_MISSED|EVENT_JOB_MAX_INSTANCES 리스너를
    붙여, 놓친(misfire) 또는 겹쳐서 스킵된(max_instances 초과) 잡마다
    job_id로 호출한다 — 4b 데몬이 이 훅에서 레저에 skipped를 남긴다.
    on_missed가 코루틴을 돌려주면(비동기 콜백이면) 실행 중인 이벤트 루프에
    얹는다; 동기 콜백이면 그 자리에서 이미 끝난 것으로 본다.
    """
    scheduler = AsyncIOScheduler(timezone=timezone)
    for gbm, fct, site in sites:
        for name, check in site.patrol.checks.items():
            trigger = build_trigger(check.schedule, timezone)
            scheduler.add_job(
                run_one, trigger, args=[gbm, fct, name, check],
                id=f"{gbm}/{fct}/{name}", max_instances=1, coalesce=True,
                misfire_grace_time=None,
            )
    scheduler.add_job(heartbeat, IntervalTrigger(seconds=heartbeat_seconds), id="heartbeat",
                      misfire_grace_time=None)

    if on_missed is not None:
        def _handle_missed(event) -> None:
            result = on_missed(event.job_id)
            if inspect.isawaitable(result):
                asyncio.ensure_future(result)
        scheduler.add_listener(_handle_missed, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)

    return scheduler
