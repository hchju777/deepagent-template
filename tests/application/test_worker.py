import asyncio
from types import SimpleNamespace
"""워커를 스크립트 LLM+스텁 어댑터+InMemorySaver로 결정론 검증한다."""
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.application.lifecycle import ENGINE_SCHEMA_VERSION
from src.application.worker import CaseQueue, InvestigationWorker
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.patrol.ledger import InMemoryLedger
from tests.application.test_graph_e2e import (ASK_JSON, FRAME_ONE_TASK, INTEGRATE_CONCLUDE,
                                              VERDICT_JSON, make_e2e_deps)

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _open_case(repo, store, cid="c-1", concern="system"):
    repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint="fp", symptom="OEE 512%",
                         t0=T, target_locator="rest:/oee", concern=concern,
                         created_at=T, updated_at=T))
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
    # I3: 재시작 지점에서 "F3 재시작" 사유로 레저 이벤트가 남고, 폐기한 스레드는
    # thread_ids/thread_versions에서 바로 제거된다(TTL 스윕을 기다리지 않는다).
    restart_events = [o for o in ledger.runs("mx", "gumi", "worker:c-1")
                      if o.error and "F3 재시작" in o.error]
    assert len(restart_events) == 1
    assert "c-1#1" not in rec.thread_ids and "c-1#1" not in rec.thread_versions


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
    # I4: 버전 불일치 재시작도 새 스레드를 investigate_case로 여는 것이라 resume
    # 메커니즘이 없다 — 재시작 전에 답변을 evidence로 박제해뒀어야 한다.
    human_answers = [r for r in store.list_evidence("c-1") if r.source == "human:answer"]
    assert len(human_answers) == 1
    assert store.get_evidence("c-1", human_answers[0].id)["answer"] == "답변"


async def test_run_once는_read_modify_write로_동시_finding_첨부를_보존한다(monkeypatch):
    # I1: 엔진 호출(ainvoke)이 도는 동안 게이트가 같은 케이스에 finding을
    # 동시 첨부했다고 가정한다 — 워커가 호출 전 스냅샷을 그대로 wholesale
    # 저장하면 이 갱신을 잃는다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {"topology": "d1"})
    import src.application.worker as wk
    real_investigate_case = wk.investigate_case

    async def investigate_then_attach(*a, **k):
        result = await real_investigate_case(*a, **k)
        current = repo.get("c-1")                    # 게이트의 admit_finding과 동일한 패턴
        repo.save(current.model_copy(update={"finding_ids": current.finding_ids + ["f-concurrent"]}))
        return result

    monkeypatch.setattr(wk, "investigate_case", investigate_then_attach)
    assert await worker.run_once("c-1") == "closed"
    assert "f-concurrent" in repo.get("c-1").finding_ids


async def test_run_once_종결_후_case_file에_계획_5용_스냅샷이_남는다():
    # I6: 스레드 체크포인트는 TTL로 폐기될 수 있으므로, 계획 5가 읽을 보고서
    # 소스는 Store의 케이스 파일에 별도로 박제돼야 한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {"topology": "d1"})
    assert await worker.run_once("c-1") == "closed"
    case_file = store.get_case_file("c-1")
    assert case_file is not None
    assert case_file["round"] >= 1
    assert case_file["plan_tasks"] and case_file["plan_tasks"][0]["id"] == "t-1"
    assert "hypotheses" in case_file and "qa_log" in case_file and "verify_problems" in case_file


async def test_run_once가_사람에게_묻고_멈추면_질문을_케이스에_저장한다():
    # I6: awaiting_human 전이 시 result["__interrupt__"][0].value["question"]을
    # 케이스 레코드에 박제한다 — case show가 나중에 그 질문을 보여줄 수 있게.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {"topology": "d1"})
    assert await worker.run_once("c-1") == "awaiting_human"
    rec = repo.get("c-1")
    assert rec.status == "awaiting_human"
    assert rec.question == "계획 변경이 있었나요?"


async def test_미등록_사이트는_케이스를_닫지_않고_skipped를_남긴다():
    # 트리아지: deps_for_site가 None을 돌려주면(daemon의 계약 — 사이트가
    # 레지스트리에 없다) F1(그래프 밖 실패)과 달리 케이스를 닫지 않는다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "skipped"
    rec = repo.get("c-1")
    # deps 확인이 스레드 등록·lease 저장보다 먼저라 아무것도 안 건드린 채
    # 그대로 open으로 남는다 — 다음 requeue_open이 다시 집어준다.
    assert rec.status == "open" and rec.owner is None and rec.thread_ids == []
    outcome = ledger.last_run("mx", "gumi", "worker:c-1")
    assert outcome.status == "skipped" and "미등록 사이트" in outcome.skipped_reason


async def test_미등록_사이트는_investigating_이벤트를_내지_않는다():
    # I2: run_once/resume_once가 _emit_status(..., "investigating")를 repo.save
    # 전에 부르면, 미등록 사이트라 저장 없이 "skipped"로 빠질 때도 이벤트는
    # 이미 나가버려 UI가 유령 investigating을 영원히 들고 있게 된다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    seen = []
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 on_event=seen.append)
    assert await worker.run_once("c-1") == "skipped"
    assert [e for e in seen if e.event == "case_status_changed"] == []
    assert repo.get("c-1").status == "open"


