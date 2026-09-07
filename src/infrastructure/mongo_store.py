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

from pydantic_core import to_jsonable_python
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from src.domain.case import Verdict
from src.domain.cases import (CaseRecord, CaseRepositoryPort, OPEN_STATUSES,
                              lease_is_free, lease_is_held)
from src.domain.events import EngineEvent, EventStorePort
from src.domain.patrol import CheckOutcome
from src.domain.label import LabelStorePort, RootCauseLabel
from src.domain.rollup import DigestStorePort, FleetReport
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
    db.cases.create_index([("status", 1), ("target_locator", 1)])   # 이력 tier 2~4(계획 15)
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
    db.metrics.create_index([("name", 1), ("at", -1)])
    db.metrics.create_index("at")
    db.verdict_snapshots.create_index("case_id", unique=True)
    db.labels.create_index([("case_id", 1), ("labeled_at", 1)])     # append-only(계획 15)
    db.fleet_runs.create_index([("scenario", 1), ("generated_at", -1)])   # 집계 실행(계획 16)


class MongoCaseStore(CaseStorePort):
    """증거·코드 지식·판정을 담는 Mongo Store."""

    def __init__(self, db: Database):
        self._db = db

    def put_evidence(self, case_id, source, body, *,
                     as_of=None, complete=True, truncated_reason=None,
                     effective_as_of=None):
        seq = _next_seq(self._db, f"evidence:{case_id}")
        evidence_id = f"ev-{seq}"
        # EvidenceRecord를 거쳐 as_of/effective_as_of를 tz-aware 왕복 가능한
        # ISO 문자열로 만든다 — body_digest는 컬렉션 스키마상 "digest"로 저장.
        record = EvidenceRecord(id=evidence_id, source=source,
                                body_digest=canonical_digest(body),
                                as_of=as_of, complete=complete,
                                truncated_reason=truncated_reason,
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
            "truncated_reason": dumped["truncated_reason"],
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
            # 이 필드 이전에 쓰인 문서에는 키가 없다 — .get으로 읽어 옛 증거를 깨지 않는다.
            "truncated_reason": doc.get("truncated_reason"),
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

    def _cas(self, case_id: str, guard: dict, fields: dict) -> bool:
        """읽은 값들을 그대로 술어로 걸어 $set — 그 사이 남이 바꿨으면 진다.

        `$expr`·범위 비교를 쓰지 않는다: ISO 문자열 시각의 사전식 순서 문제(모듈
        docstring)와 mongomock 호환 둘 다 이유다. 등식 CAS면 충분하다 — 우리가 묻는
        것은 "읽은 뒤 바뀌었나"이지 값의 대소가 아니다.
        """
        return self._db.cases.update_one({"id": case_id, **guard}, {"$set": fields}).matched_count == 1

    def attach_answer(self, case_id, *, answer, key, now, expect_seq=None):
        # 두 바퀴: 첫 CAS가 지면 다시 읽어 **왜** 졌는지로 분류하고 한 번 더 시도한다.
        # 재귀로 두면 끝이 없다 — 술어가 문서와 영원히 안 맞는 경우(필드 부재를
        # 기본값으로 걸었던 버그)에 RecursionError였다. 두 번 다 지면 남이 계속 바꾸는
        # 중이니 busy(잠시 뒤 다시)로 물러난다 — 쓴 것이 없으니 같은 key의 재시도가 안전하다.
        first_seq = None
        for _ in range(2):
            doc = self._db.cases.find_one({"id": case_id})
            if doc is None:
                return "not_found"
            if first_seq is not None and doc.get("question_seq") != first_seq:
                # 첫 CAS가 진 이유가 파킹(질문이 바뀜)이면 둘째 바퀴가 같은 답을 새 질문에
                # 싣는다 — 클라이언트는 옛 질문을 보고 썼다(리뷰 L2). 번호를 실어 보낸
                # 클라이언트에게는 그 사실을 정확한 이름으로 돌려준다(계획 17).
                return "stale_question" if expect_seq is not None else "not_waiting"
            first_seq = doc.get("question_seq")
            record = self._to_record(doc)
            if record.answer_key == key:
                return "duplicate"
            if expect_seq is not None and record.question_seq != expect_seq:
                return "stale_question"
            if record.status != "awaiting_human" or record.question_kind != "investigation" \
                    or record.question_seq <= record.answered_seq:
                return "not_waiting"
            if record.pending_answer is not None:
                return "pending"
            if lease_is_held(record, now):
                return "busy"
            # 술어는 **문서에서 읽은 원값**(doc.get)이지 record의 기본값이 아니다 — 필드가
            # 없는 문서에 {"answered_seq": 0}은 안 맞고 {"answered_seq": None}은 부재에도
            # 맞는다(Mongo의 null 의미론). 인계 #8이 권하는 마이그레이션(question_seq만
            # $set)이 정확히 그런 문서를 만든다. 워커가 그 사이 claim했거나(owner/lease_until —
            # claim은 상태를 안 바꾼다) take로 answered_seq를 올렸으면 진다(리뷰 S7·S2-F).
            guard = {"status": "awaiting_human", "question_kind": "investigation",
                     "pending_answer": None, "question_seq": doc.get("question_seq"),
                     "answered_seq": doc.get("answered_seq"), "answer_key": doc.get("answer_key"),
                     "owner": doc.get("owner"), "lease_until": doc.get("lease_until")}
            # 불변식을 명시적으로 남긴다: 사전검사를 지난 이상 `expect_seq`는 읽은 값과
            # 같으므로 바로 위 `"question_seq": doc.get(...)`와 **중복**이다(검증 리뷰 M2).
            # 읽고 나서 파킹이 일어난 경우를 실제로 잡는 것은 이 줄이 아니라 재분류
            # 두 바퀴다 — 테스트가 방어한다고 주장하지 않도록 여기 적어 둔다.
            if expect_seq is not None:
                guard["question_seq"] = expect_seq
            if self._cas(case_id, guard, {"pending_answer": answer, "answer_key": key,
                                          "updated_at": to_jsonable_python(now)}):
                return "accepted"
        return "busy"

    def update_if(self, case_id, *, expect, fields, now) -> bool:
        # 술어도 저장도 `save`와 **같은 직렬화**를 쓴다. `.isoformat()`은 `+00:00`을 내고
        # `model_dump(mode="json")`은 `Z`를 내므로, 둘을 섞으면 술어가 영원히 안 맞는다 —
        # Mongo 배포에서 접수가 100% 실패했고 인메모리 테스트에는 안 보였다(검증 리뷰 B1).
        guard = {k: to_jsonable_python(v) for k, v in expect.items()}
        result = self._db.cases.update_one(
            {"id": case_id, **guard},
            {"$set": {**{k: to_jsonable_python(v) for k, v in fields.items()},
                      "updated_at": to_jsonable_python(now)}})
        return bool(result.matched_count)

    def take_answer(self, case_id, *, now):
        doc = self._db.cases.find_one({"id": case_id})
        if doc is None:
            raise KeyError(case_id)
        answer = doc.get("pending_answer")
        if answer is None:
            return None
        # 술어에 key·seq도 건다 — 같은 문자열의 다른 답(다른 key)이나 새 질문(seq가
        # 오른 뒤)에 실린 답을 옛 답으로 가져가지 않는다(ABA).
        if not self._cas(case_id, {"pending_answer": answer, "answer_key": doc.get("answer_key"),
                                   "question_seq": doc.get("question_seq")},
                         {"pending_answer": None, "answered_seq": doc.get("question_seq", 0),
                          "updated_at": to_jsonable_python(now)}):
            return None            # 그 사이 남이 가져갔다
        return answer

    def restore_answer(self, case_id, *, answer, now):
        doc = self._db.cases.find_one({"id": case_id})
        if doc is None:
            raise KeyError(case_id)     # take와 같은 계약(인메모리 구현도 KeyError)
        seq = doc.get("question_seq", 0)
        if doc.get("status") != "awaiting_human" or doc.get("pending_answer") is not None \
                or seq != doc.get("answered_seq", 0):
            return False
        # 술어는 원값(doc.get) — attach와 같은 이유(필드 부재 ≠ 기본값 0)
        return self._cas(case_id, {"status": "awaiting_human", "pending_answer": None,
                                   "question_seq": doc.get("question_seq"),
                                   "answered_seq": doc.get("answered_seq")},
                         {"pending_answer": answer, "answered_seq": seq - 1,
                          "updated_at": to_jsonable_python(now)})

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

    def _closed_newest_first(self, match: dict, limit: int) -> list[CaseRecord]:
        """정렬과 절단을 **DB가** 한다 — 10건을 얻으려고 수천 건을 검증하지 않는다.

        `find().sort()`를 못 쓰는 이유: 정렬 키가 `status_since or updated_at`이라
        coalesce다(`status_since`는 계획 4b 이후에 생겨 옛 문서에는 없다). 그래서
        집계의 `$ifNull`로 계산 필드를 만든다.

        동점을 `id`로 가르는 것은 인메모리 `_newest_first`와 같은 계약이다 — 두 백엔드가
        다른 이력을 리드에게 보이면 그 차이는 프로덕션에서만 드러난다.

        `$limit: 0`은 Mongo가 거부하므로 상한이 0 이하면 DB에 가지 않는다(인메모리도
        빈 목록을 낸다).
        """
        if limit <= 0:
            return []
        pipeline = [
            {"$match": match},
            {"$addFields": {"_closed_at": {"$ifNull": ["$status_since", "$updated_at"]}}},
            {"$sort": {"_closed_at": -1, "id": -1}},
            {"$limit": limit},
            # CaseRecord는 StrictModel이다 — 계산 필드를 남기면 검증 오류가 난다.
            {"$project": {"_closed_at": 0}},
        ]
        return [self._to_record(doc) for doc in self._db.cases.aggregate(pipeline)]

    def closed_by_fingerprint(self, fp, *, exclude_case_id, limit=10) -> list[CaseRecord]:
        return self._closed_newest_first(
            {"status": "closed", "fingerprint": fp, "id": {"$ne": exclude_case_id}}, limit)

    def closed_by_locators(self, locators, *, exclude_case_id, limit=20) -> list[CaseRecord]:
        if not locators:                # 빈 $in도 0건이지만, 의도를 코드로 못박는다
            return []
        return self._closed_newest_first(
            {"status": "closed", "target_locator": {"$in": list(locators)},
             "id": {"$ne": exclude_case_id}}, limit)

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

    def record_metric(self, name, value, *, tags, at) -> None:
        self._db.metrics.insert_one({"name": name, "value": float(value), "tags": dict(tags),
                                     "at": at.isoformat()})

    def metrics(self, name, *, limit=200) -> list[dict]:
        if limit <= 0:
            return []
        cursor = self._db.metrics.find({"name": name}).sort("at", -1).limit(limit)
        return [{"name": d["name"], "value": d["value"], "tags": d.get("tags", {}),
                 "at": datetime.fromisoformat(d["at"])} for d in cursor]

    def prune_metrics_before(self, before) -> int:
        stale = [d["_id"] for d in self._db.metrics.find({})
                 if datetime.fromisoformat(d["at"]) < before]
        if not stale:
            return 0
        return self._db.metrics.delete_many({"_id": {"$in": stale}}).deleted_count

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


class MongoDigestStore(DigestStorePort):
    """집계 실행 기록 — 추세 비교의 유일한 재료다(계획 16)."""

    def __init__(self, db: Database):
        self._db = db

    def put(self, report: FleetReport) -> None:
        self._db.fleet_runs.insert_one(report.model_dump(mode="json"))

    def _rows(self, scenario: str, limit: int):
        cursor = (self._db.fleet_runs.find({"scenario": scenario})
                  .sort("generated_at", -1).limit(limit))
        return [FleetReport.model_validate({k: v for k, v in d.items() if k != "_id"})
                for d in cursor]

    def latest(self, scenario):
        rows = self._rows(scenario, 1)
        return rows[0] if rows else None

    def list(self, scenario, limit=20):
        return self._rows(scenario, limit) if limit > 0 else []

    def prune_before(self, before) -> int:
        stale = [d["_id"] for d in self._db.fleet_runs.find({})
                 if datetime.fromisoformat(d["generated_at"]) < before]
        if not stale:
            return 0
        return self._db.fleet_runs.delete_many({"_id": {"$in": stale}}).deleted_count


class MongoLabelStore(LabelStorePort):
    """append-only — 덮어쓰지 않는다. retention도 걷지 않는다(스냅샷과 짝이다)."""

    def __init__(self, db: Database):
        self._db = db

    def append(self, label: RootCauseLabel) -> None:
        self._db.labels.insert_one(label.model_dump(mode="json"))

    def list_for(self, case_id: str) -> list[RootCauseLabel]:
        # _id를 동점 키로 — 같은 시각의 두 라벨(고정 시계 테스트, 같은 초의 두 요청)의
        # 순서가 "단 순서대로"라는 append-only 계약을 지키려면 유일 키가 필요하다.
        cursor = self._db.labels.find({"case_id": case_id}).sort([("labeled_at", 1), ("_id", 1)])
        return [RootCauseLabel.model_validate({k: v for k, v in d.items() if k != "_id"})
                for d in cursor]

    def count(self) -> int:
        return self._db.labels.count_documents({})

    def labeled_case_ids(self) -> set[str]:
        return set(self._db.labels.distinct("case_id"))


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
