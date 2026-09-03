# 계획 4b: 큐·수명주기·영속 저장소·데몬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스펙 §1.1(수명주기·case:thread 1:N)·§1.3(케이스 큐·동시 상한)·§4.4(에이전트 저장소·보존)·§5.2-F2(owner/lease 단일 실행자)·§5.4-F3/F5(재개 정책·하트비트)·§5.3 CLI(`patrol`, `patrol status`, `case`) — 순찰(4a)이 연 케이스가 큐를 거쳐 워커에서 조사되고, 상태 전이·타임아웃 종결·재개 실패 복구가 코드로 강제되며, 모든 기록이 Mongo에 영속되는 데몬을 완성한다.

**Architecture:** 수명주기 전이와 lease는 순수 함수(application/lifecycle.py)로 두고, 워커가 그것을 호출해 케이스를 조사한다. 케이스 종결(close_case)은 타임아웃 스윕과 재개 실패 정책이 공유하는 단일 유스케이스. 영속층은 sync `MongoClient` 기반 Store/Repo/Ledger(포트가 sync라 그대로) + `MongoDBSaver` 체크포인터(langgraph-checkpoint-mongodb 0.4.0은 sync `MongoClient` 기반이며 비동기 메서드는 executor로 제공 — `AsyncMongoDBSaver`는 존재하지 않는다, 실행 중 확인) — 두 클라이언트가 같은 인스턴스를 본다. 데몬은 4a의 `build_scheduler`에 `run_one`을 꽂고, 큐·워커·보존 스윕·자기 감시를 한 프로세스에서 돌린다.

**Tech Stack:** 계획 4a 위에 `langgraph-checkpoint-mongodb>=0.4`, `pymongo>=4.12,<4.17`(체크포인터 요구 핀 — 전작과 동일), dev: `mongomock>=4.1`.

**3b·4a 인계 노트 반영 (설계 구속):**
1. awaiting_human 타임아웃 종결 = **케이스 닫기 + 스레드 폐기 한 동작**(close_case).
2. `build_engine`은 워커에서 **한 번 컴파일해 캐시** — usecase에 `engine=` 주입 자리.
3. `knowledge_digests`(§2.5-3) 박제는 **데몬의 run_one이** 사이트별 digest(토폴로지·룰·deployment)로 채운다.
4. `on_missed` 훅은 fire-and-forget → 데몬은 **동기 콜백**(ledger.record_run은 sync)으로 등록해 완료를 보장한다.
5. 순찰 스크래치 케이스(`patrol:*`)는 닫히지 않으니 **보존 스윕이 명시적으로 정리**한다.
6. 기동 검증 10: `judge in (llm, rule+llm)` 점검이 있으면 `llm.profiles.judge`가 필요 — 4a의 "예산 선소비" 경로를 기동 시 봉쇄.
7. attached Finding의 진행 중 조사 알림은 스펙 요구 밖 — 워커는 조사 시작 시 `finding_ids`를 읽는 것으로 충분(문서화만).

## Global Constraints

- **전이는 코드만 쥔다**: 허용 전이 `open→investigating`, `investigating→awaiting_human|closed`, `awaiting_human→investigating|closed`, `open→closed`(기각). 그 외는 `LifecycleError`.
- **lease**: `owner`+`lease_until`. 만료되지 않은 타인 lease가 있으면 실행 불가(F2). lease TTL은 config `investigations.lease_ttl_s`(기본 900).
- **F3 재개 정책**: 스레드에 `schema_version` 스탬프(`ENGINE_SCHEMA_VERSION` 상수). 재개 시 버전 불일치 또는 `ainvoke` 예외 → 그 스레드 폐기(`adelete_thread`) + **같은 케이스에 새 스레드로 케이스 파일(Store 증거) 기반 재시작** 1회 + 레저 기록. 재시작도 실패하면 close_case(reason="재개 실패").
- **워커는 raise하지 않는다** — 케이스 단위로 실패를 레저에 남기고 lease를 해제한다. 노드·판정기 무raise 계약과 같은 이유.
- **시계 주입**, **한국어**, StrictModel, 기동 거부 철학, unknown key 거부.
- Mongo 구현은 mongomock으로 계약 테스트(실 인스턴스 통합 테스트는 YAGNI — 계획 2 판정 계승). 체크포인터 Mongo 구현은 지연 import + 생성만 스모크.
- 보고서 렌더링·이벤트 봉투·chat·메일은 계획 5.

## File Structure