async def test_resume_once의_미등록_사이트도_investigating_이벤트를_내지_않는다():
    # I2: resume_once도 같은 문제 — transition()은 순수 함수(저장 없음)라, 이걸
    # 부른 직후 emit하면 저장이 안 된(skip으로 빠지는) 케이스에도 이벤트가 난다.
    from src.application.lifecycle import transition
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    rec = transition(repo.get("c-1"), "investigating", clock=lambda: T)
    rec = transition(rec, "awaiting_human", clock=lambda: T)
    repo.save(rec)
    seen = []
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 on_event=seen.append)
    assert await worker.resume_once("c-1", "답변") == "skipped"
    assert [e for e in seen if e.event == "case_status_changed"] == []
    assert repo.get("c-1").status == "awaiting_human"


async def test_run_once는_엔진_호출_동안_lease를_keepalive로_갱신한다(monkeypatch):
    # I5: 엔진 호출이 lease_ttl_s보다 오래 걸리면(느린 조사) lease가 만료돼
    # requeue_open이 같은 케이스를 다른 워커에 또 내줄 수 있다 — keepalive가
    # 실시간으로 lease_until을 계속 밀어야 한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[])
    clock = lambda: datetime.now(timezone.utc)   # keepalive는 실제 경과 시간을 봐야 한다
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=clock, owner="w-1", max_concurrent=1, lease_ttl_s=0.06,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    import asyncio

    import src.application.worker as wk
    seen: dict[str, object] = {}

    async def slow_investigate(*a, **k):
        await asyncio.sleep(0.03)
        seen["first"] = repo.get("c-1").lease_until
        await asyncio.sleep(0.03)
        seen["second"] = repo.get("c-1").lease_until
        raise RuntimeError("의도된 실패 — keepalive만 관찰하면 된다")

    monkeypatch.setattr(wk, "investigate_case", slow_investigate)
    await worker.run_once("c-1")
    assert seen["first"] is not None and seen["second"] is not None
    assert seen["second"] > seen["first"]                      # keepalive가 그 사이 갱신했다


async def test_워커는_상태_전이_이벤트를_낸다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    seen = []
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 on_event=seen.append)
    assert await worker.run_once("c-1") == "closed"
    statuses = [e.data["status"] for e in seen if e.event == "case_status_changed"]
    assert statuses[0] == "investigating" and statuses[-1] == "closed"


async def test_실패_종결도_closed_이벤트를_낸다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    def broken_deps(g, f):
        raise RuntimeError("deps 조립 실패")
    seen = []
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=broken_deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 on_event=seen.append)
    assert await worker.run_once("c-1") == "failed"
    closed = [e for e in seen if e.event == "case_status_changed" and e.data["status"] == "closed"]
    assert len(closed) == 1 and closed[0].case_id == "c-1"


async def test_워커가_주입한_시계로_이벤트_시각이_찍힌다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    seen = []
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 on_event=seen.append)
    assert await worker.run_once("c-1") == "closed"
    assert seen and all(e.at == T for e in seen)      # 스트리밍 이벤트 포함 전부 주입 시계


async def test_resume의_스레드_재시작은_레코드의_interaction_policy를_유지한다(monkeypatch):
    # resume_once에는 interaction_policy 파라미터가 아예 없어서 두 재시작 경로가
    # 모두 기본값 "autonomous"로 떨어졌다 — interactive로 열린 chat 케이스가 재시작
    # 한 번에 조용히 강등돼 사람에게 묻기를 멈춘다. 정책은 호출자 인수가 아니라
    # 레코드에서 읽어야 한다(재개하는 프로세스가 케이스를 연 프로세스가 아니다).
    from src.application.lifecycle import transition
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    rec = transition(repo.get("c-1"), "investigating", clock=lambda: T)
    rec = transition(rec, "awaiting_human", clock=lambda: T)
    repo.save(rec.model_copy(update={"thread_ids": ["c-1#1"], "thread_versions": {"c-1#1": 0},
                                     "interaction_policy": "interactive"}))
    deps = make_e2e_deps(store, lead=[])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    import src.application.worker as wk
    seen = []
    async def boom(*a, **k):
        seen.append(k.get("interaction_policy"))
        raise RuntimeError("실패")
    monkeypatch.setattr(wk, "investigate_case", boom)
    assert await worker.resume_once("c-1", "답변") == "failed"
    assert seen == ["interactive"]


async def test_run_once는_interaction_policy를_레코드에_영속화한다():
    # 정책을 레코드에서 읽으려면 먼저 레코드에 들어가 있어야 한다 — chat이 넘긴
    # "interactive"가 영속되지 않으면 resume이 읽을 것이 기본값뿐이다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1", interaction_policy="interactive") == "closed"
    assert repo.get("c-1").interaction_policy == "interactive"


async def test_워커는_get_save가_아니라_claim으로_lease를_잡는다(monkeypatch):
    # get→save 사이에 남이 끼어드는 경합을 저장소가 한 동작으로 판정해야 한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    calls = []
    real_claim = repo.claim
    def spy_claim(case_id, owner, *, now, ttl_s):
        calls.append(owner)
        return real_claim(case_id, owner, now=now, ttl_s=ttl_s)
    monkeypatch.setattr(repo, "claim", spy_claim)

    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "closed"
    assert calls == ["w-1"]        # acquire_lease가 아니라 claim을 탔다


async def test_wall_clock_상한을_넘긴_조사는_실패로_종결된다(monkeypatch):
    # 멈춘 LLM 호출 하나가 lease와 동시 상한 슬롯을 영구 점유하는 것을 막는다.
    import asyncio as _asyncio
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 max_wall_clock_s=0.05)
    import src.application.worker as wk
    async def hang(*a, **k):
        await _asyncio.sleep(10)
    monkeypatch.setattr(wk, "investigate_case", hang)

    assert await worker.run_once("c-1") == "failed"
    rec = repo.get("c-1")
    assert rec.status == "closed" and rec.owner is None
    assert "TimeoutError" in (rec.closed_reason or "")


