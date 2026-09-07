from datetime import datetime, timedelta, timezone

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
    e1 = store.put_evidence("c-1", "rest:/oee", {"oee": 512}, as_of=T, complete=False,
                            truncated_reason="500행 중 50개만 사용")
    e2 = store.put_evidence("c-1", "mongo:x", [1, 2])
    assert (e1, e2) == ("ev-1", "ev-2") and store.put_evidence("c-2", "s", None) == "ev-1"
    rec = store.get_evidence_record("c-1", "ev-1")
    assert rec.complete is False and rec.as_of == T and store.get_evidence("c-1", "ev-1") == {"oee": 512}
    # 왕복에서 이유가 사라지면 보고서 §4가 "⚠ 불완전"까지만 말한다.
    assert rec.truncated_reason == "500행 중 50개만 사용"
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


def test_mongo_스냅샷은_케이스당_하나다(db):
    from src.domain.snapshot import VerdictSnapshot
    from src.infrastructure.mongo_store import MongoVerdictSnapshotStore
    store = MongoVerdictSnapshotStore(db)
    base = dict(case_id="c-1", closed_at=T, gbm="mx", fct="gumi", fingerprint="fp",
                origin="patrol", outcome="closed", verdict_type="data_loss",
                root_cause_component="plan-sync", confidence="high", rounds=2,
                evidence_count=3, task_error_rate="0/2", verify_demoted=False,
                knowledge_digests={"topology": "d1"})
    store.put(VerdictSnapshot(**base))
    store.put(VerdictSnapshot(**{**base, "confidence": "low"}))
    assert store.get("c-1").confidence == "low"
    assert db.verdict_snapshots.count_documents({"case_id": "c-1"}) == 1
    assert store.get("없는-케이스") is None


def test_mongo_claim은_같은_owner의_무변화_재획득도_성공이다(db):
    # 테스트는 전부 인메모리라 두 구현이 갈라지면 프로덕션 버그를 못 잡는다.
    # modified_count로 판정하면 같은 owner가 같은 now·ttl로 재획득할 때 문서가
    # 한 글자도 안 바뀌어 실패로 보인다 — keepalive가 조용히 no-op된다.
    repo = MongoCaseRepository(db)
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None   # 무변화 재획득


def test_mongo_claim은_읽기와_쓰기_사이의_갱신을_되돌리지_않는다(db, monkeypatch):
    # CAS 술어는 owner/lease_until만 지킨다. $set이 문서 전체 덤프면, 그 사이 남이
    # 바꾼 다른 필드(게이트가 붙인 finding_ids)를 자기가 읽은 옛 값으로 되돌리는데
    # 술어는 그걸 감지하지 못한다 — 첨부된 finding이 조용히 사라진다.
    repo = MongoCaseRepository(db)
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    assert repo.claim("c-1", "w-1", now=T, ttl_s=600) is not None

    # claim이 문서를 읽은 직후, update 전에 게이트가 finding을 첨부한다.
    real_find_one = db.cases.find_one
    injected = []

    def find_one_then_attach(*a, **kw):
        doc = real_find_one(*a, **kw)
        if not injected:
            injected.append(True)
            db.cases.update_one({"id": "c-1"}, {"$set": {"finding_ids": ["f-1"]}})
        return doc

    monkeypatch.setattr(db.cases, "find_one", find_one_then_attach)
    assert repo.claim("c-1", "w-1", now=T, ttl_s=600) is not None
    monkeypatch.undo()
    assert repo.get("c-1").finding_ids == ["f-1"]


def test_mongo_claim이_돌려주는_레코드는_DB의_최신값이다(db, monkeypatch):
    # $set을 lease 필드로 좁히면 로컬에서 계산한 claimed는 다른 필드가 낡는다.
    # 워커는 그 반환값을 그대로 들고 다니다 repo.save로 되쓰므로, 낡은 채로
    # 돌려주면 유실이 claim에서 워커로 자리만 옮긴다.
    repo = MongoCaseRepository(db)
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    real_find_one = db.cases.find_one
    injected = []

    def find_one_then_attach(*a, **kw):
        doc = real_find_one(*a, **kw)
        if not injected:
            injected.append(True)
            db.cases.update_one({"id": "c-1"}, {"$set": {"finding_ids": ["f-1"]}})
        return doc

    monkeypatch.setattr(db.cases, "find_one", find_one_then_attach)
    claimed = repo.claim("c-1", "w-1", now=T, ttl_s=600)
    monkeypatch.undo()
    assert claimed.finding_ids == ["f-1"]      # 반환값이 DB와 일치한다
    assert claimed.owner == "w-1"


