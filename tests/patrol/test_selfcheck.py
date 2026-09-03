from datetime import datetime, timezone

from src.domain.patrol import CheckOutcome
from src.domain.store import InMemoryCaseStore
from src.patrol.ledger import InMemoryLedger
from src.patrol.selfcheck import scan_self_check

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_연속_error가_임계를_넘으면_자기감시_finding():
    ledger, store = InMemoryLedger(), InMemoryCaseStore()
    for _ in range(3):
        ledger.record_run("mx", "gumi", "api.oee", CheckOutcome(status="error", observed_at=T, error="timeout"))
    ledger.record_run("mx", "gumi", "kafka.lag", CheckOutcome(status="ok", observed_at=T))
    findings = scan_self_check(ledger=ledger, checks=[("mx", "gumi", "api.oee"), ("mx", "gumi", "kafka.lag")],
                               threshold=3, clock=lambda: T, store=store)
    assert len(findings) == 1 and findings[0].check == "self.api.oee"
    assert store.has_evidence(findings[0].scratch_case_id, findings[0].evidence_ids[0])


def test_레저_포트는_점검과_발송으로_나뉜다():
    # 다음 계획이 MetricsSinkPort를 더한다. 그때 ABC가 하나면 추상 메서드가
    # 11개에서 15개가 되고 소비자 3종이 한 포트에 묶인다.
    from src.patrol.ledger import CheckLedgerPort, InMemoryLedger, SendLedgerPort
    check_methods = set(CheckLedgerPort.__abstractmethods__)
    send_methods = set(SendLedgerPort.__abstractmethods__)
    assert check_methods == {"record_run", "last_run", "consecutive_errors", "runs",
                             "prune_runs_before", "heartbeat", "last_heartbeat"}
    assert send_methods == {"record_send", "mark_sent", "pending_sends", "prune_sends_before"}
    assert not check_methods & send_methods
    # 구현은 아직 쪼개지 않는다 — build_persistence의 반환 형태를 유지한다.
    ledger = InMemoryLedger()
    assert isinstance(ledger, CheckLedgerPort) and isinstance(ledger, SendLedgerPort)
