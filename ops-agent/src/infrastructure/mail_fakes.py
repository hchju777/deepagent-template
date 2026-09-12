"""네트워크 없는 메일 대역 — 무엇이 나갔을지를 기록만 한다."""
from src.config.schema_mail import MailConfig
from src.domain.base import Clock
from src.domain.mail import MailPort, MailResult
from src.infrastructure.mail_agent import compose_input_value


class RecordingMailSender(MailPort):
    """보내지 않고 **보낼 뻔한 것**을 남긴다.

    `AgentMailSender`와 **같은 조립 함수**를 쓴다. 다른 문자열을 만들면
    테스트가 검증하는 것이 실제로 나가는 것과 달라진다.
    """

    def __init__(self, cfg: MailConfig, *, clock: Clock):
        self._cfg = cfg
        self._clock = clock
        self.sent: list[dict] = []

    def describe(self) -> str:
        return f"(기록만) {self._cfg.describe()}"

    async def send(self, subject: str, body: str) -> MailResult:
        if not self._cfg.enabled:
            return MailResult(status="skipped", sent_at=self._clock(), recipients=[],
                              subject=subject, reason="mail.enabled=false")
        input_value, changed = compose_input_value(self._cfg, subject, body)
        self.sent.append({"subject": subject, "input_value": input_value})
        return MailResult(status="sent", sent_at=self._clock(),
                          recipients=list(self._cfg.recipients), subject=subject,
                          neutralized_lines=changed, response={"_기록만": True})