# ── 명령 채널 프리미티브 (계획 13) — 인메모리와 같은 어휘·같은 판정 ─────────────
def _parked_doc(repo, cid="c-1", **kw):
    base = dict(id=cid, gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                created_at=T, updated_at=T, status="awaiting_human", question="q",
                question_kind="investigation", question_seq=1)
    base.update(kw)
    repo.save(CaseRecord(**base))


def test_attach는_필드_셋만_바꾸고_CAS로_진다(db):
    repo = MongoCaseRepository(db)
    # owner가 남아 있어도 lease가 만료됐으면 잡은 게 아니다 — 살아 있으면 busy(아래 테스트).
    _parked_doc(repo, owner="w-1", lease_until=T - timedelta(seconds=1), thread_ids=["t-1"])
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "accepted"
    rec = repo.get("c-1")
    assert rec.pending_answer == "답" and rec.answer_key == "k-1"
    assert rec.owner == "w-1" and rec.thread_ids == ["t-1"]      # 남의 필드는 그대로
    # 같은 키·다른 답 → duplicate / 소비 전 다른 키 → pending
    assert repo.attach_answer("c-1", answer="x", key="k-1", now=T) == "duplicate"
    assert repo.attach_answer("c-1", answer="x", key="k-2", now=T) == "pending"


def test_attach는_워커가_claim한_뒤에는_진다(db):
    # 리뷰 S7 — api가 읽은 뒤 워커가 investigating으로 옮기면 착지하면 안 된다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    db.cases.update_one({"id": "c-1"}, {"$set": {"status": "investigating", "owner": "w-1"}})
    assert repo.attach_answer("c-1", answer="답", key="k", now=T) == "not_waiting"
    assert db.cases.find_one({"id": "c-1"})["status"] == "investigating"


def test_take_restore_왕복(db):
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    repo.attach_answer("c-1", answer="답", key="k-1", now=T)
    assert repo.take_answer("c-1", now=T) == "답"
    rec = repo.get("c-1")
    assert rec.pending_answer is None and rec.answered_seq == 1
    assert repo.take_answer("c-1", now=T) is None                    # 두 번 못 가져간다
    assert repo.attach_answer("c-1", answer="둘째", key="k-2", now=T) == "not_waiting"  # 답한 질문
    assert repo.restore_answer("c-1", answer="답", now=T) is True
    rec = repo.get("c-1")
    assert rec.pending_answer == "답" and rec.answered_seq == 0
    # 새 파킹 뒤에는 되돌리지 않는다
    repo.take_answer("c-1", now=T)
    db.cases.update_one({"id": "c-1"}, {"$set": {"question_seq": 2}})
    assert repo.restore_answer("c-1", answer="답", now=T) is False


def test_없는_케이스와_옛_문서(db):
    repo = MongoCaseRepository(db)
    assert repo.attach_answer("없음", answer="x", key="k", now=T) == "not_found"
    # 계획 13 이전 문서에는 seq 필드가 없다 — 기본값(0/0)으로 읽혀 "답할 질문 없음"
    _parked_doc(repo)
    db.cases.update_one({"id": "c-1"}, {"$unset": {"question_seq": "", "answered_seq": ""}})
    assert repo.get("c-1").question_seq == 0
    assert repo.attach_answer("c-1", answer="x", key="k", now=T) == "not_waiting"


def test_seq_필드가_없는_문서에도_attach가_끝난다(db):
    # 리뷰: 가드가 pydantic 기본값(answered_seq=0)을 술어로 걸었다 — 부재 필드는
    # {"answered_seq": 0}과 안 맞아 CAS가 영원히 지고 재분류가 재귀해 RecursionError.
    # 인계 #8이 권하는 마이그레이션(`$set question_seq:1`)이 정확히 이 문서를 만든다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    db.cases.update_one({"id": "c-1"}, {"$unset": {"answered_seq": ""}})
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "accepted"
    assert repo.get("c-1").pending_answer == "답"


def _interleave(db, cid, *, before_cas: dict):
    """읽기와 CAS **사이**에 남의 쓰기를 끼워 넣는다 — 사전검사는 통과하고 CAS만 진다."""
    real = db.cases.update_one
    state = {"armed": True}

    def hijacked(filter, update, *a, **kw):
        if state["armed"] and "$set" in update and "pending_answer" in update["$set"]:
            state["armed"] = False
            real({"id": cid}, {"$set": before_cas})
        return real(filter, update, *a, **kw)

    db.cases.update_one = hijacked


