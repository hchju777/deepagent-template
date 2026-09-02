from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config.schema_site import Schedule, SiteConfig
from src.patrol.scheduler import build_scheduler, build_trigger, interval_seconds


def test_interval_파싱과_트리거_생성():
    assert (interval_seconds("30s"), interval_seconds("5m"), interval_seconds("1h")) == (30, 300, 3600)
    assert isinstance(build_trigger(Schedule(interval="5m")), IntervalTrigger)
    assert isinstance(build_trigger(Schedule(cron="0 8,20 * * *")), CronTrigger)


def test_점검마다_잡이_등록되고_하트비트가_붙는다():
    site = SiteConfig.model_validate({"target": {"rest": {"base_url": "http://x"}}, "patrol": {"checks": {
        "a": {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/oee"},
        "b": {"judge": "llm", "schedule": {"cron": "0 8 * * *"}, "target": "rest:/oee"}}}})

    async def run_one(gbm, fct, name, check): ...
    async def heartbeat(): ...

    sched = build_scheduler([("mx", "gumi", site)], run_one=run_one, heartbeat=heartbeat,
                            timezone="Asia/Seoul")
    ids = {job.id for job in sched.get_jobs()}
    assert ids == {"mx/gumi/a", "mx/gumi/b", "heartbeat"}
    job = sched.get_job("mx/gumi/a")
    assert job.max_instances == 1 and job.coalesce is True
    assert not sched.running