```
requirements.txt / requirements-dev.txt          # 핀·의존성
src/config/schema_app.py                          # (수정) InvestigationsConfig.lease_ttl_s, StoreConfig.backend/mongo_url
src/domain/cases.py                               # (수정) closed_reason·thread_versions·verdict_summary, list_open, LifecycleError 없음(순수 함수는 application)
src/domain/store.py                               # (수정) put_verdict/get_verdict, purge_case, purge_evidence_before, list_case_ids
src/application/lifecycle.py                      # ENGINE_SCHEMA_VERSION, transition, acquire/release lease, is_timed_out
src/application/close.py                          # close_case, sweep_timeouts
src/application/usecase.py                        # (수정) engine= 주입
src/application/worker.py                         # CaseQueue, InvestigationWorker (run_once/resume_once/run_forever, F3)
src/infrastructure/mongo_store.py                 # MongoCaseStore, MongoCaseRepository, MongoLedger
src/infrastructure/checkpointer.py                # build_checkpointer(memory|mongo)
src/infrastructure/retention.py                   # sweep_retention
src/patrol/ledger.py                              # (수정) prune_runs_before, skipped 기록 헬퍼
src/patrol/selfcheck.py                           # 연속 error 자기 감시 → Finding
src/patrol/daemon.py                              # PatrolDaemon 조립·run
src/boot.py                                       # (수정) 검사 10
src/__main__.py                                   # (수정) patrol / patrol status / case list|show|resume
tests/application/test_lifecycle.py, test_close.py, test_worker.py
tests/infrastructure/test_mongo_store.py, test_checkpointer.py, test_retention.py
tests/patrol/test_selfcheck.py, test_daemon.py
tests/test_cli.py (추가)
```

---

### Task 1: 의존성·config·도메인 확장 + 수명주기 순수 함수

**Files:**
- Modify: `requirements.txt`, `requirements-dev.txt`, `src/config/schema_app.py`, `src/domain/cases.py`, `src/domain/store.py`
- Create: `src/application/lifecycle.py`
- Test: `tests/application/test_lifecycle.py`, `tests/domain/test_store.py`(추가), `tests/domain/test_cases.py`(추가)

**Interfaces:**
- `requirements.txt`: `pymongo>=4.12,<4.17` (주석: langgraph-checkpoint-mongodb 요구 상한), `langgraph-checkpoint-mongodb>=0.4,<1`. `requirements-dev.txt`: `mongomock>=4.1,<5`.
- `InvestigationsConfig.lease_ttl_s: int = 900`. `StoreConfig.backend: Literal["memory","mongo"] = "memory"`, `StoreConfig.mongo_url: str | None = None`(`${AGENT_MONGO_URL}` 참조, backend=mongo면 필수 — 검증자).
- `CaseRecord` 추가: `closed_reason: str | None = None`, `thread_versions: dict[str, int] = {}`(thread_id→schema_version), `verdict_summary: str | None = None`. `CaseRepositoryPort.list_open() -> list[CaseRecord]`(OPEN_STATUSES 전부) + InMemory.
- `CaseStorePort` 추가: `put_verdict(case_id, verdict: Verdict) -> None`, `get_verdict(case_id) -> Verdict | None`, `purge_case(case_id) -> int`(삭제 건수), `purge_evidence_before(case_id, before: datetime) -> int`(as_of 기준, as_of None은 유지), `list_case_ids(prefix: str = "") -> list[str]`(정렬). InMemory 구현.
- `src/application/lifecycle.py`:
  - `ENGINE_SCHEMA_VERSION = 1`
  - `class LifecycleError(Exception)`
  - `ALLOWED = {"open": {"investigating","closed"}, "investigating": {"awaiting_human","closed"}, "awaiting_human": {"investigating","closed"}, "closed": set()}`
  - `transition(record, to, *, clock, reason=None) -> CaseRecord` — 허용 외 LifecycleError; `updated_at=clock()`; to=="closed"면 `closed_reason=reason`, lease 해제(owner/lease_until None).
  - `acquire_lease(record, owner, *, clock, ttl_s) -> CaseRecord | None` — `record.owner`가 None이거나 == owner이거나 `lease_until < clock()`(만료)이면 획득(owner·lease_until=clock()+ttl). 아니면 None.
  - `release_lease(record, owner, *, clock) -> CaseRecord` — 같은 owner만 해제(아니면 LifecycleError).
  - `is_timed_out(record, *, clock, timeout_h) -> bool` — status=="awaiting_human" and `clock() - updated_at > timeout_h`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_lifecycle.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest
from src.application.lifecycle import (ENGINE_SCHEMA_VERSION, LifecycleError, acquire_lease,
                                       is_timed_out, release_lease, transition)
from src.domain.cases import CaseRecord

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _rec(**kw):
    base = dict(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                created_at=T, updated_at=T)
    base.update(kw)
    return CaseRecord(**base)


def test_허용_전이만_통과하고_종결은_사유와_lease_해제를_남긴다():
    r = transition(_rec(), "investigating", clock=lambda: T)
    assert r.status == "investigating"
    r = transition(r, "awaiting_human", clock=lambda: T + timedelta(minutes=1))
    assert r.updated_at == T + timedelta(minutes=1)
    r = acquire_lease(r, "worker-a", clock=lambda: T, ttl_s=60)
    closed = transition(r, "closed", clock=lambda: T, reason="타임아웃")
    assert closed.closed_reason == "타임아웃" and closed.owner is None and closed.lease_until is None
    with pytest.raises(LifecycleError):
        transition(closed, "investigating", clock=lambda: T)
    with pytest.raises(LifecycleError):
        transition(_rec(), "awaiting_human", clock=lambda: T)     # open→awaiting 금지


