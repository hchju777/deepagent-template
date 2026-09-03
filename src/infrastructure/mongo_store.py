"""Mongo 영속 저장소 — Store·Repo·Ledger 3종 (스펙 §2.3, §3.4, §4.6-4, 계획 4b).

케이스 id, 케이스별 증거 id, 점검별 실행 seq는 전부 counters 컬렉션에서
`find_one_and_update($inc, upsert=True, return AFTER)`로 원자 증가시킨다 —
동시 호출에도 경쟁 없이 유일한 id/순서를 발급한다.

datetime 필드는 전부 pydantic `model_dump(mode="json")`으로 ISO 문자열로
저장하고 `model_validate`로 복원한다. BSON datetime을 그대로 쓰지 않는 이유:
mongomock·실제 MongoDB 둘 다 기본 설정에서는 저장한 datetime의 tzinfo를
왕복시키지 않는다(naive UTC로 돌아온다) — ISO 문자열 왕복이라야 tz-aware가
유지된다(브리프 계약). 범위 비교(purge_evidence_before·prune_runs_before·
prune_sends_before)는 같은 이유로 DB에 $lt를 맡기지 않고 문자열을 파싱해
Python에서 비교한다 —
ISO 문자열은 마이크로초 유무로 길이가 달라져 사전식 비교가 시간 순서와
어긋날 수 있기 때문이다.
"""
import re
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from src.domain.case import Verdict
from src.domain.cases import (CaseRecord, CaseRepositoryPort, OPEN_STATUSES,
                              lease_is_free)
from src.domain.events import EngineEvent, EventStorePort
from src.domain.patrol import CheckOutcome
from src.domain.snapshot import VerdictSnapshot, VerdictSnapshotPort
from src.domain.store import CaseStorePort, EvidenceRecord
from src.knowledge.digest import canonical_digest
from src.patrol.ledger import LedgerPort


def _next_seq(db: Database, key: str) -> int:
    """counters 컬렉션에서 key의 seq를 원자적으로 1 증가시키고 반환한다."""
    doc = db.counters.find_one_and_update(
        {"_id": key}, {"$inc": {"seq": 1}}, upsert=True,
        return_document=ReturnDocument.AFTER)
    return doc["seq"]


def ensure_indexes(db: Database) -> None:
    """운영에 필요한 인덱스를 멱등하게 만든다(계획 4b I9).

    cases.id/evidence(case_id,id)/verdicts.case_id/case_files.case_id는 각각
    한 문서만 있어야 하는 자리라 unique로 막는다 — upsert 경합이 중복 문서를
    만드는 사고를 인덱스 수준에서 방지한다. ledger_runs는 조회 패턴
    (find({gbm,fct,check}).sort(seq))과 prune_runs_before(전체 스캔 후 at 비교)를
    그대로 반영한 복합/단일 인덱스다. create_index는 이미 있으면 그대로 두므로
    여러 번 불러도 안전하다(멱등) — build_persistence가 mongo 경로마다 부른다.
    """
    db.cases.create_index("id", unique=True)
    # find_open_by_fingerprint는 게이트가 finding마다 부르므로 인덱스 없이는 매 점검이
    # cases 풀스캔이 된다. status를 앞에 둬서 열린 케이스 조회와 종결 케이스 이력 조회가
    # 같은 인덱스를 쓴다.
    db.cases.create_index([("status", 1), ("fingerprint", 1)])
    db.evidence.create_index([("case_id", 1), ("id", 1)], unique=True)
    db.verdicts.create_index("case_id", unique=True)
    db.case_files.create_index("case_id", unique=True)
    db.ledger_runs.create_index([("gbm", 1), ("fct", 1), ("check", 1), ("seq", 1)])
    db.ledger_runs.create_index("at")
    # sends.send_id unique: 발송 레저의 record_send가 사전 조회 없이 곧장 insert하고
    # DuplicateKeyError만 잡는다(리뷰 F4) — 이 인덱스가 중복 억제의 유일한 방어선이다.
    # (sent, seq) 복합 인덱스는 pending_sends의 filter({"sent": False})+sort("seq")를 그대로 받친다.
    db.sends.create_index("send_id", unique=True)
    db.sends.create_index([("sent", 1), ("seq", 1)])
    # (case_id, seq) unique: seq는 counters로 원자 증가하므로 중복이 나면 그 자체가
    # 카운터 손상 신호다 — 인덱스가 조용한 중복 대신 즉시 실패로 드러낸다.
    db.case_events.create_index([("case_id", 1), ("seq", 1)], unique=True)
    db.verdict_snapshots.create_index("case_id", unique=True)


