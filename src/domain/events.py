"""엔진 이벤트 봉투 — 계획 5(보고·채널). §4.5-① StrictModel 계보를 따른다.

이벤트 어휘는 정확히 5종으로 고정한다 — 봉투 밖(노드명·내부 상태 키)이 그대로
새 나가면 구독자(리포터·채널)가 엔진 내부 구현에 결합돼버린다. data는 자유
dict이지만 어떤 이벤트를 실을지는 application/events.py의 매핑 규칙이 정한다.
"""
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel

EVENT_SCHEMA_VERSION = 1

EventKind = Literal["case_status_changed", "round_started", "task_finished",
                    "question_raised", "report_ready"]


class EngineEvent(StrictModel):
    """엔진이 밖으로 내보내는 이벤트 봉투. data 형태는 event 종류가 정한다."""
    event: EventKind
    schema_version: int = EVENT_SCHEMA_VERSION
    case_id: str
    at: datetime
    data: dict = {}
