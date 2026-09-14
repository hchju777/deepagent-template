"""대본 파일로 조사 루프를 돌려 본다 — **LLM 없이.**

## 왜 이게 있는가

10a가 만든 것은 "라운드가 도는 것"인데, 테스트만 있으면 그게 도는 모습을 사람이 볼
방법이 없다. 그리고 10b에서 `frame`·`integrate` 자리에 LLM이 들어올 때, **LLM이
틀린 것인지 루프가 틀린 것인지**를 가르려면 LLM 없이 같은 루프를 돌려 볼 수 있어야
한다.

대본 파일이 LLM 흉내를 낸다: `tasks`가 frame의 출력이고, `rounds[i]`가 i번째
integrate의 출력이다. 대본이 떨어지면 그 다음부터는 `{"decision": "continue"}` —
그러면 라운드 상한이나 "돌릴 것 없음"이 멈춘다. 그게 코드의 울타리를 보는 방법이다.
"""
import json
from pathlib import Path

from src.application.nodes import EngineDeps
from src.application.state import CaseState
from src.domain.case import Case, Hypothesis, PlanTask


class ScriptedPlan:
    """대본 파일 하나. frame과 integrate 두 자리에 꽂힌다."""

    def __init__(self, script: dict):
        self._tasks = [PlanTask.model_validate(t) for t in script.get("tasks", [])]
        self._hypotheses = [Hypothesis.model_validate(h)
                            for h in script.get("hypotheses", [])]
        self._rounds = list(script.get("rounds", []))
        self._cursor = 0

    async def frame(self, state: CaseState) -> dict:
        return {"plan_tasks": self._tasks, "hypotheses": self._hypotheses}

    async def integrate(self, state: CaseState) -> dict:
        if self._cursor >= len(self._rounds):
            # 대본이 떨어졌다. 여기서 conclude로 끝내면 **상한이 지켜지는지를 못 본다** —
            # 계속 가자고 하고, 멈추는 것은 코드에 맡긴다.
            return {"decision": "continue"}
        step = self._rounds[self._cursor]
        self._cursor += 1
        return {"decision": step.get("decision", "continue"),
                "plan_tasks": [PlanTask.model_validate(t) for t in step.get("tasks", [])],
                "hypotheses": [Hypothesis.model_validate(h)
                               for h in step.get("hypotheses", [])]}


def load_script(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_deps(script: dict, *, runner, investigation) -> EngineDeps:
    plan = ScriptedPlan(script)
    return EngineDeps(runner=runner, frame=plan.frame, integrate=plan.integrate,
                      max_rounds=investigation.max_rounds,
                      parallel_width=investigation.parallel_width,
                      max_tasks=investigation.max_tasks)


def initial_state(script: dict, *, case_id: str, gbm: str, fct: str, clock) -> CaseState:
    return CaseState(case=Case(
        id=case_id, gbm=gbm, fct=fct, origin="human",
        symptom=script.get("symptom", "(증상 미기재)"), t0=clock()))