def test_lease는_타인의_유효_lease를_존중하고_만료면_빼앗는다():
    r = acquire_lease(_rec(), "a", clock=lambda: T, ttl_s=60)
    assert r.owner == "a" and r.lease_until == T + timedelta(seconds=60)
    assert acquire_lease(r, "b", clock=lambda: T + timedelta(seconds=30), ttl_s=60) is None
    taken = acquire_lease(r, "b", clock=lambda: T + timedelta(seconds=61), ttl_s=60)
    assert taken.owner == "b"
    same = acquire_lease(r, "a", clock=lambda: T + timedelta(seconds=30), ttl_s=60)
    assert same.owner == "a" and same.lease_until == T + timedelta(seconds=90)   # 갱신
    with pytest.raises(LifecycleError):
        release_lease(r, "b", clock=lambda: T)
    assert release_lease(r, "a", clock=lambda: T).owner is None


def test_타임아웃은_awaiting_human에만_적용():
    waiting = transition(transition(_rec(), "investigating", clock=lambda: T),
                         "awaiting_human", clock=lambda: T)
    assert not is_timed_out(waiting, clock=lambda: T + timedelta(hours=71), timeout_h=72)
    assert is_timed_out(waiting, clock=lambda: T + timedelta(hours=73), timeout_h=72)
    assert not is_timed_out(_rec(), clock=lambda: T + timedelta(hours=100), timeout_h=72)
    assert ENGINE_SCHEMA_VERSION == 1
```

`tests/domain/test_store.py`에 추가:
```python
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
```

`tests/domain/test_cases.py`에 추가:
```python
def test_list_open은_열린_상태_전부():
    repo = InMemoryCaseRepository()
    for i, status in enumerate(["open", "investigating", "awaiting_human", "closed"]):
        repo.save(CaseRecord(id=f"c-{i}", gbm="mx", fct="gumi", fingerprint=f"fp{i}",
                             symptom="s", t0=T, created_at=T, updated_at=T, status=status))
    assert sorted(r.id for r in repo.list_open()) == ["c-0", "c-1", "c-2"]
```

- [ ] **Step 2: FAIL 확인** → **Step 3: 구현** → **Step 4: 전체 PASS 후 커밋**

```bash
git add requirements.txt requirements-dev.txt src/config/schema_app.py src/domain/cases.py src/domain/store.py src/application/lifecycle.py tests/application/test_lifecycle.py tests/domain/test_store.py tests/domain/test_cases.py
git commit -m "Add case lifecycle transitions, leases, verdict storage, and the Mongo pins"
```

---

### Task 2: close_case + 타임아웃 스윕 + usecase engine 주입

**Files:**
- Create: `src/application/close.py`
- Modify: `src/application/usecase.py`
- Test: `tests/application/test_close.py`, `tests/application/test_usecase.py`(추가)

**Interfaces:**
- `async close_case(case_id, *, repo, checkpointer, clock, reason, discard_threads: bool) -> CaseRecord` — `transition(..., "closed", reason)` 저장 후, `discard_threads=True`면 `record.thread_ids` 각각 `await checkpointer.adelete_thread(tid)`(checkpointer None이면 건너뜀; 개별 실패는 삼키지 않고 `reason`에 덧붙여 기록 — 스레드 폐기 실패가 종결을 막지는 않음). 한 동작(§1.1). **정상 완료 종결은 `discard_threads=False`** — 스레드는 Time Travel·디버깅용으로 남기고 보존 스윕(`checkpoint_ttl_d`)이 지운다. 타임아웃·F3 재시작 실패는 True.
- `async sweep_timeouts(*, repo, checkpointer, clock, timeout_h) -> list[str]` — `repo.list_open()` 중 `is_timed_out`인 것을 `close_case(reason="awaiting_human 타임아웃 — 미해결 종결")`. 닫은 id 목록 반환.
- `investigate_case(..., engine=None)` / `resume_case(..., engine=None)` — 주어지면 `build_engine` 대신 그 컴파일 그래프 사용(인계 노트 2).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_close.py`

```python
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from src.application.close import close_case, sweep_timeouts
from src.application.lifecycle import transition
from src.domain.cases import CaseRecord, InMemoryCaseRepository

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _waiting(repo, cid, updated_at):
    r = CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint=cid, symptom="s", t0=T,
                   created_at=T, updated_at=T, thread_ids=[f"{cid}#1"],
                   thread_versions={f"{cid}#1": 1})
    r = transition(r, "investigating", clock=lambda: T)
    r = transition(r, "awaiting_human", clock=lambda: updated_at)
    repo.save(r)
    return r


async def test_close_case는_종결과_스레드_폐기를_한_동작으로():
    repo, saver = InMemoryCaseRepository(), InMemorySaver()
    _waiting(repo, "c-1", T)
    saver.put({"configurable": {"thread_id": "c-1#1", "checkpoint_ns": ""}},
              empty_checkpoint(), {"source": "input", "step": -1, "parents": {}}, {})
    closed = await close_case("c-1", repo=repo, checkpointer=saver, clock=lambda: T,
                              reason="테스트 종결", discard_threads=True)
    assert closed.status == "closed" and closed.closed_reason == "테스트 종결"
    assert saver.get({"configurable": {"thread_id": "c-1#1"}}) is None      # 스레드 폐기


async def test_sweep은_타임아웃된_awaiting만_닫는다():
    repo = InMemoryCaseRepository()
    _waiting(repo, "c-old", T - timedelta(hours=80))
    _waiting(repo, "c-new", T - timedelta(hours=1))
    closed = await sweep_timeouts(repo=repo, checkpointer=None, clock=lambda: T, timeout_h=72)
    assert closed == ["c-old"]
    assert repo.get("c-old").status == "closed" and "타임아웃" in repo.get("c-old").closed_reason
    assert repo.get("c-new").status == "awaiting_human"
```

