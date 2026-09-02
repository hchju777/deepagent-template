from datetime import datetime

import pytest
from pydantic import ValidationError
from src.domain.patrol import CheckOutcome, Finding, fingerprint, scratch_case_id

T = datetime(2026, 9, 3, 8, 0)


def test_지문은_사이트_점검_대상으로_결정되고_target_없음은_구분된다():
    a = fingerprint("mx", "gumi", "api.oee", "rest:/oee")
    assert a == fingerprint("mx", "gumi", "api.oee", "rest:/oee")
    assert a != fingerprint("mx", "suwon", "api.oee", "rest:/oee")
    assert fingerprint("mx", "gumi", "x", None) != fingerprint("mx", "gumi", "x", "-x")
    assert scratch_case_id("mx", "gumi", "api.oee") == "patrol:mx:gumi:api.oee"


def test_outcome_3상_검증자():
    f = Finding(id="f-1", gbm="mx", fct="gumi", check="api.oee", target="rest:/oee",
                summary="OEE 512%", evidence_ids=["ev-1"],
                scratch_case_id="patrol:mx:gumi:api.oee", observed_at=T, judge="rule")
    CheckOutcome(status="finding", observed_at=T, finding=f)
    CheckOutcome(status="error", observed_at=T, error="타임아웃")
    CheckOutcome(status="skipped", observed_at=T, skipped_reason="llm 예산 소진")
    with pytest.raises(ValidationError):
        CheckOutcome(status="finding", observed_at=T)
    with pytest.raises(ValidationError):
        CheckOutcome(status="error", observed_at=T)
