"""측정 도구(R3-5 ②): LLM 없이 **입구 + 걷기만** 돌린다. 엔진 기능이 아니다 — `case walk`가 케이스 블록만 찍어 정답 홉 표와 대조한다(측정 #12).

그래프를 안 쓰고 두 노드를 직접 부른다: 그래프는 frame부터 리드 LLM이 필요하고 체크포인터·이벤트가 딸려 온다. 여기선 읽기 전용이어야 한다 — 케이스
저장소·보고서·트레이스에 아무것도 남기지 않는다. 패치는 State의 병합 규칙(`merge_by_id`, 이어 붙이기)과 같게 합친다 — 그래프 안에서와 같은 State가
나와야 같은 케이스 블록이 나온다(측정판 테스트의 기대값이 그 블록이다).
"""
from src.application.nodes import EngineDeps, make_nodes
from src.application.state import CaseState, merge_by_id
from src.domain.case import Case

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
