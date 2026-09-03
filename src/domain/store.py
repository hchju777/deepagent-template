"""케이스 Store 포트 — 증거 본문과 코드 지식 캐시가 사는 곳 (스펙 §2.3, §3.4).

State에는 증거 id+요약만 남고 본문은 여기 있다. get_evidence의 KeyError는
의도된 계약이다: 인용 실재 검증(verify)이 "없는 id 인용"을 이 예외로 잡는다.

EvidenceRecord는 결과 봉투(Envelope)의 메타(as_of·complete·effective_as_of)를
State 밖(Store)까지 실어 나른다 — 이게 없으면 3b의 EvidenceRef 조립이 불가능하고,
verify의 "불완전 증거로 부정 결론 금지"가 저장 시점에 complete 기본값(True)으로
사라져 거짓 통과가 된다(§4.2).
"""
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime

from src.config.schema_app import StrictModel
from src.domain.case import Verdict
from src.knowledge.digest import canonical_digest


class EvidenceRecord(StrictModel):
    id: str
    source: str
    body_digest: str            # canonical_digest(body)
    as_of: datetime | None = None
    complete: bool = True
    truncated_reason: str | None = None   # complete=False면 왜 잘렸는지 — 조용한 생략 금지
    effective_as_of: datetime | None = None


class CaseStorePort(ABC):
    @abstractmethod
    def put_evidence(self, case_id: str, source: str, body: object, *,
                     as_of: datetime | None = None, complete: bool = True,
                     truncated_reason: str | None = None,
                     effective_as_of: datetime | None = None) -> str: ...

    @abstractmethod
    def get_evidence(self, case_id: str, evidence_id: str) -> object: ...

    @abstractmethod
    def get_evidence_record(self, case_id: str, evidence_id: str) -> EvidenceRecord: ...

    @abstractmethod
    def list_evidence(self, case_id: str) -> list[EvidenceRecord]: ...

    @abstractmethod
    def has_evidence(self, case_id: str, evidence_id: str) -> bool: ...

    @abstractmethod
    def put_code_knowledge(self, service: str, commit: str, spec: str) -> None: ...

    @abstractmethod
    def get_code_knowledge(self, service: str, commit: str) -> str | None: ...

    @abstractmethod
    def put_verdict(self, case_id: str, verdict: Verdict) -> None: ...

    @abstractmethod
    def get_verdict(self, case_id: str) -> Verdict | None: ...

    @abstractmethod
    def put_case_file(self, case_id: str, snapshot: dict) -> None:
        """케이스 파일 스냅샷(plan_tasks·hypotheses·round·qa_log·verify_problems)을
        저장한다(계획 4b I6) — 스레드 체크포인트가 TTL로 폐기돼도 계획 5의
        보고서가 읽을 소스가 남도록. 매번 덮어쓴다(케이스당 최신 스냅샷 하나)."""
        ...

    @abstractmethod
    def get_case_file(self, case_id: str) -> dict | None:
        """저장된 케이스 파일 스냅샷. 없으면 None."""
        ...

    @abstractmethod
    def purge_case(self, case_id: str) -> int:
        """케이스의 증거+판정+케이스 파일을 전부 삭제하고 삭제 건수를 반환한다."""
        ...

    @abstractmethod
    def purge_evidence_before(self, case_id: str, before: datetime) -> int:
        """as_of가 before 이전인 증거만 삭제한다(as_of가 None이면 유지)."""
        ...

    @abstractmethod
    def list_case_ids(self, prefix: str = "") -> list[str]:
        """prefix로 시작하는 케이스 id를 정렬하여 반환한다."""
        ...


class InMemoryCaseStore(CaseStorePort):
    def __init__(self):
        self._evidence: dict[str, dict[str, tuple[object, EvidenceRecord]]] = {}
        self._counters: dict[str, int] = defaultdict(int)
        self._code: dict[tuple[str, str], str] = {}
        self._verdicts: dict[str, Verdict] = {}
        self._case_files: dict[str, dict] = {}

    def put_evidence(self, case_id, source, body, *,
                     as_of=None, complete=True, truncated_reason=None,
                     effective_as_of=None):
        self._counters[case_id] += 1
        evidence_id = f"ev-{self._counters[case_id]}"
        record = EvidenceRecord(id=evidence_id, source=source,
                                body_digest=canonical_digest(body),
                                as_of=as_of, complete=complete,
                                truncated_reason=truncated_reason,
                                effective_as_of=effective_as_of)
        self._evidence.setdefault(case_id, {})[evidence_id] = (body, record)
        return evidence_id

    # 읽기 메서드는 전부 .get(case_id, {})로 조회한다 — defaultdict[case_id]로
    # 없는 케이스를 읽으면 빈 딕셔너리가 삽입되는 부작용이 있었다(트리아지 권고).
    def get_evidence(self, case_id, evidence_id):
        return self._evidence.get(case_id, {})[evidence_id][0]     # 없으면 KeyError(계약)

    def get_evidence_record(self, case_id, evidence_id):
        return self._evidence.get(case_id, {})[evidence_id][1]     # 없으면 KeyError(계약)

    def list_evidence(self, case_id):
        return [record for _, record in self._evidence.get(case_id, {}).values()]

    def has_evidence(self, case_id, evidence_id):
        return evidence_id in self._evidence.get(case_id, {})

    def put_code_knowledge(self, service, commit, spec):
        self._code[(service, commit)] = spec

    def get_code_knowledge(self, service, commit):
        return self._code.get((service, commit))

    def put_verdict(self, case_id, verdict):
        self._verdicts[case_id] = verdict

    def get_verdict(self, case_id):
        return self._verdicts.get(case_id)

    def put_case_file(self, case_id, snapshot):
        self._case_files[case_id] = dict(snapshot)

    def get_case_file(self, case_id):
        return self._case_files.get(case_id)

    def purge_case(self, case_id):
        """케이스의 증거+판정+케이스 파일을 전부 삭제하고 삭제 건수를 반환한다."""
        deleted = len(self._evidence.pop(case_id, {}))
        if self._verdicts.pop(case_id, None) is not None:
            deleted += 1
        if self._case_files.pop(case_id, None) is not None:
            deleted += 1
        return deleted

    def purge_evidence_before(self, case_id, before):
        """as_of가 before 이전인 증거만 삭제한다(as_of가 None이면 유지)."""
        bucket = self._evidence.get(case_id, {})
        stale_ids = [eid for eid, (_, record) in bucket.items()
                    if record.as_of is not None and record.as_of < before]
        for eid in stale_ids:
            del bucket[eid]
        return len(stale_ids)

    def list_case_ids(self, prefix=""):
        """prefix로 시작하는 케이스 id를 정렬하여 반환한다."""
        ids = set(self._evidence.keys()) | set(self._verdicts.keys())
        return sorted(cid for cid in ids if cid.startswith(prefix))