class MongoCaseStore(CaseStorePort):
    """증거·코드 지식·판정을 담는 Mongo Store."""

    def __init__(self, db: Database):
        self._db = db

    def put_evidence(self, case_id, source, body, *,
                     as_of=None, complete=True, effective_as_of=None):
        seq = _next_seq(self._db, f"evidence:{case_id}")
        evidence_id = f"ev-{seq}"
        # EvidenceRecord를 거쳐 as_of/effective_as_of를 tz-aware 왕복 가능한
        # ISO 문자열로 만든다 — body_digest는 컬렉션 스키마상 "digest"로 저장.
        record = EvidenceRecord(id=evidence_id, source=source,
                                body_digest=canonical_digest(body),
                                as_of=as_of, complete=complete,
                                effective_as_of=effective_as_of)
        dumped = record.model_dump(mode="json")
        self._db.evidence.insert_one({
            "case_id": case_id,
            "id": evidence_id,
            "source": source,
            "body": body,
            "digest": dumped["body_digest"],
            "as_of": dumped["as_of"],
            "complete": dumped["complete"],
            "effective_as_of": dumped["effective_as_of"],
            "seq": seq,
        })
        return evidence_id

    def _find_evidence_doc(self, case_id, evidence_id):
        doc = self._db.evidence.find_one({"case_id": case_id, "id": evidence_id})
        if doc is None:
            raise KeyError(evidence_id)          # 없으면 KeyError(계약) — verify가 이 예외로 잡는다
        return doc

    @staticmethod
    def _to_record(doc) -> EvidenceRecord:
        return EvidenceRecord.model_validate({
            "id": doc["id"], "source": doc["source"], "body_digest": doc["digest"],
            "as_of": doc["as_of"], "complete": doc["complete"],
            "effective_as_of": doc["effective_as_of"],
        })

    def get_evidence(self, case_id, evidence_id):
        return self._find_evidence_doc(case_id, evidence_id)["body"]

    def get_evidence_record(self, case_id, evidence_id):
        return self._to_record(self._find_evidence_doc(case_id, evidence_id))

    def list_evidence(self, case_id):
        cursor = self._db.evidence.find({"case_id": case_id}).sort("seq", 1)
        return [self._to_record(doc) for doc in cursor]

    def has_evidence(self, case_id, evidence_id):
        return self._db.evidence.count_documents(
            {"case_id": case_id, "id": evidence_id}) > 0

    def put_code_knowledge(self, service, commit, spec):
        self._db.code_knowledge.update_one(
            {"service": service, "commit": commit},
            {"$set": {"spec": spec}}, upsert=True)

    def get_code_knowledge(self, service, commit):
        doc = self._db.code_knowledge.find_one({"service": service, "commit": commit})
        return doc["spec"] if doc else None

    def put_verdict(self, case_id, verdict):
        doc = {"case_id": case_id, **verdict.model_dump(mode="json")}
        self._db.verdicts.update_one({"case_id": case_id}, {"$set": doc}, upsert=True)

    def get_verdict(self, case_id):
        doc = self._db.verdicts.find_one({"case_id": case_id})
        if doc is None:
            return None
        # _id/case_id는 저장소 메타이지 Verdict 필드가 아니다 — StrictModel(extra=forbid)에
        # 넘기기 전에 걷어낸다.
        fields = {k: v for k, v in doc.items() if k not in ("_id", "case_id")}
        return Verdict.model_validate(fields)

    def put_case_file(self, case_id, snapshot):
        self._db.case_files.update_one(
            {"case_id": case_id}, {"$set": {"case_id": case_id, "snapshot": snapshot}},
            upsert=True)

    def get_case_file(self, case_id):
        doc = self._db.case_files.find_one({"case_id": case_id})
        return doc["snapshot"] if doc else None

    def purge_case(self, case_id):
        """케이스의 증거+판정+케이스 파일을 전부 삭제하고 삭제 건수를 반환한다."""
        deleted = self._db.evidence.delete_many({"case_id": case_id}).deleted_count
        deleted += self._db.verdicts.delete_many({"case_id": case_id}).deleted_count
        deleted += self._db.case_files.delete_many({"case_id": case_id}).deleted_count
        return deleted

    def purge_evidence_before(self, case_id, before):
        """as_of가 before 이전인 증거만 삭제한다(as_of가 None이면 유지)."""
        stale_ids = []
        for doc in self._db.evidence.find({"case_id": case_id}):
            as_of = doc.get("as_of")
            if as_of is None:
                continue
            if datetime.fromisoformat(as_of) < before:
                stale_ids.append(doc["id"])
        if stale_ids:
            self._db.evidence.delete_many({"case_id": case_id, "id": {"$in": stale_ids}})
        return len(stale_ids)

    def list_case_ids(self, prefix=""):
        """prefix로 시작하는 케이스 id를 정렬하여 반환한다(증거 또는 verdict가 있는 케이스)."""
        pattern = "^" + re.escape(prefix)
        ev_ids = self._db.evidence.distinct("case_id", {"case_id": {"$regex": pattern}})
        vd_ids = self._db.verdicts.distinct("case_id", {"case_id": {"$regex": pattern}})
        return sorted(set(ev_ids) | set(vd_ids))


