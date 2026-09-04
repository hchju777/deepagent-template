from datetime import datetime, timezone

from src.config.schema_app import MailConfig
from src.patrol.ledger import InMemoryLedger
from src.presentation.mail import NullSender, retry_pending, send_report

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CFG = MailConfig(enabled=True, host="smtp", sender="a@x", recipients=["b@y"])


class RecordingSender(NullSender):
    def __init__(self, fail_times=0):
        self.sent, self._fail = [], fail_times

    async def send(self, subject, body, *, recipients, html=None):
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("SMTP 거부")
        self.sent.append((subject, recipients))


async def test_기록_먼저_발송_그다음_sent():
    ledger, sender = InMemoryLedger(), RecordingSender()
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T, concern="system") == "sent"
    assert sender.sent == [("제목", ["b@y"])] and ledger.pending_sends() == []


async def test_발송_실패는_pending으로_남고_재시도가_비운다():
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=1)
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T, concern="system") == "failed"
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-1"]
    done = await retry_pending(sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T,
                               render=lambda rec: ("제목", "본문", None))
    assert done == 1 and ledger.pending_sends() == []


async def test_중복_발송은_억제되고_비활성은_건너뛴다():
    ledger, sender = InMemoryLedger(), RecordingSender()
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T, concern="system")
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T, concern="system") == "duplicate"
    assert len(sender.sent) == 1
    off = MailConfig()
    assert await send_report("c-2", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=off, clock=lambda: T, concern="system") == "skipped"


async def test_mark_sent_실패는_재발송을_부르지_않는다():
    class FlakyLedger(InMemoryLedger):
        def mark_sent(self, send_id, at):
            raise RuntimeError("레저 블립")
    ledger, sender = FlakyLedger(), RecordingSender()
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T, concern="system") == "sent"
    assert len(sender.sent) == 1                      # 발송은 됐고 raise도 없다


async def test_render_실패는_남은_pending을_막지_않는다():
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=2)
    for cid in ("c-1", "c-2"):
        await send_report(cid, "제목", "본문", sender=sender, ledger=ledger,
                          cfg=CFG, clock=lambda: T, concern="system")
    def render(rec):
        if rec["send_id"] == "report:c-1":
            raise ValueError("망가진 레코드")
        return ("제목", "본문", None)
    done = await retry_pending(sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T,
                               render=render)
    assert done == 1                                   # c-2는 보내졌다
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-1"]


async def test_재발송은_기록된_수신자에게_간다():
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=1)
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                      cfg=CFG, clock=lambda: T, concern="system")
    changed = MailConfig(enabled=True, host="smtp", sender="a@x", recipients=["새사람@z"])
    await retry_pending(sender=sender, ledger=ledger, cfg=changed, clock=lambda: T,
                        render=lambda rec: ("제목", "본문", None))
    assert sender.sent[-1][1] == ["b@y"]                # 기록된 target 우선


async def test_비활성_상태의_스윕은_건드리지_않고_0을_돌려준다():
    # M2: retry_pending이 cfg.enabled를 안 보면, 메일을 끈 뒤에도 스윕이
    # NullSender로 "보내고" mark_sent를 찍어 "보낸 적 없는 발송"을 레저에 남긴다.
    ledger = InMemoryLedger()
    ledger.record_send("report:c-1", kind="report", target="b@y", at=T)   # 켜져 있을 때 남긴 pending
    off = MailConfig()                                        # enabled=False(기본값)
    sender = RecordingSender()
    done = await retry_pending(sender=sender, ledger=ledger, cfg=off, clock=lambda: T,
                               render=lambda rec: ("제목", "본문", None))
    assert done == 0
    assert sender.sent == []                                  # NullSender조차 "보내지" 않았다
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-1"]   # pending 그대로


