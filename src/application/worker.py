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
from src.application.history import find_history
from src.application.events import case_status_event
from src.application.graph import build_engine
from src.application.lifecycle import ENGINE_SCHEMA_VERSION, release_lease, transition
from src.application.usecase import investigate_case, resume_case
from src.domain.patrol import CheckOutcome
from src.domain.snapshot import VerdictSnapshot
from src.patrol.gate import evidence_refs_for_case
from src.domain.report_model import build_report_model


_SALVAGE_TIMEOUT_S = 30   # 실패 종결의 흔적 구제 상한 — 최선노력이지 계약이 아니다


def _dump_item(item):
    """pydantic 모델이면 JSON 값으로 내리고, 이미 평범한 값이면 그대로 둔다.

    엔진의 최종 State는 모델을 주지만, 체크포인트에서 구제한 값(_salvage_case_file)은
    역직렬화 방식에 따라 dict일 수 있다 — 무raise 규율상 여기서 터지면 안 된다.
    """
    dump = getattr(item, "model_dump", None)
    return dump(mode="json") if callable(dump) else item


def _case_file_snapshot(result: dict) -> dict:
    """엔진 최종 State(dict)에서 계획 5가 읽을 케이스 파일 스냅샷을 뽑는다(I6).

    스레드 체크포인트는 보존 TTL로 폐기될 수 있으므로(infrastructure/retention.py),
    보고서 소스는 Store에 별도로 박제한다.

    verify_attempts를 싣는 이유: 보고서 §1의 검증 단계가 "그냥 통과"와 "재작성 후
    통과(강등)"를 구별하는 유일한 구조적 신호다 — caveat 문자열을 냄새 맡는 대신
    이 값을 본다.

    knowledge_digests는 State의 Case가 들고 있다. 여기로 옮겨 두지 않으면 보고서가
    "그때 어떤 토폴로지·규칙·API 명세를 보고 있었나"를 말할 수 없다 — 구제 경로
    (_salvage_case_file)에서는 case 자체가 없을 수 있어 방어적으로 읽는다.
    """
    case = result.get("case")
    # 구제 경로(_salvage_case_file)의 case는 역직렬화 방식에 따라 dict일 수 있다 —
    # 바로 위 _dump_item docstring이 적어 둔 사실이다. getattr만 쓰면 그때 digest가
    # 조용히 사라져 보고서가 "없음(기록되지 않음)"을 찍는다.
    digests = case.get("knowledge_digests") if isinstance(case, dict) \
        else getattr(case, "knowledge_digests", None)
    # 리드에게 실제로 보여준 이력. 여기 안 남기면 "이력이 도움이 됐나, 앵커링이었나"를
    # 영원히 못 묻는다 — 지금은 공짜, 나중엔 복구 불가(방향 문서 §4.5).
    shown = case.get("history") if isinstance(case, dict) else getattr(case, "history", None)
    history_shown = [{"case_id": h["case_id"] if isinstance(h, dict) else h.case_id,
                      "tier": h["tier"] if isinstance(h, dict) else h.tier}
                     for h in (shown or [])]
    return {
        "history_shown": history_shown,
        "knowledge_digests": dict(digests) if isinstance(digests, dict) else {},
        "plan_tasks": [_dump_item(t) for t in result.get("plan_tasks", [])],
        "hypotheses": [_dump_item(h) for h in result.get("hypotheses", [])],
        "round": result.get("round", 0),
        "qa_log": result.get("qa_log", []),
        "verify_problems": result.get("verify_problems", []),
        "verify_attempts": result.get("verify_attempts", 0),
    }