class MongoCaseRepository(CaseRepositoryPort):
    """케이스 레코드를 담는 Mongo Repository."""

    def __init__(self, db: Database):
        self._db = db

    @staticmethod
    def _to_record(doc) -> CaseRecord:
        fields = {k: v for k, v in doc.items() if k != "_id"}
        return CaseRecord.model_validate(fields)

    def save(self, record: CaseRecord) -> None:
        doc = record.model_dump(mode="json")
        self._db.cases.update_one({"id": record.id}, {"$set": doc}, upsert=True)

    def get(self, case_id: str) -> CaseRecord:
        doc = self._db.cases.find_one({"id": case_id})
        if doc is None:
            raise KeyError(case_id)
        return self._to_record(doc)

    def claim(self, case_id, owner, *, now, ttl_s):
        doc = self._db.cases.find_one({"id": case_id})
        if doc is None:
            raise KeyError(case_id)
        record = self._to_record(doc)
        if not lease_is_free(record, owner, now):
            return None
        claimed = record.model_copy(update={
            "owner": owner, "lease_until": now + timedelta(seconds=ttl_s)})
        # 읽은 시점의 owner/lease_until을 그대로 술어로 걸어, 그 사이 남이 잡았으면
        # 진다(CAS). lease_until에 $lt 범위 비교를 쓰지 않는 이유는 ISO 문자열이라
        # 마이크로초 유무로 사전식 순서가 시간 순서와 어긋나기 때문이다(모듈 docstring).
        # $set을 lease 필드로 좁힌다 — 문서 전체를 덤프하면 읽기와 쓰기 사이에 남이
        # 바꾼 필드(게이트가 붙인 finding_ids 등)를 자기가 읽은 옛 값으로 되돌리는데,
        # CAS 술어는 owner/lease_until만 지켜서 그 소실을 감지하지 못한다.
        lease_fields = {k: claimed.model_dump(mode="json")[k]
                        for k in ("owner", "lease_until", "updated_at")}
        result = self._db.cases.update_one(
            {"id": case_id, "owner": doc.get("owner"), "lease_until": doc.get("lease_until")},
            {"$set": lease_fields})
        # matched_count로 판정한다 — modified_count는 같은 owner가 같은 now·ttl로
        # 재획득할 때(문서가 한 글자도 안 바뀜) 0이라, keepalive가 조용히 no-op되고
        # 인메모리 구현과 판정이 갈라진다. 우리가 물은 것은 "그 사이 남이 잡았나"이고
        # 그 답은 술어가 맞았는가(matched)이지 값이 달라졌는가(modified)가 아니다.
        if not result.matched_count:
            return None
        # 로컬에서 계산한 claimed는 lease 밖 필드가 낡았을 수 있다($set을 좁혔으므로).
        # 워커가 이 반환값을 들고 다니다 repo.save로 되쓰므로, 낡은 채로 돌려주면
        # 유실이 claim에서 워커로 자리만 옮긴다.
        return self._to_record(self._db.cases.find_one({"id": case_id}))

    def find_open_by_fingerprint(self, fp: str) -> CaseRecord | None:
        doc = self._db.cases.find_one(
            {"fingerprint": fp, "status": {"$in": list(OPEN_STATUSES)}})
        return self._to_record(doc) if doc else None

    def list_by_status(self, status) -> list[CaseRecord]:
        return [self._to_record(d) for d in self._db.cases.find({"status": status})]

    def list_open(self) -> list[CaseRecord]:
        return [self._to_record(d) for d in
                self._db.cases.find({"status": {"$in": list(OPEN_STATUSES)}})]

    def new_case_id(self) -> str:
        return f"c-{_next_seq(self._db, 'case_id')}"


