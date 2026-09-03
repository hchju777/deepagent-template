from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import RetentionConfig
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.patrol import CheckOutcome
from src.domain.store import InMemoryCaseStore
from src.infrastructure.retention import sweep_retention
from src.patrol.ledger import InMemoryLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


async def test_스윕은_오래된_종결_케이스와_레저와_스크래치를_정리한다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    old, fresh = T - timedelta(days=100), T - timedelta(days=1)
    for cid, at in (("c-old", old), ("c-fresh", fresh)):
        repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint=cid, symptom="s", t0=T,
                             created_at=at, updated_at=at, status="closed", thread_ids=[f"{cid}#1"]))
        store.put_evidence(cid, "s", {"x": 1}, as_of=at)
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=old))
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=fresh))
    store.put_evidence("patrol:mx:gumi:c", "s", {"p": 1}, as_of=old)
    store.put_evidence("patrol:mx:gumi:c", "s", {"p": 2}, as_of=fresh)
    counts = await sweep_retention(repo=repo, store=store, ledger=ledger, checkpointer=InMemorySaver(),
                                   clock=lambda: T, retention=RetentionConfig())
    assert counts["closed_cases"] == 1 and store.list_evidence("c-old") == [] and store.list_evidence("c-fresh")
    assert counts["ledger_runs"] == 1 and len(ledger.runs("mx", "gumi", "c")) == 1
    assert counts["scratch_evidence"] == 1 and len(store.list_evidence("patrol:mx:gumi:c")) == 1