def test_케이스_파일_스냅샷은_모델이_아닌_값에도_견딘다():
    # 실패 종결 구제가 체크포인트에서 읽은 값을 그대로 먹인다 — 역직렬화 결과가
    # pydantic 모델이 아닐 수 있는데, model_dump를 무조건 부르면 AttributeError로 터진다.
    from src.application.worker import _case_file_snapshot
    snap = _case_file_snapshot({
        "plan_tasks": [{"id": "t1", "status": "ok"}],      # 모델이 아니라 dict
        "hypotheses": [], "round": 2, "qa_log": [], "verify_problems": [],
        "verify_attempts": 1})
    assert snap["plan_tasks"] == [{"id": "t1", "status": "ok"}]
    assert snap["round"] == 2 and snap["verify_attempts"] == 1


def test_케이스_파일_스냅샷은_빠진_키를_기본값으로_채운다():
    from src.application.worker import _case_file_snapshot
    snap = _case_file_snapshot({})
    assert snap["verify_attempts"] == 0 and snap["round"] == 0 and snap["plan_tasks"] == []


async def test_실패_종결도_조사_흔적을_케이스_파일로_남긴다(monkeypatch):
    # _fail은 put_case_file을 부르지 않고 close_case가 스레드를 지워, 실패 종결
    # 보고서의 §5가 통째로 "없음"이 됐다 — discard_threads=True와 겹쳐 완전 유실.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    # _finish에서 터뜨려 그래프는 정상 완주하되 종결만 실패시킨다 — 체크포인트에
    # 진짜 조사 흔적이 남은 상태에서 _fail이 도는 유일한 방법이다.
    async def boom(self, record, result):
        raise RuntimeError("종결 실패")
    monkeypatch.setattr(InvestigationWorker, "_finish", boom)

    assert await worker.run_once("c-1") == "failed"
    case_file = store.get_case_file("c-1")
    assert case_file is not None
    assert case_file["partial"] is True
    assert case_file["plan_tasks"], "체크포인트에서 태스크를 구제했어야 한다"
    assert repo.get("c-1").status == "closed"


async def test_구제가_불가능해도_케이스_파일에_사유가_남고_워커는_raise하지_않는다():
    # 엔진이 캐시에 없는 실패(build_engine 전에 터짐)는 읽을 체크포인트가 없다.
    # "조사한 게 없다"와 "흔적을 잃었다"를 보고서가 구별할 수 있어야 한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    def broken_deps(g, f):
        raise RuntimeError("deps 조립 실패")
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=broken_deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "failed"
    case_file = store.get_case_file("c-1")
    assert case_file["partial"] is True and case_file["salvage_error"]
    assert case_file["plan_tasks"] == []


async def test_정상_종결은_판정_스냅샷을_남긴다():
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store, concern="operation")
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {"topology": "d1"},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "closed"
    snap = snapshots.get("c-1")
    assert snap is not None and snap.outcome == "closed"
    assert snap.verdict_type and snap.root_cause_component
    assert snap.knowledge_digests == {"topology": "d1"}
    assert snap.history_shown == []          # P8 전까지는 비어 있다
    # 스냅샷은 retention(90일)보다 오래 살고 소급이 불가능하다 — concern이 없으면
    # 나중에 "operation 판정이 더 자주 틀리는가"를 물을 수 없다.
    assert snap.concern == "operation"


async def test_실패_종결도_판정_스냅샷을_남긴다():
    # 실패 종결을 빼면 분모에 생존 편향이 생긴다.
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    def broken_deps(g, f):
        raise RuntimeError("deps 조립 실패")
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=broken_deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "failed"
    snap = snapshots.get("c-1")
    assert snap is not None and snap.outcome == "failed" and snap.verdict_type is None


async def test_스냅샷_스토어가_없어도_종결은_그대로_된다():
    # snapshots=None인 호출부(옛 테스트·CLI 일부)가 깨지면 안 된다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "closed"


async def test_이미_닫힌_케이스는_다시_조사하지_않는다():
    # 주기 재큐 잡이 같은 케이스를 여러 번 넣을 수 있고, lease_is_free는 같은
    # owner의 재획득을 항상 허용한다(데몬 owner는 프로세스당 문자열 하나다).
    # 가드가 없으면 닫힌 케이스를 처음부터 다시 조사해 판정과 케이스 파일을 덮는다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON,
                                      FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="daemon-1", max_concurrent=2,
                                 lease_ttl_s=600, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "closed"
    verdict_before = store.get_verdict("c-1").verdict_type
    case_file_before = store.get_case_file("c-1")

    assert await worker.run_once("c-1") == "stale"        # 중복 항목은 조용히 흘린다
    assert store.get_verdict("c-1").verdict_type == verdict_before
    assert store.get_case_file("c-1") == case_file_before  # 흔적이 그대로다
    assert repo.get("c-1").thread_ids == ["c-1#1"]         # 새 스레드를 열지 않았다


async def test_구제가_아무것도_못_건지면_기존_케이스_파일을_지우지_않는다():
    # _finish가 완전한 케이스 파일을 쓴 뒤 종결 과정에서 터지면 _fail이 돈다.
    # 구제가 실패했다고 partial 빈 껍데기로 덮으면, 체크포인트가 TTL로 사라진 뒤
    # 케이스 파일이 유일한 조사 기록이라는 전제가 무너진다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    store.put_case_file("c-1", {"plan_tasks": [{"id": "t1", "status": "ok"}],
                                "hypotheses": [{"id": "h1"}], "round": 3})
    def broken_deps(g, f):
        raise RuntimeError("deps 조립 실패")   # 엔진 캐시 없음 → 구제 불가
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=broken_deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "failed"
    case_file = store.get_case_file("c-1")
    assert case_file["plan_tasks"] == [{"id": "t1", "status": "ok"}]   # 보존됐다
    assert case_file["round"] == 3


