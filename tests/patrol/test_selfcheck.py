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
