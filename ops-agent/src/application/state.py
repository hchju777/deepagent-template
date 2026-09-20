"""조사 엔진의 State와, 병렬 가지들이 쓴 것을 합치는 규칙.

## 리듀서는 기계적 병합만 한다

한 라운드에 태스크 여러 개가 동시에 돌고, 각자 자기 결과만 담은 조각을 돌려준다.
리듀서는 그 조각들을 합치는 자리이고 **superstep 한가운데서 돈다** — 여기서 raise하면
그 superstep 전체가 죽어서 **성공한 다른 가지의 쓰기까지 증발한다.**

그래서 개수 상한·전이 검증 같은 판단은 리듀서가 아니라 **노드가 출력을 만들 때**
한다. 리듀서는 "같은 id면 교체, 새 id면 추가"만 안다.

## 왜 순서를 지키는가

계획 태스크는 우선순위로 고르고 **동률이면 FIFO**인데, 그 FIFO의 근거가 이 리스트의
순서다. 순서가 흔들리면 같은 입력에 다른 태스크가 실행되고, 그건 재현이 안 되는 조사다.

깨지는 쪽은 "갱신된 것을 빼고 뒤에 붙이기"다 — `[i for i in existing if i.id not in
ids] + update`. 짧고 맞아 보이는데 **교체된 항목이 맨 뒤로 밀린다.**

(`{item.id: item for item in [*existing, *update]}`는 안 깨진다. 파이썬 dict는 이미
있는 키를 갱신할 때 자리를 안 옮기기 때문이다 — 처음엔 이쪽이 위험하다고 적었는데
실제로 돌려 보니 아니었다. 돌려 보기 전에는 주석도 믿을 게 못 된다.)

## StrictModel을 쓴다

참조 구현(`../../src/application/state.py`)은 `BaseModel`이다. 그대로 따라가려다
`extra="forbid"`로 실제 그래프를 돌려 봤고 — **Send 팬아웃·조건부 엣지·리듀서까지
문제없이 돌았다.** 강제가 아니었으므로 규율 5를 지킨다. 모르는 키가 State에 조용히
섞이는 쪽이 훨씬 비싸다.
"""
import operator
from typing import Annotated, Literal

from src.domain.base import StrictModel
from src.domain.case import Case, EvidenceRef, Hypothesis, PlanTask

Decision = Literal["continue", "conclude"]


def merge_by_id(existing: list, update: list) -> list:
    """`.id` 기준 병합 — 같은 id는 교체(뒤가 이김), 새 id는 뒤에 추가, 순서 유지."""
    merged = list(existing)
    index = {item.id: i for i, item in enumerate(merged)}
    for item in update:
        if item.id in index:
            merged[index[item.id]] = item
        else:
            index[item.id] = len(merged)
            merged.append(item)
    return merged


class CaseState(StrictModel):
    case: Case
    plan_tasks: Annotated[list[PlanTask], merge_by_id] = []
    evidence: Annotated[list[EvidenceRef], merge_by_id] = []
    hypotheses: Annotated[list[Hypothesis], merge_by_id] = []
    # 몇 번째 라운드인가. **호출부가 따로 세지 않는다** — 재개했을 때 호출부의
    # 카운터는 0부터 다시 시작하고, 그러면 이벤트에 실리는 라운드 번호가 틀린다.
    # 진짜 현재 값은 언제나 State(체크포인트)에 있다.
    round: int = 0
    decision: Decision | None = None
    # 왜 끝났는가. "답을 찾아서"와 "상한에 걸려서"와 "LLM이 죽어서"는 전부 다른
    # 사실이고, 12a가 "미확정"과 "조사 실패(degraded)"를 가르는 근거가 된다.
    stopped_by: Literal["decision", "max_rounds", "no_runnable", "llm_error"] | None = None
    # LLM이 죽거나 형식을 못 지킨 기록. **비어 있지 않으면 그 조사는 반쪽이다.**
    # 없으면 "아무것도 안 했다"가 "조사할 게 없었다"와 같은 모양이 된다.
    llm_errors: Annotated[list[str], operator.add] = []

    def evidence_ids(self) -> set[str]:
        """select 게이트가 보는 우주 — **리드가 실제로 본 것**뿐이다."""
        return {ref.id for ref in self.evidence}
