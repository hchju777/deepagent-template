from datetime import datetime, timedelta, timezone

import pytest

from src.domain.snapshot import InMemoryVerdictSnapshotStore, VerdictSnapshot

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _snap(case_id="c-1", **kw):
    base = dict(case_id=case_id, closed_at=T, gbm="mx", fct="gumi", fingerprint="fp",
                origin="patrol", outcome="closed", verdict_type="data_loss",
                root_cause_component="plan-sync", confidence="high", rounds=2,
                evidence_count=3, task_error_rate="0/2", verify_demoted=False,
                knowledge_digests={"topology": "d1"})
    base.update(kw)
    return VerdictSnapshot(**base)


def test_스냅샷은_케이스당_하나로_덮어쓴다():
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap(confidence="high"))
    store.put(_snap(confidence="low"))
    assert store.get("c-1").confidence == "low"
    assert store.get("없는-케이스") is None


def test_history_shown은_기본이_비어있고_기록할_수_있다():
    # P8의 이력 검색이 아직 없다. 필드를 지금 열어 두지 않으면 그 사이 종결된
    # 케이스는 "이력을 보여준 게 도움이 됐나"를 영영 답하지 못한다.
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap())
    assert store.get("c-1").history_shown == []
    store.put(_snap(history_shown=[{"case_id": "c-0", "tier": 1}]))
    assert store.get("c-1").history_shown[0]["tier"] == 1


def test_스냅샷_보존은_종결_시각으로_거른다():
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap("c-old", closed_at=T - timedelta(days=800)))
    store.put(_snap("c-new", closed_at=T))
    assert store.prune_before(T - timedelta(days=730)) == 1
    assert store.get("c-old") is None and store.get("c-new") is not None


def test_실패_종결도_스냅샷을_남길_수_있다():
    # 실패 종결을 빼면 분모에 생존 편향이 생긴다 — 잘 끝난 케이스만 세게 된다.
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap(outcome="failed", verdict_type=None, root_cause_component=None,
                    confidence=None))
    assert store.get("c-1").outcome == "failed"


def test_알_수_없는_필드는_거부한다():
    with pytest.raises(Exception):
        VerdictSnapshot(**{**_snap().model_dump(mode="json"), "새필드": 1})
