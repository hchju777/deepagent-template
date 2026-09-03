"""케이스 큐 + 조사 워커 — 스펙 §계획 4b F2·F3.

CaseQueue는 asyncio.Queue를 얇게 감싼다: Mongo 등 영속 큐는 YAGNI다 — 재시작
내구성은 워커 기동 시 requeue_open()으로 확보한다. open 케이스뿐 아니라
lease가 만료된 investigating 케이스도 다시 큐에 넣는다 — 워커 프로세스가
그래프 실행 도중(엔진 호출 밖 포함) 죽으면 그 케이스는 investigating으로
멈춘 채 lease만 만료되고, open도 awaiting_human도 아니라서 다른 어떤
회수 경로에도 걸리지 않기 때문이다. lease가 아직 유효한 investigating은
다른 워커가 지금 붙들고 있는 것이므로 회수하지 않는다.

InvestigationWorker 한 번의 run_once/resume_once는 다음을 한 동작으로 묶는다:
lease 획득 → investigating 전이(이미 investigating이면 lease만 갱신) →
스레드 배정 → 엔진 실행(keepalive로 lease 유지) → 결과에 따른 후속 전이
(awaiting_human/closed) → lease 해제. 엔진(build_engine 결과)은 (gbm, fct)
사이트 키로 캐시해 재사용한다 — 노드 배선은 deps에만 의존하고 케이스마다
달라지지 않는다.

read-modify-write(I1): 엔진 호출(ainvoke) 이후의 모든 저장은 그 사이 다른
경로(게이트의 finding 첨부 등)가 같은 레코드를 바꿨을 수 있다는 전제로,
record를 다시 읽어(repo.get) 워커가 바꿀 필드만 그 위에 얹어 저장한다 —
엔진 호출 전에 들고 있던 스냅샷을 그대로 wholesale 저장하면 그 사이의
동시 갱신(예: finding_ids)을 잃는다.

lease keepalive(I5): 엔진 호출이 lease_ttl_s를 넘게 걸리면 lease가 만료돼
requeue_open이 같은 케이스를 다른 워커에 또 내줄 수 있다 — run_once/resume_once는
엔진 호출을 감싸는 동안 lease_ttl_s/3 간격으로 lease를 갱신하는 백그라운드
태스크를 돌리고, 엔진 호출이 끝나면(성공이든 실패든) finally에서 취소한다.

F3(재개 실패 복구): investigate_case/resume_case가 예외를 던지면(체크포인트
역직렬화 실패 등) 그 스레드를 폐기하고 새 스레드로 한 번만 더 시도한다
(resume_once가 스레드 schema 버전 불일치로 곧장 새 스레드를 여는 경로는
예외다 — 이미 "새 스레드로 재시작"한 셈이라 실패해도 또 재시작하지
않는다, allow_restart=False). _run_with_f3는 케이스를 직접 닫지 않는다 —
재시도가 소진되면 사유를 담은 예외를 다시 던질 뿐이다. 실제 재시작
지점에서는(I3) 레저에 "F3 재시작" 사유를 남기고, 폐기한 thread_id를
thread_ids/thread_versions에서 바로 제거한다 — TTL 스윕을 기다리지 않고
죽은 스레드가 목록에 남지 않게 한다. resume=answer로 재시작하는 경우(I4)는
새 스레드가 resume 메커니즘 없이 investigate_case로 다시 시작하므로, 그
답변을 잃지 않도록 재시작 전에 evidence로 박제해 evidence_refs_for_case가
새 스레드에 실어 나르게 한다.

resume_once의 버전 불일치 재시작(F3와 별개 경로)도 같은 이유로 재시작 전에
답변을 evidence로 박제한다(I4) — 이 경로는 investigate_case를 새로 여는
것이지 resume하는 게 아니므로, 사람의 답변이 자연히 사라진다.

미등록 사이트(daemon._deps_for_site가 None을 돌려주는 경우 — deps_for_site의
계약: 알 수 없는 (gbm, fct)면 None): 이건 "그래프 호출 밖 실패"(F1)와 달리
설정이 일시적으로 어긋난 것뿐 케이스 자체의 문제가 아니므로 케이스를 닫지
않는다 — 레저에 skipped를 남기고 "skipped"를 돌려준다. lease가 풀렸으므로
다음 재큐 잡(daemon.requeue_job, 기본 30초)이 같은 케이스를 다시 집어 준다. deps_for_site가 예외를
던지는 경우(진짜 조립 실패)는 기존과 같이 F1 경로로 케이스를 닫는다.

lease 획득은 저장소의 claim이 한 동작으로 수행한다 — get→save로 나누면 그 사이에
다른 프로세스가 끼어든다. 지금까지 안전했던 것은 그 사이에 await가 없어 협조적
스케줄링이 직렬화해 준 우연이고, resume_once의 버전 불일치 분기는 이미 깨져 있었다.

run_once/resume_once 최외곽의 단일 try/except가 나머지 모든 실패(F3
소진뿐 아니라 deps_for_site 예외/build_engine/evidence_refs_for_case/_finish
등 그래프 호출 "밖"에서 나는 예외까지)를 같은 방식으로 받는다: 레저에
"worker:{case_id}" error 이벤트를 남기고, close_case(discard_threads=True)
로 케이스를 종결한 뒤 "failed"를 돌려준다 — 종결 자체가 실패하면 그
실패도 레저에 남기고(레저 기록 자체가 실패해도 종결 시도는 계속한다)
더 시도하지 않는다. 어느 경로에서도 investigating 상태로 owner 없이(또는
owner를 쥔 채 프로세스만 죽어) 고아로 남는 레코드가 생기지 않는다. 이
워커는 어떤 경로로도 raise하지 않는다(§계약).
"""
import asyncio
import contextlib
from typing import Any, Awaitable, Callable