`tests/application/test_usecase.py`에 추가(그래프 없이 주입 자리만 검증):
```python
async def test_engine_주입이_build_engine을_우회한다(monkeypatch):
    import src.application.usecase as uc
    from src.domain.case import Case
    calls = []
    class FakeEngine:
        async def ainvoke(self, state, config=None):
            calls.append((state["case"].id, config["configurable"]["thread_id"]))
            return {"done": True}
    monkeypatch.setattr(uc, "build_engine", lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    from tests.application.test_nodes_frame import T, _deps
    case = Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T)
    out = await uc.investigate_case(case, deps=_deps([]), engine=FakeEngine(), thread_id="c-1#1")
    assert out == {"done": True} and calls == [("c-1", "c-1#1")]
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/application/close.py src/application/usecase.py tests/application/test_close.py tests/application/test_usecase.py
git commit -m "Close cases with their threads and let callers inject a compiled engine"
```

---

### Task 3: 케이스 큐 + 조사 워커 (F2·F3)

**Files:**
- Create: `src/application/worker.py`
- Test: `tests/application/test_worker.py`

**Interfaces:**
- `CaseQueue`: `asyncio.Queue[str]` 래퍼 — `put(case_id)`, `get()`, `qsize()`. (Mongo 영속 큐는 YAGNI — 재시작 시 워커가 `repo.list_by_status("open")`을 재큐잉하는 것으로 내구성 확보: `requeue_open()`.)
- `InvestigationWorker(queue, *, repo, store, deps_for_site: Callable[[str,str], EngineDeps], checkpointer, clock, owner: str, max_concurrent: int, lease_ttl_s: int, ledger, knowledge_digests_for_site: Callable[[str,str], dict[str,str]])`:
  - `engine`은 생성자에서 한 번 `build_engine`(사이트별 deps가 다르면 사이트 키로 캐시 dict).
  - `async run_once(case_id) -> str` — 결과 라벨 `"closed"|"awaiting_human"|"busy"|"failed"`:
    1. `record = repo.get`; `acquire_lease(record, owner)` None → `"busy"`(레저 이벤트 없음).
    2. `transition(→investigating)`, 새 `thread_id = f"{case_id}#{len(thread_ids)+1}"`, `thread_versions[thread_id] = ENGINE_SCHEMA_VERSION`, 저장.
    3. `case = record.to_case().model_copy(update={"knowledge_digests": digests})`, `initial_evidence = evidence_refs_for_case(store, case_id)`.
    4. `result = await investigate_case(case, deps=..., checkpointer=..., thread_id=..., engine=cached, initial_evidence=...)` — 예외 시 F3: 스레드 폐기 + 새 thread 재시작 1회; 재실패 → `close_case(reason="재개 실패 — ...")` → `"failed"`.
    5. `"__interrupt__" in result` → `transition(→awaiting_human)` → `"awaiting_human"`. 아니면 `verdict = result.get("verdict")` → `store.put_verdict`, `verdict_summary=verdict.narrative[:200]`, `close_case(reason="조사 완료", discard_threads=False)` → `"closed"`. (F3 재시작 실패·타임아웃 종결만 `discard_threads=True`.)
    6. `finally`: lease 해제(닫힌 케이스는 transition이 이미 해제).
  - `async resume_once(case_id, answer) -> str` — lease → 최신 thread_id의 `thread_versions` 확인: 버전 불일치면 F3 경로(스레드 폐기 + 새 스레드 재시작), 일치면 `resume_case(...)`; 이후 5와 동일.
  - `async run_forever(stop: asyncio.Event)` — `Semaphore(max_concurrent)`로 `run_once`를 동시 소비, stop 시 종료.
  - 모든 경로 최외곽 try/except → 레저에 `record_run(gbm, fct, f"worker:{case_id}", CheckOutcome(error...))` 형태로 남기고 `"failed"`(레저를 "케이스 워커 이벤트"에도 재사용).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_worker.py`

```python
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
```

`tests/application/test_graph_e2e.py`가 `make_e2e_deps`, `FRAME_ONE_TASK`, `INTEGRATE_CONCLUDE`, `VERDICT_JSON`을 모듈 수준으로 노출하도록 소폭 리팩터(기존 테스트 동작 불변) — 구현자 확인 후 필요한 이름으로 맞춘다(이름이 다르면 워커 테스트의 import를 실제 이름에 맞춰 수정하되 의미는 유지).

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/application/worker.py tests/application/test_worker.py tests/application/test_graph_e2e.py
git commit -m "Investigate queued cases under a lease with one-shot thread recovery"
```