class CaseQueue:
    """asyncio.Queue[str] 래퍼 — put/get/qsize와 재시작 재큐잉, 그리고 중복 제거.

    같은 id를 두 번 들고 있지 않는다(큐 안이든 소비 중이든). 둘째 항목이 첫째가
    investigating(자기 lease)으로 도는 사이에 소비되면 run_once→claim(같은 owner는
    항상 재획득)→"회수한 investigating" 분기→**새 스레드로 처음부터** 조사해 원래
    스레드를 버린다(계획 13 3차 검증 리뷰 W3). requeue_job은 30초마다 돌므로 슬롯이
    포화되면 중복은 정상 운영에서 난다. 소비자는 끝에 `done`을 불러야 한다 — 안 부르면
    그 케이스는 이 프로세스에서 다시는 큐에 못 들어간다(파킹 뒤 답이 실려도).
    """

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._held: set[str] = set()          # 큐 안 + 소비 중

    def _offer(self, case_id: str) -> bool:
        if case_id in self._held:
            return False
        self._held.add(case_id)
        self._queue.put_nowait(case_id)
        return True

    async def put(self, case_id: str) -> None:
        self._offer(case_id)                  # 큐는 무제한이라 대기하지 않는다

    async def get(self) -> str:
        return await self._queue.get()

    def done(self, case_id: str) -> None:
        """소비가 끝났다 — 결과와 무관하게. 이후의 put/requeue가 다시 넣을 수 있다."""
        self._held.discard(case_id)

    def qsize(self) -> int:
        return self._queue.qsize()

    def requeue_open(self, repo, *, clock) -> int:
        """접수를 마친 open 케이스 + lease가 만료된 investigating 케이스를 큐에 넣는다.

        재시작 내구성의 핵심: open은 아직 아무도 손대지 않은 케이스라 회수한다 —
        단 **접수가 끝난 것만**(`intake_done`). 접수 중인 케이스도 open이라 여기서
        구별하지 않으면 워커가 그것을 집어 대상 없이 조사하고, 접수와 워커가 같은
        레코드를 놓고 경합한다(계획 12 F1). investigating은 죽은 워커가 lease를 쥔 채
        프로세스만 죽었을 수 있는 상태다 — lease_until이 없거나(비정상
        레코드) clock() 이전으로 지났으면 그 워커는 더 이상 살아있지 않다고
        보고 회수한다. lease가 아직 유효한 investigating은 다른(살아있는)
        워커가 지금 붙들고 있는 것이므로 건드리지 않는다. awaiting_human은
        `pending_answer`가 실린 것만 — 그것이 계획 13의 명령 채널이다.

        실제로 투입한 케이스 수를 돌려준다(이미 큐에 있거나 소비 중인 것은 제외).
        큐는 무제한(maxsize=0)이므로 블로킹 없이 즉시 채운다 — 워커가 아직 돌기
        전(이벤트 루프 기동 이전)에도 호출할 수 있어야 하기 때문이다.
        """
        now = clock()
        records = [r for r in repo.list_by_status("open") if r.intake_done]
        # 답이 실린 파킹 케이스도 대상이다(계획 13 명령 채널). 답 없는 파킹은 여전히
        # 아니다 — 워커가 재개할 재료가 없다.
        records += [r for r in repo.list_by_status("awaiting_human")
                    if r.pending_answer is not None]
        for record in repo.list_by_status("investigating"):
            if record.lease_until is None or record.lease_until < now:
                records.append(record)
        return sum(self._offer(record.id) for record in records)


