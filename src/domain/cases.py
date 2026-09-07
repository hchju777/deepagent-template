"""케이스 저장소 및 도메인 모델."""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Literal

from src.config.schema_app import StrictModel
from src.domain.case import Case
from src.domain.concern import Concern

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
    concern: Concern = "system"         # 무엇이 이상한가 — 발행·브리핑 라우팅의 기반
    requested_by: str | None = None     # 요청 주체 — 접근 판정과 감사의 근거(스펙 §3.5)
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
    question_kind: Literal["intake", "investigation"] | None = None
    # requeue의 문(계획 13). 접수 중인 케이스도 status는 open이라, 이 표시가 없으면
    # 데몬이 그것을 집어 대상 없이 조사한다 — 계획 12의 F1 경합이 바로 그것이었고,
    # 가드 셋으로 좁혔지만 repo.save에 CAS가 없어 닫지 못했다. 기본값이 True인
    # 이유: 계획 13 이전 레코드는 전부 접수를 마친 것이다(그때는 접수가 끝나야
    # 레코드가 생겼다).
    intake_done: bool = True
    # 명령 채널(계획 13). api가 답을 여기 싣고 워커가 집어 간다 — 프로세스 밖에서
    # 온 답이 워커에 닿는 유일한 길이다. answer_key는 소비 뒤에도 남긴다(멱등).
    pending_answer: str | None = None
    answer_key: str | None = None
    # "어느 질문에 답했나"를 세는 짝(계획 13 리뷰 블로커 3). 파킹마다 question_seq가
    # 오르고, 워커가 답을 가져가면 answered_seq를 그 값으로 맞춘다. attach는
    # question_seq > answered_seq일 때만 — 워커가 답을 가져간 뒤 그래프가 새 질문으로
    # 다시 파킹하기 전의 창에서 둘째 답이 실려 **옛 질문의 답이 새 질문에** 소비되는
    # 것을 막는다.
    question_seq: int = 0
    answered_seq: int = 0
                                        # 어느 종류의 질문인가(계획 12) — 재개하는 쪽이
                                        # 접수를 이어갈지 그래프를 재개할지 갈라야 한다.
                                        # None은 계획 12 이전에 파킹된 레코드이고, 그때는
                                        # 그래프 파킹만 존재했으므로 investigation으로 읽는다
    interaction_policy: Literal["interactive", "autonomous"] = "autonomous"
                                        # 재개하는 프로세스가 케이스를 연 프로세스가 아닐 수 있으므로
                                        # (CLI 두 경로, 향후 API 워커) 정책을 호출자 인수가 아니라
                                        # 레코드에서 읽는다 — 스레드 재시작 경로가 조용히 autonomous로
                                        # 강등되던 버그의 근원이 정책을 인수로만 들고 다닌 것이었다
    purged_at: datetime | None = None   # retention ①이 증거+판정을 비운 시각(계획 4b I7) — 재선택 방지

    def to_case(self) -> Case:
        """저장된 레코드로부터 엔진에 넘길 도메인 Case를 재구성한다."""
        return Case(id=self.id, gbm=self.gbm, fct=self.fct, origin=self.origin,
                    symptom=self.symptom, t0=self.t0, target_locator=self.target_locator,
                    concern=self.concern)


def lease_is_free(record: CaseRecord, owner: str, now: datetime) -> bool:
    """owner가 lease를 잡을 수 있는가 — 없거나, 자기 것이거나, 만료됐을 때.

    이 규칙이 두 곳(application의 acquire_lease, 저장소의 claim)에 있으면 반드시
    갈라진다. 도메인에 한 번만 둔다.
    """
    if record.owner is None or record.owner == owner:
        return True
    return record.lease_until is not None and record.lease_until < now


