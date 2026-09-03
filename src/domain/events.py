"""엔진 이벤트 봉투 — 계획 5(보고·채널). §4.5-① StrictModel 계보를 따른다.

이벤트 어휘는 좁게 유지한다 — 봉투 밖(노드명·내부 상태 키)이 그대로 새 나가면
구독자(리포터·채널)가 엔진 내부 구현에 결합돼버린다. data는 자유 dict이지만 어떤
이벤트를 실을지는 application/events.py의 매핑 규칙이 정한다.

새 종류를 더할지 판단하는 시험은 개수가 아니라 성질이다: **"이 이름이 그래프를
다시 배선해도 그대로 유효한가?"** verdict_formed는 도메인 사실(Verdict가 생겼다)을
가리키므로 conclude/verify를 합치든 쪼개든 유효하다. node_entered·state_patch·
select_gate_evaluated는 무효다 — 그래프 모양이 바뀌면 뜻이 사라진다.
"""
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel

EVENT_SCHEMA_VERSION = 1

EventKind = Literal["case_status_changed", "round_started", "task_finished",
                    "question_raised", "report_ready", "verdict_formed"]


class EngineEvent(StrictModel):
    """엔진이 밖으로 내보내는 이벤트 봉투. data 형태는 event 종류가 정한다."""
    event: EventKind
    schema_version: int = EVENT_SCHEMA_VERSION
    case_id: str
    at: datetime
    seq: int | None = None      # 스토어가 append에서 부여한다 — 생산자는 채우지 않는다.
                                # 고정 시계 테스트에서 같은 superstep의 이벤트가 동일한 at을
                                # 가지므로 at으로는 전순서가 나오지 않는다.
    data: dict = {}


class EventStorePort(ABC):
    """이벤트 로그 — 프로세스 밖 구독자가 읽을 수 있는 유일한 자리."""

    @abstractmethod
    def append(self, event: "EngineEvent") -> "EngineEvent":
        """case_id별 단조 seq를 부여해 적재하고, seq가 채워진 사본을 돌려준다."""
        ...

    @abstractmethod
    def since(self, case_id: str, after_seq: int = 0,
              limit: int = 200) -> list["EngineEvent"]:
        """after_seq보다 큰 seq의 이벤트를 seq 오름차순으로 최대 limit건."""
        ...

    @abstractmethod
    def prune_before(self, before: datetime) -> int:
        """before 이전 이벤트를 삭제하고 삭제 건수를 반환한다."""
        ...


class InMemoryEventStore(EventStorePort):
    def __init__(self):
        self._events: dict[str, list[EngineEvent]] = defaultdict(list)
        # 카운터를 리스트 길이와 분리한다 — prune 후 길이가 줄면 이미 나간 seq를
        # 다시 부여해, `?since=N`으로 재접속한 구독자가 새 이벤트를 영영 놓친다.
        self._next: dict[str, int] = defaultdict(int)

    def append(self, event):
        self._next[event.case_id] += 1
        stamped = event.model_copy(update={"seq": self._next[event.case_id]})
        self._events[event.case_id].append(stamped)
        return stamped

    def since(self, case_id, after_seq=0, limit=200):
        if limit <= 0:                     # limit=0은 "0개"(레저의 runs와 같은 관례)
            return []
        return [e for e in self._events.get(case_id, []) if e.seq > after_seq][:limit]

    def prune_before(self, before):
        deleted = 0
        for case_id, history in self._events.items():
            kept = [e for e in history if e.at >= before]
            deleted += len(history) - len(kept)
            self._events[case_id] = kept
        return deleted
