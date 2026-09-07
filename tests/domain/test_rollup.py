"""집계의 정직성은 문서가 아니라 타입이 강제한다(계획 16/P7, 방향 문서 §4.2).

"알람 12% 감소"가 실은 "3개 법인 데이터 누락"인 사고를 validator가 막는다.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.rollup import (FleetReport, InMemoryDigestStore, MetricRollup, SiteCoverage,
                               fold_complete)

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _rollup(**kw):
    base = dict(metric="alarms", value=12.0, reduce="sum", expected_sites=30,
                covered_sites=30, complete=True)
    base.update(kw)
    return MetricRollup(**base)


def test_누락이_있으면_complete가_강제로_False다():
    # 입력이 True여도 강제된다 — 호출부의 실수가 조용한 거짓말이 되면 안 된다.
    r = _rollup(covered_sites=27, complete=True, coverage_note="3개 사이트 미확인")
    assert r.complete is False


def test_불완전은_사유를_요구한다():
    with pytest.raises(ValidationError):
        _rollup(covered_sites=27, complete=False)
    assert _rollup(covered_sites=27, complete=False, coverage_note="3개 미확인").coverage_note


def test_커버가_0이면_값은_None이어야_한다():
    # 0이 아니다 — "아무 데서도 못 읽었다"와 "전부 0이었다"는 다른 주장이다.
    with pytest.raises(ValidationError):
        _rollup(covered_sites=0, value=0.0, coverage_note="전부 실패")
    ok = _rollup(covered_sites=0, value=None, coverage_note="전부 실패")
    assert ok.value is None and ok.complete is False


def test_표본에_불완전이_하나라도_있으면_전체가_불완전이다():
    assert fold_complete([True, True]) is True
    assert fold_complete([True, False, True]) is False
    assert fold_complete([]) is False          # 표본이 없으면 완전하다고 말할 수 없다


def test_미확인_사이트는_사유를_요구한다():
    assert SiteCoverage(gbm="mx", fct="gumi", status="covered").reason is None
    with pytest.raises(ValidationError):
        SiteCoverage(gbm="mx", fct="suwon", status="missing")
    with pytest.raises(ValidationError):
        SiteCoverage(gbm="mx", fct="gumi", status="fallback")
    assert SiteCoverage(gbm="mx", fct="suwon", status="missing",
                        reason="REST 타임아웃", last_success_at=T).reason


def test_실행_기록은_시나리오별로_최신순이다():
    store = InMemoryDigestStore()
    early = FleetReport(scenario="alarm", title="알람", concern="operation",
                        scenario_digest="d1", window_from=T, window_to=T, generated_at=T)
    store.put(early)
    store.put(early.model_copy(update={"scenario_digest": "d2",
                                       "generated_at": T.replace(hour=9)}))
    store.put(early.model_copy(update={"scenario": "other"}))
    assert [r.scenario_digest for r in store.list("alarm")] == ["d2", "d1"]
    assert store.latest("alarm").scenario_digest == "d2"
    assert store.latest("없음") is None


def test_커버가_기대보다_많아도_불완전이다():
    # 리뷰 low: `<`가 아니라 `!=`여야 한다 — 5/3은 정직한 상태가 아니다.
    r = _rollup(expected_sites=3, covered_sites=5, complete=True, coverage_note="중복 사이트")
    assert r.complete is False


def test_완전한데_값이_없으면_사유를_요구한다():
    # "완전"과 "—"가 나란히 서고 대시를 설명하는 문장이 없으면 읽는 사람이 못 읽는다.
    with pytest.raises(ValidationError):
        _rollup(value=None, coverage_note=None)
    assert _rollup(value=None, coverage_note="표본이 비었다").coverage_note