def lease_is_held(record: CaseRecord, now: datetime) -> bool:
    """누군가 지금 lease를 쥐고 있는가 — owner가 있고 만료되지 않았을 때.

    lease_is_free의 부정이 아니다: 그쪽은 "이 owner가 잡을 수 있는가"라 자기 것이면
    True다. 답 채널(attach_answer)이 묻는 것은 "실행자가 붙어 있는가"이고 그 실행자가
    누구든 상관없다 — 실행자가 쥔 동안 실린 답은 통째 save에 지워지거나(리뷰 M1-b)
    파킹을 넘어 살아남아 다음 질문에 소비된다(M1-a).
    """
    if record.owner is None:
        return False
    return record.lease_until is None or record.lease_until >= now


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
    def closed_by_fingerprint(self, fp: str, *, exclude_case_id: str,
                              limit: int = 10) -> list[CaseRecord]:
        """같은 지문의 종결 케이스를 최신순으로(이력 tier 1, 계획 15)."""
        pass

    @abstractmethod
    def closed_by_locators(self, locators: list[str], *, exclude_case_id: str,
                           limit: int = 20) -> list[CaseRecord]:
        """target_locator가 목록에 있는 종결 케이스를 최신순으로(tier 2~4). 빈 목록 → 빈 결과."""
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
    def attach_answer(self, case_id: str, *, answer: str, key: str, now: datetime) -> str:
        """답을 조건부로 싣는다 — 필드 셋(`pending_answer`·`answer_key`·`updated_at`)만.

        조건: awaiting_human · 조사 질문 · pending 없음 · question_seq > answered_seq ·
        lease 없음. 전체 레코드 save로 싣으면 그 사이 워커가 잡은 lease·상태·스레드를
        되돌려 조사가 죽는다(리뷰 S7). `claim`처럼 저장소가 한 동작으로 판정해야 한다.
        반환: accepted / duplicate / pending / busy / not_waiting / not_found.
        `busy`는 실행자가 lease를 쥔 동안 — 잠시 뒤 다시 보내라(`pending`은 덮지 않는다).
        """
        pass

    @abstractmethod
    def take_answer(self, case_id: str, *, now: datetime) -> str | None:
        """실린 답을 가져가며 지우고 answered_seq를 맞춘다. 없으면 None."""
        pass

    @abstractmethod
    def restore_answer(self, case_id: str, *, answer: str, now: datetime) -> bool:
        """가져간 답을 되돌린다 — 소비가 실패했고 그 사이 새 파킹이 없을 때만.

        새 파킹이 있었으면(question_seq가 올라감) 되돌리지 않고 False — 옛 답을
        새 질문에 붙이는 것이 바로 막으려는 사고다. 호출자가 증거로 남긴다.
        """
        pass

    @abstractmethod
    def claim(self, case_id: str, owner: str, *, now: datetime,
              ttl_s: float) -> CaseRecord | None:
        """lease를 원자적으로 잡고 갱신된 레코드를 돌려준다. 못 잡으면 None.

        get→save 사이에 다른 프로세스가 끼어들 수 있으므로 획득은 저장소가 한
        동작으로 수행해야 한다 — 순수 함수 acquire_lease로는 표현할 수 없다.
        """
        pass


def _newest_first(records: list[CaseRecord], limit: int) -> list[CaseRecord]:
    """종결 시각(status_since, 없으면 updated_at) 내림차순으로 limit건."""
    if limit <= 0:
        return []
    return sorted(records, key=lambda r: r.status_since or r.updated_at, reverse=True)[:limit]


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

    # 인메모리는 단일 스레드·await 없음이라 아래 셋이 그 자체로 원자적이다. Mongo
    # 구현이 같은 판정을 CAS로 옮긴다 — 두 구현의 결과 어휘가 갈리면 안 된다.
    def attach_answer(self, case_id, *, answer, key, now):
        try:
            record = self.get(case_id)
        except KeyError:
            return "not_found"
        if record.answer_key == key:
            return "duplicate"
        if record.status != "awaiting_human" or record.question_kind != "investigation" \
                or record.question_seq <= record.answered_seq:
            return "not_waiting"
        if record.pending_answer is not None:
            return "pending"
        if lease_is_held(record, now):
            return "busy"
        self._cases[case_id] = record.model_copy(update={
            "pending_answer": answer, "answer_key": key, "updated_at": now})
        return "accepted"

    def take_answer(self, case_id, *, now):
        record = self.get(case_id)
        if record.pending_answer is None:
            return None
        self._cases[case_id] = record.model_copy(update={
            "pending_answer": None, "answered_seq": record.question_seq, "updated_at": now})
        return record.pending_answer

    def restore_answer(self, case_id, *, answer, now):
        record = self.get(case_id)
        if record.status != "awaiting_human" or record.pending_answer is not None \
                or record.question_seq != record.answered_seq:
            return False
        self._cases[case_id] = record.model_copy(update={
            "pending_answer": answer, "answered_seq": record.question_seq - 1,
            "updated_at": now})
        return True

    def find_open_by_fingerprint(self, fp: str) -> CaseRecord | None:
        """열린 상태의 케이스를 지문으로 찾기."""
        for record in self._cases.values():
            if record.fingerprint == fp and record.status in OPEN_STATUSES:
                return record
        return None

    def closed_by_fingerprint(self, fp, *, exclude_case_id, limit=10):
        return _newest_first([r for r in self._cases.values()
                              if r.status == "closed" and r.fingerprint == fp
                              and r.id != exclude_case_id], limit)

    def closed_by_locators(self, locators, *, exclude_case_id, limit=20):
        # 빈 목록은 빈 결과다 — "필터 없음"으로 읽어 전체를 긁으면 이력이 아무 케이스나
        # 물어온다(전부-또는-전무와 같은 성질의 함정).
        if not locators:
            return []
        wanted = set(locators)
        return _newest_first([r for r in self._cases.values()
                              if r.status == "closed" and r.target_locator in wanted
                              and r.id != exclude_case_id], limit)

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
