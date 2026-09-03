"""워커를 스크립트 LLM+스텁 어댑터+InMemorySaver로 결정론 검증한다."""
from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.application.lifecycle import ENGINE_SCHEMA_VERSION
from src.application.worker import CaseQueue, InvestigationWorker
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.patrol.ledger import InMemoryLedger
from tests.application.test_graph_e2e import (FRAME_ONE_TASK, INTEGRATE_CONCLUDE,
                                              VERDICT_JSON, make_e2e_deps)

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _open_case(repo, store, cid="c-1"):
    repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint="fp", symptom="OEE 512%",
                         t0=T, target_locator="rest:/oee", created_at=T, updated_at=T))
    store.put_evidence(cid, "rest:/oee", {"oee": 512}, as_of=T)


async def test_run_once는_lease로_조사하고_종결하며_verdict를_영속한다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {"topology": "d1"})
    assert await worker.run_once("c-1") == "closed"
    rec = repo.get("c-1")
    assert rec.status == "closed" and rec.owner is None and rec.thread_ids == ["c-1#1"]
    assert rec.thread_versions["c-1#1"] == ENGINE_SCHEMA_VERSION
    assert store.get_verdict("c-1") is not None and rec.verdict_summary


async def test_타인의_lease가_살아있으면_busy():
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    _open_case(repo, store)
    from src.application.lifecycle import acquire_lease
    repo.save(acquire_lease(repo.get("c-1"), "other", clock=lambda: T, ttl_s=600))
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=InMemoryLedger(), knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "busy"
    assert repo.get("c-1").status == "open"


async def test_재개_실패는_새_스레드로_한_번_재시작하고_또_실패하면_종결(monkeypatch):
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    import src.application.worker as wk
    attempts = []
    async def boom(*a, **k):
        attempts.append(k.get("thread_id"))
        raise RuntimeError("역직렬화 실패")
    monkeypatch.setattr(wk, "investigate_case", boom)
    assert await worker.run_once("c-1") == "failed"
    assert attempts == ["c-1#1", "c-1#2"]                       # 1회 재시작
    rec = repo.get("c-1")
    assert rec.status == "closed" and "재개 실패" in rec.closed_reason
    assert ledger.runs("mx", "gumi", "worker:c-1")[-1].status == "error"


async def test_그래프_밖_실패도_케이스를_고아로_두지_않는다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    def broken_deps(g, f):
        raise RuntimeError("deps 조립 실패")
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=broken_deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "failed"
    rec = repo.get("c-1")
    assert rec.status == "closed" and "워커 실패" in rec.closed_reason and rec.owner is None


async def test_requeue_open은_만료_lease의_investigating도_회수한다():
    from datetime import timedelta
    from src.application.lifecycle import acquire_lease, transition
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    _open_case(repo, store, "c-dead")
    dead = transition(repo.get("c-dead"), "investigating", clock=lambda: T)
    dead = acquire_lease(dead, "crashed-worker", clock=lambda: T - timedelta(hours=2), ttl_s=60)
    repo.save(dead)
    _open_case(repo, store, "c-live")
    live = transition(repo.get("c-live"), "investigating", clock=lambda: T)
    repo.save(acquire_lease(live, "busy-worker", clock=lambda: T, ttl_s=600))
    queue = CaseQueue()
    queue.requeue_open(repo, clock=lambda: T)
    n = queue.qsize()
    ids = sorted([await queue.get() for _ in range(n)])
    assert ids == ["c-dead"]                                   # 유효 lease는 회수 안 함


async def test_버전_불일치_재시작은_한_번뿐(monkeypatch):
    from src.application.lifecycle import acquire_lease, transition
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    rec = transition(repo.get("c-1"), "investigating", clock=lambda: T)
    rec = transition(rec, "awaiting_human", clock=lambda: T)
    repo.save(rec.model_copy(update={"thread_ids": ["c-1#1"], "thread_versions": {"c-1#1": 0}}))
    deps = make_e2e_deps(store, lead=[])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    import src.application.worker as wk
    attempts = []
    async def boom(*a, **k):
        attempts.append(k.get("thread_id"))
        raise RuntimeError("실패")
    monkeypatch.setattr(wk, "investigate_case", boom)
    assert await worker.resume_once("c-1", "답변") == "failed"
    assert attempts == ["c-1#2"]                               # 새 스레드 1회, 추가 재시도 없음
    assert repo.get("c-1").status == "closed"
