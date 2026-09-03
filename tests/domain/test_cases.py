from datetime import datetime

import pytest
from src.domain.cases import CaseRecord, InMemoryCaseRepository

T = datetime(2026, 9, 3, 8, 0)


def test_열린_케이스만_지문으로_찾는다():
    repo = InMemoryCaseRepository()
    cid = repo.new_case_id()
    assert cid == "c-1" and repo.new_case_id() == "c-2"
    repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint="fp-a",
                         symptom="OEE 512%", t0=T, created_at=T, updated_at=T))
    assert repo.find_open_by_fingerprint("fp-a").id == cid
    closed = repo.get(cid).model_copy(update={"status": "closed"})
    repo.save(closed)
    assert repo.find_open_by_fingerprint("fp-a") is None
    assert [r.id for r in repo.list_by_status("closed")] == [cid]
    with pytest.raises(KeyError):
        repo.get("c-9")


def test_list_open은_열린_상태_전부():
    repo = InMemoryCaseRepository()
    for i, status in enumerate(["open", "investigating", "awaiting_human", "closed"]):
        repo.save(CaseRecord(id=f"c-{i}", gbm="mx", fct="gumi", fingerprint=f"fp{i}",
                             symptom="s", t0=T, created_at=T, updated_at=T, status=status))
    assert sorted(r.id for r in repo.list_open()) == ["c-0", "c-1", "c-2"]
