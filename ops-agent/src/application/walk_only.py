"""측정 도구(R3-5 ②): LLM 없이 **입구 + 걷기만** 돌린다. 엔진 기능이 아니다 — `case walk`가 케이스 블록만 찍어 정답 홉 표와 대조한다(측정 #12).

그래프를 안 쓰고 두 노드를 직접 부른다: 그래프는 frame부터 리드 LLM이 필요하고 체크포인터·이벤트가 딸려 온다. 여기선 읽기 전용이어야 한다 — 케이스
저장소·보고서·트레이스에 아무것도 남기지 않는다. 패치는 State의 병합 규칙(`merge_by_id`, 이어 붙이기)과 같게 합친다 — 그래프 안에서와 같은 State가
나와야 같은 케이스 블록이 나온다(측정판 테스트의 기대값이 그 블록이다).
"""
import re

from src.application.nodes import EngineDeps, make_nodes
from src.application.state import CaseState, merge_by_id
from src.domain.case import Case, TargetPick

_BY_ID = ("plan_tasks", "evidence", "hypotheses")
_APPEND = ("llm_errors", "facts")


def apply_patch(state: CaseState, patch: dict) -> CaseState:
    """노드 패치 하나를 State에 — LangGraph의 reducer(`state.py`의 Annotated)와 같은 규칙. 여기서 어긋나면 `case walk`의 표와 조사의 표가 다른 걷기를 말한다."""
    update = {}
    for key, value in patch.items():
        if key in _BY_ID:
            update[key] = merge_by_id(getattr(state, key), value)
        elif key in _APPEND:
            update[key] = list(getattr(state, key)) + list(value)
        else:
            update[key] = value
    return state.model_copy(update=update) if update else state


async def walk_only(case: Case, deps: EngineDeps) -> CaseState:
    """입구 → 걷기. 입구 재료가 없는 케이스(사람이 연 것)면 두 노드가 빈 패치를 내고 State는 그대로다."""
    nodes = make_nodes(deps)
    state = CaseState(case=case)
    for name in ("intake", "walk"):
        state = apply_patch(state, await nodes[name](state))
    return state


def declared_for(key: str, templates) -> str:
    """키 → 그 키가 나온 선언 이름(흐름 그래프의 rediskey) — 리터럴이 같거나 템플릿 자리가 맞는 것이 **하나**일 때만, 아니면 키 그대로(입력 템플릿
    채우기는 scan으로 간다). 다음 홉 `code.uses`는 인덱스가 아는 선언 이름으로 묻는다."""
    if key in templates:
        return key
    matched = [t for t in templates if "{" in t and re.fullmatch(re.sub(r"\\\{\w+\\\}", ".+?", re.escape(t)), key)]
    return matched[0] if len(matched) == 1 else key


async def walk_from_key(case: Case, key: str, deps: EngineDeps) -> CaseState:
    """`case walk --key`(R3-5b ③): 입구 없이 그 키를 `t-1`로 읽어 대상 키로 삼고 걷기만 돌린다. 측정 #12: 배지 밖 끝점은 라우트가 리터럴 → config로 키를
    정해 입구가 막힌다(B는 아직 없다) — 사람이 키를 대고 걷기를 잰다. 쓴 쪽은 값에서, 빈 자리는 걷기와 같은 판정(값의 맨 위), 선언 이름은 흐름 그래프에서.
    키가 없거나 읽기가 실패하면 그 사실만 적고 걷지 않는다."""
    from src.application.intake_map import _finish, _task, _unwrap, empties, writer_of

    task = _task("t-1", f"대상 키 읽기 — {key} (--key)", "redis.get", {"key": key})
    outcome = await deps.runner.run(task, case=case)
    ev = outcome.evidence[0].id if outcome.status == "ok" and outcome.evidence else ""
    raw = deps.runner.raw(ev) if ev else None
    if not ev:
        pick = TargetPick(problem=f"대상 키 읽기 실패 — {outcome.error or '증거 없음'}")
    elif raw is None:
        pick = TargetPick(problem=f"키가 없다 — {key} ({ev})")
    else:
        value = _unwrap(raw)
        writer, writer_at, writer_problem = writer_of(value, deps.classes)
        arrays, objects = empties(value)
        pick = TargetPick(key=key, basis=f"--key로 댄 키 ({ev})", declared=declared_for(key, tuple(deps.declared_by)),
                          empty=arrays, empty_objects=objects, writer=writer, writer_at=writer_at, writer_problem=writer_problem,
                          writer_searched=bool(deps.classes))
    state = apply_patch(CaseState(case=case), {"plan_tasks": [_finish(task, outcome)], "evidence": list(outcome.evidence), "target": pick})
    return apply_patch(state, await make_nodes(deps)["walk"](state))
