"""워커를 스크립트 LLM+스텁 어댑터+InMemorySaver로 결정론 검증한다."""
from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.application.lifecycle import ENGINE_SCHEMA_VERSION
from src.application.worker import CaseQueue, InvestigationWorker
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.patrol.ledger import InMemoryLedger
from tests.application.test_graph_e2e import (ASK_JSON, FRAME_ONE_TASK, INTEGRATE_CONCLUDE,
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
