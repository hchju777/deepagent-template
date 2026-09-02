"""케이스 큐 + 조사 워커 — 스펙 §계획 4b F2·F3.

CaseQueue는 asyncio.Queue를 얇게 감싼다: Mongo 등 영속 큐는 YAGNI다 — 재시작
내구성은 워커 기동 시 repo.list_by_status("open")을 그대로 재큐잉하는
requeue_open()으로 확보한다(그 케이스들은 이미 저장소에 살아 있다).

InvestigationWorker 한 번의 run_once/resume_once는 다음을 한 동작으로 묶는다:
lease 획득 → investigating 전이 → 스레드 배정 → 엔진 실행 → 결과에 따른
후속 전이(awaiting_human/closed) → lease 해제. 엔진(build_engine 결과)은
(gbm, fct) 사이트 키로 캐시해 재사용한다 — 노드 배선은 deps에만 의존하고
케이스마다 달라지지 않는다.

F3(재개 실패 복구): investigate_case/resume_case가 예외를 던지면(체크포인트
역직렬화 실패 등) 그 스레드를 폐기하고 새 스레드로 한 번만 더 시도한다.
그마저 실패하면 close_case(discard_threads=True)로 케이스를 종결하고
예외를 다시 던진다 — 이 재던짐을 run_once/resume_once 최외곽의 단일
try/except가 받아 레저에 "worker:{case_id}" error 이벤트로 남기고
"failed"를 돌려준다. 이 워커는 어떤 경로로도 raise하지 않는다(§계약).
"""
import asyncio
from typing import Any, Callable

from src.application.close import close_case
from src.application.graph import build_engine
from src.application.lifecycle import (ENGINE_SCHEMA_VERSION, acquire_lease, release_lease,
                                       transition)
from src.application.usecase import investigate_case, resume_case
from src.domain.patrol import CheckOutcome
from src.patrol.gate import evidence_refs_for_case


