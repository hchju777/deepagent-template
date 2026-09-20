"""노드 하나하나가 하는 일과, **코드가 쥐는 통제 경계**.

`frame`·`integrate`는 **주입받는 함수**다 — 10b부터 그 자리에 리드 LLM이 들어온다
(`lead.py`). 그래서 이 파일은 LLM을 모르고, 여기 있는 것은 전부 코드가 고정한 규율이다.
**LLM이 오기 전에 경계가 먼저 있었다**는 것이 중요하다: 나중에 얹은 소독은 "그때는
필요 없던 것"이 아니라 "그동안 뚫려 있던 것"이다.

| 무엇 | 어디 |
|---|---|
| 라운드 상한 | `integrate` — 상한에 닿으면 LLM의 결정을 무시하고 끝낸다 |
| 병렬 폭 | `select` — 골라 낸 태스크를 잘라서 `running`으로 굴린다 |
| 실행 가능 판정 | `runnable_tasks` — 입력 증거가 **전부** 실재해야 |
| 태스크 개수 상한 | `frame`·`integrate` |
| LLM 출력 소독 | `_sanitize_task` — 수명주기 필드를 코드가 덮어쓴다 |
| 증거 인용 검사 | `_accept_hypotheses` — 실재하지 않는 id를 걷어낸다 |
| 예외 흡수 | `execute` 최외곽 |

## 노드는 `(state) -> dict`다

LangGraph 타입을 아는 것은 `graph.py` 하나뿐이고, 여기는 State를 받아 **갱신할 조각만**
담은 dict를 돌려주는 평범한 함수들이다. 테스트가 그래프 없이 노드를 부를 수 있고,
루프 런타임을 갈아끼워도 이 파일은 안 바뀐다.

## select가 **노드**인 이유

고르는 일을 라우터에서 바로 하면 "이번 라운드에 무엇을 왜 골랐나"가 어디에도 안
남는다. 노드가 고른 것을 `running`으로 굴려 State에 적으면 체크포인트에 남고,
라우터는 그걸 읽기만 한다 — 판단과 배선이 갈린다.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable

from src.application.state import CaseState, merge_by_id
from src.domain.actions import DISCOVERED_ARGS
from src.domain.case import Case, Hypothesis, PlanTask
from src.domain.investigation import TaskOutcome, TaskRunnerPort

# 이 자리에 리드 LLM(`lead.make_lead`)이나 테스트의 대본이 들어온다.
NodeFn = Callable[[CaseState], Awaitable[dict]]


@dataclass(frozen=True)
class EngineDeps:
    runner: TaskRunnerPort
    frame: NodeFn
    integrate: NodeFn
    max_rounds: int
    parallel_width: int
    max_tasks: int
    # 태스크가 댄 이름을 "실제로 찾았는가"로 검사할지. **사람이 쓴 대본은 끈다** —
    # `case dryrun`의 계획 파일은 시스템을 아는 사람이 이름을 알고 적은 것이라,
    # 켜 두면 기록이 거짓 양성으로만 찬다. 리드 LLM 경로에서만 뜻이 있다.
    check_discovery: bool = True


def _sanitize_task(task: PlanTask) -> PlanTask:
    """만들어진 태스크의 수명주기 필드를 **코드가 강제로 초기화한다**(규율 4).

    리드 LLM이 `{"status": "ok", "result_evidence_ids": ["ev-9"]}`를 실어 보내면
    그 태스크는 실행되지 않은 채 끝난 것이 되고 select 게이트를 통째로 우회한다.
    10a에서 LLM이 오기 **전에** 이 경계를 먼저 세운 이유다.
    """
    return task.model_copy(update={"status": "pending", "result_summary": None,
                                   "result_evidence_ids": [], "error": None})


def _accept_hypotheses(patch: dict, *, have: set[str]) -> tuple[list[Hypothesis], list[str]]:
    """리드가 만든 가설에서 **실재하지 않는 증거 인용을 걷어낸다**(규율 3).

    리드가 적은 증거 id는 환각일 수 있다. 그냥 들이면 다음 라운드의 브리핑이
    `<가설>` 블록에 그 id를 실어 보내고, 리드는 **자기가 지어낸 id를 근거로 다시
    추론한다.** 라운드마다 복리로 불어나는 종류의 오류라 루프가 있는 지금 막아야 한다.

    인용이 하나도 안 남으면 판정을 `open`으로 되돌린다 — 근거가 전부 사라진
    "supported"는 근거 없는 단정이고, 그게 12a의 최종 판정 재료가 된다.

    걷어낸 사실은 `llm_errors`에 남긴다. 조용히 고치면 프롬프트가 안 먹히고 있다는
    것을 아무도 모른다.
    """
    kept, complaints = [], []
    for h in patch.get("hypotheses", []):
        ghosts = [e for e in h.supporting_ids + h.refuting_ids if e not in have]
        if not ghosts:
            kept.append(h)
            continue
        supporting = [e for e in h.supporting_ids if e in have]
        refuting = [e for e in h.refuting_ids if e in have]
        demoted = h.status if (supporting or refuting) else "open"
        complaints.append(f"{h.id}: 없는 증거를 인용했다 — {', '.join(ghosts)}"
                          + ("" if demoted == h.status else f" (판정을 {h.status}→open으로 되돌렸다)"))
        kept.append(h.model_copy(update={"supporting_ids": supporting,
                                         "refuting_ids": refuting, "status": demoted}))
    return kept, complaints


def _seen(state: CaseState) -> str:
    """지금까지 본 증거를 이어 붙인 것 — "이 이름을 실제로 찾았는가"의 근거.

    요약 문자열을 그대로 훑는다. `list_collections`의 결과가 `['aa', 'bb']`처럼 실려
    있으므로 부분 문자열로 충분하고, **덜 잡는 쪽이 낫다** — 기록이 목적이라
    거짓 양성(멀쩡한데 찍었다고 적는 것)이 거짓 음성보다 비싸다.
    """
    return "\n".join(f"{ref.source} {ref.summary}" for ref in state.evidence)


def runnable_tasks(state: CaseState) -> list[PlanTask]:
    """지금 실행할 수 있는 태스크 — 우선순위 오름차순, 동률이면 FIFO.

    **입력 증거가 하나라도 없으면 실행 불가다.** `any()`가 아니라 `all()`인 이유:
    재계산 태스크는 "원천값"과 "로직 명세" 둘 다 있어야 성립한다. 하나만 있어도
    돌리면 절반의 입력으로 기대값을 만들고, 그 기대값이 실제와 다른 것을
    "이상 발견"으로 보고한다.
    """
    have = state.evidence_ids()
    ready = [t for t in state.plan_tasks
             if t.status == "pending" and all(e in have for e in t.input_evidence_ids)]
    # sorted는 안정 정렬이라 동률이면 원래 순서(FIFO)가 유지된다 — state.py의
    # 리듀서가 순서를 지키는 것이 여기서 값을 한다.
    return sorted(ready, key=lambda t: t.priority)


def _accept_tasks(patch: dict, *, room: int,
                  seen: str | None = "") -> tuple[list[PlanTask], list[str]]:
    """만들어진 태스크를 소독하고 개수 상한으로 자른다. **찾지 않고 댄 이름을 기록한다.**

    상한을 리듀서가 아니라 여기서 거는 이유는 `state.py` 맨 위에 있다 — 리듀서에서
    raise하면 superstep이 통째로 죽는다.

    `seen`이 `None`이면 검사하지 않는다(`EngineDeps.check_discovery`).
    아니면 지금까지 본 증거를 이어 붙인 텍스트다. 태스크가 `collection`·`topic` 같은
    **찾아야 아는 이름**(`DISCOVERED_ARGS`)에 거기 없는 값을 대면 기록한다 —
    **막지는 않는다.** 증상 자체가 이름을 담고 있는 정당한 경우가 있고, 무엇보다
    ⑮ 설계("이름은 리드가 찾는다")가 실제로 먹히는지를 **빈도로 알아야** 막을지
    정할 수 있다. 자주 차면 그때 거부로 올린다.
    """
    kept = [_sanitize_task(t) for t in patch.get("plan_tasks", [])][:max(0, room)]
    if seen is None:
        return kept, []
    guessed = []
    for task in kept:
        for name, value in sorted(task.params.items()):
            if name in DISCOVERED_ARGS and isinstance(value, str) and value not in seen:
                guessed.append(f"{task.id}: 찾지 않고 이름을 댔다 — {name}={value!r}")
    return kept, guessed


def make_nodes(deps: EngineDeps) -> dict:
    async def frame(state: CaseState) -> dict:
        patch = await deps.frame(state)
        # frame 시점에는 증거가 하나도 없다 — 그래서 여기서 인용을 단 가설은
        # 전부 `open`으로 돌아간다. 그게 맞다: 아직 아무것도 안 봤다.
        hypotheses, complaints = _accept_hypotheses(patch, have=state.evidence_ids())
        # frame 시점엔 증거가 없으므로 `seen`이 비어 있다 — 이름을 대면 전부 기록된다.
        # 그게 맞다: 아직 아무것도 안 봤는데 이름을 안다면 찍은 것이다.
        tasks, guessed = _accept_tasks(patch, room=deps.max_tasks,
                                       seen=_seen(state) if deps.check_discovery else None)
        return {**patch,
                "hypotheses": hypotheses,
                "plan_tasks": tasks,
                "llm_errors": list(patch.get("llm_errors", [])) + complaints + guessed,
                "round": 1}

    async def select(state: CaseState) -> dict:
        """실행할 것을 **병렬 폭만큼** 골라 `running`으로 굴린다."""
        chosen = runnable_tasks(state)[:deps.parallel_width]
        return {"plan_tasks": [t.model_copy(update={"status": "running"}) for t in chosen]}

    async def execute(payload: dict) -> dict:
        """태스크 하나. **Send로 병렬 발사되므로 여기서 던지면 안 된다.**

        가지 하나의 예외가 superstep 전체를 실패시켜 같은 라운드의 성공한 가지까지
        지운다. 실행기도 던지지 않기로 돼 있지만(`TaskRunnerPort`), 그 계약을
        믿고 방어를 빼면 계약을 어기는 구현 하나가 조사 전체를 멈춘다.
        """
        task = PlanTask.model_validate(payload["task"])
        case = Case.model_validate(payload["case"])
        try:
            outcome = await deps.runner.run(task, case=case)
        except Exception as exc:                                    # noqa: BLE001
            outcome = TaskOutcome(task_id=task.id, status="error",
                                  error=f"{type(exc).__name__}: {exc}")
        done = task.model_copy(update={
            "status": outcome.status,
            "result_summary": outcome.summary or None,
            "result_evidence_ids": [e.id for e in outcome.evidence],
            "error": outcome.error})
        return {"plan_tasks": [done], "evidence": list(outcome.evidence)}

    async def integrate(state: CaseState) -> dict:
        patch = await deps.integrate(state)
        room = deps.max_tasks - len(state.plan_tasks)
        fresh, guessed = _accept_tasks(
            patch, room=room, seen=_seen(state) if deps.check_discovery else None)
        hypotheses, complaints = _accept_hypotheses(patch, have=state.evidence_ids())
        patch = {**patch, "plan_tasks": fresh, "hypotheses": hypotheses,
                 "llm_errors": list(patch.get("llm_errors", [])) + complaints + guessed}

        # 리드가 이미 끝낸 이유를 댔으면(LLM 실패 등) 그것이 이긴다. 아래 규칙들이
        # 덮어쓰면 "LLM이 죽어서"가 "상한에 걸려서"로 둔갑한다 — 12a가 "미확정"과
        # "조사 실패"를 가르는 근거가 바로 이 값이다.
        if patch.get("stopped_by"):
            return {**patch, "decision": "conclude"}

        # 상한은 **결정을 본 뒤에** 본다. 먼저 보면 "계속하자"가 상한을 넘긴다.
        if state.round >= deps.max_rounds:
            return {**patch, "decision": "conclude", "stopped_by": "max_rounds"}
        if patch.get("decision") == "conclude":
            return {**patch, "stopped_by": "decision"}

        # 계속하기로 했는데 **돌릴 것이 없으면** 상한까지 빈 라운드를 돈다.
        # 그러면 보고서가 "N라운드 조사했다"고 적는데 실제로는 아무것도 안 했다.
        # 새 태스크를 합친 상태로 봐야 한다 — integrate가 방금 만든 것이 있을 수 있다.
        ahead = state.model_copy(update={
            "plan_tasks": merge_by_id(state.plan_tasks, fresh)})
        if not runnable_tasks(ahead):
            return {**patch, "decision": "conclude", "stopped_by": "no_runnable"}
        return {**patch, "decision": "continue", "round": state.round + 1}

    return {"frame": frame, "select": select, "execute": execute, "integrate": integrate}


def route_after_frame(state: CaseState) -> str:
    """frame이 끝낸 이유를 댔으면(리드 LLM이 죽었다) 라운드를 시작하지 않는다.

    흘려보내면 select가 0건 → integrate가 LLM을 **또** 부르고, 또 죽고, 끝난 이유가
    `no_runnable`로 덮여 "조사할 게 없었다"가 된다. LLM이 안 붙은 것과 볼 게 없는
    것은 완전히 다른 사실이고, 12a가 그 둘을 갈라 적는다.
    """
    return "__end__" if state.stopped_by else "select"


def route_after_select(state: CaseState):
    """select가 방금 `running`으로 굴린 것만 발사한다.

    execute가 끝나면 ok/error로 바뀌므로 이 조건은 **이번 라운드 몫만** 정확히 잡는다.
    페이로드를 JSON으로 덤프하는 이유: Send 인자는 체크포인트에 실려 저장된다
    (13단계의 재개가 여기 걸린다).
    """
    from langgraph.types import Send

    running = [t for t in state.plan_tasks if t.status == "running"]
    if not running:
        return "integrate"
    return [Send("execute", {"task": t.model_dump(mode="json"),
                             "case": state.case.model_dump(mode="json")})
            for t in running]


def route_after_integrate(state: CaseState) -> str:
    return "select" if state.decision == "continue" else "__end__"
