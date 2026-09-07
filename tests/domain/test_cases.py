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


def test_claim은_남의_살아있는_lease를_뺏지_않는다():
    from datetime import timedelta

    from src.domain.cases import InMemoryCaseRepository
    repo = InMemoryCaseRepository()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None
    assert repo.claim("c-1", "w-2", now=T, ttl_s=60) is None          # 살아있는 남의 lease
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None      # 같은 owner는 갱신
    later = T + timedelta(seconds=120)
    assert repo.claim("c-1", "w-2", now=later, ttl_s=60) is not None  # 만료됐으면 회수


# ---- 계획 15(P8): 이력 조회 표면 -------------------------------------------------------
def _closed(repo, cid, *, fp="fp", locator=None, gbm="mx", fct="gumi", at=None):
    at = at or T
    repo.save(CaseRecord(id=cid, gbm=gbm, fct=fct, fingerprint=fp, symptom="s", t0=at,
                         created_at=at, updated_at=at, status_since=at, status="closed",
                         target_locator=locator, closed_reason="조사 완료"))


def test_지문으로_종결_케이스를_최신순으로_찾는다():
    from datetime import timedelta
    repo = InMemoryCaseRepository()
    _closed(repo, "c-1", fp="fp-a", at=T - timedelta(days=2))
    _closed(repo, "c-2", fp="fp-a", at=T)
    _closed(repo, "c-3", fp="fp-b")
    repo.save(CaseRecord(id="c-4", gbm="mx", fct="gumi", fingerprint="fp-a", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="investigating"))   # 안 닫혔다
    assert [r.id for r in repo.closed_by_fingerprint("fp-a", exclude_case_id="c-9")] == ["c-2", "c-1"]
    assert [r.id for r in repo.closed_by_fingerprint("fp-a", exclude_case_id="c-2")] == ["c-1"]
    assert repo.closed_by_fingerprint("fp-a", exclude_case_id="c-9", limit=1)[0].id == "c-2"
    assert repo.closed_by_fingerprint("없음", exclude_case_id="c-9") == []


def test_locator로_종결_케이스를_찾되_빈_목록은_전체를_긁지_않는다():
    from datetime import timedelta
    repo = InMemoryCaseRepository()
    _closed(repo, "c-1", locator="rest:/oee", at=T - timedelta(days=1))
    _closed(repo, "c-2", locator="mongo:twin_state", at=T)
    _closed(repo, "c-3", locator=None)
    assert [r.id for r in repo.closed_by_locators(["rest:/oee", "mongo:twin_state"],
                                                  exclude_case_id="c-9")] == ["c-2", "c-1"]
    assert repo.closed_by_locators([], exclude_case_id="c-9") == []      # 전체 조회로 번지지 않는다
    assert [r.id for r in repo.closed_by_locators(["rest:/oee"], exclude_case_id="c-1")] == []
