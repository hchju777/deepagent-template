"""케이스 저장소 및 도메인 모델."""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Literal

from src.config.schema_app import StrictModel
from src.domain.case import Case

CaseStatus = Literal["open", "investigating", "awaiting_human", "closed"]
OPEN_STATUSES = ("open", "investigating", "awaiting_human")


class CaseRecord(StrictModel):
    """케이스 레코드.

    symptom/t0/target_locator/origin은 게이트가 케이스를 열 때 함께 채운다
    — 저장소에서 다시 읽었을 때 엔진에 넘길 도메인 Case를 재구성(to_case)
    하려면 이 넷이 레코드 안에 있어야 한다(§계획 3 브리지).
    """
    id: str                             # 케이스 id
    gbm: str                            # 사업부
    fct: str                            # 시설
    fingerprint: str                    # 지문
    symptom: str                        # 증상 요약 — Case 재구성용
    t0: datetime                        # 최초 관찰 시각 — Case 재구성용
    target_locator: str | None = None   # 대상 locator — Case 재구성용
    origin: Literal["human", "patrol"] = "patrol"  # 케이스 개설 경로
    status: CaseStatus = "open"         # 상태
    created_at: datetime                # 생성 시간
    updated_at: datetime                # 갱신 시간
    finding_ids: list[str] = []         # 발견 id 목록
    thread_ids: list[str] = []          # 스레드 id 목록
    owner: str | None = None            # 소유자
    lease_until: datetime | None = None # 임차 만료 시간
    closed_reason: str | None = None    # 종결 사유 (status=="closed"일 때)
    thread_versions: dict[str, int] = {}  # thread_id → 저장 시점 schema_version
    verdict_summary: str | None = None  # 판정 요약 (목록/브리핑용)
    status_since: datetime | None = None  # 현재 status로 전이된 시각(계획 4b I2) — updated_at은
                                          # attach 등 status와 무관한 갱신에도 움직이므로 타임아웃·
                                          # 보존 판단은 이 필드를 우선 본다(없으면 updated_at으로 대체)
    question: str | None = None         # awaiting_human으로 파킹된 질문(계획 4b I6) — resume 후 None
    interaction_policy: Literal["interactive", "autonomous"] = "autonomous"
                                        # 재개하는 프로세스가 케이스를 연 프로세스가 아닐 수 있으므로
                                        # (CLI 두 경로, 향후 API 워커) 정책을 호출자 인수가 아니라
                                        # 레코드에서 읽는다 — 스레드 재시작 경로가 조용히 autonomous로
                                        # 강등되던 버그의 근원이 정책을 인수로만 들고 다닌 것이었다
    purged_at: datetime | None = None   # retention ①이 증거+판정을 비운 시각(계획 4b I7) — 재선택 방지

    def to_case(self) -> Case:
        """저장된 레코드로부터 엔진에 넘길 도메인 Case를 재구성한다."""
        return Case(id=self.id, gbm=self.gbm, fct=self.fct, origin=self.origin,
                    symptom=self.symptom, t0=self.t0, target_locator=self.target_locator)


def lease_is_free(record: CaseRecord, owner: str, now: datetime) -> bool:
    """owner가 lease를 잡을 수 있는가 — 없거나, 자기 것이거나, 만료됐을 때.

    이 규칙이 두 곳(application의 acquire_lease, 저장소의 claim)에 있으면 반드시
    갈라진다. 도메인에 한 번만 둔다.
    """
    if record.owner is None or record.owner == owner:
        return True
    return record.lease_until is not None and record.lease_until < now


class CaseRepositoryPort(ABC):
    """케이스 저장소 포트."""

    @abstractmethod
    def save(self, record: CaseRecord) -> None:
        """케이스 저장."""
        pass

    @abstractmethod
    def get(self, case_id: str) -> CaseRecord:
        """케이스 조회 (없으면 KeyError)."""
        pass

    @abstractmethod
    def find_open_by_fingerprint(self, fp: str) -> CaseRecord | None:
        """열린 상태의 케이스를 지문으로 찾기."""
        pass

    @abstractmethod
    def list_by_status(self, status: CaseStatus) -> list[CaseRecord]:
        """상태별 케이스 목록."""
        pass

    @abstractmethod
    def list_open(self) -> list[CaseRecord]:
        """열린 상태(OPEN_STATUSES) 케이스 전부."""
        pass

    @abstractmethod
    def new_case_id(self) -> str:
        """새 케이스 id 생성."""
        pass

    @abstractmethod
    def claim(self, case_id: str, owner: str, *, now: datetime,
              ttl_s: float) -> CaseRecord | None:
        """lease를 원자적으로 잡고 갱신된 레코드를 돌려준다. 못 잡으면 None.

        get→save 사이에 다른 프로세스가 끼어들 수 있으므로 획득은 저장소가 한
        동작으로 수행해야 한다 — 순수 함수 acquire_lease로는 표현할 수 없다.
        """
        pass


class InMemoryCaseRepository(CaseRepositoryPort):
    """인메모리 케이스 저장소."""

    def __init__(self):
        self._cases: dict[str, CaseRecord] = {}
        self._counter = 0

    def save(self, record: CaseRecord) -> None:
        """케이스 저장."""
        self._cases[record.id] = record

    def get(self, case_id: str) -> CaseRecord:
        """케이스 조회 (없으면 KeyError)."""
        if case_id not in self._cases:
            raise KeyError(case_id)
        return self._cases[case_id]

    def claim(self, case_id, owner, *, now, ttl_s):
        record = self.get(case_id)
        if not lease_is_free(record, owner, now):
            return None
        claimed = record.model_copy(update={
            "owner": owner, "lease_until": now + timedelta(seconds=ttl_s)})
        self._cases[case_id] = claimed
        return claimed

    def find_open_by_fingerprint(self, fp: str) -> CaseRecord | None:
        """열린 상태의 케이스를 지문으로 찾기."""
        for record in self._cases.values():
            if record.fingerprint == fp and record.status in OPEN_STATUSES:
                return record
        return None

    def list_by_status(self, status: CaseStatus) -> list[CaseRecord]:
        """상태별 케이스 목록."""
        return [r for r in self._cases.values() if r.status == status]

    def list_open(self) -> list[CaseRecord]:
        """열린 상태(OPEN_STATUSES) 케이스 전부."""
        return [r for r in self._cases.values() if r.status in OPEN_STATUSES]

    def new_case_id(self) -> str:
        """새 케이스 id 생성."""
        self._counter += 1
        return f"c-{self._counter}"
