"""메일 발송 설정.

수신자·Agent 주소는 **비밀이 아니므로 config에 적는다.** `.env`에 넣으면
"이 배치가 누구에게 보내는가"를 git에서 볼 수 없고, 그건 감사 대상이다.
`.env`에는 API 키만 간다.
"""
import re

from pydantic import SecretStr, field_validator, model_validator

from src.config.schema_llm import TlsConfig
from src.domain.base import StrictModel

# 완전한 RFC 검증은 하지 않는다 — 정규식으로 이메일을 완벽히 가리려는 시도는
# 거의 항상 정상 주소를 거부하는 쪽으로 틀린다. 여기서 막으려는 것은
# **오타로 인한 조용한 미발송**과 **줄바꿈을 이용한 주입**이다.
_OBVIOUSLY_BAD = re.compile(r"[\s,;<>]")


class MailConfig(StrictModel):
    enabled: bool = False
    recipients: list[str] = []
    api_base: str = ""
    agent_id: str = ""
    # `${MAIL_AGENT_API_KEY}`로 참조한다. 어댑터가 os.environ을 몰래 읽으면
    # config만 봐서는 무엇이 필요한지 알 수 없고, **기동 검증이 빈 키를 미리
    # 말해 줄 수 없다** — 그러면 첫 발송에서야 401로 드러난다.
    api_key: SecretStr | None = None
    subject_prefix: str = "[운영리포트]"
    timeout_s: float = 60.0
    tls: TlsConfig = TlsConfig()

    @field_validator("recipients")
    @classmethod
    def _addresses_are_sane(cls, v: list[str]) -> list[str]:
        for address in v:
            if not address or "@" not in address:
                raise ValueError(f"수신자가 이메일 주소로 보이지 않는다 — {address!r}")
            if _OBVIOUSLY_BAD.search(address):
                # 줄바꿈이 든 주소는 **그 자체가 주입 통로**다. Agent가 받는
                # 텍스트 필드에 "to_email : a@b\nsubject : ..." 를 심을 수 있다.
                raise ValueError(
                    f"수신자에 공백·줄바꿈·구분자가 들어 있다 — {address!r}. "
                    f"여러 명은 리스트의 별도 항목으로 적어라")
        return v

    @field_validator("api_base")
    @classmethod
    def _scheme(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"api_base는 http(s)://로 시작해야 한다 — {v}")
        return v.rstrip("/")

    @model_validator(mode="after")
    def _enabled_needs_everything(self):
        # 켜 두고 반쯤 채우면 "보내는 줄 알았는데 안 갔다"가 된다.
        # 그 실패는 조용해서, 몇 주 뒤 "왜 메일이 안 오지"로 발견된다.
        if not self.enabled:
            return self
        missing = [name for name in ("recipients", "api_base", "agent_id", "api_key")
                   if not getattr(self, name)]
        if missing:
            raise ValueError(f"mail.enabled=true인데 비어 있다 — {', '.join(missing)}")
        return self

    def describe(self) -> str:
        if not self.enabled:
            return "mail 꺼짐 (enabled=false)"
        tls = ("시스템 저장소" if self.tls.use_system_store
               else self.tls.ca_bundle or ("검증 켬" if self.tls.verify else "⚠ 검증 끔"))
        return (f"agent {self.agent_id} → {self.api_base} "
                f"[수신 {len(self.recipients)}명, TLS: {tls}]")
