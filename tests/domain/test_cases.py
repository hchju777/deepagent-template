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
