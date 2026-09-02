from datetime import datetime, timedelta, timezone

import pytest
from src.domain.envelope import Envelope, ProbeResult
from src.patrol.rules import KnownRuleError, judge_by_rule

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _ok(data):
    return ProbeResult(status="ok", envelope=Envelope(observed_at=T), data=data)


def test_range_rule():
    params = {"rule": "range", "field": "body.oee", "min": 0, "max": 100}
    assert judge_by_rule(_ok({"body": {"oee": 87}}), params, clock=lambda: T).status == "ok"
    bad = judge_by_rule(_ok({"body": {"oee": 512}}), params, clock=lambda: T)
    assert bad.status == "finding" and "512" in bad.reason
    assert judge_by_rule(_ok({"body": {}}), params, clock=lambda: T).status == "finding"


def test_exists_freshness_max():
    assert judge_by_rule(_ok(None), {"rule": "exists"}, clock=lambda: T).status == "finding"
    assert judge_by_rule(_ok("480"), {"rule": "exists"}, clock=lambda: T).status == "ok"
    fresh = {"rule": "freshness", "field": "ts", "max_age_s": 60}
    old = (T - timedelta(seconds=300)).isoformat()
    assert judge_by_rule(_ok({"ts": old}), fresh, clock=lambda: T).status == "finding"
    assert judge_by_rule(_ok({"ts": T.isoformat()}), fresh, clock=lambda: T).status == "ok"
    assert judge_by_rule(_ok({"lag": 1500}), {"rule": "max", "field": "lag", "max": 1000},
                         clock=lambda: T).status == "finding"


def test_미지의_rule은_KnownRuleError():
    with pytest.raises(KnownRuleError):
        judge_by_rule(_ok({}), {"rule": "ghost"}, clock=lambda: T)