def test_케이스_파일이_지식_digest를_실어_나른다():
    # 보고서가 "그때 무엇을 보고 있었나"를 말하려면 State의 Case가 든 digest가
    # 케이스 파일까지 와야 한다. 체크포인트는 retention이 지우므로 여기 박제한다.
    from datetime import datetime, timezone
    from src.application.worker import _case_file_snapshot
    from src.domain.case import Case
    case = Case(id="c-1", gbm="mx", fct="gumi", symptom="s", origin="patrol",
                t0=datetime(2026, 9, 3, tzinfo=timezone.utc),
                knowledge_digests={"topology": "a" * 64, "target_api": "c" * 64})
    snapshot = _case_file_snapshot({"case": case, "plan_tasks": [], "hypotheses": []})
    assert snapshot["knowledge_digests"] == {"topology": "a" * 64, "target_api": "c" * 64}


def test_케이스가_없어도_스냅샷이_터지지_않는다():
    # _salvage_case_file은 체크포인트에서 부분 값을 긁어 오므로 case가 없을 수 있다.
    from src.application.worker import _case_file_snapshot
    assert _case_file_snapshot({})["knowledge_digests"] == {}


def test_구제된_케이스가_dict여도_digest를_살린다():
    # _dump_item의 docstring이 "구제한 값은 역직렬화 방식에 따라 dict일 수 있다"고
    # 적어 뒀다. getattr만 쓰면 그때 digest가 조용히 사라져 보고서가 "없음"을 찍는다.
    # LANGGRAPH_STRICT_MSGPACK=true가 실제로 그 모양을 만든다.
    from src.application.worker import _case_file_snapshot
    snapshot = _case_file_snapshot({"case": {"knowledge_digests": {"target_api": "c" * 64}}})
    assert snapshot["knowledge_digests"] == {"target_api": "c" * 64}


def test_접수_중인_케이스는_requeue가_집지_않는다():
    # 계획 12의 F1 경합의 근본 원인 — 가드 셋으로 좁혔지만 repo.save에 CAS가 없어
    # 닫지 못했다. requeue가 접수 중인 케이스를 구별하면 워커가 붙을 경로 자체가 없다.
    repo = InMemoryCaseRepository()
    for cid, done in (("c-1", False), ("c-2", True)):
        repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                             t0=T, created_at=T, updated_at=T, intake_done=done))
    queue = CaseQueue()
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    assert queue._queue.get_nowait() == "c-2"


def test_실린_답이_있는_파킹_케이스를_requeue가_집는다():
    repo = InMemoryCaseRepository()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q",
                         question_kind="intake", pending_answer="답", answer_key="k"))
    repo.save(CaseRecord(id="c-2", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q"))
    queue = CaseQueue()
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    assert queue._queue.get_nowait() == "c-1"          # 답 없는 파킹은 여전히 대상이 아니다


async def test_워커가_실린_답을_소비해_접수를_이어간다():
    # 명령 채널도 answer_case를 거친다 — 분기는 한 곳이다. 접수 질문에 파킹된
    # 케이스로 확인한다(그래프 스레드가 필요 없어 채널 자체를 본다).
    from src.infrastructure.llm import ScriptedLLM
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="어느 라인?",
                         question_kind="intake", intake_done=False,
                         pending_answer="라인 7", answer_key="k-1"))
    deps = make_e2e_deps(store, lead=['{"target_locator": "rest:/oee", "missing": []}',
                                      FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    queue = CaseQueue()
    worker = InvestigationWorker(queue, repo=repo, store=store, deps_for_site=lambda g, f: deps,
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    queue.requeue_open(repo, clock=lambda: T)
    stop = asyncio.Event()

    async def _stop_soon():
        while repo.get("c-1").status != "closed":
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.gather(worker.run_forever(stop), asyncio.wait_for(_stop_soon(), 5))
    record = repo.get("c-1")
    assert record.status == "closed"
    assert record.pending_answer is None and record.answer_key == "k-1"   # 키는 남긴다
    assert record.target_locator == "rest:/oee"


async def test_소비는_지운_뒤_실행한다():
    # 지우기 전에 실행하면 실패 시 다음 requeue가 같은 답을 또 넣는다.
    seen = []
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q",
                         question_kind="investigation", pending_answer="답", answer_key="k-1"))
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})

    async def _spy(case_id, answer, *, expect_seq=None):
        seen.append((answer, repo.get(case_id).pending_answer))
        return "failed"

    worker.resume_once = _spy
    await worker.consume("c-1")
    assert seen == [("답", None)]                          # 실행 시점에 이미 지워져 있다


async def test_소비가_실패하면_답을_되돌린다():
    # 리뷰 S2-A/C/D: answer_case가 busy/skipped/not_ours를 돌려주면 pending은 이미
    # 지워졌고 증거도 없어 **답이 소실**되고 타임아웃까지 파킹된다. "human:answer
    # 증거로 박제돼 있어 잃지 않는다"는 거짓이었다 — 되돌린다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q",
                         question_kind="investigation", question_seq=1,
                         pending_answer="답", answer_key="k-1"))
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None,        # 미등록 사이트 → skipped
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    result = await worker.consume("c-1")
    assert result == "skipped"
    after = repo.get("c-1")
    assert after.pending_answer == "답" and after.status == "awaiting_human"   # 잃지 않았다


