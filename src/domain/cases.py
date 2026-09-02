"""케이스 저장소 및 도메인 모델."""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel

CaseStatus = Literal["open", "investigating", "awaiting_human", "closed"]
OPEN_STATUSES = ("open", "investigating", "awaiting_human")


class CaseRecord(StrictModel):
    """케이스 레코드."""
    id: str                             # 케이스 id
    gbm: str                            # 사업부
    fct: str                            # 시설
    fingerprint: str                    # 지문
    status: CaseStatus = "open"         # 상태
    created_at: datetime                # 생성 시간
    updated_at: datetime                # 갱신 시간
    finding_ids: list[str] = []         # 발견 id 목록
    thread_ids: list[str] = []          # 스레드 id 목록
    owner: str | None = None            # 소유자
    lease_until: datetime | None = None # 임차 만료 시간


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
    def new_case_id(self) -> str:
        """새 케이스 id 생성."""
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

    def find_open_by_fingerprint(self, fp: str) -> CaseRecord | None:
        """열린 상태의 케이스를 지문으로 찾기."""
        for record in self._cases.values():
            if record.fingerprint == fp and record.status in OPEN_STATUSES:
                return record
        return None

    def list_by_status(self, status: CaseStatus) -> list[CaseRecord]:
        """상태별 케이스 목록."""
        return [r for r in self._cases.values() if r.status == status]

    def new_case_id(self) -> str:
        """새 케이스 id 생성."""
        self._counter += 1
        return f"c-{self._counter}"
