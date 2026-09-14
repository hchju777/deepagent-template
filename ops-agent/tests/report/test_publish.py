"""발행 — **파일이 먼저, 메일이 나중.** 그 순서와 "실패도 값"을 지킨다.

여기 있는 테스트들이 막으려는 사고는 하나다: **리포트가 안 나갔는데 아무도
모르는 것.** 메일 게이트웨이가 죽었을 때 파일이라도 남는가, 실패가 종료 코드로
드러나는가, dry-run이 정말 안 보내는가.
"""
from datetime import datetime

from src.config.schema_mail import MailConfig
from src.domain.mail import MailPort, MailResult
from src.infrastructure.mail_fakes import RecordingMailSender
from src.report.publish import output_path, publish, subject_for

from tests.support import YESTERDAY, scenario

HTML = "<!DOCTYPE html><html><body>리포트</body></html>"

ENABLED = MailConfig(enabled=True, recipients=["ops@example.com"],
                     api_base="https://agent.example.net/api", agent_id="a1",
                     api_key="hunter2-secret")


FIXED = lambda: datetime(2026, 9, 7, 8, 0)          # noqa: E731


async def _publish(tmp_path, window, *, mail=None, dry_run=False):
    return await publish(HTML, scenario=scenario(), scenario_name="daily-alarm",
                         window=window, output_dir=tmp_path / "output", mail=mail,
                         clock=FIXED, dry_run=dry_run)


class WatchingMail(RecordingMailSender):
    """`send`가 불린 **그 순간** 파일이 이미 있었는지를 기록한다."""

    def __init__(self, cfg, *, clock, path):
        super().__init__(cfg, clock=clock)
        self._path = path
        self.file_existed_at_send: bool | None = None

    async def send(self, subject, body):
        self.file_existed_at_send = self._path.exists()
        return await super().send(subject, body)


class BrokenMail(MailPort):
    """계약을 어기고 **던지는** 어댑터. 최외곽 방어선이 실제로 있는지 본다."""

    def describe(self):
        return "(깨진 대역)"

    def full_subject(self, key):
        return key

    def preview(self, subject, body):
        return {"recipients": []}

    async def send(self, subject, body):
        raise RuntimeError("게이트웨이가 죽었다")


class FailingMail(RecordingMailSender):
    async def send(self, subject, body):
        return MailResult(status="error", sent_at=self._clock(),
                          recipients=list(self._cfg.recipients), subject=subject,
                          error="HTTP 503")


# ── 순서: 파일이 먼저 ─────────────────────────────────────────────────

async def test_파일은_메일보다_먼저_쓰인다(tmp_path, window, clock):
    """순서가 뒤집히면 발송 실패가 곧 "아무것도 없음"이 된다.

    "파일이 존재한다"만 보면 순서를 못 본다 — 메일이 성공하면 둘 다 있다.
    그래서 **send가 불린 순간**을 본다.
    """
    path = output_path(tmp_path / "output", "daily-alarm", window)
    mail = WatchingMail(ENABLED, clock=clock, path=path)

    await _publish(tmp_path, window, mail=mail)

    assert mail.file_existed_at_send is True, "메일이 파일보다 먼저 나갔다"


async def test_메일이_실패해도_파일은_남는다(tmp_path, window, clock):
    published = await _publish(tmp_path, window,
                              mail=FailingMail(ENABLED, clock=clock))

    assert published.path is not None and published.path.exists()
    assert published.path.read_text(encoding="utf-8") == HTML
    assert published.failed, "메일 실패가 결과에 안 드러난다"
    assert "503" in "\n".join(published.describe())


async def test_어댑터가_던져도_값으로_돌아온다(tmp_path, window):
    """무raise 규율의 최외곽. 스케줄러가 예외를 받으면 그 잡을 조용히 뺄 수 있다."""
    published = await _publish(tmp_path, window, mail=BrokenMail())

    assert published.mail is not None and published.mail.status == "error"
    assert "RuntimeError" in published.mail.error
    assert published.path is not None and published.path.exists()


# ── 메일이 없거나 꺼져 있어도 파일은 나온다 ───────────────────────────

async def test_메일이_꺼져_있으면_건너뛰고_파일은_나온다(tmp_path, window, clock):
    off = RecordingMailSender(MailConfig(enabled=False), clock=clock)

    published = await _publish(tmp_path, window, mail=off)

    assert published.path is not None and published.path.exists()
    assert published.mail is not None and published.mail.status == "skipped"
    assert not published.failed, "안 보내도록 설정한 것은 실패가 아니다"
    assert off.sent == []


async def test_메일_포트가_없어도_파일은_나온다(tmp_path, window):
    """`--no-mail`. 사람이 손으로 확인하려고 돌리는 경우다."""
    published = await _publish(tmp_path, window, mail=None)

    assert published.path is not None and published.path.exists()
    assert published.mail is None and published.preview is None
    assert not published.failed


