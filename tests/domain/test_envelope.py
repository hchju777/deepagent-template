from datetime import datetime

import pytest
from pydantic import ValidationError
from src.domain.envelope import Envelope, ProbeResult

T = datetime(2026, 9, 3, 8, 0, 0)


def test_불완전_결과는_사유가_필수다():
    Envelope(observed_at=T, complete=False, truncated_reason="max_rows")
    with pytest.raises(ValidationError):
        Envelope(observed_at=T, complete=False)


def test_error_결과는_원인이_필수고_ok는_원인_금지():
    env = Envelope(observed_at=T)
    ProbeResult(status="error", envelope=env, error="타임아웃")
    with pytest.raises(ValidationError):
        ProbeResult(status="error", envelope=env)
    with pytest.raises(ValidationError):
        ProbeResult(status="ok", envelope=env, error="이상한 조합")