class MongoLedger(LedgerPort):
    """점검 실행 이력과 하트비트를 담는 Mongo Ledger."""

    def __init__(self, db: Database):
        self._db = db

    def record_run(self, gbm, fct, check, outcome: CheckOutcome) -> None:
        seq = _next_seq(self._db, f"ledger:{gbm}:{fct}:{check}")
        dumped = outcome.model_dump(mode="json")
        self._db.ledger_runs.insert_one({
            "gbm": gbm, "fct": fct, "check": check, "seq": seq,
            "outcome": dumped, "at": dumped["observed_at"],
        })

    def _history(self, gbm, fct, check) -> list[CheckOutcome]:
        cursor = self._db.ledger_runs.find(
            {"gbm": gbm, "fct": fct, "check": check}).sort("seq", 1)
        return [CheckOutcome.model_validate(doc["outcome"]) for doc in cursor]

    def last_run(self, gbm, fct, check):
        history = self._history(gbm, fct, check)
        return history[-1] if history else None

    def consecutive_errors(self, gbm, fct, check) -> int:
        count = 0
        for outcome in reversed(self._history(gbm, fct, check)):
            if outcome.status == "skipped":       # skipped는 투명 — 스트릭을 끊지 않는다(4a 미너)
                continue
            if outcome.status != "error":
                break
            count += 1
        return count

    def runs(self, gbm, fct, check, limit=50) -> list[CheckOutcome]:
        if limit <= 0:                            # limit=0은 "0개" — -0 슬라이스 함정을 피한다
            return []
        history = self._history(gbm, fct, check)
        return list(reversed(history[-limit:]))

    def heartbeat(self, at) -> None:
        self._db.ledger_meta.update_one(
            {"_id": "heartbeat"}, {"$set": {"at": at.isoformat()}}, upsert=True)

    def last_heartbeat(self):
        doc = self._db.ledger_meta.find_one({"_id": "heartbeat"})
        return datetime.fromisoformat(doc["at"]) if doc else None

    def prune_runs_before(self, before) -> int:
        """before 이전에 기록된 실행 이력을 전부 삭제하고 삭제 건수를 반환한다."""
        stale_ids = [doc["_id"] for doc in self._db.ledger_runs.find({})
                    if datetime.fromisoformat(doc["at"]) < before]
        if not stale_ids:
            return 0
        return self._db.ledger_runs.delete_many({"_id": {"$in": stale_ids}}).deleted_count

    def record_send(self, send_id, *, kind, target, at) -> bool:
        # find_one 사전조회 없이 곧장 insert한다(리뷰 F4) — sends.send_id unique
        # 인덱스(ensure_indexes)가 유일한 방어선이다. find_one을 앞에 두면 왕복이
        # 3회로 늘고, 경합 상황에서는 어차피 DuplicateKeyError를 잡아야 해 이득이
        # 없다. 인덱스가 없는 경로(예: ensure_indexes를 안 부른 호출자)에서는 이
        # 계약이 성립하지 않는다 — 프로덕션 mongo 배선은 항상 ensure_indexes를
        # 먼저 부른다(build_persistence).
        seq = _next_seq(self._db, "sends")
        try:
            self._db.sends.insert_one({
                "send_id": send_id, "kind": kind, "target": target,
                "at": at.isoformat(), "sent": False, "seq": seq,
            })
        except DuplicateKeyError:
            return False
        return True

    def mark_sent(self, send_id, at) -> None:
        self._db.sends.update_one(
            {"send_id": send_id}, {"$set": {"sent": True, "sent_at": at.isoformat()}})

    def pending_sends(self, limit=50) -> list[dict]:
        if limit <= 0:                             # limit=0은 "0개"(runs와 동일 관례)
            return []
        cursor = self._db.sends.find({"sent": False}).sort("seq", 1).limit(limit)
        return [{"send_id": d["send_id"], "kind": d["kind"], "target": d["target"],
                "at": datetime.fromisoformat(d["at"])} for d in cursor]

    def prune_sends_before(self, before) -> int:
        """before 이전에 기록된 발송 이력(완료분 포함)을 전부 삭제하고 삭제 건수를 반환한다."""
        stale_ids = [doc["_id"] for doc in self._db.sends.find({})
                    if datetime.fromisoformat(doc["at"]) < before]
        if not stale_ids:
            return 0
        return self._db.sends.delete_many({"_id": {"$in": stale_ids}}).deleted_count


