"""사내 Agent API로 메일을 보낸다.

## 이 API의 모양

수신자·제목·본문을 **한 텍스트 필드**에 담아 보내고, Agent가 그것을 파싱해
메일로 만든다. 사내 규약이므로 그대로 따른다.

```
POST {api_base}/{agent_id}?stream=false
x-api-key: <키>

{"input_type": "chat", "output_type": "chat",
 "input_value": "to_email : a@x, b@x\\nsubject : [운영리포트] ...\\nbody : \\n<본문>"}
```

## 그래서 생기는 위험: 본문이 지시가 될 수 있다

`input_value`는 구조화된 필드가 아니라 **텍스트**다. 그리고 본문에는 LLM이 쓴
문장과 대상 시스템에서 읽은 데이터가 들어간다. 본문에 이런 줄이 섞이면:

```
to_email : someone-else@example.com
```

Agent의 파서가 그것을 수신자 지시로 읽을 수 있다. **의도적 공격이 아니어도**
일어난다 — 설비 알람 메시지나 로그 한 줄이 우연히 그 모양일 수 있고, 조사
보고서에는 그런 원문이 그대로 인용된다.

세 겹으로 막는다:

1. **수신자는 config에서만 온다.** 포트의 `send`가 수신자를 인자로 받지 않으므로
   호출부가 정할 방법이 아예 없다. 이게 제일 단단한 층이다.
2. **본문의 필드 머리글을 무력화한다.** 줄 앞에 `| `를 붙여 파서의 줄 앵커를
   깨뜨린다. 지운 게 아니라 **인용 표시**라 사람이 읽을 때 내용이 남는다.
3. **본문은 맨 마지막**에 둔다. 앞쪽 필드를 먼저 읽는 파서에 대해 한 겹 더.

**한계를 정직하게 적는다**: Agent의 파서를 우리가 모르므로, 줄 앵커가 아니라
문자열 어디서나 찾는 파서라면 2번이 안 통한다. 그때 남는 방어는 1번이고,
그건 "우리가 보낸 수신자"가 바뀌지 않음을 보장한다 — Agent가 다른 데로 보내면
그건 Agent의 문제이고 `MailResult.response`에 흔적이 남는다.

**구조화된 필드를 받는 엔드포인트가 있다면 그쪽이 정답이다.** 이 클래스 전체가
불필요해진다.
"""
import re
import time
from typing import Any

from src.config.schema_mail import MailConfig
from src.domain.base import Clock
from src.domain.mail import MailPort, MailResult
from src.infrastructure.tls import verify_arg

# 줄 앞에서 시작하는 필드 머리글. 전각 콜론(：)도 본다 — 한국어 문서에 흔하고,
# 파서가 그것도 받아들일지 우리가 모른다.
_FIELD_LINE = re.compile(
    r"^\s*(to_email|to|cc|bcc|from|subject|title|body|content)\s*[:：]", re.IGNORECASE)

# 제어 문자는 뺀다(줄바꿈·탭 제외). Agent 파서가 어떻게 반응할지 모르고,
# 메일 본문에 있어야 할 이유도 없다.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 제목 안에 박힌 필드 머리글. 제목은 한 줄이라 `| ` 인용이 안 먹으므로
# 콜론을 `-`로 바꿔 파서가 필드로 읽지 못하게 한다. 제목은 짧고 우리가 만드는
# 문자열이라 이 정도 훼손은 감수할 수 있다(본문은 증거라서 그럴 수 없다).
_FIELD_INLINE = re.compile(
    r"\b(to_email|to|cc|bcc|from|subject|title|body|content)\s*[:：]", re.IGNORECASE)

MAX_BODY_CHARS = 100_000


def neutralize_subject(subject: str) -> str:
    """제목을 한 줄로 만들고, 그 안의 필드 머리글을 무력화한다.

    줄바꿈을 공백으로만 바꾸면 `제목\nto_email : x`가 `제목 to_email : x`가 되어
    줄 앵커는 벗어나지만 **문자열 안에는 그대로 남는다.** 어디서나 찾는 파서에는
    그것으로 충분하다.
    """
    flat = " ".join(subject.split())
    return _FIELD_INLINE.sub(lambda m: m.group(0).replace(":", "-").replace("：", "-"), flat)