class CaseQueue:
    """asyncio.Queue[str] 래퍼 — put/get/qsize와 재시작 재큐잉만 안다."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()

    async def put(self, case_id: str) -> None:
        await self._queue.put(case_id)

    async def get(self) -> str:
        return await self._queue.get()

    def qsize(self) -> int:
        return self._queue.qsize()

    def requeue_open(self, repo) -> int:
        """repo.list_by_status("open") 전부를 큐에 다시 넣는다(재시작 내구성).

        투입한 케이스 수를 돌려준다. 큐는 무제한(maxsize=0)이므로 블로킹
        없이 put_nowait로 즉시 채운다 — 워커가 아직 돌기 전(이벤트 루프
        기동 이전)에도 호출할 수 있어야 하기 때문이다.
        """
        records = repo.list_by_status("open")
        for record in records:
            self._queue.put_nowait(record.id)
        return len(records)


class InvestigationWorker:
    """lease로 케이스 하나씩 조사 엔진에 태우고 결과에 따라 수명주기를 진행한다."""

    def __init__(self, queue: CaseQueue, *, repo, store,
                deps_for_site: Callable[[str, str], Any], checkpointer, clock,
                owner: str, max_concurrent: int, lease_ttl_s: int, ledger,
                knowledge_digests_for_site: Callable[[str, str], dict[str, str]]):
        self._queue = queue
        self._repo = repo
        self._store = store
        self._deps_for_site = deps_for_site
        self._checkpointer = checkpointer
        self._clock = clock
        self._owner = owner
        self._max_concurrent = max_concurrent
        self._lease_ttl_s = lease_ttl_s
        self._ledger = ledger
        self._knowledge_digests_for_site = knowledge_digests_for_site
        self._engines: dict[tuple[str, str], Any] = {}   # 사이트 키(gbm, fct) → 컴파일된 그래프

    def _engine_for(self, gbm: str, fct: str, deps) -> Any:
        key = (gbm, fct)
        if key not in self._engines:
            self._engines[key] = build_engine(deps, checkpointer=self._checkpointer)
        return self._engines[key]

    @staticmethod
    def _next_thread_id(record, case_id: str) -> str:
        return f"{case_id}#{len(record.thread_ids) + 1}"

    @staticmethod
    def _register_thread(record, thread_id: str):
        # model_copy는 얕은 복사다 — thread_ids/thread_versions를 제자리에서
        # mutate하면 원본 record(및 그걸 참조하는 다른 곳)까지 같이 바뀐다.
        # 그래서 항상 새 list/dict를 만들어 update에 싣는다.
        return record.model_copy(update={
            "thread_ids": record.thread_ids + [thread_id],
            "thread_versions": {**record.thread_versions, thread_id: ENGINE_SCHEMA_VERSION},
        })

    async def _discard_thread(self, thread_id: str | None) -> None:
        if thread_id is None or self._checkpointer is None:
            return
        try:
            await self._checkpointer.adelete_thread(thread_id)
        except Exception:                                          # noqa: BLE001 — 정리 실패는 무시
            pass

    async def _run_with_f3(self, record, case, deps, engine, case_id: str,
                           first_thread_id: str, initial_evidence, *, resume=None):
        """첫 시도(신규 조사 또는 resume) 후 실패하면 스레드를 폐기하고 새 스레드로
        신규 조사를 한 번만 재시도한다. 그마저 실패하면 케이스를 종결하고
        (discard_threads=True) 마지막 예외를 그대로 다시 던진다 — 레저 기록과
        "failed" 반환은 run_once/resume_once의 최외곽 try/except 하나가 맡는다.
        """
        try:
            if resume is not None:
                result = await resume_case(resume, deps=deps, checkpointer=self._checkpointer,
                                           thread_id=first_thread_id, engine=engine)
            else:
                result = await investigate_case(
                    case, deps=deps, checkpointer=self._checkpointer, thread_id=first_thread_id,
                    engine=engine, initial_evidence=initial_evidence)
            return record, result
        except Exception:
            await self._discard_thread(first_thread_id)
            retry_thread_id = self._next_thread_id(record, case_id)
            record = self._register_thread(record, retry_thread_id)
            self._repo.save(record)
            # 첫 시도(특히 resume)가 실패 전에 일부 라운드를 커밋했을 수 있으므로
            # Store에서 다시 읽는다 — 호출부가 넘긴 스냅샷을 그대로 재사용하면
            # 그 사이 쌓인 증거를 재시작 스레드가 놓친다.
            fresh_evidence = evidence_refs_for_case(self._store, case_id)
            try:
                result = await investigate_case(
                    case, deps=deps, checkpointer=self._checkpointer, thread_id=retry_thread_id,
                    engine=engine, initial_evidence=fresh_evidence)
                return record, result
            except Exception as second_exc:
                await close_case(
                    case_id, repo=self._repo, checkpointer=self._checkpointer, clock=self._clock,
                    reason=f"재개 실패 — 스레드 재시작 후에도 실패: {second_exc}",
                    discard_threads=True)
                raise

    async def _finish(self, record, result: dict) -> str:
        if "__interrupt__" in result:
            self._repo.save(transition(record, "awaiting_human", clock=self._clock))
            return "awaiting_human"
        verdict = result.get("verdict")
        self._store.put_verdict(record.id, verdict)
        summary = verdict.narrative[:200] if verdict is not None else None
        self._repo.save(record.model_copy(update={"verdict_summary": summary}))
        await close_case(record.id, repo=self._repo, checkpointer=self._checkpointer,
                         clock=self._clock, reason="조사 완료", discard_threads=False)
        return "closed"

    def _log_failure(self, record, case_id: str, exc: Exception) -> None:
        gbm = record.gbm if record is not None else "unknown"
        fct = record.fct if record is not None else "unknown"
        self._ledger.record_run(gbm, fct, f"worker:{case_id}", CheckOutcome(
            status="error", observed_at=self._clock(), error=f"{type(exc).__name__}: {exc}"))

    async def _release_safely(self, case_id: str) -> None:
        # 닫힌 케이스는 transition(→closed)이 이미 lease를 해제했다 — 그런 레코드에
        # release_lease를 또 부르면 owner 불일치로 LifecycleError가 난다. 워커는
        # 어떤 경로로도 raise하지 않으므로 여기서 조용히 방어한다.
        try:
            current = self._repo.get(case_id)
        except Exception:                                          # noqa: BLE001
            return
        if current.owner != self._owner:
            return
        try:
            self._repo.save(release_lease(current, self._owner, clock=self._clock))
        except Exception:                                          # noqa: BLE001
            pass

    async def run_once(self, case_id: str) -> str:
        """큐에서 꺼낸 케이스 하나를 lease 아래 조사하고 종결까지 시도한다.

        "closed"/"awaiting_human"/"busy"/"failed" 중 하나를 돌려준다 — 어떤
        경로로도 raise하지 않는다.
        """
        record = None
        try:
            record = self._repo.get(case_id)
            leased = acquire_lease(record, self._owner, clock=self._clock, ttl_s=self._lease_ttl_s)
            if leased is None:
                return "busy"                                       # 레저 이벤트 없음(경합은 정상)
            record = transition(leased, "investigating", clock=self._clock)
            thread_id = self._next_thread_id(record, case_id)
            record = self._register_thread(record, thread_id)
            self._repo.save(record)

            deps = self._deps_for_site(record.gbm, record.fct)
            engine = self._engine_for(record.gbm, record.fct, deps)
            digests = self._knowledge_digests_for_site(record.gbm, record.fct)
            case = record.to_case().model_copy(update={"knowledge_digests": digests})
            initial_evidence = evidence_refs_for_case(self._store, case_id)

            record, result = await self._run_with_f3(
                record, case, deps, engine, case_id, thread_id, initial_evidence)
            return await self._finish(record, result)
        except Exception as exc:
            self._log_failure(record, case_id, exc)
            return "failed"
        finally:
            await self._release_safely(case_id)

    async def resume_once(self, case_id: str, answer) -> str:
        """awaiting_human 케이스를 사람의 답변으로 재개한다.

        최신 스레드의 저장된 schema 버전이 지금 엔진과 다르면(엔진 배선이
        바뀐 뒤 재개하려는 경우) resume을 시도하지 않고 F3 경로(스레드 폐기 +
        새 스레드로 신규 조사)를 그대로 탄다 — 옛 체크포인트를 새 그래프
        모양으로 재개하면 어떤 실패를 낼지 예측할 수 없기 때문이다.
        """
        record = None
        try:
            record = self._repo.get(case_id)
            leased = acquire_lease(record, self._owner, clock=self._clock, ttl_s=self._lease_ttl_s)
            if leased is None:
                return "busy"
            record = transition(leased, "investigating", clock=self._clock)

            deps = self._deps_for_site(record.gbm, record.fct)
            engine = self._engine_for(record.gbm, record.fct, deps)
            digests = self._knowledge_digests_for_site(record.gbm, record.fct)
            case = record.to_case().model_copy(update={"knowledge_digests": digests})
            initial_evidence = evidence_refs_for_case(self._store, case_id)

            latest_thread_id = record.thread_ids[-1] if record.thread_ids else None
            version_matches = (latest_thread_id is not None
                              and record.thread_versions.get(latest_thread_id)
                              == ENGINE_SCHEMA_VERSION)

            if version_matches:
                self._repo.save(record)
                record, result = await self._run_with_f3(
                    record, case, deps, engine, case_id, latest_thread_id, initial_evidence,
                    resume=answer)
            else:
                await self._discard_thread(latest_thread_id)
                fresh_thread_id = self._next_thread_id(record, case_id)
                record = self._register_thread(record, fresh_thread_id)
                self._repo.save(record)
                record, result = await self._run_with_f3(
                    record, case, deps, engine, case_id, fresh_thread_id, initial_evidence)
            return await self._finish(record, result)
        except Exception as exc:
            self._log_failure(record, case_id, exc)
            return "failed"
        finally:
            await self._release_safely(case_id)

    async def run_forever(self, stop: asyncio.Event) -> None:
        """stop이 설정될 때까지 큐를 Semaphore(max_concurrent)로 동시 소비한다."""
        semaphore = asyncio.Semaphore(self._max_concurrent)
        running: set[asyncio.Task] = set()
        try:
            while not stop.is_set():
                get_task = asyncio.ensure_future(self._queue.get())
                stop_task = asyncio.ensure_future(stop.wait())
                done, _ = await asyncio.wait(
                    {get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
                if stop_task in done:
                    get_task.cancel()
                    break
                stop_task.cancel()
                case_id = get_task.result()
                await semaphore.acquire()

                async def _consume(cid: str) -> None:
                    try:
                        await self.run_once(cid)
                    finally:
                        semaphore.release()

                task = asyncio.ensure_future(_consume(case_id))
                running.add(task)
                task.add_done_callback(running.discard)
        finally:
            if running:
                await asyncio.gather(*running, return_exceptions=True)