---

### Task 4: Mongo 영속 저장소 (Store·Repo·Ledger) + 체크포인터 팩토리

**Files:**
- Create: `src/infrastructure/mongo_store.py`, `src/infrastructure/checkpointer.py`
- Modify: `src/patrol/ledger.py`(prune_runs_before)
- Test: `tests/infrastructure/test_mongo_store.py`, `tests/infrastructure/test_checkpointer.py`

**Interfaces:**
- `MongoCaseStore(db)`, `MongoCaseRepository(db)`, `MongoLedger(db)` — `db`는 pymongo `Database`(sync). 컬렉션: `evidence`(case_id, id, source, body, digest, as_of, complete, effective_as_of, seq), `code_knowledge`, `verdicts`, `cases`, `ledger_runs`(gbm,fct,check,outcome,at), `ledger_meta`(heartbeat). 증거 id는 `counters` 컬렉션 `find_one_and_update($inc)`로 케이스별 원자 증가. 포트 메서드 전부 구현(Task 1 확장 포함). Verdict/CheckOutcome/CaseRecord는 `model_dump(mode="json")`로 저장, 읽을 때 `model_validate`.
- `LedgerPort.prune_runs_before(before: datetime) -> int` 추가(InMemory·Mongo).
- `build_checkpointer(cfg: StoreConfig)`: backend=memory → `InMemorySaver()`; mongo → 지연 import `from langgraph.checkpoint.mongodb import MongoDBSaver` + `MongoClient(cfg.mongo_url)`, `db_name=cfg.mongo_db`로 생성(0.4.0 실제 API — 계획 초안의 `AsyncMongoDBSaver`는 오기). 테스트는 memory 경로 + mongo 경로의 import 가능성 스모크(`importlib.util.find_spec`).
- `build_persistence(cfg: StoreConfig) -> tuple[store, repo, ledger]`: memory면 InMemory 3종, mongo면 `MongoClient(cfg.mongo_url)[cfg.mongo_db]`(db 이름은 `StoreConfig.mongo_db: str = "deepagent"` 추가)로 Mongo 3종.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/infrastructure/test_mongo_store.py`

```python
from datetime import datetime, timezone

import mongomock
import pytest

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.domain.patrol import CheckOutcome
from src.infrastructure.mongo_store import MongoCaseRepository, MongoCaseStore, MongoLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    return mongomock.MongoClient()["deepagent_test"]


def test_store_계약(db):
    store = MongoCaseStore(db)
    e1 = store.put_evidence("c-1", "rest:/oee", {"oee": 512}, as_of=T, complete=False)
    e2 = store.put_evidence("c-1", "mongo:x", [1, 2])
    assert (e1, e2) == ("ev-1", "ev-2") and store.put_evidence("c-2", "s", None) == "ev-1"
    rec = store.get_evidence_record("c-1", "ev-1")
    assert rec.complete is False and rec.as_of == T and store.get_evidence("c-1", "ev-1") == {"oee": 512}
    assert [r.id for r in store.list_evidence("c-1")] == ["ev-1", "ev-2"]
    assert store.has_evidence("c-1", "ev-2") and not store.has_evidence("c-1", "ev-9")
    with pytest.raises(KeyError):
        store.get_evidence("c-1", "ev-9")
    store.put_code_knowledge("svc", "abc", "spec")
    assert store.get_code_knowledge("svc", "abc") == "spec" and store.get_code_knowledge("svc", "zzz") is None
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]))
    store.put_verdict("c-1", v)
    assert store.get_verdict("c-1").root_cause.component == "plan-sync"
    assert store.list_case_ids("c-") == ["c-1", "c-2"]
    assert store.purge_case("c-1") == 3 and store.list_evidence("c-1") == []


def test_repo_계약(db):
    repo = MongoCaseRepository(db)
    assert repo.new_case_id() == "c-1" and repo.new_case_id() == "c-2"
    r = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                   created_at=T, updated_at=T)
    repo.save(r)
    assert repo.get("c-1").symptom == "s" and repo.find_open_by_fingerprint("fp").id == "c-1"
    repo.save(r.model_copy(update={"status": "closed"}))
    assert repo.find_open_by_fingerprint("fp") is None and repo.list_open() == []
    assert [x.id for x in repo.list_by_status("closed")] == ["c-1"]
    with pytest.raises(KeyError):
        repo.get("c-9")


def test_ledger_계약(db):
    ledger = MongoLedger(db)
    ok = CheckOutcome(status="ok", observed_at=T)
    err = CheckOutcome(status="error", observed_at=T, error="x")
    for o in (ok, err, err):
        ledger.record_run("mx", "gumi", "c", o)
    assert ledger.last_run("mx", "gumi", "c").status == "error"
    assert ledger.consecutive_errors("mx", "gumi", "c") == 2
    assert len(ledger.runs("mx", "gumi", "c", limit=2)) == 2
    ledger.heartbeat(T)
    assert ledger.last_heartbeat() == T
    assert ledger.prune_runs_before(T.replace(year=2027)) == 3