from src.application.close import close_case
from src.application.events import case_status_event
from src.application.graph import build_engine
from src.application.lifecycle import ENGINE_SCHEMA_VERSION, release_lease, transition
from src.application.usecase import investigate_case, resume_case
from src.domain.patrol import CheckOutcome
from src.patrol.gate import evidence_refs_for_case


def _case_file_snapshot(result: dict) -> dict:
    """엔진 최종 State(dict)에서 계획 5가 읽을 케이스 파일 스냅샷을 뽑는다(I6).

    스레드 체크포인트는 보존 TTL로 폐기될 수 있으므로(infrastructure/retention.py),
    보고서 소스는 Store에 별도로 박제한다 — pydantic 모델은 model_dump(mode="json")로
    평범한 JSON 값으로 내린다.
    """
    return {
        "plan_tasks": [t.model_dump(mode="json") for t in result.get("plan_tasks", [])],
        "hypotheses": [h.model_dump(mode="json") for h in result.get("hypotheses", [])],
        "round": result.get("round", 0),
        "qa_log": result.get("qa_log", []),
        "verify_problems": result.get("verify_problems", []),
    }


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

    def requeue_open(self, repo, *, clock) -> int:
        """open 케이스 전부 + lease가 만료된 investigating 케이스를 큐에 넣는다.

        재시작 내구성의 핵심: open은 아직 아무도 손대지 않은 케이스라
        무조건 회수한다. investigating은 죽은 워커가 lease를 쥔 채
        프로세스만 죽었을 수 있는 상태다 — lease_until이 없거나(비정상
        레코드) clock() 이전으로 지났으면 그 워커는 더 이상 살아있지 않다고
        보고 회수한다. lease가 아직 유효한 investigating은 다른(살아있는)
        워커가 지금 붙들고 있는 것이므로 건드리지 않는다.

        투입한 케이스 수를 돌려준다. 큐는 무제한(maxsize=0)이므로 블로킹
        없이 put_nowait로 즉시 채운다 — 워커가 아직 돌기 전(이벤트 루프
        기동 이전)에도 호출할 수 있어야 하기 때문이다.
        """
        now = clock()
        records = list(repo.list_by_status("open"))
        for record in repo.list_by_status("investigating"):
            if record.lease_until is None or record.lease_until < now:
                records.append(record)
        for record in records:
            self._queue.put_nowait(record.id)
        return len(records)


