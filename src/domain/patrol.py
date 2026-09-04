"""순찰 도메인 모델 — 점검 결과, 발견, 지문."""
from datetime import datetime
from hashlib import sha256
from typing import Literal

from pydantic import model_validator

from src.config.schema_app import StrictModel
from src.domain.concern import Concern


class Finding(StrictModel):
    """점검 중 발견된 항목."""
    id: str
    gbm: str                           # 사업부
    fct: str                           # 시설
    check: str                         # 점검 항목
    target: str | None                 # 대상 (None일 수 있음)
    summary: str                       # 발견 요약
    evidence_ids: list[str]            # 증거 id 목록
    scratch_case_id: str               # 스냅샷이 저장된 순찰 스크래치 케이스 id
    observed_at: datetime              # 관찰 시간
    judge: Literal["rule", "llm", "rule+llm"]  # 판정 방식
    concern: Concern = "system"        # 무엇이 이상한가 — 수신자·브리핑을 가른다


class CheckOutcome(StrictModel):
    """점검 결과."""
    status: Literal["ok", "finding", "error", "skipped"]
    observed_at: datetime
    finding: Finding | None = None
    error: str | None = None
    skipped_reason: str | None = None
    llm_calls: int = 0

    @model_validator(mode="after")
    def _status_payload(self):
        """상태별 필수 필드 검증."""
        need = {
            "finding": self.finding,
            "error": self.error,
            "skipped": self.skipped_reason
        }
        if self.status in need and need[self.status] is None:
            raise ValueError(f"status={self.status}에는 해당 필드가 필요하다")
        return self


def fingerprint(gbm: str, fct: str, check: str, target: str | None) -> str:
    """점검 지문 생성.

    Args:
        gbm: 사업부
        fct: 시설
        check: 점검 항목
        target: 대상 (None일 경우 "-"로 표현)

    Returns:
        sha256 해시의 처음 16글자
    """
    parts = [gbm, fct, check, target if target is not None else "-"]
    combined = "|".join(parts)
    return sha256(combined.encode()).hexdigest()[:16]


def scratch_case_id(gbm: str, fct: str, check: str) -> str:
    """스크래치 케이스 id 생성.

    Args:
        gbm: 사업부
        fct: 시설
        check: 점검 항목

    Returns:
        "patrol:gbm:fct:check" 형식의 id
    """
    return f"patrol:{gbm}:{fct}:{check}"
