from datetime import datetime, timezone

import mongomock
import pytest

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.domain.patrol import CheckOutcome
from src.infrastructure.mongo_store import (MongoCaseRepository, MongoCaseStore, MongoLedger,
                                            ensure_indexes)

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
    store.put_case_file("c-1", {"round": 3, "plan_tasks": []})
    assert store.get_case_file("c-1") == {"round": 3, "plan_tasks": []}
    assert store.get_case_file("c-9") is None
    assert store.purge_case("c-1") == 4 and store.list_evidence("c-1") == []
    assert store.get_case_file("c-1") is None


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


def test_ledger_발송_레저_계약(db):
    ensure_indexes(db)          # record_send의 중복 억제는 sends.send_id unique 인덱스가 방어선(F4)
    ledger = MongoLedger(db)
    assert ledger.record_send("report:c-1", kind="report", target="a@x", at=T) is True
    assert ledger.record_send("report:c-1", kind="report", target="a@x", at=T) is False
    assert ledger.record_send("report:c-2", kind="report", target="b@y", at=T) is True
    pending = ledger.pending_sends()
    assert [p["send_id"] for p in pending] == ["report:c-1", "report:c-2"]
    assert pending[0] == {"send_id": "report:c-1", "kind": "report", "target": "a@x", "at": T}
    ledger.mark_sent("report:c-1", T)
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-2"]
    assert ledger.prune_sends_before(T.replace(year=2027)) == 2
    assert ledger.pending_sends() == []


def test_ensure_indexes는_unique_인덱스를_만든다(db):
    ensure_indexes(db)
    cases_idx = db.cases.index_information()
    assert any(spec["key"] == [("id", 1)] and spec.get("unique") for spec in cases_idx.values())
    evidence_idx = db.evidence.index_information()
    assert any(spec["key"] == [("case_id", 1), ("id", 1)] and spec.get("unique")
              for spec in evidence_idx.values())
    verdicts_idx = db.verdicts.index_information()
    assert any(spec["key"] == [("case_id", 1)] and spec.get("unique") for spec in verdicts_idx.values())
    case_files_idx = db.case_files.index_information()
    assert any(spec["key"] == [("case_id", 1)] and spec.get("unique")
              for spec in case_files_idx.values())
    ledger_idx = db.ledger_runs.index_information()
    assert any(spec["key"] == [("gbm", 1), ("fct", 1), ("check", 1), ("seq", 1)]
              for spec in ledger_idx.values())
    assert any(spec["key"] == [("at", 1)] for spec in ledger_idx.values())
    sends_idx = db.sends.index_information()
    assert any(spec["key"] == [("send_id", 1)] and spec.get("unique") for spec in sends_idx.values())


def test_ensure_indexes는_지문_조회_인덱스를_만든다(db):
    # find_open_by_fingerprint는 게이트가 finding마다 부르는데 인덱스가 없어
    # cases 컬렉션 풀스캔이었다. 이력 검색(종결 케이스 지문 조회)이 같은 키를
    # 쓰므로 status를 앞에 둔 복합 인덱스 하나로 둘을 같이 받친다.
    ensure_indexes(db)
    cases_idx = db.cases.index_information()
    assert any(spec["key"] == [("status", 1), ("fingerprint", 1)]
              for spec in cases_idx.values())


def test_mongo_이벤트_스토어는_seq_순서로_돌려준다(db):
    from src.domain.events import EngineEvent
    from src.infrastructure.mongo_store import MongoEventStore
    events = MongoEventStore(db)
    a = events.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    b = events.append(EngineEvent(event="task_finished", case_id="c-1", at=T))
    events.append(EngineEvent(event="round_started", case_id="c-2", at=T))
    assert (a.seq, b.seq) == (1, 2)
    assert [e.event for e in events.since("c-1")] == ["round_started", "task_finished"]
    assert [e.seq for e in events.since("c-1", after_seq=1)] == [2]


def test_mongo_이벤트_보존은_마이크로초_길이에_속지_않는다(db):
    # at은 ISO 문자열로 저장된다. 마이크로초가 있는 값과 없는 값은 길이가 달라
    # 사전식 비교가 시간 순서와 어긋난다 — DB $lt가 아니라 Python 파싱으로 걸러야 한다.
    from datetime import timedelta

    from src.domain.events import EngineEvent
    from src.infrastructure.mongo_store import MongoEventStore
    events = MongoEventStore(db)
    old = T - timedelta(days=40)
    events.append(EngineEvent(event="round_started", case_id="c-1", at=old))
    events.append(EngineEvent(event="round_started", case_id="c-1",
                              at=old.replace(microsecond=123456)))
    events.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    assert events.prune_before(T - timedelta(days=1)) == 2
    assert [e.seq for e in events.since("c-1")] == [3]


def test_mongo_claim은_그_사이_남이_잡았으면_진다(db):
    repo = MongoCaseRepository(db)
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None
    assert repo.claim("c-1", "w-2", now=T, ttl_s=60) is None
    assert repo.get("c-1").owner == "w-1"


def test_mongo_claim은_ISO_길이_차이에_속지_않는다(db):
    # lease_until을 DB $lt로 비교하면 마이크로초 유무로 길이가 달라 사전식 순서가
    # 시간 순서와 어긋난다 — 만료 판정이 뒤집혀 살아있는 lease를 뺏는다.
    from datetime import timedelta
    repo = MongoCaseRepository(db)
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    holder = T.replace(microsecond=500000)
    assert repo.claim("c-1", "w-1", now=holder, ttl_s=3600) is not None
    assert repo.claim("c-1", "w-2", now=holder + timedelta(seconds=1), ttl_s=60) is None
