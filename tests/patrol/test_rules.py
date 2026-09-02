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


def test_잘못된_bound와_field는_KnownRuleError():
    for params in ({"rule": "freshness", "field": "ts"},
                   {"rule": "max", "field": "lag"},
                   {"rule": "max", "field": "lag", "max": "1000"},
                   {"rule": "range", "field": "x", "min": "0", "max": 1},
                   {"rule": "max", "field": 123, "max": 1}):
        with pytest.raises(KnownRuleError):
            judge_by_rule(_ok({"lag": 5, "ts": T.isoformat(), "x": 1}), params, clock=lambda: T)


def test_naive_aware_혼합은_어느_방향이든_TypeError가_없다():
    fresh = {"rule": "freshness", "field": "ts", "max_age_s": 60}
    naive_clock = datetime(2026, 9, 3, 8, 0)
    aware_ts = "2026-09-03T07:00:00+00:00"
    assert judge_by_rule(_ok({"ts": aware_ts}), fresh, clock=lambda: naive_clock).status == "finding"
    naive_ts = "2026-09-03T07:59:30"
    assert judge_by_rule(_ok({"ts": naive_ts}), fresh, clock=lambda: T).status == "ok"


def test_NaN은_range를_통과하지_못한다():
    params = {"rule": "range", "field": "v", "min": 0, "max": 100}
    assert judge_by_rule(_ok({"v": float("nan")}), params, clock=lambda: T).status == "finding"


def test_range는_bound_하나는_있어야_하고_NaN_bound는_거부():
    with pytest.raises(KnownRuleError):
        judge_by_rule(_ok({"v": 1}), {"rule": "range", "field": "v"}, clock=lambda: T)
    with pytest.raises(KnownRuleError):
        judge_by_rule(_ok({"v": 1}), {"rule": "range", "field": "v", "min": float("nan")},
                      clock=lambda: T)
    assert judge_by_rule(_ok({"v": 1}), {"rule": "range", "field": "v", "min": 0},
                         clock=lambda: T).status == "ok"
