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