def test_attach의_CAS는_읽기_뒤의_claim에_진다(db):
    # 리뷰 X1: 가드를 통째로 지워도 전부 초록이었다 — 사전검사가 막는 경우만 있었고
    # CAS 자체가 지는 경로는 한 번도 안 지났다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    _interleave(db, "c-1", before_cas={"status": "investigating", "owner": "w-1"})
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "not_waiting"
    doc = db.cases.find_one({"id": "c-1"})
    assert doc["status"] == "investigating" and doc.get("pending_answer") is None


def test_take의_CAS는_읽기_뒤의_다른_take에_진다(db):
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    repo.attach_answer("c-1", answer="답", key="k-1", now=T)
    _interleave(db, "c-1", before_cas={"pending_answer": None, "answered_seq": 1})
    assert repo.take_answer("c-1", now=T) is None


def test_restore의_CAS는_읽기_뒤의_새_파킹에_진다(db):
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    repo.attach_answer("c-1", answer="답", key="k-1", now=T)
    repo.take_answer("c-1", now=T)
    _interleave(db, "c-1", before_cas={"question_seq": 2, "question": "새"})
    assert repo.restore_answer("c-1", answer="답", now=T) is False
    assert db.cases.find_one({"id": "c-1"}).get("pending_answer") is None


def test_restore도_없는_케이스는_KeyError다(db):
    # take는 KeyError, restore는 False였다 — 소비 중 케이스가 사라지면 두 구현의
    # 결과(failed vs skipped+없는 케이스에 증거)가 갈렸다.
    repo = MongoCaseRepository(db)
    with pytest.raises(KeyError):
        repo.restore_answer("없음", answer="x", now=T)


# ---- 검증 리뷰 2차: lease가 살아 있는 동안 attach는 문을 안 연다 ---------------------------
def _hold_lease(db, cid="c-1", *, until):
    db.cases.update_one({"id": cid}, {"$set": {"owner": "w-1", "lease_until": until.isoformat()}})


def test_lease가_살아_있으면_attach는_busy다(db):
    # 실행자가 잡고 있는 동안 실린 답은 resume_once의 통째 덤프에 지워지거나(M1-b)
    # 파킹을 넘어 살아남아 다음 질문에 소비된다(M1-a). 만료된 lease는 잡은 게 아니다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    _hold_lease(db, until=T + timedelta(seconds=60))
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "busy"
    _hold_lease(db, until=T - timedelta(seconds=1))
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "accepted"


def test_attach의_CAS는_읽기_뒤의_claim만으로도_진다(db):
    # claim은 상태를 안 바꾼다(owner/lease_until만) — status만 걸면 이 경우를 못 본다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    _interleave(db, "c-1", before_cas={"owner": "w-1",
                                       "lease_until": (T + timedelta(seconds=60)).isoformat()})
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "busy"
    assert db.cases.find_one({"id": "c-1"}).get("pending_answer") is None


def test_attach의_재분류는_두_바퀴에서_멈춘다(db):
    # 리뷰 M2(b): 재귀를 루프로 바꿨다는 주장에 테스트가 없었다 — 매번 지게 만들면
    # 재귀는 RecursionError, range(3)은 세 번 쓴다. 두 번 진 것은 남이 계속 바꾸는
    # 중이라는 뜻이니 not_waiting(재시도 말라)이 아니라 busy(잠시 뒤 다시)다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    real, calls = db.cases.update_one, []

    def always_lose(filter, update, *a, **kw):
        if "$set" in update and "pending_answer" in update["$set"]:
            calls.append(1)
            real({"id": "c-1"}, {"$set": {"answer_key": f"k-other-{len(calls)}"}})
        return real(filter, update, *a, **kw)

    db.cases.update_one = always_lose
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "busy"
    assert len(calls) == 2


def test_attach는_그_사이_질문이_바뀌면_둘째_바퀴에서_싣지_않는다(db):
    # 리뷰 L2: 첫 CAS가 Q1→Q2 파킹 때문에 졌는데 둘째 바퀴가 같은 답을 Q2에 실었다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    _interleave(db, "c-1", before_cas={"question_seq": 2, "question": "Q2"})
    assert repo.attach_answer("c-1", answer="Q1을 보고 쓴 답", key="k-1", now=T) == "not_waiting"
    assert db.cases.find_one({"id": "c-1"}).get("pending_answer") is None


