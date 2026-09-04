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


def test_verdict_저장과_케이스_정리():
    from datetime import datetime, timedelta
    from src.domain.case import CauseLink, Verdict
    store = InMemoryCaseStore()
    t = datetime(2026, 9, 3, 8, 0)
    store.put_evidence("c-1", "s", {"a": 1}, as_of=t)
    store.put_evidence("c-1", "s", {"a": 2}, as_of=t + timedelta(days=5))
    store.put_evidence("c-1", "s", {"a": 3})                       # as_of None → 유지
    store.put_evidence("patrol:mx:gumi:x", "s", {"p": 1}, as_of=t)
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]))
    store.put_verdict("c-1", v)
    assert store.get_verdict("c-1").root_cause.component == "plan-sync"
    assert store.get_verdict("c-9") is None
    assert store.purge_evidence_before("c-1", t + timedelta(days=1)) == 1
    assert [r.id for r in store.list_evidence("c-1")] == ["ev-2", "ev-3"]
    assert store.list_case_ids("patrol:") == ["patrol:mx:gumi:x"]
    assert store.purge_case("c-1") == 3 and store.list_evidence("c-1") == []   # 증거 2 + verdict 1


def test_이유_없는_불완전은_그_사실을_기록한다():
    # Envelope은 "complete=False면 이유 필수"를 검증자로 지키는데 EvidenceRecord는
    # 안 지켰다 — 증거 층에서 이유가 다시 조용히 사라질 수 있었다. 여기서 raise하면
    # 무raise 규율이 깨지므로(put_evidence는 프로브 안에서 불린다) 채워서 드러낸다.
    from src.domain.store import EvidenceRecord
    rec = EvidenceRecord(id="ev-1", source="s", body_digest="d", complete=False)
    assert rec.truncated_reason and "미기재" in rec.truncated_reason