class MongoEventStore(EventStorePort):
    """이벤트 로그를 담는 Mongo 스토어 — 프로세스 밖 구독자의 읽기 지점."""

    def __init__(self, db: Database):
        self._db = db

    def append(self, event: EngineEvent) -> EngineEvent:
        seq = _next_seq(self._db, f"events:{event.case_id}")
        stamped = event.model_copy(update={"seq": seq})
        self._db.case_events.insert_one(stamped.model_dump(mode="json"))
        return stamped

    def since(self, case_id, after_seq=0, limit=200):
        if limit <= 0:
            return []
        cursor = (self._db.case_events
                  .find({"case_id": case_id, "seq": {"$gt": after_seq}})
                  .sort("seq", 1).limit(limit))
        return [EngineEvent.model_validate({k: v for k, v in doc.items() if k != "_id"})
                for doc in cursor]

    def prune_before(self, before):
        # at은 ISO 문자열이라 DB의 $lt로 거르면 마이크로초 유무로 순서가 어긋난다
        # (모듈 docstring). purge_evidence_before와 같은 방식으로 Python에서 판정한다.
        stale = [doc["_id"] for doc in self._db.case_events.find({}, {"_id": 1, "at": 1})
                 if datetime.fromisoformat(doc["at"]) < before]
        if stale:
            self._db.case_events.delete_many({"_id": {"$in": stale}})
        return len(stale)


class MongoVerdictSnapshotStore(VerdictSnapshotPort):
    """종결 판정 스냅샷 — purge_case가 건드리지 않는 별도 컬렉션이다."""

    def __init__(self, db: Database):
        self._db = db

    def put(self, snapshot: VerdictSnapshot) -> None:
        doc = snapshot.model_dump(mode="json")
        self._db.verdict_snapshots.update_one({"case_id": snapshot.case_id},
                                              {"$set": doc}, upsert=True)

    def get(self, case_id):
        doc = self._db.verdict_snapshots.find_one({"case_id": case_id})
        if doc is None:
            return None
        return VerdictSnapshot.model_validate({k: v for k, v in doc.items() if k != "_id"})

    def prune_before(self, before):
        # closed_at은 ISO 문자열이라 DB $lt로 거르면 마이크로초 유무로 순서가
        # 어긋난다(모듈 docstring) — Python에서 판정한다.
        stale = [doc["_id"] for doc in
                 self._db.verdict_snapshots.find({}, {"_id": 1, "closed_at": 1})
                 if datetime.fromisoformat(doc["closed_at"]) < before]
        if stale:
            self._db.verdict_snapshots.delete_many({"_id": {"$in": stale}})
        return len(stale)