async def test_되돌릴_수_없으면_증거로_남긴다():
    # 되돌리기가 실패하는 유일한 경우는 그 사이 그래프가 새 질문으로 파킹한 것 —
    # 옛 답을 조용히 버리지 않고 human:answer_dropped로 남긴다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q",
                         question_kind="investigation", question_seq=1,
                         pending_answer="옛 답", answer_key="k-1"))

    class _Reparks:
        """소비 중 그래프가 새 질문으로 파킹하는 상황."""
        async def __call__(self, case_id, answer, *, expect_seq=None):
            repo.save(repo.get(case_id).model_copy(update={"question_seq": 2, "question": "새"}))
            return "busy"

    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: SimpleNamespace(topology=None),
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    worker.resume_once = _Reparks()
    await worker.consume("c-1")
    assert repo.get("c-1").pending_answer is None
    assert any(r.source == "human:answer_dropped" for r in store.list_evidence("c-1"))


async def test_소비는_raise하지_않는다():
    # 리뷰: repo.save 실패가 consume 밖으로 새어 run_forever 태스크에 삼켜졌다 —
    # 데몬은 안 죽지만 레저 흔적이 없다(규율 1).
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q",
                         question_kind="investigation", question_seq=1,
                         pending_answer="답", answer_key="k-1"))
    def boom(*a, **kw):
        raise RuntimeError("mongo down")
    repo.take_answer = boom
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.consume("c-1") == "failed"
    assert ledger.last_run("mx", "gumi", "worker:c-1").status == "error"


async def test_파킹마다_question_seq가_오른다():
    # attach_answer의 조건(question_seq > answered_seq)이 성립하려면 파킹이 세야 한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "awaiting_human"
    assert repo.get("c-1").question_seq == 1


async def test_소비는_첫_읽기가_터져도_raise하지_않는다():
    # 리뷰 C3: 첫 repo.get은 KeyError만 잡았다 — "mongo down"이면 그대로 raise되어
    # run_forever 태스크가 조용히 삼켰다(규율 1).
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()

    def boom(cid):
        raise RuntimeError("mongo down")
    repo.get = boom
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: None, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.consume("c-1") == "failed"


# ---- 검증 리뷰 2차(계획 13): 답 채널의 supersede는 lease 아래에서만 -----------------------
from tests.application.test_graph_e2e import INTEGRATE_CONCLUDE as _E2E_CONCLUDE  # noqa: E402
from tests.application.test_graph_e2e import _mongo_call as _e2e_mongo_call, _report as _e2e_report  # noqa: E402

_VERDICT_EV3 = ('{"verdict_type": "stale_data", "confidence": "high", "narrative": "n", '
                '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-3"]}}')


def _closing_worker(repo, store, deps_for_site=None):
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON, _E2E_CONCLUDE, _VERDICT_EV3],
                         subagent=[_e2e_mongo_call("twin_state"), _e2e_report(["ev-3"])])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    return InvestigationWorker(CaseQueue(), repo=repo, store=store,
                               deps_for_site=deps_for_site or (lambda g, f: deps),
                               checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                               max_concurrent=1, lease_ttl_s=60, ledger=InMemoryLedger(),
                               knowledge_digests_for_site=lambda g, f: {}), deps


async def test_재개는_실린_답을_lease_아래에서_가져가_증거로_남긴다():
    # 리뷰 L4: answer_case가 lease 없이 take하면 resume이 busy로 끝나도 API 답은 이미
    # 버려져 있다. 가져가는 자리는 claim 뒤 — 거기서는 attach가 거절되므로 본 것이 전부다.
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    _open_case(repo, store)
    worker, _ = _closing_worker(repo, store)
    assert await worker.run_once("c-1") == "awaiting_human"
    assert repo.attach_answer("c-1", answer="API의 Q1 답", key="k-1", now=T) == "accepted"
    assert await worker.resume_once("c-1", "CLI의 Q1 답") == "closed"
    after = repo.get("c-1")
    assert after.pending_answer is None and after.answered_seq == 1
    dropped = [r for r in store.list_evidence("c-1") if r.source == "human:answer_dropped"]
    assert len(dropped) == 1 and store.get_evidence("c-1", dropped[0].id)["reason"] == "superseded"


async def test_재개_중_창에_실리는_답은_busy로_거절된다():
    # 리뷰 M1: claim 뒤·save 앞(deps_for_site가 불리는 자리)에 API 답이 착지하면
    # 통째 덤프가 지우거나(b) 파킹을 넘어 살아남아 Q2에 소비됐다(a). lease가 살아
    # 있는 동안 attach는 문을 안 연다.
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    _open_case(repo, store)
    seen = []
    holder = {}

    def deps_for_site(g, f):
        seen.append(repo.attach_answer("c-1", answer="창 안의 답", key="k-w", now=T))
        return holder["deps"]

    worker, deps = _closing_worker(repo, store, deps_for_site)
    holder["deps"] = deps
    assert await worker.run_once("c-1") == "awaiting_human"
    assert await worker.resume_once("c-1", "CLI 답") == "closed"
    assert seen[-1] == "busy", seen
    after = repo.get("c-1")
    assert after.pending_answer is None and after.answer_key is None
    assert not any(r.source == "human:answer_dropped" for r in store.list_evidence("c-1"))