# ── dry-run ───────────────────────────────────────────────────────────

async def test_dry_run은_보내지_않고_나갈_요청을_보여준다(tmp_path, window, clock):
    mail = RecordingMailSender(ENABLED, clock=clock)

    published = await _publish(tmp_path, window, mail=mail, dry_run=True)

    assert mail.sent == [], "dry-run인데 보냈다"
    assert published.mail is None
    assert published.preview is not None
    assert published.preview["recipients"] == ["ops@example.com"]
    # 파일은 쓴다 — dry-run의 목적은 **나갈 본문을 눈으로 보는 것**이다.
    assert published.path is not None and published.path.exists()
    assert not published.failed


async def test_dry_run_미리보기에_키가_없다(tmp_path, window, clock):
    published = await _publish(tmp_path, window,
                              mail=RecordingMailSender(ENABLED, clock=clock),
                              dry_run=True)

    assert published.preview["headers"]["x-api-key"] == "***"
    assert "hunter2-secret" not in str(published.preview)


# ── 파일을 못 써도 발송은 계속한다 ────────────────────────────────────

async def test_파일을_못_쓰면_값으로_돌아오고_메일은_그래도_간다(tmp_path, window, clock):
    """파일은 보관이고 메일이 목적이다 — 디스크가 가득 찼다고 발송을 멈추면
    사람이 오늘 리포트를 아예 못 본다. 대신 `failed`로 시끄럽게 말한다."""
    blocked = tmp_path / "output"
    blocked.write_text("이 자리는 파일이다", encoding="utf-8")   # mkdir이 실패한다
    mail = RecordingMailSender(ENABLED, clock=clock)

    published = await _publish(tmp_path, window, mail=mail)

    assert published.path is None and published.write_error
    assert published.failed
    assert len(mail.sent) == 1, "파일 실패가 발송까지 막았다"
    assert "파일을 쓰지 못했다" in "\n".join(published.describe())


# ── 제목과 파일 이름의 기준일 ─────────────────────────────────────────

def test_제목의_기준일은_어제다(window):
    """돌린 날을 쓰면 재실행·지연 실행에서 같은 데이터가 다른 제목으로 두 번 간다."""
    subject = subject_for(scenario(), window)

    assert YESTERDAY.isoformat() in subject
    assert "(금)" in subject, subject
    assert "2026-09-07" not in subject, "돌린 날이 제목에 들어갔다"


def test_제목에_시나리오_제목이_들어간다(window):
    assert "일일 알람 리포트" in subject_for(scenario(), window)


def test_파일_이름에_기준일이_들어간다(tmp_path, window):
    path = output_path(tmp_path, "daily-alarm", window)

    assert path.name == "daily-alarm-2026-09-04.html"


def test_같은_날_두_번_돌리면_같은_파일을_가리킨다(tmp_path, window):
    """덮어쓰기는 의도다 — 재실행이 파일을 늘리면 어느 것이 최신인지 모른다."""
    assert output_path(tmp_path, "d", window) == output_path(tmp_path, "d", window)


async def test_발송_제목에_config_접두사가_붙는다(tmp_path, window, clock):
    mail = RecordingMailSender(ENABLED, clock=clock)

    published = await _publish(tmp_path, window, mail=mail)

    assert published.subject.startswith("[운영리포트] ")
    assert mail.sent[0]["subject"] == published.subject


# ── 구조: 발행 조립이 한 곳뿐인가 ─────────────────────────────────────

# 메일을 **직접 보내도 되는 곳**. 산문 규율은 읽지 않으면 무력하므로 테스트가 지킨다.
MAY_SEND_MAIL = {
    "src/report/publish.py",     # 발행 조립의 유일한 지점
    "src/__main__.py",           # `mail send` — 사람이 연결 확인용으로 직접 부르는 명령
}


def test_메일_발송_호출은_정해진_곳에만_있다():
    """새 발행 경로가 "파일 쓰고 메일 보내기"를 따로 베끼는 것을 막는다.

    원본 템플릿에서 `case resume`이 그 조립을 베끼면서 메일 한 줄을 빠뜨렸고,
    한동안 아무도 몰랐다. 조립이 한 곳이면 빠뜨릴 자리가 없다.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    offenders = []
    for path in sorted((root / "src").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in MAY_SEND_MAIL:
            continue
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if ".send(" in line:
                offenders.append(f"{relative}:{number}")

    assert not offenders, (
        f"메일을 직접 보내는 곳이 늘었다 — {offenders}. "
        "발행은 src.report.publish.publish()를 거쳐야 한다 — 파일을 먼저 쓰는 순서와 "
        "제목의 기준일이 그 안에 있고, 베끼면 언젠가 하나가 빠진다")
