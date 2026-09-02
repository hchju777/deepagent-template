"""케이스 Store 포트 — 증거 본문과 코드 지식 캐시가 사는 곳 (스펙 §2.3, §3.4).

State에는 증거 id+요약만 남고 본문은 여기 있다. get_evidence의 KeyError는
의도된 계약이다: 인용 실재 검증(verify)이 "없는 id 인용"을 이 예외로 잡는다.
"""
from abc import ABC, abstractmethod
from collections import defaultdict


class CaseStorePort(ABC):
    @abstractmethod
    def put_evidence(self, case_id: str, source: str, body: object) -> str: ...

    @abstractmethod
    def get_evidence(self, case_id: str, evidence_id: str) -> object: ...

    @abstractmethod
    def has_evidence(self, case_id: str, evidence_id: str) -> bool: ...

    @abstractmethod
    def put_code_knowledge(self, service: str, commit: str, spec: str) -> None: ...

    @abstractmethod
    def get_code_knowledge(self, service: str, commit: str) -> str | None: ...


class InMemoryCaseStore(CaseStorePort):
    def __init__(self):
        self._evidence: dict[str, dict[str, tuple[str, object]]] = defaultdict(dict)
        self._counters: dict[str, int] = defaultdict(int)
        self._code: dict[tuple[str, str], str] = {}

    def put_evidence(self, case_id, source, body):
        self._counters[case_id] += 1
        evidence_id = f"ev-{self._counters[case_id]}"
        self._evidence[case_id][evidence_id] = (source, body)
        return evidence_id

    def get_evidence(self, case_id, evidence_id):
        return self._evidence[case_id][evidence_id][1]     # 없으면 KeyError(계약)

    def has_evidence(self, case_id, evidence_id):
        return evidence_id in self._evidence[case_id]

    def put_code_knowledge(self, service, commit, spec):
        self._code[(service, commit)] = spec

    def get_code_knowledge(self, service, commit):
        return self._code.get((service, commit))
