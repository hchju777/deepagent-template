"""메일 발송 조립의 **유일한 지점**(규율 8과 같은 이유).

케이스 종결 경로가 셋이 될 때(데몬·chat·resume) 각자 발송을 조립하면 언젠가
하나가 빠뜨린다. 원본 템플릿에서 실제로 `case resume`이 한동안 그랬다.
"""
from src.config.schema_mail import MailConfig
from src.domain.base import Clock
from src.domain.mail import MailPort
from src.infrastructure.tls import warn_if_unverified


def build_mail(cfg: MailConfig, *, clock: Clock, warn=None) -> MailPort:
    if cfg.enabled:
        warn_if_unverified(cfg.tls, warn=warn)
    from src.infrastructure.mail_agent import AgentMailSender
    return AgentMailSender(cfg, clock=clock)
