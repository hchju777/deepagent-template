"""대본 파일로 조사 루프를 돌려 본다 — **LLM 없이.**

## 왜 이게 있는가

10a가 만든 것은 "라운드가 도는 것"인데, 테스트만 있으면 그게 도는 모습을 사람이 볼
방법이 없다. 그리고 10b에서 `frame`·`integrate` 자리에 LLM이 들어올 때, **LLM이
틀린 것인지 루프가 틀린 것인지**를 가르려면 LLM 없이 같은 루프를 돌려 볼 수 있어야
한다.

대본 파일이 LLM 흉내를 낸다: `tasks`가 frame의 출력이고, `rounds[i]`가 i번째
integrate의 출력이다. 대본이 떨어지면 그 다음부터는 `{"decision": "continue"}` —
그러면 라운드 상한이나 "돌릴 것 없음"이 멈춘다. 그게 코드의 울타리를 보는 방법이다.

## 대본도 StrictModel로 받는다

손으로 쓰는 파일이다. `"round"`(s 빠짐)라고 적으면 조용히 무시되고, 사람은 대본대로
돌았다고 믿는다 — 이 리포가 config에서 배운 것과 같은 실패다(`app.json`의 `timezone`).
모르는 키는 거부한다.

## `_`로 시작하는 키는 주석이다

JSON에는 주석이 없다. 그런데 이 파일은 사람이 읽고 고치는 것이라 "이 태스크가 왜
여기 있는가"를 적을 자리가 필요하다. 검증 전에 `_` 키를 걷어 내서, **주석을 허용하되
오타는 여전히 잡는다.**
"""
import json
from pathlib import Path

from pydantic import ValidationError

from src.application.nodes import EngineDeps
from src.application.state import CaseState, Decision
from src.domain.base import StrictModel
from src.domain.case import Case, Hypothesis, PlanTask


class ScriptRound(StrictModel):
    """integrate가 한 라운드에 낼 것."""

    decision: Decision = "continue"
    tasks: list[PlanTask] = []
    hypotheses: list[Hypothesis] = []


class Script(StrictModel):
    symptom: str = "(증상 미기재)"
    hypotheses: list[Hypothesis] = []
    tasks: list[PlanTask] = []
    rounds: list[ScriptRound] = []


def strip_comments(node):
    """`_`로 시작하는 키를 걷어 낸다. 중첩된 것까지 — 태스크 안에도 적을 수 있게."""
    if isinstance(node, dict):
        return {k: strip_comments(v) for k, v in node.items() if not k.startswith("_")}
    if isinstance(node, list):
        return [strip_comments(item) for item in node]
    return node


def load_script(path: Path) -> Script:
    """읽고 검증한다. **실패는 사람이 읽을 한 줄로 바꾼다.**

    pydantic의 `ValidationError`를 그대로 올리면 스택트레이스 20줄이 나오는데,
    사람이 방금 손으로 고친 파일에 대해 그건 "어디를 고쳐야 하는가"를 가린다.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        return Script.model_validate(strip_comments(raw))
    except ValidationError as exc:
        problems = "\n".join(
            f"  {'.'.join(str(p) for p in err['loc']) or '(최상위)'} — {err['msg']}"
            for err in exc.errors())
        raise SystemExit(f"{path}의 대본이 잘못됐다:\n{problems}\n"
                         f"  (설명을 적고 싶으면 키 이름을 _로 시작하라 — 주석으로 걷어 낸다)")


class ScriptedPlan:
    """대본 하나. frame과 integrate 두 자리에 꽂힌다."""

    def __init__(self, script: Script):
        self._script = script
        self._cursor = 0

    async def frame(self, state: CaseState) -> dict:
        return {"plan_tasks": list(self._script.tasks),
                "hypotheses": list(self._script.hypotheses)}

    async def integrate(self, state: CaseState) -> dict:
        if self._cursor >= len(self._script.rounds):
            # 대본이 떨어졌다. 여기서 conclude로 끝내면 **상한이 지켜지는지를 못 본다** —
            # 계속 가자고 하고, 멈추는 것은 코드에 맡긴다.
            return {"decision": "continue"}
        step = self._script.rounds[self._cursor]
        self._cursor += 1
        return {"decision": step.decision, "plan_tasks": list(step.tasks),
                "hypotheses": list(step.hypotheses)}


def build_deps(script: Script, *, runner, investigation) -> EngineDeps:
    plan = ScriptedPlan(script)
    return EngineDeps(runner=runner, frame=plan.frame, integrate=plan.integrate,
                      max_rounds=investigation.max_rounds,
                      parallel_width=investigation.parallel_width,
                      max_tasks=investigation.max_tasks)


def initial_state(script: Script, *, case_id: str, gbm: str, fct: str,
                  clock) -> CaseState:
    return CaseState(case=Case(id=case_id, gbm=gbm, fct=fct, origin="human",
                               symptom=script.symptom, t0=clock()))
