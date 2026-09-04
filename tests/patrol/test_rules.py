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


def test_field_없는_range_max_freshness는_KnownRuleError():
    # exists만 field 부재를 "데이터 전체를 본다"는 뜻으로 허용한다 — range/max/
    # freshness는 field 없이는 애초에 무엇을 검사할지 정할 수 없는 설정 결함이다.
    for params in ({"rule": "range", "min": 0, "max": 100},
                   {"rule": "max", "max": 1000},
                   {"rule": "freshness", "max_age_s": 60}):
        with pytest.raises(KnownRuleError):
            judge_by_rule(_ok({"lag": 5, "ts": T.isoformat()}), params, clock=lambda: T)


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


def _zero(data, **params):
    return judge_by_rule(_ok({"body": data}), {"rule": "all_zero", **params}, clock=lambda: T)


def test_all_zero는_전부_0일_때만_finding이다():
    # exists로는 안 된다(값이 있으니 통과), max로도 안 된다(0은 임계를 안 넘는다),
    # range(min=1)은 "하나라도 0이면"이라 야간에 한 라인만 쉬어도 울린다.
    assert _zero({"badge": [0, 0, 0]}, field="body.badge").status == "finding"
    assert _zero({"badge": [0, 3, 0]}, field="body.badge").status == "ok"
    assert _zero({"badge": 0}, field="body.badge").status == "finding"          # 스칼라
    assert _zero({"badge": {"a": 0, "b": 0.0}}, field="body.badge").status == "finding"  # dict
    assert _zero({"badge": {"a": 0, "b": 2}}, field="body.badge").status == "ok"


def test_빈_표본은_전부_0과_다른_사유다():
    # "질문을 잘못했다"와 "현장이 멈췄다"를 한 통에 넣으면 구별할 수 없게 된다 —
    # 계획 9가 전부-또는-전무로 막으려던 바로 그 혼동이다.
    v = _zero({"badge": []}, field="body.badge")
    assert v.status == "finding" and "표본" in v.reason
    assert "모두 0이다" not in v.reason      # 전부 0이라고 **단정하지** 않는다


def test_min_count_미만이면_전부_0을_단정하지_않는다():
    # 라인 30개 중 2개만 돌아온 표본으로 "현장이 멈췄다"를 말할 수 없다.
    v = _zero({"badge": [0, 0]}, field="body.badge", min_count=3)
    assert v.status == "finding" and "표본" in v.reason


def test_비수치가_섞이면_데이터_이상이지_설정_오류가_아니다():
    # 값이 이상한 것과 설정이 잘못된 것은 다른 문제다(규율 1).
    v = _zero({"badge": [0, "없음", 0]}, field="body.badge")
    assert v.status == "finding" and "수치" in v.reason


def test_bool은_0이_아니다():
    # 파이썬에서 False == 0이다. 그대로 두면 {"ok": False}가 "현장이 멈췄다"가 된다.
    v = _zero({"badge": [False, False]}, field="body.badge")
    assert v.status == "finding" and "수치" in v.reason


def test_NaN은_0도_수치도_아니다():
    v = _zero({"badge": [0, float("nan")]}, field="body.badge")
    assert v.status == "finding" and "수치" in v.reason


def test_all_zero의_설정_오류는_KnownRuleError다():
    with pytest.raises(KnownRuleError):
        _zero({"badge": [0]})                                   # field 부재
    with pytest.raises(KnownRuleError):
        _zero({"badge": [0]}, field="body.badge", min_count="셋")
    with pytest.raises(KnownRuleError):
        _zero({"badge": [0]}, field="body.badge", min_count=0)   # 0개로는 판정 불가


def test_필드가_없으면_데이터_이상이다():
    v = _zero({}, field="body.badge")
    assert v.status == "finding" and "부재" in v.reason
