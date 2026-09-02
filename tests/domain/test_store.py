import pytest
from src.domain.store import InMemoryCaseStore


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