async def test_소비는_답_없는_파킹을_처음부터_재조사하지_않는다():
    # 리뷰 B1: 실린 답 때문에 큐에 들어간 항목이 (CLI가 답을 가져간 뒤) 소비되면
    # take가 None → run_once → awaiting_human을 "회수한 investigating"으로 보고
    # 새 스레드로 처음부터 조사했다. 답 없는 파킹은 재개할 재료가 없다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "awaiting_human"
    threads_before = list(repo.get("c-1").thread_ids)
    assert await worker.consume("c-1") == "stale"
    after = repo.get("c-1")
    assert after.status == "awaiting_human" and after.thread_ids == threads_before
    assert after.owner is None                                  # lease는 돌려줬다


async def test_재개가_stale이면_가져간_답을_증거로_남긴다():
    # 리뷰 L6: restore 분기가 busy/skipped/not_ours만 봐서 stale(그 사이 닫힘)이면
    # 가져간 답이 증거 없이 사라졌다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q",
                         question_kind="investigation", question_seq=1,
                         pending_answer="옛 답", answer_key="k-1"))

    async def closed_meanwhile(case_id, answer, *, expect_seq=None):
        repo.save(repo.get(case_id).model_copy(update={"status": "closed", "question": None}))
        return "stale"

    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: SimpleNamespace(topology=None),
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    worker.resume_once = closed_meanwhile
    assert await worker.consume("c-1") == "stale"
    assert any(r.source == "human:answer_dropped" for r in store.list_evidence("c-1"))


def test_lease_is_held는_만료_순간까지_쥔_것으로_본다():
    # 리뷰 L-c: 경계(>= vs >)와 lease_until 없는 owner — 둘 다 lease_is_free와 정합해야
    # 한다(그쪽은 lease_until < now일 때만 남의 것을 뺏는다).
    from src.domain.cases import lease_is_held
    base = dict(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                created_at=T, updated_at=T)
    assert lease_is_held(CaseRecord(**base, owner="w-1", lease_until=T), now=T) is True
    assert lease_is_held(CaseRecord(**base, owner="w-1", lease_until=T - timedelta(seconds=1)),
                         now=T) is False
    assert lease_is_held(CaseRecord(**base, owner="w-1"), now=T) is True       # 만료 없음 = 영원
    assert lease_is_held(CaseRecord(**base), now=T) is False


# ---- 계획 13 인계 #12: 큐 중복 제거 -----------------------------------------------------
async def test_큐는_같은_케이스를_두_번_들고_있지_않는다():
    # 같은 id가 두 번 들어가면 첫 소비가 investigating(자기 lease)으로 도는 사이 둘째가
    # run_once→claim(같은 owner는 항상 재획득)→"회수한 investigating" 분기→새 스레드로
    # 처음부터 조사한다(3차 검증 리뷰 W3). 큐가 진행 중인 id를 기억해야 한다.
    queue = CaseQueue()
    await queue.put("c-1")
    await queue.put("c-1")
    assert queue.qsize() == 1
    assert await queue.get() == "c-1"
    await queue.put("c-1")                  # 아직 소비 중 — 안 들어간다
    assert queue.qsize() == 0
    queue.done("c-1")
    await queue.put("c-1")                  # 소비가 끝났으면 다시 들어간다
    assert queue.qsize() == 1


def test_requeue는_진행_중인_케이스를_다시_넣지_않는다():
    # requeue_job은 30초마다 돈다 — 슬롯 포화로 아직 큐에 있거나 소비 중인 케이스를
    # 매번 또 넣으면 위 재시작이 CLI 없이도 난다.
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    _open_case(repo, store)
    queue = CaseQueue()
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    assert queue.requeue_open(repo, clock=lambda: T) == 0
    assert queue.qsize() == 1