def neutralize_field_lines(text: str) -> tuple[str, int]:
    """본문에서 필드 머리글처럼 보이는 줄을 무력화한다.

    지우지 않고 `| `를 붙이는 이유: 조사 보고서의 그 줄이 **증거일 수 있다.**
    지워 버리면 사람이 원문을 못 본다. 인용 표시는 내용을 남기면서 줄 앵커를 깬다.
    """
    lines, changed = [], 0
    for line in text.split("\n"):
        if _FIELD_LINE.match(line):
            lines.append(f"| {line}")
            changed += 1
        else:
            lines.append(line)
    return "\n".join(lines), changed


def compose_input_value(cfg: MailConfig, subject: str, body: str) -> tuple[str, int]:
    """Agent에 보낼 한 덩어리 텍스트. **조립은 여기 하나뿐이다.**"""
    safe_body, changed = neutralize_field_lines(_CONTROL.sub("", body))
    if len(safe_body) > MAX_BODY_CHARS:
        safe_body = safe_body[:MAX_BODY_CHARS] + "\n\n…(본문이 너무 길어 잘렸다)"
    return ("to_email : " + ", ".join(cfg.recipients) + "\n"
            + "subject : " + neutralize_subject(subject) + "\n"
            + "body : \n" + safe_body), changed


class AgentMailSender(MailPort):
    def __init__(self, cfg: MailConfig, *, clock: Clock, ticker=time.perf_counter):
        self._cfg = cfg
        self._clock = clock
        self._ticker = ticker

    def describe(self) -> str:
        return self._cfg.describe()

    def full_subject(self, key: str) -> str:
        prefix = self._cfg.subject_prefix
        return f"{prefix} {key}".strip() if prefix else key

    def preview(self, subject: str, body: str) -> dict:
        """실제로 나갈 요청. `--dry-run`이 이것을 보여준다.

        보내기 전에 **눈으로 확인할 수 있어야** 한다 — 수신자가 맞는지, 본문의
        어느 줄이 무력화됐는지. 키는 담지 않는다.
        """
        input_value, changed = compose_input_value(self._cfg, subject, body)
        return {"url": f"{self._cfg.api_base}/{self._cfg.agent_id}?stream=false",
                "headers": {"Content-Type": "application/json", "x-api-key": "***"},
                "body": {"input_type": "chat", "output_type": "chat",
                         "input_value": input_value},
                "recipients": list(self._cfg.recipients),
                "neutralized_lines": changed}

    async def send(self, subject: str, body: str) -> MailResult:
        if not self._cfg.enabled:
            # 조용히 아무것도 안 하면 "보낸 줄 알았는데 안 갔다"가 된다.
            return MailResult(status="skipped", sent_at=self._clock(),
                              recipients=[], subject=subject,
                              reason="mail.enabled=false")
        input_value, changed = compose_input_value(self._cfg, subject, body)
        url = f"{self._cfg.api_base}/{self._cfg.agent_id}?stream=false"
        try:
            import httpx

            async with httpx.AsyncClient(timeout=self._cfg.timeout_s,
                                         verify=verify_arg(self._cfg.tls)) as client:
                response = await client.post(
                    url,
                    json={"input_type": "chat", "output_type": "chat",
                          "input_value": input_value},
                    headers={"Content-Type": "application/json",
                             "x-api-key": self._cfg.api_key.get_secret_value()})
        except Exception as exc:                                   # noqa: BLE001
            return MailResult(status="error", sent_at=self._clock(),
                              recipients=list(self._cfg.recipients), subject=subject,
                              neutralized_lines=changed,
                              error=f"{type(exc).__name__}: {exc}")

        payload = _decode(response)
        if response.status_code >= 400:
            return MailResult(status="error", sent_at=self._clock(),
                              recipients=list(self._cfg.recipients), subject=subject,
                              neutralized_lines=changed, response=payload,
                              error=f"HTTP {response.status_code}")
        # 200이면 "보냈다"로 본다. Agent가 내부적으로 실패했을 때의 응답 규약을
        # 우리가 모르므로 **응답을 그대로 남긴다** — 삼키지 않는 것이 최선이다.
        return MailResult(status="sent", sent_at=self._clock(),
                          recipients=list(self._cfg.recipients), subject=subject,
                          neutralized_lines=changed, response=payload)


def _decode(response: Any) -> Any:
    try:
        payload = response.json()
    except Exception:                                              # noqa: BLE001
        return {"_비JSON응답": response.text[:1000]}
    # 응답이 클 수 있다 — 결과에 통째로 실으면 로그와 케이스 파일이 부푼다.
    text = str(payload)
    return payload if len(text) <= 4000 else {"_잘린응답": text[:4000]}