class InvestigationWorker:
    """lease로 케이스 하나씩 조사 엔진에 태우고 결과에 따라 수명주기를 진행한다."""

    def __init__(self, queue: CaseQueue, *, repo, store,
                deps_for_site: Callable[[str, str], Any], checkpointer, clock,
                owner: str, max_concurrent: int, lease_ttl_s: float, ledger,
                knowledge_digests_for_site: Callable[[str, str], dict[str, str]],
                on_event: Callable[[Any], None] | None = None,
                on_closed: Callable[[str], Awaitable] | None = None,
                max_intake_turns: int = 3,
                max_wall_clock_s: float | None = None,
                snapshots=None, ticker=None):
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
        self._max_wall_clock_s = max_wall_clock_s
        self._snapshots = snapshots      # VerdictSnapshotPort | None
        self._ticker = ticker            # Ticker | None — None이면 경과는 "미측정"이다
        # 케이스별 엔진 경과의 누적. 재시작(F3)이 같은 케이스에 두 구간을 만들 수 있어
        # 더한다. 큐가 케이스당 한 번만 소비하므로(계획 13 인계 #12의 픽스) 섞이지 않는다.
        self._elapsed: dict[str, float] = {}
        self._on_closed = on_closed   # 계획 5 — 케이스가 닫힌 직후(성공/실패 종결 모두) 부르는 발행 훅
        self._max_intake_turns = max_intake_turns
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

    def _case_for(self, record, deps, digests: dict):
        """그래프에 넘길 Case — 지식 digest와 **이번 케이스의 이력**을 실어서.

        이력이 deps가 아니라 Case에 실리는 이유: 엔진은 사이트당 한 번 조립돼 캐시되므로
        (_engine_for) deps의 정적 필드는 케이스마다 바꿀 수 없다. State에 실리면
        체크포인트에도 남아 "리드에게 무엇을 보여줬나"가 나중에 복구 가능해진다.
        """
        return record.to_case().model_copy(update={
            "knowledge_digests": digests,
            "history": find_history(record, repo=self._repo, snapshots=self._snapshots,
                                    topology=getattr(deps, "topology", None))})

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
            # 종류를 명시해 둔다(계획 12) — 안 붙이면 접수/조사 구별이 "라벨이
            # 있다"가 아니라 "라벨이 없다"에 기대게 되고, 접수 쪽 가드가 새로
            # 파킹된 케이스에는 아무 효과가 없다.
            self._repo.save(waiting.model_copy(update={
                "question": question, "question_kind": "investigation",
                "question_seq": waiting.question_seq + 1}))     # 새 질문 — attach가 열린다
            self._emit_status(record.id, "awaiting_human")
            return "awaiting_human"
        verdict = result.get("verdict")
        if verdict is None:
            # conclude가 항상 verdict를 만들지만, 방어적으로 막는다(I6) — 여기서
            # raise하면 run_once/resume_once의 최외곽 except가 F1과 동일하게
            # 레저 기록 후 케이스를 닫는다(_fail 경로).
            raise RuntimeError("verdict 없이 종료")
        self._store.put_verdict(record.id, verdict)
        elapsed = self._elapsed.pop(record.id, None)
        self._store.put_case_file(record.id, {**_case_file_snapshot(result),
                                              "duration_s": elapsed})
        summary = verdict.narrative[:200]
        self._repo.save(current.model_copy(update={"verdict_summary": summary, "question": None}))
        await close_case(record.id, repo=self._repo, checkpointer=self._checkpointer,
                         clock=self._clock, reason="조사 완료", discard_threads=False)
        self._emit_status(record.id, "closed")
        self._record_metrics(record.gbm, record.fct, outcome="closed", elapsed=elapsed)
        self._record_snapshot(record.id, outcome="closed")
        await self._emit_closed(record.id)
        return "closed"

    def _record_metrics(self, gbm: str, fct: str, *, outcome: str, elapsed: float | None) -> None:
        """관측치를 sink에 남긴다 — 실패해도 조사에 영향이 없다(규율 1).

        스냅샷 기록보다 **앞에** 둔다: `_record_snapshot`은 snapshots가 없으면 즉시
        돌아가므로, 그 안에 넣으면 스냅샷 저장소를 안 쓰는 배치에서 메트릭이 조용히
        사라진다(CLAUDE.md "판정 로직보다 먼저 실행되는 가드").
        """
        if elapsed is None:
            return                        # 안 잰 것을 0으로 적지 않는다
        try:
            self._ledger.record_metric("investigation.duration_s", elapsed,
                                       tags={"gbm": gbm, "fct": fct, "outcome": outcome},
                                       at=self._clock())
        except Exception:                                          # noqa: BLE001 — 무raise
            pass

    def _record_snapshot(self, case_id: str, *, outcome: str) -> None:
        """종결 시점의 기계 판정을 박제한다 — retention이 Verdict를 지운 뒤에도 남는다.

        기록 실패가 이미 끝난 종결을 뒤집지 않는다(발행 훅과 같은 계약). 순서도
        같은 이유로 종결 뒤다 — 스냅샷을 남기려다 케이스가 안 닫히면 본말전도다.

        verify_demoted를 ReportModel의 단계 판정에서 가져오는 이유는 그 규칙이 이미
        한 곳에 있기 때문이다 — 여기서 verify_attempts를 다시 해석하면 보고서와
        스냅샷이 언젠가 다른 말을 한다.
        """
        if self._snapshots is None:
            return
        try:
            record = self._repo.get(case_id)
            verdict = self._store.get_verdict(case_id)
            model = build_report_model(record, verdict=verdict,
                                       evidence=self._store.list_evidence(case_id),
                                       case_file=self._store.get_case_file(case_id),
                                       clock=self._clock)
            verify_stage = next((s for s in model.stages if s.stage == "verify"), None)
            self._snapshots.put(VerdictSnapshot(
                case_id=case_id, closed_at=self._clock(), gbm=record.gbm, fct=record.fct,
                fingerprint=record.fingerprint, target_locator=record.target_locator,
                origin=record.origin, concern=record.concern, outcome=outcome,
                verdict_type=verdict.verdict_type if verdict else None,
                root_cause_component=(verdict.root_cause.component
                                      if verdict and verdict.root_cause else None),
                alternates=[a.component for a in verdict.alternates] if verdict else [],
                confidence=verdict.confidence if verdict else None,
                rounds=model.round_no or 0, duration_s=model.observability.duration_s,
                evidence_count=len(model.evidence),
                task_error_rate=model.task_error_rate,
                verify_demoted=bool(verify_stage and verify_stage.mark == "warn"),
                history_shown=[h for h in (self._store.get_case_file(case_id) or {})
                               .get("history_shown", []) if isinstance(h, dict)],
                knowledge_digests=self._knowledge_digests_for_site(record.gbm, record.fct)))
        except Exception:                                          # noqa: BLE001
            pass

    async def _salvage_case_file(self, record, case_id: str, *, elapsed=None) -> None:
        """close_case가 스레드를 지우기 전에 체크포인트에서 조사 흔적을 구제한다.

        순서가 계약이다 — discard_threads=True로 스레드를 폐기한 뒤에는 읽을 것이
        없다. 구제 실패도 케이스 파일에 남긴다: 보고서가 "조사한 게 없다"와
        "흔적을 잃었다"를 구별할 수 있어야 조용한 생략이 되지 않는다.

        스레드는 역순으로 훑는다 — F3 재시작 경로에서 첫 스레드는 이미 폐기됐고
        재시작 스레드가 최신이다.
        """
        snapshot: dict = {"partial": True}
        try:
            engine = self._engines.get((record.gbm, record.fct)) if record is not None else None
            if engine is None:
                raise RuntimeError("엔진이 조립되기 전에 실패해 체크포인트가 없다")
            # 상한을 건다 — keepalive는 이미 취소됐으므로 여기서 늘어지면 lease가
            # 만료돼 재큐가 같은 케이스를 다시 내주는 동시에 동시 상한 슬롯은 계속
            # 점유된다. 구제는 최선노력이지 계약이 아니다.
            async def _read_latest():
                for thread_id in reversed(list(record.thread_ids)):
                    state = await engine.aget_state({"configurable": {"thread_id": thread_id}})
                    values = getattr(state, "values", None)
                    if isinstance(values, dict) and values:
                        return values
                return None

            values = await asyncio.wait_for(_read_latest(), timeout=_SALVAGE_TIMEOUT_S)
            if values is not None:
                snapshot.update(_case_file_snapshot(values))
        except Exception as exc:                                   # noqa: BLE001
            snapshot["salvage_error"] = f"{type(exc).__name__}: {exc}"
        for key in ("plan_tasks", "hypotheses", "qa_log", "verify_problems"):
            snapshot.setdefault(key, [])
        # 실패한 조사가 분모에서 빠지면 "느린 조사가 더 틀리나"에 생존 편향이 생긴다.
        snapshot["duration_s"] = elapsed
        try:
            # 구제가 아무것도 못 건졌는데 기존 케이스 파일이 있으면 덮지 않는다.
            # _finish가 완전본을 쓴 뒤 종결 과정에서 터지면 _fail이 도는데, 그때
            # partial 빈 껍데기로 덮으면 체크포인트 TTL 이후 유일한 조사 기록이
            # 사라진다 — 이 함수의 존재 이유 자체가 무너진다.
            salvaged_nothing = not any(snapshot[k] for k in
                                       ("plan_tasks", "hypotheses", "qa_log", "verify_problems"))
            if salvaged_nothing and self._store.get_case_file(case_id) is not None:
                return
            self._store.put_case_file(case_id, snapshot)
        except Exception:                                          # noqa: BLE001
            pass

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
        elapsed = self._elapsed.pop(case_id, None)
        await self._salvage_case_file(record, case_id, elapsed=elapsed)  # close_case가 스레드를 지우기 전에
        if record is not None:
            self._record_metrics(record.gbm, record.fct, outcome="failed", elapsed=elapsed)
        reason = f"워커 실패 — {type(exc).__name__}: {exc}"
        try:
            await close_case(case_id, repo=self._repo, checkpointer=self._checkpointer,
                             clock=self._clock, reason=reason, discard_threads=True)
            self._emit_status(case_id, "closed", reason=reason)
            self._record_snapshot(case_id, outcome="failed")
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
        started = self._ticker() if self._ticker is not None else None
        try:
            call = self._run_with_f3(
                record, case, deps, engine, case_id, thread_id, initial_evidence,
                resume=resume, allow_restart=allow_restart, interaction_policy=interaction_policy)
            if self._max_wall_clock_s is None:
                return await call
            # 상한 초과는 예외로 던져 최외곽 except가 F1과 동일하게 처리하게 한다 —
            # 여기서 직접 케이스를 닫으면 종결 경로가 둘로 갈린다.
            return await asyncio.wait_for(call, timeout=self._max_wall_clock_s)
        finally:
            if started is not None:
                self._elapsed[case_id] = self._elapsed.get(case_id, 0.0) + (self._ticker() - started)
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
            if leased.status == "closed":
                # 주기 재큐가 같은 케이스를 여러 번 넣을 수 있고, lease_is_free는 같은
                # owner(데몬은 프로세스당 문자열 하나다)의 재획득을 항상 허용한다.
                # 가드가 없으면 닫힌 케이스를 처음부터 다시 조사해 판정과 케이스 파일을
                # 덮고, close_case가 closed→closed로 터져 _fail이 흔적까지 지운다.
                return "stale"
            if leased.status == "awaiting_human":
                # 답 없는 파킹은 재개할 재료가 없다. 실린 답 때문에 큐에 들어간 항목이
                # 소비되기 전에 다른 경로(case resume)가 그 답을 가져가면 여기로 오는데,
                # 아래 else 분기는 이것을 "회수한 investigating"으로 보고 새 스레드로
                # 처음부터 조사해 원래 스레드와 사람에게 물은 질문을 버린다(리뷰 B1).
                return "stale"
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
            case = self._case_for(record, deps, digests)
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
            if leased.status == "closed":
                return "stale"                                      # run_once와 같은 이유
            if leased.pending_answer is not None:
                # 채널에 실린 답이 있는데 직접 답(case resume)이 먼저 왔다. **lease 아래에서**
                # 가져간다 — attach는 lease가 살아 있으면 busy로 거절하므로 여기서 본 것이
                # 전부다. 두고 재개하면 그래프가 다음 질문으로 파킹했을 때 requeue가 옛
                # 질문의 답을 새 질문에 소비하고(블로커 3의 혼용 경로), 아래 통째 save가
                # 지우면 202를 받은 답이 증거 없이 사라진다. 직접 답이 이긴다(사람이 지금
                # 보고 있는 쪽). lease 밖(answer_case)에서 하면 claim이 busy로 끝나도 답은
                # 이미 버려진 뒤다(리뷰 L4).
                superseded = self._repo.take_answer(case_id, now=self._clock())
                if superseded is not None:
                    self._store.put_evidence(
                        case_id, "human:answer_dropped",
                        {"answer": superseded, "reason": "superseded"}, as_of=self._clock())
                leased = self._repo.get(case_id)    # take가 바꾼 필드를 들고 가야 save가 안 되돌린다
            record = transition(leased, "investigating", clock=self._clock)

            deps = self._deps_for_site(record.gbm, record.fct)
            if deps is None:
                return await self._skip_unregistered_site(record, case_id, record.gbm, record.fct)
            engine = self._engine_for(record.gbm, record.fct, deps)
            digests = self._knowledge_digests_for_site(record.gbm, record.fct)
            case = self._case_for(record, deps, digests)

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

    async def consume(self, case_id: str) -> str:
        """큐에서 나온 케이스 하나를 처리한다 — 실린 답이 있으면 그것부터.

        답은 `take_answer`로 **가져가며 지운다**(answered_seq를 맞춘다). 지운 뒤
        소비가 busy/skipped/not_ours/stale로 끝나면 `restore_answer`로 되돌린다 — 그 사이
        그래프가 새 질문으로 파킹했으면 되돌리지 않고(옛 답이 새 질문에 붙는다)
        `human:answer_dropped` 증거로 남긴다. "지운 뒤 실패해도 증거로 남아 잃지
        않는다"는 전 커밋의 주장은 거짓이었다 — 그 경로들은 증거 박제 전에 끝난다.

        분기는 `answer_case` 하나다(계획 12). run_once처럼 절대 raise하지 않는다.
        """
        from src.application.answer import answer_case      # 순환 회피: answer→intake
        record = None
        try:
            # 첫 읽기도 try 안에 — KeyError만 잡으면 "mongo down"이 그대로 raise되어
            # run_forever의 태스크가 조용히 삼킨다(규율 1). 사이트는 그때 모른다.
            try:
                record = self._repo.get(case_id)
            except KeyError:
                return "skipped"
            answer = self._repo.take_answer(case_id, now=self._clock())
            if answer is None:
                return await self.run_once(case_id)
            deps = self._deps_for_site(record.gbm, record.fct)
            result = await answer_case(
                case_id, answer, repo=self._repo, store=self._store, deps=deps,
                topology=getattr(deps, "topology", None), worker=self, clock=self._clock,
                max_intake_turns=self._max_intake_turns,
                interaction_policy=record.interaction_policy, on_event=self._on_event)
            if result in ("busy", "skipped", "not_ours", "stale"):
                if not self._repo.restore_answer(case_id, answer=answer, now=self._clock()):
                    self._store.put_evidence(case_id, "human:answer_dropped",
                                             {"answer": answer, "reason": result},
                                             as_of=self._clock())
            return result
        except Exception as exc:                                   # noqa: BLE001 — 무raise
            try:
                gbm, fct = (record.gbm, record.fct) if record is not None else ("", "")
                self._ledger.record_run(gbm, fct, f"worker:{case_id}", CheckOutcome(
                    status="error", observed_at=self._clock(),
                    error=f"답 소비 실패 — {type(exc).__name__}: {exc}"))
            except Exception:                                      # noqa: BLE001
                pass
            return "failed"

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
                    if get_task.done() and not get_task.cancelled():
                        # get도 같은 wait에서 끝났다 — id는 큐에서 빠졌고 cancel은 no-op.
                        # 처리하지 않지만(새 프로세스의 requeue가 회수) held에는 안 남긴다.
                        self._queue.done(get_task.result())
                    break
                stop_task.cancel()
                case_id = get_task.result()
                await semaphore.acquire()

                async def _consume(cid: str) -> None:
                    try:
                        await self.consume(cid)
                    finally:
                        self._queue.done(cid)         # 큐 중복 제거의 해제 — 결과 무관
                        semaphore.release()

                task = asyncio.ensure_future(_consume(case_id))
                running.add(task)
                task.add_done_callback(running.discard)
        finally:
            if running:
                await asyncio.gather(*running, return_exceptions=True)