async def test_같은_케이스가_두_번_큐에_들어가도_조사는_한_번만_시작한다(monkeypatch):
    # W3 그대로: 슬롯이 둘이면 둘째 항목이 첫째가 investigating(자기 lease)으로 도는
    # 사이에 소비돼 새 스레드로 처음부터 조사한다 — 원래 스레드는 버려진다.
    import src.application.worker as wm
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    started, real = [], wm.investigate_case

    async def spy(case, **kw):
        started.append(kw.get("thread_id"))
        return await real(case, **kw)
    monkeypatch.setattr(wm, "investigate_case", spy)

    queue = CaseQueue()
    worker = InvestigationWorker(queue, repo=repo, store=store, deps_for_site=lambda g, f: deps,
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="daemon",
                                 max_concurrent=2, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    await queue.put("c-1")
    await queue.put("c-1")                  # requeue_job이 30초 뒤 또 넣은 것
    stop = asyncio.Event()

    async def _stop_soon():
        while repo.get("c-1").status != "closed":
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.gather(worker.run_forever(stop), asyncio.wait_for(_stop_soon(), 5))
    assert started == ["c-1#1"], started
    assert repo.get("c-1").thread_ids == ["c-1#1"]


async def test_소비가_끝난_케이스는_파킹_뒤_답이_실리면_다시_큐에_들어간다():
    # 중복 제거의 해제가 run_forever에 없으면 한 번 소비된 케이스는 이 프로세스에서
    # 영원히 큐에 못 들어간다 — 파킹 뒤 답이 실려도 requeue가 0을 낸다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    queue = CaseQueue()
    worker = InvestigationWorker(queue, repo=repo, store=store, deps_for_site=lambda g, f: deps,
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    stop = asyncio.Event()

    async def _stop_when_parked():
        while repo.get("c-1").status != "awaiting_human":
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.gather(worker.run_forever(stop), asyncio.wait_for(_stop_when_parked(), 5))
    assert repo.attach_answer("c-1", answer="답", key="k-1", now=T) == "accepted"
    assert queue.requeue_open(repo, clock=lambda: T) == 1


async def test_소비_중인_케이스는_저장_전_창에서도_다시_들어가지_않는다():
    # 리뷰 L-a: held의 "소비 중" 절반. 큐에서 나온 뒤 investigating으로 save되기 전
    # (deps_for_site가 불리는 자리 — DB는 아직 open) requeue_job이 뜨면 상태만 봐서는
    # 회수 대상이다. done을 소비 앞으로 옮기면 W3가 그대로 돌아온다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    queue = CaseQueue()
    seen = []

    def deps_for_site(g, f):
        seen.append((repo.get("c-1").status, queue.requeue_open(repo, clock=lambda: T)))
        return deps

    worker = InvestigationWorker(queue, repo=repo, store=store, deps_for_site=deps_for_site,
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="daemon",
                                 max_concurrent=2, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    stop = asyncio.Event()

    async def _stop_soon():
        while repo.get("c-1").status != "closed":
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.gather(worker.run_forever(stop), asyncio.wait_for(_stop_soon(), 5))
    assert seen == [("open", 0)], seen
    assert repo.get("c-1").thread_ids == ["c-1#1"]


async def test_stop과_get이_같이_끝나면_꺼낸_id를_놓아준다():
    # 리뷰 L-c: get과 stop.wait가 둘 다 대기 중일 때 put과 stop.set이 같은 스텝에서
    # 일어나면 asyncio.wait가 둘 다 완료로 돌려준다 — get은 이미 id를 꺼냈고 cancel은
    # no-op이라 큐에서는 빠졌는데 held에만 남았다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    queue = CaseQueue()
    worker = InvestigationWorker(queue, repo=repo, store=store, deps_for_site=lambda g, f: None,
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    stop = asyncio.Event()
    run = asyncio.ensure_future(worker.run_forever(stop))
    for _ in range(3):
        await asyncio.sleep(0)              # run_forever가 wait에 들어가게
    await queue.put("c-1")                  # put은 대기 없이 끝난다 — 같은 스텝에서
    stop.set()
    await asyncio.wait_for(run, 5)
    assert queue.qsize() == 0               # get이 꺼냈다(처리는 안 한다 — 새 프로세스가 회수)
    await queue.put("c-1")                  # 놓아줬으면 다시 들어간다
    assert queue.qsize() == 1


async def test_판정_스냅샷은_후보의_컴포넌트를_남긴다():
    # 계획 7이 열어 둔 자리(VerdictSnapshot.alternates) — retention이 Verdict를 지운 뒤에도
    # "후보에 정답이 있었나"를 라벨과 대조할 수 있어야 한다(P8 캘리브레이션).
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    verdict_json = ('{"verdict_type": "stale_data", "confidence": "high", "narrative": "n", '
                    '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-2"]}, '
                    '"alternates": [{"component": "twin-state", "evidence_ids": ["ev-2"], "confidence": "low"}]}')
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, verdict_json])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "closed"
    assert snapshots.get("c-1").alternates == ["twin-state"]


# ---- 계획 15(P8): 관측성 ------------------------------------------------------------------
def _ticks(*values):
    it = iter(values)
    return lambda: next(it)                 # 더 불리면 StopIteration — 호출 횟수까지 고정된다


async def test_조사는_경과를_재서_케이스_파일과_스냅샷과_sink에_남긴다():
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots, ticker=_ticks(100.0, 103.5))
    assert await worker.run_once("c-1") == "closed"
    assert store.get_case_file("c-1")["duration_s"] == 3.5
    assert snapshots.get("c-1").duration_s == 3.5
    rows = ledger.metrics("investigation.duration_s")
    assert [r["value"] for r in rows] == [3.5]
    assert rows[0]["tags"] == {"gbm": "mx", "fct": "gumi", "outcome": "closed"}
    assert rows[0]["at"] == T


async def test_ticker가_없으면_경과는_미측정이다():
    # 0으로 적으면 나중에 분모가 거짓이 된다 — 안 잰 것은 안 쟀다고 말한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "closed"
    assert store.get_case_file("c-1").get("duration_s") is None
    assert ledger.metrics("investigation.duration_s") == []


async def test_실패_종결도_경과를_남긴다(monkeypatch):
    # 실패한 조사가 분모에서 빠지면 "느린 조사가 더 틀리나"에 생존 편향이 생긴다.
    import src.application.worker as wm
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)

    async def boom(*a, **k):
        raise RuntimeError("엔진 실패")
    monkeypatch.setattr(wm, "investigate_case", boom)
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: make_e2e_deps(store, lead=[]),
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots, ticker=_ticks(10.0, 11.25))
    assert await worker.run_once("c-1") == "failed"
    assert snapshots.get("c-1").duration_s == 1.25
    assert ledger.metrics("investigation.duration_s")[0]["tags"]["outcome"] == "failed"


async def test_메트릭_sink가_던져도_조사는_종결된다():
    # 관측성이 시스템을 더 나쁘게 만들면 안 된다(규율 1).
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()

    class _BrokenSink(InMemoryLedger):
        def record_metric(self, *a, **k):
            raise RuntimeError("sink down")
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=_BrokenSink(), knowledge_digests_for_site=lambda g, f: {},
                                 ticker=_ticks(1.0, 2.0))
    assert await worker.run_once("c-1") == "closed"


