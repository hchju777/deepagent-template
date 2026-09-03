"""종결 시점 판정 스냅샷 — 스펙 §4.5.

retention ①이 closed_case_evidence_d(기본 90일)에 store.purge_case로 Verdict·
증거·케이스 파일을 전부 지운다. 살아남는 것은 CaseRecord.verdict_summary 200자뿐이라,
100일 뒤 사람이 실제 원인을 알려줘도 대조할 구조화 데이터가 없다 — **일방향 문이다.**
그래서 종결 시점에 별도로 박제한다. 보존기한도 다른 것들보다 훨씬 길다.

history_shown은 "이력 검색이 frame에 무엇을 먹였는가"다. 지금은 항상 비어 있지만
(이력 검색은 P8) 필드를 나중에 더하면 그 사이 종결된 케이스는 "이력을 보여준 게
도움이 됐나, 앵커링이었나"를 영영 답하지 못한다.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel


class VerdictSnapshot(StrictModel):
    """케이스가 닫힌 시점의 기계 판정 — 나중에 사람 라벨과 대조할 대상."""
    case_id: str
    closed_at: datetime
    gbm: str
    fct: str
    fingerprint: str
    target_locator: str | None = None
    origin: Literal["human", "patrol"] = "patrol"
    outcome: Literal["closed", "failed"]     # failed도 남긴다 — 빼면 분모에 생존 편향
    verdict_type: str | None = None
    root_cause_component: str | None = None
    alternates: list[str] = []               # 다중 RCA 후보(P6에서 채운다)
    confidence: str | None = None
    rounds: int = 0
    evidence_count: int = 0
    task_error_rate: str = "없음"
    verify_demoted: bool = False
    knowledge_digests: dict[str, str] = {}
    history_shown: list[dict] = []           # [{"case_id": ..., "tier": ...}] — P8이 채운다


class VerdictSnapshotPort(ABC):
    @abstractmethod
    def put(self, snapshot: VerdictSnapshot) -> None:
        """케이스당 하나로 덮어쓴다."""
        ...

    @abstractmethod
    def get(self, case_id: str) -> VerdictSnapshot | None: ...

    @abstractmethod
    def prune_before(self, before: datetime) -> int:
        """closed_at이 before 이전인 스냅샷을 삭제하고 건수를 반환한다."""
        ...


class InMemoryVerdictSnapshotStore(VerdictSnapshotPort):
    def __init__(self):
        self._snapshots: dict[str, VerdictSnapshot] = {}

    def put(self, snapshot):
        self._snapshots[snapshot.case_id] = snapshot

    def get(self, case_id):
        return self._snapshots.get(case_id)

    def prune_before(self, before):
        stale = [cid for cid, s in self._snapshots.items() if s.closed_at < before]
        for cid in stale:
            del self._snapshots[cid]
        return len(stale)
