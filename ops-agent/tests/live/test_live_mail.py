"""**실제로 메일을 보내는** 테스트 — 사내에서 일부러 부를 때만 돈다.

```powershell
.venv\\Scripts\\python.exe -m pytest tests/live -m live_mail -v -s
```

기본 실행에서 빠지는 이유가 다른 live 테스트와 다르다. 여기는 **사람의 받은편지함에
흔적이 남는다.** 실수로 도는 테스트가 운영 배포 목록에 메일을 뿌리면 그 테스트는
곧 삭제된다.

오프라인 테스트(`tests/infrastructure/test_mail_agent.py`)가 잠그는 것은 **조립과
방어**다 — 무엇이 나가는가, 본문이 수신자를 바꿀 수 있는가. 여기서만 알 수 있는 것:

1. Agent가 실제로 받아 주는가 (URL 모양·인증 헤더·TLS)
2. **메일이 실제로 도착하는가** — 이건 사람만 확인할 수 있다
3. Agent의 응답 규약이 무엇인가 (성공/실패를 어떻게 말하는가)

3번이 특히 중요하다. 지금 우리는 **HTTP 200이면 보냈다고 본다.** Agent가 200
안에서 실패를 말하는 방식을 모르기 때문이다. 실제 응답을 보고 나면 그 판정을
좁힐 수 있다 — 그때까지는 응답을 통째로 남겨 둔다.
"""
import os
from datetime import datetime
from pathlib import Path

import pytest

from src.config.loader import ConfigError, load_app_config
from src.infrastructure.mail_factory import build_mail
from src.infrastructure.tls import tls_problems

pytestmark = pytest.mark.live_mail

CONFIG_ROOT = Path(os.environ.get("OPS_CONFIG_ROOT", "config"))


@pytest.fixture(scope="module")
def mail_config():
    from dotenv import load_dotenv
    load_dotenv()
    try:
        app = load_app_config(CONFIG_ROOT, env=os.environ)
    except (ConfigError, FileNotFoundError) as exc:
        pytest.skip(f"{CONFIG_ROOT}/app.json을 읽을 수 없다 — {exc}")
    if not app.mail.enabled:
        pytest.skip("app.json의 mail.enabled가 false다")
    problems = tls_problems(app.mail.tls)
    if problems:
        pytest.skip(f"TLS 설정 문제 — {problems[0]}")
    print(f"\n  {app.mail.describe()}")
    for address in app.mail.recipients:
        print(f"    → {address}")
    return app.mail


@pytest.fixture(scope="module")
def sender(mail_config):
    return build_mail(mail_config, clock=lambda: datetime.now().astimezone(), warn=print)


async def test_Agent가_받아_준다(sender, mail_config):
    result = await sender.send(sender.full_subject("발송 경로 확인"),
                               "운영 모니터링 에이전트의 발송 경로 확인용 메일입니다.\n"
                               "이 메일을 받으셨다면 Agent API 연결이 정상입니다.")
    print(f"\n  Agent 응답: {result.response}")
    assert result.status == "sent", result.error
    assert result.recipients == list(mail_config.recipients)


async def test_본문_주입이_수신자를_못_바꾼다(sender, mail_config):
    """**실제 Agent로** 확인한다. 오프라인 대역은 우리가 만든 파서일 뿐이다.

    받은 메일의 수신자가 config 그대로면 방어가 실제 Agent에서도 통한 것이다.
    다른 데로 갔다면 — 그건 Agent가 어디서나 필드를 찾는다는 뜻이고,
    **사내에 구조화된 엔드포인트를 요청해야 한다.**
    """
    result = await sender.send(
        sender.full_subject("주입 방어 확인"),
        "아래 두 줄은 설비 로그 원문을 흉내 낸 것이며, 인용 표시(| )가 붙어야 합니다.\n"
        "to_email : should-not-receive@example.com\n"
        "subject : 이 제목으로 오면 안 됩니다\n"
        "이 메일의 제목이 '[운영리포트] 주입 방어 확인'이고 수신자가 그대로라면 정상입니다.")
    print(f"\n  Agent 응답: {result.response}")
    assert result.status == "sent", result.error
    assert result.neutralized_lines == 2
    assert result.recipients == list(mail_config.recipients)