async def test_조사는_과거_케이스를_리드에게_먹이고_무엇을_먹였는지_남긴다():
    # 계획 15: history_shown을 안 남기면 "이력이 도움이 됐나, 앵커링이었나"를 영원히 못 묻는다.
    from src.domain.snapshot import InMemoryVerdictSnapshotStore, VerdictSnapshot
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    now = repo.get("c-1")
    repo.save(now.model_copy(update={"target_locator": "rest:/oee"}))
    past = now.model_copy(update={"id": "c-old", "status": "closed", "status_since": T,
                                  "target_locator": "rest:/oee", "verdict_summary": "plan-sync가 멈췄다",
                                  "closed_reason": "조사 완료"})
    repo.save(past)
    snapshots.put(VerdictSnapshot(case_id="c-old", closed_at=T, gbm="mx", fct="gumi",
                                  fingerprint=past.fingerprint, outcome="closed",
                                  verdict_type="stale_data", root_cause_component="plan-sync"))
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "closed"
    assert "c-old" in str(deps.lead_llm.calls[0])            # 리드가 실제로 봤다
    assert snapshots.get("c-1").history_shown == [{"case_id": "c-old", "tier": 1}]


async def test_이력이_없으면_보여준_것도_없다():
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "closed"
    assert snapshots.get("c-1").history_shown == []


async def test_경과는_파킹과_재개를_가로질러_합산된다():
    # 리뷰 돌연변이 #13: 누적을 대입으로 바꿔도 초록이었다.
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON, INTEGRATE_CONCLUDE, VERDICT_JSON])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots, ticker=_ticks(0.0, 1.0, 10.0, 12.5))
    assert await worker.run_once("c-1") == "awaiting_human"
    assert await worker.resume_once("c-1", "없다") == "closed"
    assert snapshots.get("c-1").duration_s == 3.5           # 1.0 + 2.5


async def test_메트릭은_스냅샷_저장소가_없어도_남는다():
    # 리뷰 돌연변이 #12: 커밋이 "가드보다 앞에 둔다"고 주장한 성질에 테스트가 없었다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=None, ticker=_ticks(1.0, 3.0))
    assert await worker.run_once("c-1") == "closed"
    assert [r["value"] for r in ledger.metrics("investigation.duration_s")] == [2.0]


async def test_이력_조회_실패는_리드의_브리핑에_보인다():
    # 리뷰 M4 + 돌연변이 R5·R6: 코드를 고쳐도 배선 테스트가 없으면 조용히 되돌아간다.
    # "이력이 없다"와 "이력을 못 읽었다"는 다른 말이다(조용한 생략 금지).
    class _Broken(InMemoryCaseRepository):
        def closed_by_fingerprint(self, *a, **k):
            raise RuntimeError("mongo down")
    repo, store, ledger = _Broken(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "closed"
    prompt = str(deps.lead_llm.calls[0])
    assert "이력 조회 실패" in prompt and "RuntimeError" in prompt


async def test_이력을_못_읽은_케이스는_스냅샷에_그_사실이_남는다():
    # 재검증 low: 이력이 **비었던** 케이스와 **못 읽은** 케이스를 나중에 구별하려면
    # 지금 남겨야 한다(지금은 공짜, 나중엔 복구 불가).
    from src.domain.snapshot import InMemoryVerdictSnapshotStore

    class _Broken(InMemoryCaseRepository):
        def closed_by_fingerprint(self, *a, **k):
            raise RuntimeError("mongo down")
    repo, store, ledger = _Broken(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "closed"
    snap = snapshots.get("c-1")
    assert snap.history_shown == [] and "RuntimeError" in (snap.history_error or "")


async def test_재개는_lease를_잡은_뒤_질문_번호를_대조한다():
    # 검증 리뷰 M-1: 대조가 lease 밖 사전검사면 그 사이가 통째로 창이다. 계획이 지정한
    # 자리는 claim 뒤다 — "판정과 쓰기는 한 동작이다"의 재개 판이다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    worker, _ = _closing_worker(repo, store)
    assert await worker.run_once("c-1") == "awaiting_human"
    parked = repo.get("c-1")
    assert parked.question_seq == 1
    assert await worker.resume_once("c-1", "옛 답", expect_seq=99) == "stale_question"
    after = repo.get("c-1")
    assert after.status == "awaiting_human" and after.owner is None    # lease를 돌려줬다
    assert await worker.resume_once("c-1", "맞는 답", expect_seq=1) == "closed"


async def test_대조는_실린_답을_가져가기_전에_한다():
    # 검증 리뷰 N22: 순서가 뒤집히면 지나간 직접 답이 seq 검증을 통과한 실린 답을
    # 파괴한다(가져간 뒤 stale로 끝나므로 되돌릴 것도 없다).
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    worker, _ = _closing_worker(repo, store)
    assert await worker.run_once("c-1") == "awaiting_human"
    assert repo.attach_answer("c-1", answer="API의 답", key="k-1", now=T, expect_seq=1) == "accepted"
    assert await worker.resume_once("c-1", "지나간 직접 답", expect_seq=99) == "stale_question"
    assert repo.get("c-1").pending_answer == "API의 답"        # 파괴되지 않았다


async def test_소비가_stale_question이면_가져간_답을_되돌린다():
    # 검증 리뷰 N-3: 어휘를 넓혔으면 복구 목록도 같이 봐야 한다 — 안 그러면 가져간 답이
    # restore도 증거도 없이 증발한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="q",
                         question_kind="investigation", question_seq=1,
                         pending_answer="답", answer_key="k-1"))

    async def stale(case_id, answer, *, expect_seq=None):
        return "stale_question"

    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: SimpleNamespace(topology=None),
                                 checkpointer=InMemorySaver(), clock=lambda: T, owner="w-1",
                                 max_concurrent=1, lease_ttl_s=60, ledger=ledger,
                                 knowledge_digests_for_site=lambda g, f: {})
    worker.resume_once = stale
    assert await worker.consume("c-1") == "stale_question"
    assert repo.get("c-1").pending_answer == "답"           # 되돌렸다
