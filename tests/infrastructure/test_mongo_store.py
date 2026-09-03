from datetime import datetime, timezone

import mongomock
import pytest

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.domain.patrol import CheckOutcome
from src.infrastructure.mongo_store import MongoCaseRepository, MongoCaseStore, MongoLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    return mongomock.MongoClient()["deepagent_test"]


def test_store_계약(db):
    store = MongoCaseStore(db)
    e1 = store.put_evidence("c-1", "rest:/oee", {"oee": 512}, as_of=T, complete=False)
    e2 = store.put_evidence("c-1", "mongo:x", [1, 2])
    assert (e1, e2) == ("ev-1", "ev-2") and store.put_evidence("c-2", "s", None) == "ev-1"
    rec = store.get_evidence_record("c-1", "ev-1")
    assert rec.complete is False and rec.as_of == T and store.get_evidence("c-1", "ev-1") == {"oee": 512}
    assert [r.id for r in store.list_evidence("c-1")] == ["ev-1", "ev-2"]
    assert store.has_evidence("c-1", "ev-2") and not store.has_evidence("c-1", "ev-9")
    with pytest.raises(KeyError):
        store.get_evidence("c-1", "ev-9")
    store.put_code_knowledge("svc", "abc", "spec")
    assert store.get_code_knowledge("svc", "abc") == "spec" and store.get_code_knowledge("svc", "zzz") is None
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]))
    store.put_verdict("c-1", v)
    assert store.get_verdict("c-1").root_cause.component == "plan-sync"
    assert store.list_case_ids("c-") == ["c-1", "c-2"]
    assert store.purge_case("c-1") == 3 and store.list_evidence("c-1") == []


def test_repo_계약(db):
    repo = MongoCaseRepository(db)
    assert repo.new_case_id() == "c-1" and repo.new_case_id() == "c-2"
    r = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                   created_at=T, updated_at=T)
    repo.save(r)
    assert repo.get("c-1").symptom == "s" and repo.find_open_by_fingerprint("fp").id == "c-1"
    repo.save(r.model_copy(update={"status": "closed"}))
    assert repo.find_open_by_fingerprint("fp") is None and repo.list_open() == []
    assert [x.id for x in repo.list_by_status("closed")] == ["c-1"]
    with pytest.raises(KeyError):
        repo.get("c-9")


def test_ledger_계약(db):
    ledger = MongoLedger(db)
    ok = CheckOutcome(status="ok", observed_at=T)
    err = CheckOutcome(status="error", observed_at=T, error="x")
    for o in (ok, err, err):
        ledger.record_run("mx", "gumi", "c", o)
    assert ledger.last_run("mx", "gumi", "c").status == "error"
    assert ledger.consecutive_errors("mx", "gumi", "c") == 2
    assert len(ledger.runs("mx", "gumi", "c", limit=2)) == 2
    ledger.heartbeat(T)
    assert ledger.last_heartbeat() == T
    assert ledger.prune_runs_before(T.replace(year=2027)) == 3