```

`tests/infrastructure/test_checkpointer.py`:
```python
import importlib.util

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import StoreConfig
from src.infrastructure.checkpointer import build_checkpointer


def test_memory_백엔드와_mongo_모듈_존재():
    assert isinstance(build_checkpointer(StoreConfig(backend="memory")), InMemorySaver)
    assert importlib.util.find_spec("langgraph.checkpoint.mongodb") is not None
```

- [ ] **Step 2~4**: FAIL → 구현(mongomock이 `find_one_and_update`·정렬·`$regex` prefix를 지원함을 확인하며) → 전체 PASS → 커밋

```bash
git add src/infrastructure/mongo_store.py src/infrastructure/checkpointer.py src/patrol/ledger.py tests/infrastructure/test_mongo_store.py tests/infrastructure/test_checkpointer.py
git commit -m "Persist cases, evidence, verdicts, and the ledger in MongoDB"
```

---

### Task 5: 보존 스윕 + 자기 감시 점검 + 기동 검증 10

**Files:**
- Create: `src/infrastructure/retention.py`, `src/patrol/selfcheck.py`
- Modify: `src/boot.py`
- Test: `tests/infrastructure/test_retention.py`, `tests/patrol/test_selfcheck.py`, `tests/test_boot.py`(추가)

**Interfaces:**
- `async sweep_retention(*, repo, store, ledger, checkpointer, clock, retention: RetentionConfig) -> dict[str,int]` — ① `repo.list_by_status("closed")` 중 `updated_at < now - closed_case_evidence_d` → `store.purge_case` + 스레드 `adelete_thread` ② `ledger.prune_runs_before(now - ledger_d)` ③ `store.list_case_ids("patrol:")` 각각 `purge_evidence_before(id, now - ledger_d)`(인계 노트 5) ④ `checkpoint_ttl_d`: 열린 케이스라도 `updated_at < now - checkpoint_ttl_d`인 스레드는 폐기하고 `thread_ids`에서 제거(다음 재개는 F3로 새 스레드). 반환: 항목별 건수.
- `scan_self_check(*, ledger, checks: list[tuple[str,str,str]], threshold: int, clock, store) -> list[Finding]` — `consecutive_errors >= threshold`인 (gbm,fct,check)마다 최근 runs 요약을 `store.put_evidence("patrol:self:{gbm}:{fct}", ...)`로 박제하고 `Finding(check=f"self.{check}", judge="rule", target=None, summary=f"점검 {check} 연속 error {n}회", ...)`. 게이트에 넣으면 지문이 `(gbm,fct,"self.<check>",None)`이라 자기 감시 케이스도 중복 억제된다.
- 기동 검증 10: enabled 사이트에 `judge in ("llm","rule+llm")` 점검이 하나라도 있으면 app config `llm.profiles.judge`가 비어 있지 않아야 함(빈 문자열이면 BootError "judge LLM 프로파일 필요").

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infrastructure/test_retention.py`:
```python
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import RetentionConfig
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.patrol import CheckOutcome
from src.domain.store import InMemoryCaseStore
from src.infrastructure.retention import sweep_retention
from src.patrol.ledger import InMemoryLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


async def test_스윕은_오래된_종결_케이스와_레저와_스크래치를_정리한다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    old, fresh = T - timedelta(days=100), T - timedelta(days=1)
    for cid, at in (("c-old", old), ("c-fresh", fresh)):
        repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint=cid, symptom="s", t0=T,
                             created_at=at, updated_at=at, status="closed", thread_ids=[f"{cid}#1"]))
        store.put_evidence(cid, "s", {"x": 1}, as_of=at)
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=old))
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=fresh))
    store.put_evidence("patrol:mx:gumi:c", "s", {"p": 1}, as_of=old)
    store.put_evidence("patrol:mx:gumi:c", "s", {"p": 2}, as_of=fresh)
    counts = await sweep_retention(repo=repo, store=store, ledger=ledger, checkpointer=InMemorySaver(),
                                   clock=lambda: T, retention=RetentionConfig())
    assert counts["closed_cases"] == 1 and store.list_evidence("c-old") == [] and store.list_evidence("c-fresh")
    assert counts["ledger_runs"] == 1 and len(ledger.runs("mx", "gumi", "c")) == 1
    assert counts["scratch_evidence"] == 1 and len(store.list_evidence("patrol:mx:gumi:c")) == 1
```

`tests/patrol/test_selfcheck.py`:
```python
from datetime import datetime, timezone

from src.domain.patrol import CheckOutcome
from src.domain.store import InMemoryCaseStore
from src.patrol.ledger import InMemoryLedger
from src.patrol.selfcheck import scan_self_check

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_연속_error가_임계를_넘으면_자기감시_finding():
    ledger, store = InMemoryLedger(), InMemoryCaseStore()
    for _ in range(3):
        ledger.record_run("mx", "gumi", "api.oee", CheckOutcome(status="error", observed_at=T, error="timeout"))
    ledger.record_run("mx", "gumi", "kafka.lag", CheckOutcome(status="ok", observed_at=T))
    findings = scan_self_check(ledger=ledger, checks=[("mx", "gumi", "api.oee"), ("mx", "gumi", "kafka.lag")],
                               threshold=3, clock=lambda: T, store=store)
    assert len(findings) == 1 and findings[0].check == "self.api.oee"
    assert store.has_evidence(findings[0].scratch_case_id, findings[0].evidence_ids[0])
```