class InvestigationWorker:
    """lease로 케이스 하나씩 조사 엔진에 태우고 결과에 따라 수명주기를 진행한다."""

    def __init__(self, queue: CaseQueue, *, repo, store,
                deps_for_site: Callable[[str, str], Any], checkpointer, clock,
                owner: str, max_concurrent: int, lease_ttl_s: float, ledger,
                knowledge_digests_for_site: Callable[[str, str], dict[str, str]],
                on_event: Callable[[Any], None] | None = None,
                on_closed: Callable[[str], Awaitable] | None = None):
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
        self._on_event = on_event
        self._on_closed = on_closed   # 계획 5 — 케이스가 닫힌 직후(성공/실패 종결 모두) 부르는 발행 훅
        self._engines: dict[tuple[str, str], Any] = {}   # 사이트 키(gbm, fct) → 컴파일된 그래프

    def _emit_status(self, case_id: str, status: str, *, reason: str | None = None) -> None:
        """케이스 상태 전이(investigating/awaiting_human/closed)를 싱크에 낸다.

        on_event가 None이면 아무것도 하지 않는다. 이벤트 발행 실패(싱크가 raise)가
        조사·전이 자체를 죽이면 안 되므로 여기서 삼킨다 — case_status_event 생성
        실패(예: clock 오류)까지 함께 방어한다.
        """
        if self._on_event is None:
            return
        try:
            self._on_event(case_status_event(case_id, status, clock=self._clock, reason=reason))
        except Exception:                                          # noqa: BLE001
            pass

    async def _emit_closed(self, case_id: str) -> None:
        """케이스를 닫은 직후(_finish의 closed 경로, _fail의 close_case 성공 뒤) 부르는
        발행 훅(계획 5) — 보고서 발행은 여기 걸린다(daemon._publish_report). on_event(동기)와
        달리 await로 완료를 기다릴 수 있어 "파일 먼저 쓰고 나서" 순서를 지킬 수 있다.
        훅이 raise해도 이미 끝난 종결 결과는 뒤집지 않는다 — 여기서 삼킨다.
        """
        if self._on_closed is None:
            return
        try:
            await self._on_closed(case_id)
        except Exception:                                          # noqa: BLE001
            pass

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

    @staticmethod
    def _restart_thread(record, discarded_thread_id: str, new_thread_id: str):
        """F3 재시작 지점 전용(I3) — _register_thread(단순 append)와 달리 폐기한
        thread_id를 thread_ids/thread_versions에서 바로 걷어내고 새 것을 등록한다.
        죽은 스레드가 TTL 스윕 전까지 목록에 남아있지 않게 한다."""
        remaining_ids = [t for t in record.thread_ids if t != discarded_thread_id]
        remaining_versions = {t: v for t, v in record.thread_versions.items()
                              if t != discarded_thread_id}
        return record.model_copy(update={
            "thread_ids": remaining_ids + [new_thread_id],
            "thread_versions": {**remaining_versions, new_thread_id: ENGINE_SCHEMA_VERSION},
        })

    async def _discard_thread(self, thread_id: str | None) -> None:
        if thread_id is None or self._checkpointer is None:
            return
        try:
            await self._checkpointer.adelete_thread(thread_id)
        except Exception:                                          # noqa: BLE001 — 정리 실패는 무시
            pass

    async def _keepalive_loop(self, case_id: str) -> None:
        """엔진 호출 동안 lease_ttl_s/3 간격으로 lease를 갱신한다(I5).

        저장소의 claim으로 갱신한다 — 같은 owner의 재획득은 항상 허용되므로
        (domain.cases.lease_is_free) 정상 경로에서는 늘 성공하고, None이 돌아오면
        남이 가져갔다는 뜻이라 더 갱신하지 않는다. 실패(레코드가 이미 종결됐거나
        repo 장애 등)는 조용히 삼킨다 — keepalive는 최선노력이지 계약이 아니다.
        CancelledError는 그대로 전파한다(태스크 취소 계약).
        """
        interval = max(self._lease_ttl_s / 3, 0.001)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = self._repo.claim(case_id, self._owner,
                                           now=self._clock(), ttl_s=self._lease_ttl_s)
                if renewed is not None:
                    self._repo.save(renewed)
            except Exception:                                      # noqa: BLE001
                pass

    async def _run_with_f3(self, record, case, deps, engine, case_id: str,
                           first_thread_id: str, initial_evidence, *, resume=None,
                           allow_restart: bool = True, interaction_policy: str = "autonomous"):
        """첫 시도(신규 조사 또는 resume) 후 실패하면 스레드를 폐기하고 새 스레드로
        신규 조사를 한 번만 재시도한다(allow_restart=True일 때만 — resume_once의
        스레드 schema 버전 불일치 경로는 이미 "새 스레드"로 여는 첫 시도이므로
        allow_restart=False를 넘겨 추가 재시작을 막는다).

        이 메서드는 케이스를 직접 닫지 않는다 — 재시도가 없거나 소진되면
        "재개 실패 — ..." 사유를 담은 예외를 새로 던질 뿐이다. 레저 기록과
        close_case(discard_threads=True)는 run_once/resume_once 최외곽의
        단일 except 하나가 전담한다(F1: 그래프 호출 밖 실패와 동일하게).

        실제 재시작 지점(except 블록)은 I3·I4를 같이 처리한다: 레저에 "F3
        재시작" 사유를 남기고, resume=answer였다면 그 답을 evidence로 먼저
        박제한 뒤(I4 — 새 스레드는 investigate_case로 다시 시작하므로 resume
        메커니즘이 없다), record를 다시 읽어(RMW) 폐기한 스레드를 제거하고
        새 스레드를 등록한다.
        """
        try:
            if resume is not None:
                result = await resume_case(resume, deps=deps, checkpointer=self._checkpointer,
                                           thread_id=first_thread_id, engine=engine,
                                           on_event=self._on_event, case_id=case_id,
                                           clock=self._clock)
            else:
                result = await investigate_case(
                    case, deps=deps, checkpointer=self._checkpointer, thread_id=first_thread_id,
                    engine=engine, initial_evidence=initial_evidence, on_event=self._on_event,
                    clock=self._clock, interaction_policy=interaction_policy)
            return record, result
        except Exception as first_exc:
            if not allow_restart:
                raise RuntimeError(
                    f"재개 실패 — 새 스레드 시도 실패: {first_exc}") from first_exc
            await self._discard_thread(first_thread_id)
            retry_thread_id = self._next_thread_id(record, case_id)
            if resume is not None:
                self._store.put_evidence(
                    case_id, "human:answer", {"question": record.question, "answer": resume},
                    as_of=self._clock())
            self._ledger.record_run(record.gbm, record.fct, f"worker:{case_id}", CheckOutcome(
                status="error", observed_at=self._clock(), error=f"F3 재시작 — {first_exc}"))
            current = self._repo.get(case_id)                       # RMW(I1): finding_ids 등 보존
            record = self._restart_thread(current, first_thread_id, retry_thread_id)
            self._repo.save(record)
            # 첫 시도(특히 resume)가 실패 전에 일부 라운드를 커밋했을 수 있으므로
            # Store에서 다시 읽는다 — 호출부가 넘긴 스냅샷을 그대로 재사용하면
            # 그 사이 쌓인 증거(및 위에서 박제한 human:answer)를 재시작 스레드가
            # 놓친다.
            fresh_evidence = evidence_refs_for_case(self._store, case_id)
            try:
                result = await investigate_case(
                    case, deps=deps, checkpointer=self._checkpointer, thread_id=retry_thread_id,
                    engine=engine, initial_evidence=fresh_evidence, on_event=self._on_event,
                    clock=self._clock, interaction_policy=interaction_policy)
                return record, result
            except Exception as second_exc:
                raise RuntimeError(
                    f"재개 실패 — 스레드 재시작 후에도 실패: {second_exc}") from second_exc

    async def _finish(self, record, result: dict) -> str:
        """엔진 호출 결과에 따라 후속 전이를 저장한다 — read-modify-write(I1):
        record를 다시 읽어 워커가 바꿀 필드만 그 위에 얹는다."""
        current = self._repo.get(record.id)
        if "__interrupt__" in result:
            interrupts = result["__interrupt__"]
            question = interrupts[0].value.get("question") if interrupts else None
            waiting = transition(current, "awaiting_human", clock=self._clock)
            self._repo.save(waiting.model_copy(update={"question": question}))
            self._emit_status(record.id, "awaiting_human")
            return "awaiting_human"
        verdict = result.get("verdict")
        if verdict is None:
            # conclude가 항상 verdict를 만들지만, 방어적으로 막는다(I6) — 여기서
            # raise하면 run_once/resume_once의 최외곽 except가 F1과 동일하게
            # 레저 기록 후 케이스를 닫는다(_fail 경로).
            raise RuntimeError("verdict 없이 종료")
        self._store.put_verdict(record.id, verdict)
        self._store.put_case_file(record.id, _case_file_snapshot(result))
        summary = verdict.narrative[:200]
        self._repo.save(current.model_copy(update={"verdict_summary": summary, "question": None}))
        await close_case(record.id, repo=self._repo, checkpointer=self._checkpointer,
                         clock=self._clock, reason="조사 완료", discard_threads=False)
        self._emit_status(record.id, "closed")
        await self._emit_closed(record.id)
        return "closed"

    def _log_failure(self, record, case_id: str, exc: Exception) -> None:
        try:
            gbm = record.gbm if record is not None else "unknown"
            fct = record.fct if record is not None else "unknown"
            self._ledger.record_run(gbm, fct, f"worker:{case_id}", CheckOutcome(
                status="error", observed_at=self._clock(), error=f"{type(exc).__name__}: {exc}"))
        except Exception:                                          # noqa: BLE001 — 레저 장애로 종결을 막지 않는다(트리아지)
            pass

    async def _fail(self, record, case_id: str, exc: Exception) -> str:
        """실패 경로의 단일 합류점(F1·F3 공통) — 레저 기록 후 케이스를 종결한다.

        F3 소진(_run_with_f3가 다시 던진 "재개 실패 — ..." 예외)이든,
        deps_for_site/build_engine/evidence_refs_for_case/_finish처럼 그래프
        호출 밖에서 난 예외든 구분하지 않는다 — 어느 쪽이든 이 시점에서
        레코드는 아직 investigating이므로(닫힌 적이 없다) 그대로
        close_case(discard_threads=True)로 닫아 owner 없는 investigating
        고아를 남기지 않는다. 종결 자체가 실패하면(예: repo/checkpointer
        장애) 그 실패만 추가로 레저에 남기고 더 시도하지 않는다 — 워커는
        어떤 경로로도 raise하지 않는다.

        run_once/resume_once의 반환값("failed")과 도메인 CaseStatus("closed")는
        다른 축이다 — close_case가 실제로 상태를 closed로 전이시키므로, 성공하면
        _finish의 두 종결 경로(awaiting_human/closed)와 동일하게 case_status_event를
        낸다. 그래야 보고 채널(계획 5)이 실패 종결도 놓치지 않는다. close_case
        자체가 실패한 경로는 상태가 실제로 안 바뀌었을 수 있으므로 이벤트를 내지
        않는다.
        """
        self._log_failure(record, case_id, exc)
        reason = f"워커 실패 — {type(exc).__name__}: {exc}"
        try:
            await close_case(case_id, repo=self._repo, checkpointer=self._checkpointer,
                             clock=self._clock, reason=reason, discard_threads=True)
            self._emit_status(case_id, "closed", reason=reason)
            await self._emit_closed(case_id)
        except Exception as close_exc:                              # noqa: BLE001
            self._log_failure(record, case_id, close_exc)
        return "failed"

    async def _skip_unregistered_site(self, record, case_id: str, gbm: str, fct: str) -> str:
        """미등록 사이트(deps_for_site가 None) — 케이스를 닫지 않고 레저에만
        skipped를 남긴다(트리아지). 설정이 일시적으로 어긋난 것뿐 케이스의
        문제가 아니므로, F1과 달리 종결하지 않는다 — lease는 finally의
        _release_safely가 풀어주므로 다음 재큐 잡(daemon.requeue_job)이 다시 집어 준다."""
        try:
            self._ledger.record_run(gbm, fct, f"worker:{case_id}", CheckOutcome(
                status="skipped", observed_at=self._clock(),
                skipped_reason=f"미등록 사이트 — {gbm}/{fct}"))
        except Exception:                                          # noqa: BLE001
            pass
        return "skipped"

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

    async def _invoke_with_keepalive(self, record, case, deps, engine, case_id: str,
                                     thread_id: str, initial_evidence, *, resume=None,
                                     allow_restart: bool = True,
                                     interaction_policy: str = "autonomous"):
        """_run_with_f3를 keepalive 태스크로 감싼다(I5) — 엔진 호출이 lease_ttl_s를
        넘게 걸려도 그 사이 lease가 만료돼 다른 워커에 넘어가지 않도록, 호출이
        끝날 때까지 lease_ttl_s/3 간격으로 lease를 갱신하고 finally에서 취소한다."""
        keepalive = asyncio.ensure_future(self._keepalive_loop(case_id))
        try:
            return await self._run_with_f3(
                record, case, deps, engine, case_id, thread_id, initial_evidence,
                resume=resume, allow_restart=allow_restart, interaction_policy=interaction_policy)
        finally:
            keepalive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive

    async def run_once(self, case_id: str, *, interaction_policy: str = "autonomous") -> str:
        """큐에서 꺼낸 케이스 하나를 lease 아래 조사하고 종결까지 시도한다.

        "closed"/"awaiting_human"/"busy"/"skipped"/"failed" 중 하나를
        돌려준다 — 어떤 경로로도 raise하지 않는다.

        interaction_policy(계획 5): investigate_case로 그대로 패스스루한다 —
        데몬 경로는 기본값 "autonomous"를 그대로 쓰고, CLI `chat`은
        "interactive"를 넘겨 ask_human이 자동응답 대신 정말로 멈추게(interrupt)
        한다. 기존 호출부(daemon 등)는 이 인자를 넘기지 않으므로 동작이
        바뀌지 않는다.
        """
        record = None
        try:
            record = self._repo.get(case_id)
            leased = self._repo.claim(case_id, self._owner,
                                      now=self._clock(), ttl_s=self._lease_ttl_s)
            if leased is None:
                return "busy"                                       # 레저 이벤트 없음(경합은 정상)
            if leased.status == "open":
                record = transition(leased, "investigating", clock=self._clock)
                became_investigating = True
            else:
                # requeue_open이 죽은 워커에게서 회수한 investigating 케이스 —
                # 전이표에 investigating→investigating이 없으므로 재전이하지
                # 않고 lease만(claim이 이미) 새로 잡은 채로 진행한다.
                record = leased
                became_investigating = False

            # deps_for_site를 스레드 등록보다 먼저 확인한다 — 미등록 사이트라
            # 아무것도 저장하지 않고 skip하면(레코드는 손대지 않은 채) 다음
            # requeue_open이 그대로 다시 집어준다. 순서를 반대로 하면(등록부터)
            # 실제로 열리지도 않은 스레드가 thread_ids에 phantom으로 쌓인다.
            deps = self._deps_for_site(record.gbm, record.fct)
            if deps is None:
                return await self._skip_unregistered_site(record, case_id, record.gbm, record.fct)

            thread_id = self._next_thread_id(record, case_id)
            record = self._register_thread(record, thread_id)
            # 정책을 레코드에 박제한다 — resume_once는 자기 호출부에서 정책을 받지
            # 않고 여기 저장된 값을 읽는다(스레드 재시작이 조용히 autonomous로
            # 강등되던 버그). 반드시 아래 repo.save 앞이어야 한다.
            if record.interaction_policy != interaction_policy:
                record = record.model_copy(update={"interaction_policy": interaction_policy})
            self._repo.save(record)
            # I2: 저장 뒤에야 emit한다 — 저장 전에 내면(미등록 사이트로 skip될 경우
            # 등) 저장이 아예 안 일어난 record에 대해 "investigating" 이벤트만
            # 나가는 유령 전이가 생긴다.
            if became_investigating:
                self._emit_status(case_id, "investigating")
            engine = self._engine_for(record.gbm, record.fct, deps)
            digests = self._knowledge_digests_for_site(record.gbm, record.fct)
            case = record.to_case().model_copy(update={"knowledge_digests": digests})
            initial_evidence = evidence_refs_for_case(self._store, case_id)

            record, result = await self._invoke_with_keepalive(
                record, case, deps, engine, case_id, thread_id, initial_evidence,
                interaction_policy=interaction_policy)
            return await self._finish(record, result)
        except Exception as exc:
            return await self._fail(record, case_id, exc)
        finally:
            await self._release_safely(case_id)

    async def resume_once(self, case_id: str, answer) -> str:
        """awaiting_human 케이스를 사람의 답변으로 재개한다.

        최신 스레드의 저장된 schema 버전이 지금 엔진과 다르면(엔진 배선이
        바뀐 뒤 재개하려는 경우) resume을 시도하지 않고 새 스레드로 신규
        조사를 연다 — 옛 체크포인트를 새 그래프 모양으로 재개하면 어떤
        실패를 낼지 예측할 수 없기 때문이다. 이 경로는 이미 "새 스레드로
        재시작"한 것이므로 실패해도 또 재시작하지 않는다(allow_restart=False)
        — 그래야 총 재시작 횟수가 F3와 마찬가지로 최대 1회로 유지된다.
        이 경로도 새 스레드가 investigate_case로 시작해 resume 메커니즘이
        없으므로, 재시작 전에 답변을 evidence로 박제한다(I4).
        """
        record = None
        try:
            record = self._repo.get(case_id)
            leased = self._repo.claim(case_id, self._owner,
                                      now=self._clock(), ttl_s=self._lease_ttl_s)
            if leased is None:
                return "busy"
            record = transition(leased, "investigating", clock=self._clock)

            deps = self._deps_for_site(record.gbm, record.fct)
            if deps is None:
                return await self._skip_unregistered_site(record, case_id, record.gbm, record.fct)
            engine = self._engine_for(record.gbm, record.fct, deps)
            digests = self._knowledge_digests_for_site(record.gbm, record.fct)
            case = record.to_case().model_copy(update={"knowledge_digests": digests})

            latest_thread_id = record.thread_ids[-1] if record.thread_ids else None
            version_matches = (latest_thread_id is not None
                              and record.thread_versions.get(latest_thread_id)
                              == ENGINE_SCHEMA_VERSION)

            # I2: 두 분기 모두 emit을 자기 repo.save 바로 뒤로 미룬다 — deps_for_site가
            # None이면(위에서 이미 skip) 이 아래로 내려오지 않으므로 저장 없는
            # "investigating" 유령 이벤트가 나가지 않는다.
            if version_matches:
                initial_evidence = evidence_refs_for_case(self._store, case_id)
                self._repo.save(record)
                self._emit_status(case_id, "investigating")
                record, result = await self._invoke_with_keepalive(
                    record, case, deps, engine, case_id, latest_thread_id, initial_evidence,
                    resume=answer, interaction_policy=record.interaction_policy)
            else:
                # 새 스레드로 신규 조사를 여는 재시작 — resume 메커니즘이 없으므로
                # 답변이 자연히 사라진다. 재시작 전에 evidence로 박제한다(I4).
                self._store.put_evidence(
                    case_id, "human:answer", {"question": record.question, "answer": answer},
                    as_of=self._clock())
                await self._discard_thread(latest_thread_id)
                fresh_thread_id = self._next_thread_id(record, case_id)
                record = self._register_thread(record, fresh_thread_id)
                self._repo.save(record)
                self._emit_status(case_id, "investigating")
                initial_evidence = evidence_refs_for_case(self._store, case_id)
                record, result = await self._invoke_with_keepalive(
                    record, case, deps, engine, case_id, fresh_thread_id, initial_evidence,
                    allow_restart=False, interaction_policy=record.interaction_policy)
            return await self._finish(record, result)
        except Exception as exc:
            return await self._fail(record, case_id, exc)
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
