"""메일 발송 — 발송 레저 2상(F6, 스펙 §5.4).

"기록(pending) → 발송 → sent" 2상으로 레저에 남긴다: 발송을 시도하기 전에
먼저 `record_send`로 pending을 박아 두고, 실제 발송이 성공한 뒤에야
`mark_sent`로 완료 처리한다. 그래서 프로세스가 발송 도중 죽어도 pending
기록이 남고, 다음 스윕(`retry_pending`)이 그 case_id를 다시 시도한다.
`send_id = f"report:{case_id}"`로 고정해 같은 케이스를 두 번 큐잉해도
`record_send`가 False를 돌려주며 중복 발송을 막는다(레저의 send_id 중복
억제 계약).

렌더러(report.py)와 마찬가지로 발송기·유스케이스 함수는 raise하지 않는다
— 호출자가 데몬(계획 5 Task 5)이라 메일 발송 실패가 조사 종결이나 다음
케이스 처리를 막아서는 안 된다. `send_report`가 잡는 예외는 실제
SMTP 왕복(`sender.send`)에서 난 것만이다: 그 앞의 레저 기록 자체가 깨지면
(예: DB 장애) 호출자에게 그대로 전파해 문제를 드러낸다 — 레저 없이 "성공"을
가장하는 편이 더 위험하기 때문이다.

발송이 성공한 뒤의 `mark_sent`도 `sender.send`와 **같은 try 안**에 있어야
한다(리뷰 F1) — 밖에 두면 발송은 실제로 끝났는데 레저만 "블립"으로
pending에 남고, 다음 스윕(`retry_pending`)이 이미 도착한 메일을 또 보낸다.
2상 설계가 막으려던 바로 그 중복이다. 그래서 `mark_sent` 실패는 로그만
남기고 "sent"를 그대로 반환한다 — 수신자 입장에서는 이미 성공한 발송이고,
레저가 pending으로 착각해 재시도하는 편이 더 나쁘다.

`retry_pending`은 레저에 적힌 `kind`/`target`을 그냥 장식으로 두지 않는다
(리뷰 F3): `kind != "report"`인 레코드는 건너뛰어 다른 채널이 실수로 SMTP로
새지 않게 하고, 수신자는 `cfg.recipients`(현재 설정)가 아니라 기록 당시의
`target`을 우선한다 — 설정이 나중에 바뀌어도 "원래 보내려던 사람에게
재발송"이라는 의미가 유지된다. `render()` 콜백이 레코드 하나에서 예외를
내도(리뷰 F2) 그 레코드만 건너뛰고 로그를 남긴다 — 망가진 레코드 하나가
스윕 전체를 막아서는 안 된다.

비밀값(SMTP 비밀번호)은 `SecretStr.get_secret_value()`로만 꺼내 쓰고, 로그에는
절대 남기지 않는다 — 예외 로그도 subject/case_id/오류 타입만 남긴다.
"""
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime

from src.config.schema_app import MailConfig
from src.domain.concern import Concern
from src.patrol.ledger import SendLedgerPort

Clock = Callable[[], datetime]
Render = Callable[[dict], tuple[str, str, str | None]]  # (제목, 평문, HTML|None)

logger = logging.getLogger(__name__)


class MailSenderPort(ABC):
    @abstractmethod
    async def send(self, subject: str, body: str, *, recipients: list[str],
                   html: str | None = None) -> None: ...


class NullSender(MailSenderPort):
    """실제로 메일을 보내지 않는다 — mail.enabled=False 기본 배선이나 테스트용."""

    async def send(self, subject: str, body: str, *, recipients: list[str],
                   html: str | None = None) -> None:
        logger.info("메일 발송 생략(NullSender): subject=%s recipients=%d건",
                    subject, len(recipients))


class SmtpSender(MailSenderPort):
    """aiosmtplib로 실제 발송한다. mail.enabled일 때만 이 클래스가 쓰이도록
    배선은 상위(데몬 조립)에서 고른다 — aiosmtplib import는 여기 send() 안에서
    지연시켜, mail을 안 쓰는 배포가 이 무거운 의존을 강제로 짊어지지 않게 한다.
    """

    def __init__(self, cfg: MailConfig):
        self._cfg = cfg

    async def send(self, subject: str, body: str, *, recipients: list[str],
                   html: str | None = None) -> None:
        import aiosmtplib          # 지연 import — checkpointer.py의 mongo saver와 같은 이유
        from email.message import EmailMessage

        cfg = self._cfg
        message = EmailMessage()
        message["From"] = cfg.sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content(body)
        # HTML은 대체 파트로 얹는다 — set_content만 쓰면 text/plain 하나뿐이라
        # 수신자가 태그 원문을 본다. 평문 파트를 남겨 두는 이유는 HTML을 못 읽는
        # 클라이언트와 검색 인덱스가 여전히 내용을 읽을 수 있어야 하기 때문이다.
        if html is not None:
            message.add_alternative(html, subtype="html")

        password = cfg.password.get_secret_value() if cfg.password is not None else None
        await aiosmtplib.send(
            message,
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=password,
            use_tls=cfg.use_tls,
        )


