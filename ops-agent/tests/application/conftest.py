"""조사 엔진 테스트의 공용 재료.

시계는 고정값이고, `frame`·`integrate`는 대본이다 — **10a에는 LLM이 없다.**
라운드 상한이나 게이트가 지켜지는지를 LLM의 답과 섞어서 보면, 깨졌을 때 어느
쪽 때문인지 알 수 없다.
"""
from datetime import datetime

import pytest

from src.application.nodes import EngineDeps
from src.application.state import CaseState
from src.config.schema_site import SiteConfig
from src.domain.case import Case, PlanTask
from src.domain.investigation import TaskOutcome

T0 = datetime(2026, 9, 14, 9, 0, 0)

# 프롬프트에 새면 안 되는 것들. 값이 여기 한 곳에 있어야 "안 샌다"는 단정이
# 실제로 그 값을 가리킨다 — 테스트마다 다른 문자열을 쓰면 하나만 고쳐도 통과한다.
SECRET = "s3cr3t-pw"
TOPIC = "GUMI_PRODUCTION_EVENTS"       # 대상 데이터의 이름(decisions ⑮)
DATABASE = "dt_gumi"


def site_config(**infra_overrides) -> SiteConfig:
    """네 시스템이 전부 붙어 있는 사이트. `kafka=None`으로 하나씩 뗄 수 있다."""
    infra = {
        "redis": {"url": "redis://h:6379", "password": SECRET},
        "mongodb": {"url": "mongodb://h:27017", "database": DATABASE,
                    "user": "dmfReadOnly", "password": SECRET},
        "kafka": {"consumer": {"bootstrap_server": ["h:9092"],
                               "group_ids": ["dt-processor-mx-gumi"],
                               "topic": {"topic1": TOPIC}}},
        "rest": {"base_url": "https://h/api", "headers": {"X-KEY": SECRET},
                 "entries": {"summary_badge": {
                     "method": "POST", "path": "/summary/badge",
                     "params": {"line_code": {"type": "list"},
                                "part_code": {"type": "list"}}}}},
    }
    infra.update(infra_overrides)
    infra = {k: v for k, v in infra.items() if v is not None}
    return SiteConfig.model_validate(
        {"site": {"gbm": "mx", "fct": "gumi"}, "infra": infra})


@pytest.fixture
def clock():
    return lambda: T0


@pytest.fixture
def case() -> Case:
    return Case(id="c-1", gbm="mx", fct="gumi", origin="human",
                symptom="OEE가 512다", t0=T0)


def task(task_id: str, **overrides) -> PlanTask:
    # 키를 태스크마다 다르게 준다. 전부 `key="k"`면 **질의가 같아서** 중복 차단
    # (`_accept_tasks`의 `done`)에 걸린다 — 실제 조사에서 태스크 둘이 완전히 같은
    # 읽기를 하는 일은 없으므로, 같게 두면 픽스처가 현실과 달라진다.
    body = {"id": task_id, "goal": f"목표 {task_id}", "role": "data_prober",
            "action": "redis.get", "params": {"key": f"k-{task_id}"}}
    body.update(overrides)
    return PlanTask.model_validate(body)


def state_with(case: Case, **overrides) -> CaseState:
    return CaseState(case=case, **overrides)


def plan(*task_lists):
    """라운드마다 낼 것을 순서대로 주는 `integrate` 대본을 만든다.

    `None`이면 그 라운드는 "계속하되 새 태스크는 없다". 대본이 떨어지면 계속
    `continue`를 낸다 — **멈추는 것은 코드에 맡긴다**(그래야 울타리가 보인다).
    """
    steps = list(task_lists)
    box = {"i": 0}

    async def integrate(state: CaseState) -> dict:
        i = box["i"]
        box["i"] += 1
        fresh = steps[i] if i < len(steps) else None
        return {"decision": "continue", "plan_tasks": list(fresh or [])}

    return integrate


def deps_for(runner, *, first_tasks=(), integrate=None, max_rounds=3,
             parallel_width=2, max_tasks=20) -> EngineDeps:
    async def frame(state: CaseState) -> dict:
        return {"plan_tasks": list(first_tasks)}

    async def stop(state: CaseState) -> dict:
        return {"decision": "continue"}

    return EngineDeps(runner=runner, frame=frame, integrate=integrate or stop,
                      max_rounds=max_rounds, parallel_width=parallel_width,
                      max_tasks=max_tasks)


def ok(task_id: str, *, summary: str = "봤다") -> TaskOutcome:
    return TaskOutcome(task_id=task_id, status="ok", summary=summary)
