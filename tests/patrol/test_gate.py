from datetime import datetime, timezone

from src.domain.cases import InMemoryCaseRepository
from src.domain.patrol import Finding
from src.domain.store import InMemoryCaseStore
from src.patrol.gate import admit_finding

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _finding(store, summary="OEE 512%"):
    snap = store.put_evidence("patrol:mx:gumi:api.oee", "rest:/oee", {"oee": 512}, as_of=T)
    return Finding(id=f"api.oee@{T.isoformat()}", gbm="mx", fct="gumi", check="api.oee",
                   target="rest:/oee", summary=summary, evidence_ids=[snap],
                   scratch_case_id="patrol:mx:gumi:api.oee", observed_at=T, judge="rule")


def test_첫_finding은_케이스를_열고_스냅샷을_T0_증거로_복사한다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    result = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    assert result.action == "opened" and result.case.origin == "patrol"
    assert result.case.t0 == T and result.case.target_locator == "rest:/oee"
    assert store.list_evidence(result.case_id)[0].as_of == T       # 메타 보존 복사


def test_같은_지문의_열린_케이스에는_첨부한다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    first = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    second = admit_finding(_finding(store, "OEE 530%"), repo=repo, store=store, clock=lambda: T)
    assert second.action == "attached" and second.case_id == first.case_id
    assert len(repo.get(first.case_id).finding_ids) == 2
    assert len(store.list_evidence(first.case_id)) == 2


def test_인용_스냅샷이_없으면_기각():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    f = _finding(store).model_copy(update={"evidence_ids": ["ev-99"]})
    result = admit_finding(f, repo=repo, store=store, clock=lambda: T)
    assert result.action == "rejected" and repo.list_by_status("open") == []
