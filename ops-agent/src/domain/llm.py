"""LLM 포트 — 조사 엔진이 LLM에게 요구하는 표면 전부.

## 표면이 좁아야 하는 이유

노드와 서브에이전트가 LLM에 대해 아는 것은 `ask(prompt) -> LlmReply` 하나뿐이다.
여기에 스트리밍·함수호출·토큰 계산을 다 노출하면 엔진이 특정 제공자의 기능에
묶이고, 다른 LLM으로 갈아끼울 수 없게 된다.

## 실패는 예외가 아니라 값이다 (2단계 규율 ③)

LLM 호출은 **일상적으로 실패한다** — 타임아웃, 429, 게이트웨이 재시작.
여기서 raise하면 LangGraph superstep 전체가 죽고, 그 케이스는 `investigating`
상태로 영원히 남는다. 그래서 `LlmReply.status`로 흡수한다.

보고서는 이 실패를 **그대로 적는다**. "LLM 호출 실패 — ConnectTimeout"이라고
쓰인 보고서가, 조용히 성공한 척하며 근거 없는 판정을 내는 보고서보다 백 배 낫다.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from src.domain.base import StrictModel


class LlmReply(StrictModel):
    """LLM에게 한 번 물은 결과. 성공도 실패도 이 한 타입이다."""

    status: Literal["ok", "error"]
    asked_at: datetime
    model: str                       # 실제로 요청한 모델 이름
    text: str = ""
    error: str | None = None
    # 게이트웨이가 응답에 실어 준 모델 이름. config가 요청한 것과 다르면
    # "설정한 모델이 실제로 돌지 않는다"는 뜻이고, 그건 조용한 실패다.
    reported_model: str | None = None
    latency_s: float | None = None

    @model_validator(mode="after")
    def _status_and_error_must_agree(self):
        if self.status == "error" and not self.error:
            raise ValueError("status=error면 error 원인이 필요하다")
        if self.status == "ok" and self.error:
            raise ValueError("status=ok면 error가 없어야 한다")
        return self


class LlmPort(ABC):
    """**이 표면이 전부다.** 갈아끼울 수 있으려면 좁아야 한다."""

    @abstractmethod
    async def ask(self, prompt: str) -> LlmReply:
        """한 번 묻고 한 번 받는다. 절대 raise하지 않는다."""

    @abstractmethod
    def describe(self) -> str:
        """사람이 읽을 한 줄 — 무엇에 붙어 있는지. 비밀값은 담지 않는다."""