def test_메일을_켜면_host와_recipients가_필요하다():
    import pytest
    from pydantic import ValidationError
    MailConfig()                                        # 꺼져 있으면 빈 값 허용
    with pytest.raises(ValidationError):
        MailConfig(enabled=True, host="", recipients=["a@x"])
    with pytest.raises(ValidationError):
        MailConfig(enabled=True, host="smtp", recipients=[])


def _fake_aiosmtplib(monkeypatch, sink: dict):
    import sys
    import types

    async def fake_send(message, **kw):
        sink["message"] = message

    module = types.ModuleType("aiosmtplib")
    module.send = fake_send
    monkeypatch.setitem(sys.modules, "aiosmtplib", module)


def test_HTML_본문은_대체_파트로_실린다(monkeypatch):
    # set_content만 쓰면 text/plain 하나뿐이라 수신자가 태그 원문을 본다.
    import asyncio

    from src.config.schema_app import MailConfig
    from src.presentation.mail import SmtpSender
    sink = {}
    _fake_aiosmtplib(monkeypatch, sink)
    cfg = MailConfig(enabled=True, host="smtp.x", sender="a@x", recipients=["b@x"])
    asyncio.run(SmtpSender(cfg).send("제목", "평문 본문",
                                     recipients=["b@x"], html="<p>본문</p>"))
    types_seen = {part.get_content_type() for part in sink["message"].walk()}
    assert "text/plain" in types_seen and "text/html" in types_seen


def test_html이_없으면_평문만_보낸다(monkeypatch):
    import asyncio

    from src.config.schema_app import MailConfig
    from src.presentation.mail import SmtpSender
    sink = {}
    _fake_aiosmtplib(monkeypatch, sink)
    cfg = MailConfig(enabled=True, host="smtp.x", sender="a@x", recipients=["b@x"])
    asyncio.run(SmtpSender(cfg).send("제목", "평문", recipients=["b@x"]))
    types_seen = {part.get_content_type() for part in sink["message"].walk()}
    assert "text/html" not in types_seen


_ROUTED = MailConfig(enabled=True, host="smtp", sender="a@x", recipients=["platform@y"],
                     recipients_by_concern={"operation": ["ops@y"]})


async def test_operation_케이스는_다른_수신자에게_간다():
    # concern 축의 존재 이유가 여기서 처음으로 실제 효과를 낸다 — 그 전까지는
    # 필드가 실려 다니기만 한다.
    ledger, sender = InMemoryLedger(), RecordingSender()
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                      cfg=_ROUTED, clock=lambda: T, concern="operation")
    assert sender.sent == [("제목", ["ops@y"])]


async def test_선언되지_않은_concern은_기본_수신자로_간다():
    # 전부 적으라고 강제하면 같은 목록을 두 번 쓰게 되고, 한쪽만 고치는 순간
    # 조용히 갈라진다.
    ledger, sender = InMemoryLedger(), RecordingSender()
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                      cfg=_ROUTED, clock=lambda: T, concern="system")
    assert sender.sent == [("제목", ["platform@y"])]


async def test_레저의_수신자_기록도_concern을_따른다():
    # target이 실제 수신자와 다르면 "누구에게 갔나"를 사후에 알 수 없다.
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=1)
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                      cfg=_ROUTED, clock=lambda: T, concern="operation")
    assert ledger.pending_sends()[0]["target"] == "ops@y"


def test_알_수_없는_concern_키는_config_검증이_거부한다():
    # 오타("operations")면 그 목록이 영원히 안 쓰이고 아무도 모른다.
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MailConfig(enabled=True, host="h", sender="s", recipients=["a@b"],
                   recipients_by_concern={"operations": ["x@y"]})


def test_빈_수신자_목록은_config_검증이_거부한다():
    # "이 축은 보내지 마라"인지 "기본으로 폴백"인지 사람이 헷갈린다. 채널을 끄려면
    # 그 키를 지우면 된다 — 애매한 표기를 두지 않는다.
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="빈 목록"):
        MailConfig(enabled=True, host="h", sender="s", recipients=["a@b"],
                   recipients_by_concern={"operation": []})