def recipients_for(cfg: MailConfig, concern: Concern) -> list[str]:
    """concern에 맞는 수신자 목록. 선언이 없으면 기본 목록으로 폴백한다.

    폴백을 두는 이유: 전부 적으라고 강제하면 사람이 같은 목록을 두 번 쓰게 되고,
    한쪽만 고치는 순간 조용히 갈라진다.
    """
    return cfg.recipients_by_concern.get(concern) or cfg.recipients


async def send_report(case_id: str, subject: str, body: str, *, sender: MailSenderPort,
                      ledger: SendLedgerPort, cfg: MailConfig, clock: Clock,
                      concern: Concern, html: str | None = None) -> str:
    """케이스 보고서 메일을 pending 기록 → 발송 → sent 갱신 순으로 보낸다.

    반환값은 "sent"|"skipped"|"duplicate"|"failed" 중 하나다. cfg.enabled가
    False면 레저에 아무 흔적도 남기지 않고 "skipped" — 꺼진 채널은 pending을
    쌓지 않는다(나중에 켜져도 재시도 대상이 되지 않도록).
    """
    if not cfg.enabled:
        return "skipped"

    send_id = f"report:{case_id}"
    # 레저의 target이 실제 수신자와 다르면 "누구에게 갔나"를 사후에 알 수 없다.
    recipients = recipients_for(cfg, concern)
    target = ", ".join(recipients)
    if not ledger.record_send(send_id, kind="report", target=target, at=clock()):
        return "duplicate"

    try:
        await sender.send(subject, body, recipients=recipients, html=html)
    except Exception as exc:                                    # noqa: BLE001 — raise 금지(계약)
        logger.warning("메일 발송 실패(재시도 대상으로 pending 유지): case_id=%s err=%s: %s",
                       case_id, type(exc).__name__, exc)
        return "failed"

    try:
        ledger.mark_sent(send_id, clock())
    except Exception as exc:                                    # noqa: BLE001 — F1: 발송은
        # 이미 끝났다 — mark_sent만 깨졌다고 pending으로 남기면 다음 스윕이 이미
        # 도착한 메일을 또 보낸다(2상 설계가 막으려던 바로 그 중복). 로그만 남기고 sent.
        logger.warning("mark_sent 실패(발송은 완료됨, pending 유지 안 함): send_id=%s "
                       "err=%s: %s", send_id, type(exc).__name__, exc)
    return "sent"


async def retry_pending(*, sender: MailSenderPort, ledger: SendLedgerPort, cfg: MailConfig,
                        clock: Clock, render: Render) -> int:
    """pending 발송을 각각 재시도한다. 성공한 건수를 반환한다 — 데몬 스윕이 호출.

    실패한 건은 레저에 그대로 pending으로 남아 다음 스윕이 다시 집는다
    (record_send를 다시 부르지 않는다 — 이미 기록돼 있다).

    cfg.enabled가 False면(M2) 곧장 0을 돌려주고 아무것도 건드리지 않는다 —
    메일을 끈 뒤에도 이 스윕이 NullSender로 계속 "보내고" mark_sent를 찍으면
    실제로는 아무도 받지 못한 발송이 레저엔 sent로 남는다.
    """
    if not cfg.enabled:
        return 0
    done = 0
    for record in ledger.pending_sends():
        if record["kind"] != "report":     # F3: 다른 채널(향후 확장분)이 SMTP로 새지 않게
            continue

        try:
            subject, body, html = render(record)
        except Exception as exc:                                # noqa: BLE001 — F2: 레코드
            # 하나가 망가져도 나머지 pending을 계속 처리한다 — 스윕 전체를 막지 않는다.
            logger.warning("재시도 렌더 실패(건너뜀): send_id=%s err=%s: %s",
                           record["send_id"], type(exc).__name__, exc)
            continue

        # F3: 현재 cfg.recipients가 아니라 기록 당시의 target을 우선한다 — 설정이
        # 바뀌어도 "원래 보내려던 사람에게 재발송"이라는 의미가 유지된다.
        recipients = [r.strip() for r in record["target"].split(",") if r.strip()]
        recipients = recipients or cfg.recipients
        try:
            await sender.send(subject, body, recipients=recipients, html=html)
        except Exception as exc:                                # noqa: BLE001 — raise 금지(계약)
            logger.warning("재시도 발송 실패(pending 유지): send_id=%s err=%s: %s",
                           record["send_id"], type(exc).__name__, exc)
            continue

        try:
            ledger.mark_sent(record["send_id"], clock())
        except Exception as exc:                                # noqa: BLE001 — F1과 동일 이유:
            # 발송은 끝났다. mark_sent만 깨져도 성공으로 센다(pending에 남기지 않는다).
            logger.warning("mark_sent 실패(발송은 완료됨, pending 유지 안 함): send_id=%s "
                           "err=%s: %s", record["send_id"], type(exc).__name__, exc)
        done += 1
    return done
