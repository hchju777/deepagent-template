"""메일 발송 포트 — 보고서를 사람에게 보내는 유일한 표면.

## 왜 실패가 값인가 (2단계 규율 ③)

메일이 안 나갔다고 조사 결과가 사라져서는 안 된다. 보고서 파일은 이미 쓰였고,
발송은 그 뒤의 일이다. 여기서 raise하면 케이스 종결 경로가 죽고, 케이스는
`investigating`으로 남는다 — **메일 서버 장애가 조사 시스템을 멈추는** 형태다.

## 왜 send가 subject와 body를 따로 받는가

사내 Agent API는 둘을 한 텍스트 필드에 합쳐 보내지만, **그 합치는 일은 어댑터의
몫**이다. 포트가 이미 합쳐진 문자열을 받으면 호출부마다 다른 형식으로 합치게 되고,
그러면 "본문이 수신자를 바꿀 수 없다"를 한 곳에서 보장할 수 없다.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from src.domain.base import StrictModel


class MailResult(StrictModel):
    """한 번 보낸 결과. 성공·실패·건너뜀이 이 한 타입이다."""

    status: Literal["sent", "skipped", "error"]
    sent_at: datetime
    recipients: list[str]        # **실제로** 보낸 대상 (config에서 온 것)
    subject: str
    error: str | None = None
    # 본문에서 필드 머리글처럼 보여 무력화한 줄 수. 0이 아니면 사람이 봐야 한다.
    neutralized_lines: int = 0
    # Agent가 돌려준 응답. 성공 판정 규약을 모르므로 **그대로 남긴다** —
    # 200인데 내부적으로 실패했을 가능성을 우리가 삼키지 않기 위해서다.
    response: Any = None
    # Agent가 응답에 실은 경고. 지금 우리의 성공 판정은 HTTP 200뿐이라
    # **이것이 유일한 추가 신호**다 — 200인데 문제가 있었던 경우를 여기서 본다.
    warnings: list[str] = []
    reason: str | None = None    # skipped일 때 왜 건너뛰었는가

    @model_validator(mode="after")
    def _status_and_cause_must_agree(self):
        if self.status == "error" and not self.error:
            raise ValueError("status=error면 error 원인이 필요하다")
        if self.status == "skipped" and not self.reason:
            raise ValueError("status=skipped면 reason이 필요하다")
        if self.status == "sent" and self.error:
            raise ValueError("status=sent면 error가 없어야 한다")
        return self


class MailPort(ABC):
    """**이 표면이 전부다.**"""

    @abstractmethod
    async def send(self, subject: str, body: str) -> MailResult:
        """수신자는 **인자가 아니다** — config가 정한다.

        인자로 받으면 호출부가 정할 수 있게 되고, 그 호출부에는 LLM이 만든
        문자열이 흘러든다. 수신자를 바꿀 수 있는 경로를 애초에 만들지 않는다.
        """

    @abstractmethod
    def describe(self) -> str:
        """사람이 읽을 한 줄 — 비밀값은 담지 않는다."""
