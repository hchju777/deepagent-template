from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.label import InMemoryLabelStore, RootCauseLabel

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _label(**kw):
    base = dict(case_id="c-1", agreement="wrong", labeled_at=T)
    base.update(kw)
    return RootCauseLabel(**base)


def test_라벨은_덮어쓰지_않고_쌓인다():
    # 사람이 생각을 바꾼 사실 자체가 캘리브레이션의 재료다.
    store = InMemoryLabelStore()
    store.append(_label(agreement="correct"))
    store.append(_label(agreement="wrong", resolution="false_positive"))
    store.append(_label(case_id="c-2", agreement="unknown"))
    assert [l.agreement for l in store.list_for("c-1")] == ["correct", "wrong"]
    assert store.count() == 3 and store.labeled_case_ids() == {"c-1", "c-2"}
    assert store.list_for("없음") == []


def test_4분류_밖의_동의는_거부된다():
    # 자유 문자열이면 나중에 집계가 정규화기를 측정하게 된다.
    with pytest.raises(ValidationError):
        _label(agreement="아마도")
    with pytest.raises(ValidationError):
        _label(resolution="글쎄")


def test_앵커링_표시와_실제_원인은_선택이다():
    label = _label(saw_report=True, actual_root_cause_component="plan-sync",
                   actual_verdict_type="stale_data", labeled_by="hchju", resolution="fixed")
    assert label.saw_report and label.actual_root_cause_component == "plan-sync"
    assert _label().saw_report is False and _label().resolution is None
