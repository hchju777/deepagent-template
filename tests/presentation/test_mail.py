from datetime import datetime, timezone

from src.config.schema_app import MailConfig
from src.patrol.ledger import InMemoryLedger
from src.presentation.mail import NullSender, retry_pending, send_report

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CFG = MailConfig(enabled=True, host="smtp", sender="a@x", recipients=["b@y"])


class RecordingSender(NullSender):
    def __init__(self, fail_times=0):
        self.sent, self._fail = [], fail_times

    async def send(self, subject, body, *, recipients):
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("SMTP 거부")
        self.sent.append((subject, recipients))


async def test_기록_먼저_발송_그다음_sent():
    ledger, sender = InMemoryLedger(), RecordingSender()
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T) == "sent"
    assert sender.sent == [("제목", ["b@y"])] and ledger.pending_sends() == []


async def test_발송_실패는_pending으로_남고_재시도가_비운다():
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=1)
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T) == "failed"
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-1"]
    done = await retry_pending(sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T,
                               render=lambda rec: ("제목", "본문"))
    assert done == 1 and ledger.pending_sends() == []


async def test_중복_발송은_억제되고_비활성은_건너뛴다():
    ledger, sender = InMemoryLedger(), RecordingSender()
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T)
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T) == "duplicate"
    assert len(sender.sent) == 1
    off = MailConfig()
    assert await send_report("c-2", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=off, clock=lambda: T) == "skipped"


async def test_mark_sent_실패는_재발송을_부르지_않는다():
    class FlakyLedger(InMemoryLedger):
        def mark_sent(self, send_id, at):
            raise RuntimeError("레저 블립")
    ledger, sender = FlakyLedger(), RecordingSender()
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T) == "sent"
    assert len(sender.sent) == 1                      # 발송은 됐고 raise도 없다


async def test_render_실패는_남은_pending을_막지_않는다():
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=2)
    for cid in ("c-1", "c-2"):
        await send_report(cid, "제목", "본문", sender=sender, ledger=ledger,
                          cfg=CFG, clock=lambda: T)
    def render(rec):
        if rec["send_id"] == "report:c-1":
            raise ValueError("망가진 레코드")
        return ("제목", "본문")
    done = await retry_pending(sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T,
                               render=render)
    assert done == 1                                   # c-2는 보내졌다
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-1"]


async def test_재발송은_기록된_수신자에게_간다():
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=1)
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                      cfg=CFG, clock=lambda: T)
    changed = MailConfig(enabled=True, host="smtp", sender="a@x", recipients=["새사람@z"])
    await retry_pending(sender=sender, ledger=ledger, cfg=changed, clock=lambda: T,
                        render=lambda rec: ("제목", "본문"))
    assert sender.sent[-1][1] == ["b@y"]                # 기록된 target 우선


async def test_비활성_상태의_스윕은_건드리지_않고_0을_돌려준다():
    # M2: retry_pending이 cfg.enabled를 안 보면, 메일을 끈 뒤에도 스윕이
    # NullSender로 "보내고" mark_sent를 찍어 "보낸 적 없는 발송"을 레저에 남긴다.
    ledger = InMemoryLedger()
    ledger.record_send("report:c-1", kind="report", target="b@y", at=T)   # 켜져 있을 때 남긴 pending
    off = MailConfig()                                        # enabled=False(기본값)
    sender = RecordingSender()
    done = await retry_pending(sender=sender, ledger=ledger, cfg=off, clock=lambda: T,
                               render=lambda rec: ("제목", "본문"))
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