@pytest.mark.parametrize("write, expected", [
    # 각 쓰기는 술어 필드 **하나만** 바꾼다 — 짝 필드를 같이 바꾸면 나머지가 대신 잡아
    # 그 필드를 술어에서 빼도 초록이다(리뷰 L-a). status는 중복이 아니다: sweep_timeouts→
    # close_case는 lease 없이 상태만 바꾸므로, 빠지면 닫힌 케이스에 답이 착지한다.
    ({"status": "closed"}, "not_waiting"),
    ({"question_kind": "intake"}, "not_waiting"),
    ({"pending_answer": "남의 답"}, "pending"),
    ({"answered_seq": 1}, "not_waiting"),
    ({"owner": "w-9"}, "busy"),
])
def test_attach의_CAS는_술어_필드_하나만_바뀌어도_진다(db, write, expected):
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    _interleave(db, "c-1", before_cas=write)
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == expected


@pytest.mark.parametrize("write", [{"answer_key": "k-2"}, {"question_seq": 2, "question": "Q2"}])
def test_take의_CAS는_key나_질문이_바뀌면_진다(db, write):
    # 리뷰 M2: take 술어의 answer_key·question_seq(ABA)에 테스트가 없었다 — 같은 글자의
    # 다른 답, 또는 새 질문에 실린 답을 옛 답으로 가져가면 answered_seq가 틀어진다.
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    repo.attach_answer("c-1", answer="답", key="k-1", now=T)
    _interleave(db, "c-1", before_cas=write)
    assert repo.take_answer("c-1", now=T) is None
    assert db.cases.find_one({"id": "c-1"})["pending_answer"] == "답"     # 남의 것은 그대로


@pytest.mark.parametrize("write", [{"status": "closed"}, {"pending_answer": "x"}])
def test_restore의_CAS는_상태나_pending이_바뀌면_진다(db, write):
    repo = MongoCaseRepository(db)
    _parked_doc(repo)
    repo.attach_answer("c-1", answer="답", key="k-1", now=T)
    repo.take_answer("c-1", now=T)
    _interleave(db, "c-1", before_cas=write)
    assert repo.restore_answer("c-1", answer="답", now=T) is False


# ---- 계획 15(P8): 메트릭 sink -----------------------------------------------------------
def test_메트릭은_이름별_최신순으로_읽히고_오래된_것만_걷힌다(db):
    ledger = MongoLedger(db)
    ledger.record_metric("investigation.duration_s", 3.5,
                         tags={"gbm": "mx", "outcome": "closed"}, at=T - timedelta(days=40))
    ledger.record_metric("investigation.duration_s", 9.0, tags={"gbm": "mx"}, at=T)
    ledger.record_metric("other", 1.0, tags={}, at=T)
    rows = ledger.metrics("investigation.duration_s")
    assert [r["value"] for r in rows] == [9.0, 3.5]
    assert rows[1]["tags"] == {"gbm": "mx", "outcome": "closed"} and rows[0]["at"] == T
    assert ledger.metrics("investigation.duration_s", limit=1)[0]["value"] == 9.0
    assert ledger.prune_metrics_before(T - timedelta(days=30)) == 1
    assert [r["value"] for r in ledger.metrics("investigation.duration_s")] == [9.0]
    assert ledger.metrics("other") != []            # 이름이 다른 것은 안 걷힌다


# ---- 계획 15(P8): 이력 조회 표면(인메모리와 같은 계약) -----------------------------------
def test_Mongo도_지문과_locator로_종결_케이스를_최신순으로_찾는다(db):
    repo = MongoCaseRepository(db)
    def _closed(cid, *, fp, locator, at):
        repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint=fp, symptom="s", t0=at,
                             created_at=at, updated_at=at, status_since=at, status="closed",
                             target_locator=locator, closed_reason="조사 완료"))
    _closed("c-1", fp="fp-a", locator="rest:/oee", at=T - timedelta(days=2))
    _closed("c-2", fp="fp-a", locator="rest:/oee", at=T)
    repo.save(CaseRecord(id="c-3", gbm="mx", fct="gumi", fingerprint="fp-a", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="open", target_locator="rest:/oee"))
    assert [r.id for r in repo.closed_by_fingerprint("fp-a", exclude_case_id="c-9")] == ["c-2", "c-1"]
    assert [r.id for r in repo.closed_by_locators(["rest:/oee"], exclude_case_id="c-2")] == ["c-1"]
    assert repo.closed_by_locators([], exclude_case_id="c-9") == []
    from src.infrastructure.mongo_store import ensure_indexes
    ensure_indexes(db)          # tier 2~4가 풀스캔이 되지 않게(계획 15)
    assert "status_1_target_locator_1" in db.cases.index_information()
