"""조사 엔진의 State — 스펙 §2.3의 세 축(계획·케이스 파일 참조·판정).

리듀서는 기계적 병합만 한다: 같은 id 교체, 새 id 추가. 개수 상한·전이 검증은
노드가 출력을 만들 때 수행한다 — 리듀서가 superstep 중간에 raise하면 안 되므로.
"""
import operator
from typing import Annotated, Literal

from pydantic import BaseModel

from src.domain.case import Case, EvidenceRef, Hypothesis, PlanTask, Verdict


def merge_by_id(existing: list, update: list) -> list:
    """Pydantic 모델 리스트를 .id 기준으로 병합한다.

    같은 id는 교체(뒤가 이김), 새 id는 뒤에 추가, 기존 순서 유지.
    LangGraph 리듀서로 쓰인다.
    """
    merged = list(existing)
    index = {item.id: i for i, item in enumerate(merged)}
    for item in update:
        if item.id in index:
            merged[index[item.id]] = item
        else:
            index[item.id] = len(merged)
            merged.append(item)
    return merged


class CaseState(BaseModel):
    """조사 엔진의 상태: 케이스, 계획 태스크, 증거 참조, 가설, 라운드, 판정."""
    case: Case
    plan_tasks: Annotated[list[PlanTask], merge_by_id] = []
    evidence: Annotated[list[EvidenceRef], merge_by_id] = []
    hypotheses: Annotated[list[Hypothesis], merge_by_id] = []
    round: int = 0
    decision: Literal["continue", "ask", "conclude"] | None = None
    question: str | None = None
    qa_log: Annotated[list[dict], operator.add] = []
    verdict: Verdict | None = None
    verify_attempts: int = 0
    verify_problems: list[str] = []
    interaction_policy: Literal["interactive", "autonomous"] = "autonomous"
    autonomous_question_policy: Literal["default_and_log", "park"] = "default_and_log"
