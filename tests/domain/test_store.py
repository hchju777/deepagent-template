from datetime import datetime, timezone

import pytest
from src.domain.store import InMemoryCaseStore
from src.knowledge.digest import canonical_digest


def test_증거는_케이스별_증가_id로_저장되고_조회된다():
    store = InMemoryCaseStore()
    e1 = store.put_evidence("c-1", "mongo:twin_state", {"oee": 5.12})
    e2 = store.put_evidence("c-1", "redis:plan:7", None)
    other = store.put_evidence("c-2", "rest:/oee", {"v": 1})
    assert (e1, e2, other) == ("ev-1", "ev-2", "ev-1")     # 케이스별 독립 증가
    assert store.get_evidence("c-1", "ev-1") == {"oee": 5.12}
    assert store.has_evidence("c-1", "ev-2") and not store.has_evidence("c-1", "ev-9")
    with pytest.raises(KeyError):
        store.get_evidence("c-1", "ev-9")


def test_코드_지식_캐시는_커밋_키로():
    store = InMemoryCaseStore()
    assert store.get_code_knowledge("twin-aggregator", "a3f9c2") is None
    store.put_code_knowledge("twin-aggregator", "a3f9c2", "OEE = output/planned_time")
    assert "planned_time" in store.get_code_knowledge("twin-aggregator", "a3f9c2")


def test_증거_레코드는_결과_봉투_메타를_왕복한다():
    store = InMemoryCaseStore()
    as_of = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
    eff = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)
    eid = store.put_evidence("c-1", "mongo:twin_state", {"oee": 5.12},
                             as_of=as_of, complete=False, effective_as_of=eff)
    record = store.get_evidence_record("c-1", eid)
    assert record.id == eid and record.source == "mongo:twin_state"
    assert record.as_of == as_of
    assert record.complete is False
    assert record.effective_as_of == eff
    assert record.body_digest == canonical_digest({"oee": 5.12})
    with pytest.raises(KeyError):
        store.get_evidence_record("c-1", "ev-9")


def test_봉투_메타_없이_호출하면_complete_기본값_True로_하위호환된다():
    store = InMemoryCaseStore()
    eid = store.put_evidence("c-1", "redis:plan:7", {"v": 1})   # 기존 호출부와 동일한 시그니처
    record = store.get_evidence_record("c-1", eid)
    assert record.complete is True
    assert record.as_of is None and record.effective_as_of is None


def test_list_evidence는_케이스별_생성_순으로_전_레코드를_반환한다():
    store = InMemoryCaseStore()
    store.put_evidence("c-1", "mongo:a", 1)
    store.put_evidence("c-1", "mongo:b", 2)
    store.put_evidence("c-2", "mongo:z", 9)          # 다른 케이스 — 섞이지 않는다
    records = store.list_evidence("c-1")
    assert [r.id for r in records] == ["ev-1", "ev-2"]
    assert [r.source for r in records] == ["mongo:a", "mongo:b"]
    assert store.list_evidence("c-no-such-case") == []   # 없는 케이스는 빈 리스트(부작용 없음)
