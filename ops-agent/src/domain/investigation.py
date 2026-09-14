"""태스크를 실제로 실행하는 표면.

## 왜 `ports.py`에 두지 않는가

`ports.py`는 **대상 시스템에 닿는 표면**이고 `tests/domain/test_ports.py`가 그 목록을
단정한다. 실행기는 대상 시스템이 아니라 우리 엔진의 부품이다. 거기 섞으면 "쓰기
메서드가 없다"를 단정하는 테스트가 실행기까지 훑게 되고, 그 테스트가 무엇을 지키는
것이었는지가 흐려진다.

## 왜 이음매가 필요한가

10a의 `ProbeRunner`는 태스크가 **선언한** 읽기 하나를 수행한다. 11b의 서브에이전트는
LLM이 도구를 골라 여러 번 읽는다. 둘은 "태스크를 받아 증거를 낸다"는 점만 같고
속이 완전히 다르다 — 그 공통점을 타입으로 고정해 두면 11b가 그래프를 안 건드린다.

## 무raise

브랜치 하나의 예외는 LangGraph superstep 전체를 실패시켜 **성공한 다른 브랜치의
쓰기까지 증발시킨다.** 그래서 실행기는 던지지 않고 `status="error"`로 돌려준다.
"조회했더니 비어 있음"(그 자체가 증거)과 "조회 실패"(아무것도 모름)는 다른 사실이므로
integrate가 둘을 구별할 수 있어야 한다.
"""
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import model_validator

from src.domain.base import StrictModel
from src.domain.case import Case, EvidenceRef, PlanTask


class TaskOutcome(StrictModel):
    """태스크 하나를 실행한 결과. 성공도 실패도 이 한 타입이다."""

    task_id: str
    status: Literal["ok", "error"]
    summary: str = ""
    # **실제로 일어난 읽기**에서 나온 것만 담는다. 11b에서 LLM이 "ev-9를 봤다"고
    # 말해도 도구가 만들지 않았으면 여기 없어야 한다(규율 3).
    evidence: list[EvidenceRef] = []
    error: str | None = None

    @model_validator(mode="after")
    def _status_and_error_must_agree(self):
        if self.status == "error" and not self.error:
            raise ValueError("status=error면 error 원인이 필요하다")
        if self.status == "ok" and self.error:
            raise ValueError("status=ok면 error가 없어야 한다")
        return self


class TaskRunnerPort(ABC):
    @abstractmethod
    async def run(self, task: PlanTask, *, case: Case) -> TaskOutcome:
        """**절대 raise하지 않는다.** 실패는 `status="error"`로 돌려준다."""

    @abstractmethod
    def describe(self) -> str:
        """사람이 읽을 한 줄. 비밀값은 담지 않는다."""
