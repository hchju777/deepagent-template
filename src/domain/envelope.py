"""결과 봉투 — 스펙 §4.2. "요청한 것"과 "실제로 얻은 것"의 차이를 표현한다.

- complete=False: 상한(max_rows 등)에 잘렸다 — 부정 증거("없음")로 결론 금지(verify가 소비).
- effective_as_of: 요청 as_of와 실제 달성 as_of가 다르면 명시 — Kafka 보존 밖
  earliest 폴백이 조용히 "나중" 데이터를 T0 증거로 위장하는 것을 막는다.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from src.config.schema_app import StrictModel


class Envelope(StrictModel):
    observed_at: datetime
    complete: bool = True
    truncated_reason: str | None = None
    requested_as_of: datetime | None = None
    effective_as_of: datetime | None = None

    @model_validator(mode="after")
    def _incomplete_needs_reason(self):
        if not self.complete and not self.truncated_reason:
            raise ValueError("complete=False면 truncated_reason이 필요하다")
        return self


class ProbeResult(StrictModel):
    status: Literal["ok", "error"]
    envelope: Envelope
    data: Any = None
    error: str | None = None

    @model_validator(mode="after")
    def _error_needs_cause(self):
        if self.status == "error" and not self.error:
            raise ValueError("status=error면 error 원인이 필요하다")
        if self.status == "ok" and self.error:
            raise ValueError("status=ok면 error가 없어야 한다")
        return self