`tests/test_boot.py`에 추가:
```python
def test_llm_판정기가_있는데_judge_프로파일이_비면_기동_거부(tmp_path):
    _tree(tmp_path)
    app = tmp_path / "config" / "app.json"
    app.write_text(json.dumps({"llm": {"profiles": {"judge": "", "subagent": "b", "lead": "c"}}}), encoding="utf-8")
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["patrol"]["checks"]["c1"]["judge"] = "llm"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("judge" in e.problem for e in errors)
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/infrastructure/retention.py src/patrol/selfcheck.py src/boot.py tests/infrastructure/test_retention.py tests/patrol/test_selfcheck.py tests/test_boot.py
git commit -m "Sweep retention, watch the patrol's own errors, and require a judge model"
```

---

### Task 6: 순찰 데몬 조립

**Files:**
- Create: `src/patrol/daemon.py`
- Test: `tests/patrol/test_daemon.py`

**Interfaces:**
- `SiteRuntime`(dataclass): `gbm, fct, cfg: SiteConfig, adapters: AdapterSet, deps: EngineDeps, digests: dict[str,str]`.
- `PatrolDaemon(*, app: AppConfig, sites: list[SiteRuntime], store, repo, ledger, checkpointer, clock, judge_llm, budget: LlmBudget, owner: str, timezone: str)`:
  - `async run_one(gbm, fct, name, check)`: `run_check(...)` → `ledger.record_run` → finding이면 `admit_finding` → opened면 `queue.put(case_id)`; attached/rejected는 레저에만(레저 `record_run`에 CheckOutcome 그대로 — 게이트 결과는 `worker:` 접두 이벤트가 아니라 `gate:{name}` 이벤트로 `CheckOutcome(status="ok"|"skipped", skipped_reason=...)` 기록). 최외곽 try/except.
  - `on_missed(job_id)`(동기): `ledger.record_run(gbm, fct, name, CheckOutcome(status="skipped", skipped_reason="misfire/중복 실행 스킵"))` — job_id `gbm/fct/name` 파싱; `heartbeat` 잡 id는 무시.
  - `async heartbeat()`: `ledger.heartbeat(clock())`.
  - `async self_check_job()`: `scan_self_check(...)` → 각 Finding을 `admit_finding` → opened면 enqueue. threshold는 app config `patrol.self_check_errors: int = 3`(AppPatrol에 필드 추가).
  - `async sweep_job()`: `sweep_timeouts` + `sweep_retention`.
  - `build()`: `build_scheduler(sites, run_one=..., heartbeat=..., on_missed=self.on_missed, timezone=...)` + `add_job(self_check_job, IntervalTrigger(minutes=10), id="self_check")` + `add_job(sweep_job, IntervalTrigger(hours=1), id="sweep")`, 워커 생성(`requeue_open()` 선행).
  - `async run(stop: asyncio.Event)`: scheduler.start() + worker.run_forever(stop) → stop 시 scheduler.shutdown(wait=False).
- `assemble_sites(config_root, repo_root, env, *, clock, stub_seeds=None) -> tuple[AppConfig, list[SiteRuntime]]`: registry의 enabled 사이트마다 site config·topology·deployment·digest(`canonical_digest`)·adapters·EngineDeps(lead/subagent llm은 `build_chat_model(profile, base_url=env LLM_BASE_URL, api_key=env LLM_API_KEY)`; **테스트에서는 `llm_factory` 인자로 ScriptedLLM/ToolFake 주입**). knowledge digests: `{"topology": digest, "rules": digest(checks), "deployment": digest or "absent"}`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/patrol/test_daemon.py`

```python
"""데몬의 run_one→게이트→큐→워커 사슬을 스텁 위에서 결정론 검증한다."""
from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import AppConfig
from src.config.schema_site import CheckConfig, SiteConfig
from src.domain.cases import InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters
from src.patrol.daemon import PatrolDaemon, SiteRuntime
from src.patrol.ledger import InMemoryLedger
from src.patrol.llm_judge import LlmBudget
from tests.application.test_graph_e2e import (FRAME_ONE_TASK, INTEGRATE_CONCLUDE,
                                              VERDICT_JSON, make_e2e_deps)
from tests.patrol.test_probes import TOPO

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
APP = AppConfig.model_validate({"llm": {"profiles": {"judge": "j", "subagent": "s", "lead": "l"}}})
CHECK = CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                    "target": "rest:/oee",
                                    "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}})


