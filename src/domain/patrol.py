"""순찰 도메인 모델 — 점검 결과, 발견, 지문."""
from datetime import datetime
from hashlib import sha256
from typing import Literal

from pydantic import model_validator

from src.config.schema_app import StrictModel


Concern = Literal["system", "operation"]
"""무엇이 이상한가의 축 — 라우팅의 기반(스펙 §3.4).

- `system`: 파이프라인이 고장. Kafka lag, Redis TTL 만료, Mongo 미갱신, API 5xx.
- `operation`: 데이터는 흐르는데 현장 상태가 이상. 0/0/0, 생산중이어야 하는데 NO PLAN.

**사람이 config에 적는다.** 응답 모양으로 추론하지 않는 이유는 라우팅 근거가
재현·감사 가능해야 하기 때문이다(규율 6) — "왜 이 메일이 나한테 왔나"에 답할 수
있어야 한다. 기본값이 `"system"`인 것은 편의가 아니라 분류다: 지금 있는 rule
4종(range·exists·freshness·max)은 전부 파이프라인 신호를 판정한다.

값이 두 개인 이유도 이벤트 어휘와 같다(규율 7) — 두 개로 표현 불가능한 것을
만나기 전엔 늘리지 않는다.
"""


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
