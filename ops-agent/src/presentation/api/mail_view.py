"""메일 수신자 보기 — 주소를 가린다.

Swagger 화면은 캡처돼 메신저·이슈에 붙는다. 수신자 주소는 개인정보라 거기 그대로 남기지 않는다. 사람이 이 화면에서 확인하려는
것은 "몇 명에게, 사내인가 밖인가"이므로 수와 도메인은 남기고, 이름 부분은 첫 글자만 둔다 — 길이도 안 드러나게 `***`는 고정이다.
"""
from src.config.schema_mail import MailConfig


def mask_address(address: str) -> str:
    local, at, domain = address.rpartition("@")
    if not at:
        return "***"
    return f"{local[:1]}***@{domain}"


def recipients_view(mail: MailConfig) -> dict:
    return {"enabled": mail.enabled, "count": len(mail.recipients),
            "recipients": [mask_address(address) for address in mail.recipients],
            "notify_count": len(mail.notify_to), "notify_to": [mask_address(address) for address in mail.notify_to]}