def _daemon(store, repo, ledger, lead, clock=lambda: T):
    site = SiteConfig.model_validate({"target": {"rest": {"base_url": "http://x"}},
                                      "patrol": {"checks": {"api.oee": CHECK.model_dump()}}})
    adapters = build_adapters(site, TOPO, clock=clock,
                              stub_seeds=StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    deps = make_e2e_deps(store, lead=lead)
    deps.adapters = adapters
    rt = SiteRuntime(gbm="mx", fct="gumi", cfg=site, adapters=adapters, deps=deps,
                     digests={"topology": "d-topo"})
    return PatrolDaemon(app=APP, sites=[rt], store=store, repo=repo, ledger=ledger,
                        checkpointer=InMemorySaver(), clock=clock, judge_llm=None,
                        budget=LlmBudget(5, clock=clock), owner="daemon-test", timezone="Asia/Seoul")


async def test_run_one은_finding을_케이스로_열어_큐에_넣고_워커가_종결한다():
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert ledger.last_run("mx", "gumi", "api.oee").status == "finding"
    assert daemon.queue.qsize() == 1 and repo.list_by_status("open")[0].id == "c-1"
    result = await daemon.worker.run_once(await daemon.queue.get())
    assert result == "closed" and store.get_verdict("c-1") is not None


async def test_같은_지문의_재발은_첨부만_하고_큐에_안_넣는다():
    from datetime import timedelta
    now = [T]
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[], clock=lambda: now[0])
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    now[0] = T + timedelta(minutes=5)                    # 다른 observed_at → 다른 Finding.id
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert daemon.queue.qsize() == 1 and len(repo.get("c-1").finding_ids) == 2


def test_on_missed는_skipped를_레저에_남기고_잡이_전부_등록된다():
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[])
    sched = daemon.build()
    ids = {j.id for j in sched.get_jobs()}
    assert {"mx/gumi/api.oee", "heartbeat", "self_check", "sweep"} <= ids
    daemon.on_missed("mx/gumi/api.oee")
    assert ledger.last_run("mx", "gumi", "api.oee").status == "skipped"
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/patrol/daemon.py src/config/schema_app.py tests/patrol/test_daemon.py
git commit -m "Assemble the patrol daemon: checks, gate, queue, worker, sweeps"
```

---

### Task 7: CLI — `patrol`, `patrol status`, `case list|show|resume`

**Files:**
- Modify: `src/__main__.py`
- Test: `tests/test_cli.py`(추가)

**Interfaces:**
- `patrol run [--config-root] [--repo-root] [--for-seconds N]` — `assemble_sites` → `build_persistence`/`build_checkpointer`(app `store`) → `PatrolDaemon(...).run(stop)`; `--for-seconds`면 그 뒤 stop(스모크·개발용). 기동 검증 실패면 오류 나열 후 exit 1(§4.6).
- `patrol status` — `ledger.last_heartbeat()`와 사이트·점검별 `last_run` 요약 표 출력. backend=memory면 "메모리 백엔드 — 프로세스 간 상태 없음" 안내 후 exit 0.
- `case list [--status]` / `case show <id>`(레코드 + verdict 요약 + 증거 수) / `case resume <id> --answer TEXT` — F2: `case resume`은 실행자가 아니라 클라이언트… 단, v1은 데몬-프로세스 간 명령 채널이 없으므로 **lease가 비어 있을 때만 인라인으로 `worker.resume_once`** 실행(lease 점유 중이면 "데몬이 실행 중 — 잠시 후 재시도" exit 2). 이 제약을 도움말에 명시.
- `.env` 로드는 기존 `load_dotenv()` 경로 재사용.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cli.py`에 추가

```python
def test_patrol_status_memory_백엔드_안내(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["patrol", "status", "--config-root", str(tmp_path / "config"),
                 "--repo-root", str(tmp_path)])
    assert code == 0 and "메모리 백엔드" in capsys.readouterr().out


def test_patrol_run_은_기동_검증_실패면_exit_1(tmp_path, capsys, monkeypatch):
    _tree(tmp_path, check_target="rest:/ghost")
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["patrol", "run", "--for-seconds", "0",
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 1 and "rest:/ghost" in capsys.readouterr().err


def test_case_list_는_빈_저장소에서_빈_출력(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["case", "list", "--config-root", str(tmp_path / "config")])
    assert code == 0
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/__main__.py tests/test_cli.py
git commit -m "Add the patrol daemon, status, and case commands to the CLI"
```

---

## 완료 기준 (계획 4b)

- `.venv/bin/pytest` 전체 통과.
- 스텁+스크립트 LLM+InMemorySaver로 **순찰 점검 → Finding → 게이트 → 큐 → 워커 → 종결(verdict 영속)** 사슬이 결정론 완주하고, lease 충돌·재개 실패 복구·타임아웃 종결·보존 스윕이 각각 실증됨.
- Mongo 3종이 mongomock 계약 테스트를 통과하고, `python -m src patrol run --for-seconds 3`이 예시 트리에서 기동 검증을 지나 데몬을 띄웠다 내림(컨트롤러 수동 검증).

## 계획 5 예고

보고서 렌더러(md 템플릿, §5.1 5절), 이벤트 봉투(§5.2 F1)와 updates→이벤트 매퍼, `chat` CLI(접수 3노드·interrupt 응답·스트리밍), 메일 발송(pending→sent, F6), 부록 A 두 시나리오의 E2E 벤치(회귀/평가 모드), 순찰 알림 발송.
