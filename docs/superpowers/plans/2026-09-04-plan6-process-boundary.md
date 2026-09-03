# 계획 6 — 프로세스 경계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 케이스 큐·lease·이벤트를 프로세스 메모리에서 Mongo로 옮겨, 별도 `api`/`worker` 프로세스가 같은 케이스를 안전하게 나눠 갖고 같은 이벤트를 볼 수 있게 만든다.

**Architecture:** v1은 "단일 프로세스가 자기 메모리의 큐를 자기 세마포어로 소비한다"는 가정 위에 있다. 이 계획은 그 가정을 세 곳에서 걷어낸다 — ①`requeue_open`을 1회성에서 주기 잡으로 바꿔 다른 프로세스가 쓴 케이스를 보이게 하고, ②`lease` 획득을 read-modify-write에서 CAS로 바꿔 동시 claim을 막고, ③이벤트를 프로세스 내 stdout에서 `case_events` 컬렉션으로 옮겨 밖에서 tail할 수 있게 한다. 여기에 조사 wall-clock 상한과 `LedgerPort` ABC 분할을 얹는다.

**Tech Stack:** Python 3.12 · pydantic 2 · pymongo(동기) · APScheduler 3.x · pytest(`asyncio_mode=auto`) · mongomock

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) 의 **P1 + P2**. §3.1(프로세스 경계) · §3.2(포트 목표) · §3.3(이벤트 어휘) · §5(서비스화 핵 3개).

## Global Constraints

프로젝트 전역 규율이다. **모든 태스크의 요구사항에 암묵적으로 포함된다.**

- **무raise**: 어댑터·프로브·판정기·게이트·서브에이전트·워커·순찰·발행 전 층은 예외를 던지지 않는다. 실패는 반환값의 상태로 흡수하고, 최외곽 `try/except Exception`이 마지막 방어선이다. 허용된 예외는 셋뿐이다: `CaseStorePort.get_evidence`의 `KeyError`, `KnownRuleError`, 워커의 `record_send`/`pending_sends`.
- **시계 주입**: `src/__main__.py`(CLI 경계) 밖에서 `datetime.now()`를 직접 부르지 않는다. `clock: Callable[[], datetime]`을 인자로 받는다.
- **StrictModel**: 새 pydantic 모델은 `BaseModel`이 아니라 `src/config/schema_app.py`의 `StrictModel`(`extra="forbid"`)을 상속한다.
- **주석·문서는 한국어, WHY만.** 식별자가 이미 WHAT을 말하면 주석을 달지 않는다. **커밋 메시지는 영어 제목 + 한국어 본문**(WHY). 변수·함수·클래스 이름은 영어.
- **커밋 트레일러**: 모든 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **테스트는 실제 시스템도 실제 LLM도 요구하지 않는다.** Mongo 계약 테스트만 `mongomock`, LLM은 `ScriptedLLM`/`GenericFakeChatModel`.
- **결정론이 최우선.** 시계는 항상 고정값을 주입한다.
- **ISO 문자열 범위 비교 금지**: Mongo 문서의 `datetime`은 `model_dump(mode="json")`으로 ISO 문자열이 된다. 마이크로초 유무로 길이가 달라져 **사전식 비교가 시간 순서와 어긋난다.** DB의 `$lt`/`$gt`/`sort()`에 시각 필드를 쓰지 말고 Python에서 파싱해 비교한다(`mongo_store.py` 모듈 docstring, `purge_evidence_before` 선례).
- **문서가 주장하는 배선을 믿지 않는다**: 어떤 문서나 주석이 "X가 Y를 부른다"고 하면 `grep`으로 호출부를 확인하기 전까지 사실로 취급하지 않는다.
- **테스트 명령**: `.venv/bin/python -m pytest tests/ -q` (기준선 **262 passed**).
- **작업 디렉터리**: `/home/hchju777/langgraph_ws/deepagent-template`. 서브에이전트에게 파일 경로를 지시할 때는 **반드시 절대 경로**를 쓴다(상대 경로로 메인 체크아웃을 오염시킨 사고가 실제로 있었다).

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `src/domain/events.py` | 이벤트 봉투 + 이벤트 스토어 포트 | `seq` 필드, `verdict_formed` 어휘, `EventStorePort`, `InMemoryEventStore` |
| `src/domain/cases.py` | 케이스 레코드 + 저장소 포트 | `lease_is_free` 순수 함수, `CaseRepositoryPort.claim`, 인메모리 구현 |
| `src/application/lifecycle.py` | 상태 전이·lease 규칙 | `acquire_lease`가 `lease_is_free`에 위임(규칙 단일화) |
| `src/application/events.py` | State 변화 → 이벤트 매핑 | `verdict_formed` 매핑 |
| `src/application/worker.py` | 조사 실행 | `claim` 사용, wall-clock 상한, 소프트 전역 상한 |
| `src/infrastructure/mongo_store.py` | Mongo 어댑터 | `MongoEventStore`, `MongoCaseRepository.claim`, 인덱스 |
| `src/infrastructure/checkpointer.py` | 영속 조립 | `build_persistence`가 4-튜플 반환 |
| `src/infrastructure/retention.py` | 보존 스윕 | 이벤트 보존 |
| `src/patrol/daemon.py` | 데몬 조립 | 주기 재큐 잡, `open` 이벤트 |
| `src/patrol/ledger.py` | 레저 | ABC 2분할(`CheckLedgerPort`/`SendLedgerPort`) |
| `src/__main__.py` | CLI 경계 | 이벤트 싱크 조립, `build_persistence` 호출부 |
| `src/config/schema_app.py` | config 스키마 | `max_wall_clock_s`, `requeue_interval_s`, `events_d` |

**새 파일 없음.** 전부 기존 모듈의 확장이다 — 이 계획이 만드는 것은 새 서브시스템이 아니라 기존 배선의 경계 이동이다.

---

## Task 1: 이벤트 봉투에 seq와 스토어 포트

**Files:**
- Modify: `src/domain/events.py`
- Test: `tests/domain/test_events.py`

**Interfaces:**
- Produces: `EngineEvent.seq: int | None`, `EventStorePort.append(event) -> EngineEvent`, `EventStorePort.since(case_id, after_seq=0, limit=200) -> list[EngineEvent]`, `EventStorePort.prune_before(before) -> int`, `InMemoryEventStore()`

`seq`를 생산자가 아니라 스토어가 부여하는 이유: 순서의 권위는 쓰기를 직렬화하는 곳에만 있다. 그리고 결정론 테스트에서 시계가 고정값이라 같은 superstep의 이벤트가 **동일한 `at`을 갖는다** — `at`으로는 전순서가 나오지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/domain/test_events.py` 끝에 추가:

```python
def test_이벤트_스토어는_케이스별로_단조_seq를_부여한다():
    from src.domain.events import InMemoryEventStore
    store = InMemoryEventStore()
    a = store.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    b = store.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    c = store.append(EngineEvent(event="round_started", case_id="c-2", at=T))
    assert (a.seq, b.seq, c.seq) == (1, 2, 1)
    assert [e.seq for e in store.since("c-1")] == [1, 2]
    assert [e.seq for e in store.since("c-1", after_seq=1)] == [2]


def test_보존_삭제_후에도_seq는_재사용되지_않는다():
    # seq를 len(history)+1로 계산하면 prune 후 이미 나간 번호로 되돌아간다 —
    # 구독자가 `?since=2`로 재접속했을 때 새 이벤트를 영원히 못 본다.
    from datetime import timedelta
    from src.domain.events import InMemoryEventStore
    store = InMemoryEventStore()
    store.append(EngineEvent(event="round_started", case_id="c-1", at=T - timedelta(days=40)))
    store.append(EngineEvent(event="round_started", case_id="c-1", at=T - timedelta(days=40)))
    assert store.prune_before(T) == 2
    assert store.append(EngineEvent(event="round_started", case_id="c-1", at=T)).seq == 3
```

`tests/domain/test_events.py` 상단에 `T`가 없으면 추가한다:

```python
from datetime import datetime, timezone
T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/domain/test_events.py -q -k "seq"`
Expected: FAIL — `ImportError: cannot import name 'InMemoryEventStore'`

- [ ] **Step 3: 최소 구현**

`src/domain/events.py`에 `EngineEvent`의 `case_id` 아래로 필드를 추가하고, 파일 끝에 포트와 구현을 붙인다:

```python
class EngineEvent(StrictModel):
    """엔진이 밖으로 내보내는 이벤트 봉투. data 형태는 event 종류가 정한다."""
    event: EventKind
    schema_version: int = EVENT_SCHEMA_VERSION
    case_id: str
    at: datetime
    seq: int | None = None      # 스토어가 append에서 부여한다 — 생산자는 채우지 않는다.
                                # 고정 시계 테스트에서 같은 superstep의 이벤트가 동일한 at을
                                # 가지므로 at으로는 전순서가 나오지 않는다.
    data: dict = {}


class EventStorePort(ABC):
    """이벤트 로그 — 프로세스 밖 구독자가 읽을 수 있는 유일한 자리."""

    @abstractmethod
    def append(self, event: EngineEvent) -> EngineEvent:
        """case_id별 단조 seq를 부여해 적재하고, seq가 채워진 사본을 돌려준다."""
        ...

    @abstractmethod
    def since(self, case_id: str, after_seq: int = 0, limit: int = 200) -> list[EngineEvent]:
        """after_seq보다 큰 seq의 이벤트를 seq 오름차순으로 최대 limit건."""
        ...

    @abstractmethod
    def prune_before(self, before: datetime) -> int:
        """before 이전 이벤트를 삭제하고 삭제 건수를 반환한다."""
        ...


class InMemoryEventStore(EventStorePort):
    def __init__(self):
        self._events: dict[str, list[EngineEvent]] = defaultdict(list)
        # 카운터를 리스트 길이와 분리한다 — prune 후 길이가 줄면 이미 나간 seq를
        # 다시 부여해, `?since=N`으로 재접속한 구독자가 새 이벤트를 영영 놓친다.
        self._next: dict[str, int] = defaultdict(int)

    def append(self, event):
        self._next[event.case_id] += 1
        stamped = event.model_copy(update={"seq": self._next[event.case_id]})
        self._events[event.case_id].append(stamped)
        return stamped

    def since(self, case_id, after_seq=0, limit=200):
        if limit <= 0:                     # limit=0은 "0개"(레저의 runs와 같은 관례)
            return []
        return [e for e in self._events.get(case_id, []) if e.seq > after_seq][:limit]

    def prune_before(self, before):
        deleted = 0
        for case_id, history in self._events.items():
            kept = [e for e in history if e.at >= before]
            deleted += len(history) - len(kept)
            self._events[case_id] = kept
        return deleted
```

파일 상단 import에 추가:

```python
from abc import ABC, abstractmethod
from collections import defaultdict
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/domain/ -q`
Expected: PASS, 회귀 없음

- [ ] **Step 5: 커밋**

```bash
git add src/domain/events.py tests/domain/test_events.py
git commit -m "$(cat <<'EOF'
Let the event store own the ordering key

이벤트에 순서 키가 없어 Timeline도 재접속 재생도 불가능했다. at은 주입된
시계 판독값이라 결정론 테스트에서 같은 superstep의 이벤트가 동일한 값을 갖고,
프로덕션에서도 NTP 역점프에 순서가 뒤집힌다. seq는 쓰기를 직렬화하는 곳
(스토어)만 부여할 수 있으므로 생산자는 비워 둔다.

카운터를 리스트 길이와 분리한 것은 보존 삭제 때문이다 — 길이 기반이면 prune
후 이미 나간 번호를 재사용해 `?since=N` 구독자가 새 이벤트를 영원히 놓친다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Mongo 이벤트 스토어

**Files:**
- Modify: `src/infrastructure/mongo_store.py`, `src/infrastructure/checkpointer.py`, `src/__main__.py`(7개 호출부), `tests/infrastructure/test_checkpointer.py`
- Test: `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
- Consumes: Task 1의 `EventStorePort`, `EngineEvent.seq`
- Produces: `MongoEventStore(db)`, `build_persistence(cfg) -> (store, repo, ledger, events)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/infrastructure/test_mongo_store.py` 끝에 추가:

```python
def test_mongo_이벤트_스토어는_seq_순서로_돌려준다(db):
    from src.domain.events import EngineEvent
    from src.infrastructure.mongo_store import MongoEventStore
    events = MongoEventStore(db)
    a = events.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    b = events.append(EngineEvent(event="task_finished", case_id="c-1", at=T))
    events.append(EngineEvent(event="round_started", case_id="c-2", at=T))
    assert (a.seq, b.seq) == (1, 2)
    assert [e.event for e in events.since("c-1")] == ["round_started", "task_finished"]
    assert [e.seq for e in events.since("c-1", after_seq=1)] == [2]


def test_mongo_이벤트_보존은_마이크로초_길이에_속지_않는다(db):
    # at은 ISO 문자열로 저장된다. 마이크로초가 있는 값과 없는 값은 길이가 달라
    # 사전식 비교가 시간 순서와 어긋난다 — DB $lt가 아니라 Python 파싱으로 걸러야 한다.
    from datetime import timedelta
    from src.domain.events import EngineEvent
    from src.infrastructure.mongo_store import MongoEventStore
    events = MongoEventStore(db)
    old = T - timedelta(days=40)
    events.append(EngineEvent(event="round_started", case_id="c-1", at=old))
    events.append(EngineEvent(event="round_started", case_id="c-1",
                              at=old.replace(microsecond=123456)))
    events.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    assert events.prune_before(T - timedelta(days=1)) == 2
    assert [e.seq for e in events.since("c-1")] == [3]
```

`tests/infrastructure/test_mongo_store.py` 상단에 `T`가 없으면 추가한다:

```python
from datetime import datetime, timezone
T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_mongo_store.py -q -k "이벤트"`
Expected: FAIL — `ImportError: cannot import name 'MongoEventStore'`

- [ ] **Step 3: 최소 구현**

`src/infrastructure/mongo_store.py`의 `ensure_indexes`에 추가:

```python
    # (case_id, seq) unique: seq는 counters로 원자 증가하므로 중복이 나면 그 자체가
    # 카운터 손상 신호다 — 인덱스가 조용한 중복 대신 즉시 실패로 드러낸다.
    db.case_events.create_index([("case_id", 1), ("seq", 1)], unique=True)
```

파일 끝에 클래스를 추가:

```python
class MongoEventStore(EventStorePort):
    """이벤트 로그를 담는 Mongo 스토어 — 프로세스 밖 구독자의 읽기 지점."""

    def __init__(self, db: Database):
        self._db = db

    def append(self, event: EngineEvent) -> EngineEvent:
        seq = _next_seq(self._db, f"events:{event.case_id}")
        stamped = event.model_copy(update={"seq": seq})
        self._db.case_events.insert_one(stamped.model_dump(mode="json"))
        return stamped

    def since(self, case_id, after_seq=0, limit=200):
        if limit <= 0:
            return []
        cursor = (self._db.case_events
                  .find({"case_id": case_id, "seq": {"$gt": after_seq}})
                  .sort("seq", 1).limit(limit))
        return [EngineEvent.model_validate({k: v for k, v in doc.items() if k != "_id"})
                for doc in cursor]

    def prune_before(self, before):
        # at은 ISO 문자열이라 DB의 $lt로 거르면 마이크로초 유무로 순서가 어긋난다
        # (모듈 docstring). purge_evidence_before와 같은 방식으로 Python에서 판정한다.
        stale = [doc["_id"] for doc in self._db.case_events.find({}, {"_id": 1, "at": 1})
                 if datetime.fromisoformat(doc["at"]) < before]
        if stale:
            self._db.case_events.delete_many({"_id": {"$in": stale}})
        return len(stale)
```

`src/infrastructure/mongo_store.py` 상단 import에 추가:

```python
from src.domain.events import EngineEvent, EventStorePort
```

`src/infrastructure/checkpointer.py`의 `build_persistence`를 고친다:

```python
def build_persistence(cfg: StoreConfig):
    """cfg.backend에 따라 (store, repo, ledger, events) 4종 세트를 만든다."""
    if cfg.backend == "memory":
        return (InMemoryCaseStore(), InMemoryCaseRepository(),
                InMemoryLedger(), InMemoryEventStore())
    db = MongoClient(cfg.mongo_url)[cfg.mongo_db]
    ensure_indexes(db)
    return MongoCaseStore(db), MongoCaseRepository(db), MongoLedger(db), MongoEventStore(db)
```

import을 맞춘다:

```python
from src.domain.events import InMemoryEventStore
from src.infrastructure.mongo_store import (MongoCaseRepository, MongoCaseStore,
                                            MongoEventStore, MongoLedger, ensure_indexes)
```

`src/__main__.py`의 **7개 호출부 전부**를 4-튜플로 고친다(줄 번호는 이동할 수 있으니 `grep -n "build_persistence(" src/__main__.py`로 찾는다):

```python
store, repo, ledger, events = build_persistence(app.store)          # 82행 부근
_store, _repo, ledger, _events = build_persistence(app.store)       # 120행 부근
_store, repo, _ledger, _events = build_persistence(app.store)       # 154행 부근
store, repo, _ledger, _events = build_persistence(app.store)        # 166행 부근
store, repo, ledger, events = build_persistence(app.store)          # 224행 부근
store, repo, ledger, events = build_persistence(app.store)          # 429행 부근
```

`tests/infrastructure/test_checkpointer.py:18`도 맞춘다:

```python
    store, repo, ledger, events = build_persistence(StoreConfig(backend="memory"))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/infrastructure/mongo_store.py src/infrastructure/checkpointer.py src/__main__.py tests/
git commit -m "$(cat <<'EOF'
Persist engine events so another process can read them

이벤트가 전량 fire-and-forget이라 조사가 A에서 돌고 UI가 B에서 구독하면
아무것도 못 받고, 새로고침하면 재생할 것이 없었다. case_events 컬렉션에
남기면 구독자가 `since(seq)`로 tail하고, 진실이 워커 메모리가 아니라 저장소에
있으므로 어느 프로세스가 서빙해도 같은 답이 나온다.

보존 삭제를 Python 파싱으로 하는 이유는 at이 ISO 문자열이기 때문이다 —
마이크로초 유무로 길이가 달라 사전식 비교가 시간 순서와 어긋난다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 이벤트를 스토어로 흘리는 싱크

**Files:**
- Modify: `src/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 2의 `build_persistence` 4-튜플
- Produces: `_make_event_sink(events, downstream=None) -> Callable[[EngineEvent], None]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli.py` 끝에 추가:

```python
def test_이벤트_싱크는_저장한_뒤_downstream으로_넘긴다():
    from src.domain.events import EngineEvent, InMemoryEventStore
    from src.__main__ import _make_event_sink
    events = InMemoryEventStore()
    seen = []
    sink = _make_event_sink(events, downstream=seen.append)
    sink(EngineEvent(event="round_started", case_id="c-1", at=T))
    assert [e.seq for e in events.since("c-1")] == [1]
    assert seen[0].seq == 1          # downstream도 seq가 채워진 사본을 본다


def test_이벤트_저장이_실패해도_싱크는_raise하지_않는다():
    from src.domain.events import EngineEvent, EventStorePort
    from src.__main__ import _make_event_sink

    class BrokenStore(EventStorePort):
        def append(self, event): raise RuntimeError("스토어 장애")
        def since(self, case_id, after_seq=0, limit=200): return []
        def prune_before(self, before): return 0

    seen = []
    sink = _make_event_sink(BrokenStore(), downstream=seen.append)
    sink(EngineEvent(event="round_started", case_id="c-1", at=T))   # raise하면 실패
    assert len(seen) == 1            # 저장이 죽어도 stdout 출력은 계속된다
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q -k "이벤트_싱크 or 이벤트_저장"`
Expected: FAIL — `ImportError: cannot import name '_make_event_sink'`

- [ ] **Step 3: 최소 구현**

`src/__main__.py`의 `_make_event_printer` 바로 아래에 추가:

```python
def _make_event_sink(events: EventStorePort,
                     downstream: Callable[[EngineEvent], None] | None = None
                     ) -> Callable[[EngineEvent], None]:
    """이벤트를 스토어에 적재한 뒤 downstream으로 넘긴다.

    적재 실패를 삼키는 이유: on_event는 조사를 실패시킬 수 없는 부수효과로
    설계돼 있다(worker._emit_status·daemon._publish_report·usecase의 세 군데가
    독립적으로 삼킨다). 저장소 장애가 조사를 죽이면 그 방향이 뒤집힌다.
    적재가 실패하면 seq 없는 원본이 그대로 downstream으로 간다.
    """
    def sink(event: EngineEvent) -> None:
        try:
            event = events.append(event)
        except Exception:                                          # noqa: BLE001
            pass
        if downstream is not None:
            downstream(event)
    return sink
```

import에 `EventStorePort`를 추가한다(`EngineEvent`는 이미 있다).

`_run_patrol`의 데몬 조립에서 싱크를 쓴다:

```python
                          on_event=_make_event_sink(events, _make_event_printer()),
```

`_build_publisher`도 같은 싱크를 쓴다 — 시그니처에 `events`를 받게 하고 내부를 바꾼다:

```python
def _build_publisher(app, sites, store, repo, ledger, events, checkpointer, clock
                     ) -> tuple[Callable[[EngineEvent], None], Callable[[str], Awaitable[None]]]:
    ...
    print_event = _make_event_sink(events, _make_event_printer())
```

`_build_publisher` 호출부 2곳(`_cmd_case_resume`, `_run_chat`)에 `events`를 넘긴다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/__main__.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
Route every event through the store on its way to stdout

프로세스 밖 구독자가 읽을 수 있는 자리는 저장소뿐이므로, 싱크가 stdout으로
가기 전에 반드시 append를 거치게 한다. 세 종결 경로(데몬·chat·case resume)가
같은 싱크를 쓰므로 어느 경로로 돈 조사든 같은 로그에 쌓인다.

적재 실패를 삼키는 것은 방어가 아니라 계약이다 — on_event는 조사를 실패시킬
수 없는 부수효과로 설계돼 있고, 저장소 장애가 조사를 죽이면 그 방향이 뒤집힌다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: verdict_formed 이벤트

**Files:**
- Modify: `src/domain/events.py`, `src/application/events.py`
- Test: `tests/application/test_events_mapper.py`

**Interfaces:**
- Produces: `EventKind`에 `"verdict_formed"` 추가. data 형태 `{"verdict_type": str, "confidence": str, "rewritten": bool}`

resume 이후 구간(`conclude`/`verify`)이 이벤트를 내지 않아 사람이 답을 넣은 순간부터 `report_ready`까지 UI가 공백이었다. 규율 7의 시험을 통과한다 — **`verdict_formed`는 도메인 사실이라 conclude/verify를 합치든 쪼개든 유효하다.**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/application/test_events_mapper.py` 끝에 추가:

```python
def test_conclude와_verify는_판정_이벤트를_낸다():
    from src.domain.case import CauseLink, Verdict
    verdict = Verdict(verdict_type="data_loss", confidence="high",
                      root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                      narrative="계획 동기화 누락")
    fresh = map_update_to_events({"conclude": {"verdict": verdict}},
                                 case_id="c-1", clock=lambda: T)
    assert [e.event for e in fresh] == ["verdict_formed"]
    assert fresh[0].data == {"verdict_type": "data_loss", "confidence": "high",
                             "rewritten": False}

    # verify가 verdict를 실을 때는 강등 통과뿐이다(재작성도 실패해 낮은 확신으로 통과).
    demoted = map_update_to_events({"verify": {"verdict": verdict, "verify_problems": []}},
                                   case_id="c-1", clock=lambda: T)
    assert demoted[0].data["rewritten"] is True


def test_verify가_문제만_실은_청크는_판정_이벤트가_아니다():
    # verify_problems만 있는 청크는 판정이 아니라 conclude에 대한 재작성 요구다.
    assert map_update_to_events({"verify": {"verify_problems": ["없는 id ev-9 인용"]}},
                                case_id="c-1", clock=lambda: T) == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/application/test_events_mapper.py -q -k "판정_이벤트"`
Expected: FAIL — `assert [] == ['verdict_formed']`

- [ ] **Step 3: 최소 구현**

`src/domain/events.py`의 어휘를 늘리고 docstring을 고친다:

```python
EventKind = Literal["case_status_changed", "round_started", "task_finished",
                    "question_raised", "report_ready", "verdict_formed"]
```

모듈 docstring의 "정확히 5종" 문장을 바꾼다:

```
이벤트 어휘는 좁게 유지한다 — 봉투 밖(노드명·내부 상태 키)이 그대로 새 나가면
구독자(리포터·채널)가 엔진 내부 구현에 결합돼버린다. 새 종류를 더할지 판단하는
시험은 개수가 아니라 성질이다: **"이 이름이 그래프를 다시 배선해도 그대로
유효한가?"** verdict_formed는 도메인 사실(Verdict가 생겼다)이라 conclude/verify를
합치든 쪼개든 유효하다. node_entered·state_patch·select_gate_evaluated는 무효다.
```

`src/application/events.py`의 매핑에 분기를 추가한다:

```python
            elif node in ("conclude", "verify"):
                events.extend(_verdict_events(partial, case_id=case_id, clock=clock,
                                              rewritten=(node == "verify")))
```

그리고 함수를 추가한다:

```python
def _verdict_events(partial: dict, *, case_id: str, clock: Clock,
                    rewritten: bool) -> list[EngineEvent]:
    """판정이 실린 청크만 이벤트가 된다.

    verify가 verdict를 싣는 경우는 강등 통과뿐이므로(재작성도 실패해 낮은 확신으로
    통과시키는 경로) rewritten=True가 정확하다. verify_problems만 실은 청크는
    판정이 아니라 conclude에 대한 재작성 요구라 이벤트가 없다.
    """
    verdict = partial.get("verdict")
    if verdict is None:
        return []
    return [EngineEvent(event="verdict_formed", case_id=case_id, at=clock(), data={
        "verdict_type": verdict.verdict_type, "confidence": verdict.confidence,
        "rewritten": rewritten})]
```

매퍼 docstring의 규칙 목록도 고친다:

```
    - conclude/verify → verdict가 실렸으면 verdict_formed
    - 그 외(frame/ask_human)·미지의 노드·형태 이상 → 이벤트 없음
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/domain/events.py src/application/events.py tests/application/test_events_mapper.py
git commit -m "$(cat <<'EOF'
Say when a verdict is formed

resume 이후 구간이 조용했다 — 사람이 답을 넣은 순간부터 report_ready까지
UI에 아무 신호가 없고, verify가 conclude로 되돌려 재작성시키면 LLM 호출 2회가
더 조용히 지나간다.

어휘를 늘려도 원래 의도를 깨지 않는 이유는 이 이름이 노드가 아니라 도메인
사실을 가리키기 때문이다. 그래서 규율 7의 시험을 개수에서 성질로 바꿔 적었다:
"이 이름이 그래프를 다시 배선해도 그대로 유효한가?"

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 케이스 개설 이벤트

**Files:**
- Modify: `src/patrol/daemon.py`, `src/__main__.py`
- Test: `tests/patrol/test_daemon.py`

**Interfaces:**
- Consumes: `case_status_event(case_id, status, *, clock, reason=None)` (기존)
- Produces: `PatrolDaemon._emit(event)` — 싱크 실패를 삼키는 내부 헬퍼

어휘 증설이 **0이다.** `case_status_changed(status="open")`은 이미 표현 가능한데 호출부가 없었다 — Timeline의 첫 항목이 통째로 빠져 있었다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/patrol/test_daemon.py` 끝에 추가한다. `_daemon`의 실제 시그니처는 `_daemon(store, repo, ledger, lead, tmp_path, *, clock=..., report_cfg=None, on_event=None)`이고, `CHECK`는 파일 상단에 이미 정의돼 있다. `lead=[]`로 두면 워커가 돌지 않아 LLM 대본이 필요 없다 — `run_one`은 점검→게이트→큐까지만 한다.

```python
async def test_게이트가_케이스를_열면_open_이벤트가_나간다(tmp_path):
    # Timeline의 첫 항목("이 케이스가 왜 열렸나")이 통째로 빠져 있었다.
    # 어휘는 이미 있고 호출부만 없던 문제다.
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seen = []
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path, on_event=seen.append)
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert repo.list_by_status("open")[0].id == "c-1"
    opened = [e for e in seen
              if e.event == "case_status_changed" and e.data["status"] == "open"]
    assert [e.case_id for e in opened] == ["c-1"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/patrol/test_daemon.py -q -k "open_이벤트"`
Expected: FAIL — `AttributeError: 'PatrolDaemon' object has no attribute '_emit_case_opened'`

- [ ] **Step 3: 최소 구현**

`src/patrol/daemon.py`에 헬퍼를 추가한다(`_publish_report` 근처):

```python
    def _emit_case_opened(self, case_id: str) -> None:
        """게이트가 케이스를 연 직후 부른다 — Timeline의 첫 항목이다.

        싱크가 raise해도 순찰을 죽이지 않는다(worker._emit_status와 같은 계약).
        """
        if self.on_event is None:
            return
        try:
            self.on_event(case_status_event(case_id, "open", clock=self.clock))
        except Exception:                                          # noqa: BLE001
            pass
```

import에 `case_status_event`를 추가한다:

```python
from src.application.events import case_status_event
```

`run_one`의 게이트 분기에서 부른다:

```python
            if admit.action == "opened":
                self._emit_case_opened(admit.case_id)
                await self.queue.put(admit.case_id)
```

`self_check_job`의 `admit_finding` 뒤에도 같은 분기가 있으니 동일하게 넣는다(`grep -n "admit_finding" src/patrol/daemon.py`로 두 자리를 모두 찾는다).

`src/__main__.py`의 `_drive_chat`도 같은 이벤트를 내야 하는데, **이 함수는 `on_event`를 받지 않는다**(현재 시그니처: `_drive_chat(args, rt, repo, store, worker, symptom, clock, ask, app)`). 인자를 하나 꿰어야 한다.

시그니처를 바꾼다:

```python
async def _drive_chat(args, rt, repo, store, worker, symptom: str, clock, ask, app,
                      on_event) -> int:
```

`repo.save(record)` 바로 다음 줄에 추가한다:

```python
    on_event(case_status_event(case_id, "open", clock=clock))
```

유일한 호출부(`_run_chat`, `grep -n "_drive_chat(" src/__main__.py`로 찾는다)에 `_build_publisher`가 이미 만들어 둔 `on_event`를 넘긴다:

```python
    return asyncio.run(_drive_chat(args, rt, repo, store, worker, symptom, clock, ask, app,
                                   on_event))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/patrol/daemon.py src/__main__.py tests/patrol/test_daemon.py
git commit -m "$(cat <<'EOF'
Announce a case when it opens

Timeline의 첫 항목("이 케이스가 왜 열렸나")이 통째로 빠져 있었다. 게이트의
opened는 레저에만 남고 이벤트가 없어, 대시보드가 개설을 알려면 Mongo를 직접
폴링해야 했다 — 이벤트 봉투가 막으려던 결합이다.

어휘 증설은 0이다. case_status_changed(status="open")은 처음부터 표현 가능했고
호출부만 없었다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 원자적 lease claim

**Files:**
- Modify: `src/domain/cases.py`, `src/application/lifecycle.py`, `src/infrastructure/mongo_store.py`
- Test: `tests/domain/test_cases.py`, `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
- Produces: `lease_is_free(record, owner, now) -> bool`, `CaseRepositoryPort.claim(case_id, owner, *, now, ttl_s) -> CaseRecord | None`

지금 `run_once`가 안전한 것은 **우연이다** — `repo.get`과 `repo.save` 사이에 `await`가 하나도 없어 협조적 스케줄링이 직렬화해 준다. 문서화되지 않은 불변식이고, `resume_once`의 버전 불일치 분기는 이미 그 사이에 `await self._discard_thread(...)`가 있어 깨져 있다.

Mongo 술어에 `lease_until`의 **범위 비교를 쓰지 않는다** — ISO 문자열이라 사전식 순서가 시간 순서와 어긋난다. 대신 읽은 값을 그대로 술어로 거는 CAS를 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/domain/test_cases.py` 끝에 추가:

```python
def test_claim은_남의_살아있는_lease를_뺏지_않는다():
    from datetime import timedelta
    from src.domain.cases import InMemoryCaseRepository
    repo = InMemoryCaseRepository()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None
    assert repo.claim("c-1", "w-2", now=T, ttl_s=60) is None          # 살아있는 남의 lease
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None      # 같은 owner는 갱신
    later = T + timedelta(seconds=120)
    assert repo.claim("c-1", "w-2", now=later, ttl_s=60) is not None  # 만료됐으면 회수
```

`tests/infrastructure/test_mongo_store.py` 끝에 추가:

```python
def test_mongo_claim은_그_사이_남이_잡았으면_진다(db):
    from src.infrastructure.mongo_store import MongoCaseRepository
    repo = MongoCaseRepository(db)
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None
    assert repo.claim("c-1", "w-2", now=T, ttl_s=60) is None
    assert repo.get("c-1").owner == "w-1"


def test_mongo_claim은_ISO_길이_차이에_속지_않는다(db):
    # lease_until을 DB $lt로 비교하면 마이크로초 유무로 길이가 달라 사전식 순서가
    # 시간 순서와 어긋난다 — 만료 판정이 뒤집혀 살아있는 lease를 뺏는다.
    from datetime import timedelta
    from src.infrastructure.mongo_store import MongoCaseRepository
    repo = MongoCaseRepository(db)
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    holder = T.replace(microsecond=500000)
    assert repo.claim("c-1", "w-1", now=holder, ttl_s=3600) is not None
    assert repo.claim("c-1", "w-2", now=holder + timedelta(seconds=1), ttl_s=60) is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/domain/test_cases.py tests/infrastructure/test_mongo_store.py -q -k "claim"`
Expected: FAIL — `AttributeError: 'InMemoryCaseRepository' object has no attribute 'claim'`

- [ ] **Step 3: 최소 구현**

`src/domain/cases.py`에 순수 규칙을 추가한다(`CaseRecord` 정의 아래):

```python
def lease_is_free(record: CaseRecord, owner: str, now: datetime) -> bool:
    """owner가 lease를 잡을 수 있는가 — 없거나, 자기 것이거나, 만료됐을 때.

    이 규칙이 두 곳(application의 acquire_lease, 저장소의 claim)에 있으면
    반드시 갈라진다. 도메인에 한 번만 둔다.
    """
    if record.owner is None or record.owner == owner:
        return True
    return record.lease_until is not None and record.lease_until < now
```

`CaseRepositoryPort`에 추상 메서드를 추가한다:

```python
    @abstractmethod
    def claim(self, case_id: str, owner: str, *, now: datetime,
              ttl_s: float) -> CaseRecord | None:
        """lease를 원자적으로 잡고 갱신된 레코드를 돌려준다. 못 잡으면 None.

        get→save 사이에 다른 프로세스가 끼어들 수 있으므로 획득은 저장소가
        한 동작으로 수행해야 한다 — 순수 함수 acquire_lease로는 표현할 수 없다.
        """
        pass
```

`InMemoryCaseRepository`에 구현을 추가한다:

```python
    def claim(self, case_id, owner, *, now, ttl_s):
        record = self.get(case_id)
        if not lease_is_free(record, owner, now):
            return None
        claimed = record.model_copy(update={
            "owner": owner, "lease_until": now + timedelta(seconds=ttl_s)})
        self._cases[case_id] = claimed
        return claimed
```

import에 `from datetime import datetime, timedelta`를 맞춘다.

`src/application/lifecycle.py`의 `acquire_lease`가 같은 규칙을 쓰게 바꾼다:

```python
def acquire_lease(record: CaseRecord, owner: str, *, clock: Clock,
                   ttl_s: float) -> CaseRecord | None:
    """owner가 lease를 획득(또는 갱신)할 수 있으면 갱신된 레코드를, 아니면 None.

    획득 조건은 도메인의 lease_is_free가 쥔다 — 저장소의 claim과 같은 규칙을
    써야 하므로 여기서 다시 쓰지 않는다.
    """
    now = clock()
    if not lease_is_free(record, owner, now):
        return None
    return record.model_copy(update={"owner": owner, "lease_until": now + timedelta(seconds=ttl_s)})
```

import에 `lease_is_free`를 추가한다.

`src/infrastructure/mongo_store.py`의 `MongoCaseRepository`에 구현을 추가한다:

```python
    def claim(self, case_id, owner, *, now, ttl_s):
        doc = self._db.cases.find_one({"id": case_id})
        if doc is None:
            raise KeyError(case_id)
        record = self._to_record(doc)
        if not lease_is_free(record, owner, now):
            return None
        claimed = record.model_copy(update={
            "owner": owner, "lease_until": now + timedelta(seconds=ttl_s)})
        # 읽은 시점의 owner/lease_until을 그대로 술어로 걸어, 그 사이 남이 잡았으면
        # 진다(CAS). lease_until에 $lt 범위 비교를 쓰지 않는 이유는 ISO 문자열이라
        # 마이크로초 유무로 사전식 순서가 시간 순서와 어긋나기 때문이다(모듈 docstring).
        result = self._db.cases.update_one(
            {"id": case_id, "owner": doc.get("owner"), "lease_until": doc.get("lease_until")},
            {"$set": claimed.model_dump(mode="json")})
        return claimed if result.modified_count else None
```

import에 `lease_is_free`와 `timedelta`를 추가한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/domain/cases.py src/application/lifecycle.py src/infrastructure/mongo_store.py tests/
git commit -m "$(cat <<'EOF'
Make lease acquisition one atomic step

지금 run_once가 안전한 것은 우연이다 — repo.get과 repo.save 사이에 await가
하나도 없어 협조적 스케줄링이 직렬화해 줄 뿐이고, 문서화되지 않은 불변식이다.
resume_once의 버전 불일치 분기는 이미 그 사이에 await가 있어 깨져 있었다.
프로세스가 둘이 되면 같은 스레드에 두 그래프가 쓴다.

Mongo 술어에 lease_until 범위 비교를 쓰지 않은 이유는 ISO 문자열이라 마이크로초
유무로 사전식 순서가 시간 순서와 어긋나기 때문이다 — 만료 판정이 뒤집히면
살아있는 lease를 뺏는다. 읽은 값을 그대로 술어로 거는 CAS면 시각 비교가
Python에서 일어난다.

획득 조건을 도메인의 lease_is_free 하나로 모은 것은 저장소와 application 두
곳에 같은 규칙이 있으면 반드시 갈라지기 때문이다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 워커가 claim으로 lease를 잡는다

**Files:**
- Modify: `src/application/worker.py`
- Test: `tests/application/test_worker.py`

**Interfaces:**
- Consumes: Task 6의 `CaseRepositoryPort.claim`

Task 6이 능력을 만들었고, 이 태스크가 **실제로 그것을 쓰게** 한다. 둘을 나눈 이유는 리뷰어가 "claim이 원자적인가"와 "워커가 올바르게 쓰는가"를 따로 판정할 수 있어야 하기 때문이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/application/test_worker.py` 끝에 추가:

```python
async def test_워커는_get_save가_아니라_claim으로_lease를_잡는다(monkeypatch):
    # get→save 사이에 남이 끼어드는 경합을 재현한다: claim을 쓰면 저장소가 한
    # 동작으로 판정하므로 두 번째 워커가 진다.
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/application/test_worker.py -q -k "claim으로"`
Expected: FAIL — `assert [] == ['w-1']`

- [ ] **Step 3: 최소 구현**

`src/application/worker.py`의 `run_once`에서 획득 부분을 바꾼다:

```python
            record = self._repo.get(case_id)
            leased = self._repo.claim(case_id, self._owner,
                                      now=self._clock(), ttl_s=self._lease_ttl_s)
            if leased is None:
                return "busy"                                       # 레저 이벤트 없음(경합은 정상)
```

`resume_once`도 같은 형태로 바꾼다:

```python
            record = self._repo.get(case_id)
            leased = self._repo.claim(case_id, self._owner,
                                      now=self._clock(), ttl_s=self._lease_ttl_s)
            if leased is None:
                return "busy"
```

`_keepalive_loop`도 claim으로 갱신하게 바꾼다 — 같은 owner의 재획득은 항상 허용되므로 의미가 같고, 갱신 도중 경합도 저장소가 판정한다:

```python
            try:
                if self._repo.claim(case_id, self._owner,
                                    now=self._clock(), ttl_s=self._lease_ttl_s) is None:
                    return          # 남이 가져갔다 — 더 갱신하지 않는다
            except Exception:                                      # noqa: BLE001
                pass
```

모듈 docstring의 lease 설명에 한 줄을 더한다:

```
lease 획득은 저장소의 claim이 한 동작으로 수행한다 — get→save로 나누면 그 사이에
다른 프로세스가 끼어든다. 지금까지 안전했던 것은 그 사이에 await가 없어 협조적
스케줄링이 직렬화해 준 우연이고, resume_once의 버전 불일치 분기는 이미 깨져 있었다.
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/application/worker.py tests/application/test_worker.py
git commit -m "$(cat <<'EOF'
Let the store decide who owns a case

run_once/resume_once/keepalive 세 자리가 전부 저장소의 claim을 타게 한다.
순수 함수 acquire_lease는 "이 레코드로 잡을 수 있나"만 답할 수 있고 "그 사이
남이 잡았나"는 답할 수 없다 — 후자는 쓰기를 직렬화하는 곳만 안다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 주기 재큐 — 다른 프로세스가 쓴 케이스를 보이게

**Files:**
- Modify: `src/config/schema_app.py`, `src/patrol/daemon.py`
- Test: `tests/patrol/test_daemon.py`

**Interfaces:**
- Consumes: `CaseQueue.requeue_open(repo, *, clock) -> int` (기존)
- Produces: `PatrolDaemon.requeue_job()`, `InvestigationsConfig.requeue_interval_s: float`

`requeue_open`은 `PatrolDaemon.build()`에서 **딱 한 번** 불린다. 그래서 `api`가 `open` 케이스를 써도 돌고 있는 데몬은 영원히 모르고, `_skip_unregistered_site`의 "다음 requeue_open이 다시 집어 준다"는 주석도 거짓이다 — 그 "다음"이 없다.

**범위 밖(의도적)**: `awaiting_human` 자동 재개. 재개하려면 사람의 답이 있어야 하는데 그 명령 채널이 아직 없다(계획 P6). 큐에 넣어도 워커가 할 수 있는 일이 없으므로 여기서는 넣지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/patrol/test_daemon.py` 끝에 추가:

`tests/patrol/test_daemon.py`의 import에 `CaseRecord`를 더한다(현재는 `InMemoryCaseRepository`만 들여온다):

```python
from src.domain.cases import CaseRecord, InMemoryCaseRepository
```

그리고 파일 끝에 추가한다:

```python
async def test_주기_재큐는_나중에_생긴_open_케이스를_집어온다(tmp_path):
    # 기동 후에 다른 프로세스(api·다른 워커)가 연 케이스를 데몬이 보려면
    # 재스캔이 주기적이어야 한다. build()의 1회 스캔만으로는 영원히 못 본다.
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path)
    daemon.build()
    assert daemon.queue.qsize() == 0

    repo.save(CaseRecord(id="c-late", gbm="mx", fct="gumi", fingerprint="fp-late",
                         symptom="다른 프로세스가 연 케이스", t0=T,
                         created_at=T, updated_at=T))
    await daemon.requeue_job()
    assert daemon.queue.qsize() == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/patrol/test_daemon.py -q -k "주기_재큐"`
Expected: FAIL — `AttributeError: 'PatrolDaemon' object has no attribute 'requeue_job'`

- [ ] **Step 3: 최소 구현**

`src/config/schema_app.py`의 `InvestigationsConfig`에 추가:

```python
    # 다른 프로세스(api·다른 워커)가 연 케이스를 이 데몬이 보게 하는 재스캔 간격.
    # 기동 시 1회 스캔만으로는 나중에 생긴 케이스를 영원히 못 본다.
    requeue_interval_s: float = 30
```

`src/patrol/daemon.py`에 잡을 추가한다(`self_check_job` 근처):

```python
    async def requeue_job(self) -> None:
        """열린 케이스와 만료 lease를 다시 큐에 넣는다.

        중복 투입은 해롭지 않다 — run_once가 claim에 실패하면 "busy"를 돌려주고
        끝난다. 반대로 재스캔이 없으면 다른 프로세스가 연 케이스를 영원히 못 본다.
        다른 잡과 같이 절대 raise하지 않는다.
        """
        try:
            self.queue.requeue_open(self.repo, clock=self.clock)
        except Exception:                                          # noqa: BLE001
            pass
```

`build()`의 스케줄러 등록에 잡을 더한다. 잡 id는 `on_missed`가 `split("/", 2)`로 3세그먼트를 언팩하므로(`daemon.py`) **반드시 3세그먼트여야 한다** — 아니면 `ValueError`가 `except: pass`에 삼켜져 misfire가 기록 없이 사라진다. `_IGNORED_JOB_IDS`에도 넣는다:

```python
_IGNORED_JOB_IDS = {"heartbeat", "self_check", "sweep", "requeue"}
```

```python
        scheduler.add_job(self.requeue_job,
                          IntervalTrigger(seconds=self.app.investigations.requeue_interval_s),
                          id="requeue", max_instances=1, coalesce=True)
```

`src/application/worker.py`의 `_skip_unregistered_site` docstring에서 거짓말을 고친다 — 이제 참이 됐으므로 근거를 적는다:

```python
        문제가 아니므로, F1과 달리 종결하지 않는다 — lease는 finally의
        _release_safely가 풀어주므로 다음 재큐 잡(daemon.requeue_job, 기본 30초)이
        다시 집어 준다.
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/config/schema_app.py src/patrol/daemon.py src/application/worker.py tests/patrol/test_daemon.py
git commit -m "$(cat <<'EOF'
Rescan for open cases instead of scanning once at boot

requeue_open이 build()에서 딱 한 번만 불려서, 기동 후 다른 프로세스가 연
케이스를 데몬이 영원히 보지 못했다. 인메모리 큐가 유일한 작업 목록이었으므로
api 프로세스는 케이스를 넘길 방법이 없어 스스로 실행자가 될 수밖에 없었다.

같은 이유로 _skip_unregistered_site의 "다음 requeue_open이 집어 준다"도 거짓이었다
— 그 "다음"이 없었다. 이제 참이 됐으므로 주석에 근거를 적었다.

잡 id를 3세그먼트로 맞춘 것은 on_missed가 split("/", 2)로 언팩하기 때문이다.
2세그먼트면 ValueError가 except에 삼켜져 misfire가 기록 없이 사라진다 —
misfire_grace_time=None으로 조용한 드롭을 금지한 의도가 무력화된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 조사 wall-clock 상한

**Files:**
- Modify: `src/config/schema_app.py`, `src/application/worker.py`
- Test: `tests/application/test_worker.py`

**Interfaces:**
- Produces: `InvestigationsConfig.max_wall_clock_s: float`, `InvestigationWorker(..., max_wall_clock_s: float | None = None)`

조사에 wall-clock 상한이 없어 `_keepalive_loop`가 lease를 무한 갱신한다. LLM 게이트웨이가 응답을 흘리며 멈추면 그 조사가 lease와 동시 상한 슬롯을 영구 점유하고 UI는 "investigating"을 영원히 보여주며 회수 수단이 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/application/test_worker.py` 끝에 추가:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/application/test_worker.py -q -k "wall_clock"`
Expected: FAIL — 테스트가 타임아웃 없이 매달리거나 `TypeError: unexpected keyword argument 'max_wall_clock_s'`

- [ ] **Step 3: 최소 구현**

`src/config/schema_app.py`의 `InvestigationsConfig`에 추가:

```python
    # 조사 한 건의 벽시계 상한(초). keepalive가 lease를 무한 갱신하므로 이 상한이
    # 없으면 멈춘 LLM 호출 하나가 lease와 동시 상한 슬롯을 영구 점유한다.
    max_wall_clock_s: float = 1800
```

`src/application/worker.py`의 `InvestigationWorker.__init__` 시그니처에 인자를 더하고 저장한다:

```python
                 max_wall_clock_s: float | None = None,
```
```python
        self._max_wall_clock_s = max_wall_clock_s
```

`_invoke_with_keepalive`에서 상한을 건다:

```python
        keepalive = asyncio.ensure_future(self._keepalive_loop(case_id))
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
            keepalive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive
```

`src/patrol/daemon.py`의 워커 생성에 config를 넘긴다:

```python
            max_wall_clock_s=self.app.investigations.max_wall_clock_s,
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/config/schema_app.py src/application/worker.py src/patrol/daemon.py tests/application/test_worker.py
git commit -m "$(cat <<'EOF'
Bound an investigation in wall-clock time

라운드 수와 서브에이전트 recursion에는 상한이 있었지만 벽시계에는 없었다.
keepalive가 lease를 무한 갱신하므로, LLM 게이트웨이가 응답을 흘리며 멈추면
그 조사가 lease와 동시 상한 슬롯을 영구 점유하고 회수할 방법이 없다 — UI는
"investigating"을 영원히 보여준다.

초과를 예외로 던져 기존 최외곽 except가 F1과 동일하게 처리하게 했다. 여기서
직접 닫으면 종결 경로가 둘로 갈린다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: LedgerPort ABC 2분할

**Files:**
- Modify: `src/patrol/ledger.py`, `src/infrastructure/mongo_store.py`, `src/infrastructure/retention.py`, `src/presentation/mail.py`, `src/patrol/selfcheck.py`, `src/application/worker.py`, `src/patrol/daemon.py`
- Test: `tests/patrol/test_ledger.py`(없으면 `tests/patrol/test_selfcheck.py`에 추가)

**Interfaces:**
- Produces: `CheckLedgerPort`(record_run·last_run·consecutive_errors·runs·prune_runs_before·heartbeat·last_heartbeat), `SendLedgerPort`(record_send·mark_sent·pending_sends·prune_sends_before). `LedgerPort`는 둘을 상속하는 별칭으로 남겨 기존 타입 힌트를 깨지 않는다.

**구현은 쪼개지 않는다.** `InMemoryLedger`/`MongoLedger`가 두 ABC를 모두 상속하므로 `build_persistence`의 반환은 그대로다. 바뀌는 것은 **소비자의 타입 힌트가 자기가 실제로 쓰는 포트로 좁아지는 것**뿐이다 — `MongoLedger`는 이미 컬렉션이 `ledger_runs`/`sends`/`ledger_meta`로 갈라져 있고 retention knob도 `ledger_d`/`sends_d`로 분리돼 있다. **저장은 이미 갈라졌고 인터페이스만 융착돼 있다.**

지금 하는 이유: 다음 계획이 `MetricsSinkPort`를 더한다. 그때 이 ABC가 하나면 추상 메서드가 11개에서 15개가 되고 소비자 3종이 한 포트에 묶인다.

**순서 주의**: 워커와 게이트가 `check=f"worker:{case_id}"`·`f"gate:{name}"`으로 점검 레저를 범용 이벤트 로그처럼 쓰고 있고 **벤치가 그 문자열을 단정한다**(`tests/test_bench_scenarios.py`). 그 이주는 이 태스크가 하지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/patrol/test_selfcheck.py` 끝에 추가:

```python
def test_레저_포트는_점검과_발송으로_나뉜다():
    # 다음 계획이 MetricsSinkPort를 더한다. 그때 ABC가 하나면 추상 메서드가
    # 11개에서 15개가 되고 소비자 3종이 한 포트에 묶인다.
    from src.patrol.ledger import CheckLedgerPort, InMemoryLedger, SendLedgerPort
    check_methods = set(CheckLedgerPort.__abstractmethods__)
    send_methods = set(SendLedgerPort.__abstractmethods__)
    assert check_methods == {"record_run", "last_run", "consecutive_errors", "runs",
                             "prune_runs_before", "heartbeat", "last_heartbeat"}
    assert send_methods == {"record_send", "mark_sent", "pending_sends", "prune_sends_before"}
    assert not check_methods & send_methods
    # 구현은 아직 쪼개지 않는다 — build_persistence의 반환 형태를 유지한다.
    ledger = InMemoryLedger()
    assert isinstance(ledger, CheckLedgerPort) and isinstance(ledger, SendLedgerPort)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/patrol/test_selfcheck.py -q -k "레저_포트"`
Expected: FAIL — `ImportError: cannot import name 'CheckLedgerPort'`

- [ ] **Step 3: 최소 구현**

`src/patrol/ledger.py`의 `LedgerPort`를 두 ABC로 쪼개고 별칭을 남긴다:

```python
class CheckLedgerPort(ABC):
    """점검 실행 이력과 데몬 하트비트. 소비자: runner·daemon·selfcheck·worker·patrol status."""

    @abstractmethod
    def record_run(self, gbm: str, fct: str, check: str, outcome: CheckOutcome) -> None: ...

    @abstractmethod
    def last_run(self, gbm: str, fct: str, check: str) -> CheckOutcome | None: ...

    @abstractmethod
    def consecutive_errors(self, gbm: str, fct: str, check: str) -> int: ...

    @abstractmethod
    def heartbeat(self, at: datetime) -> None: ...

    @abstractmethod
    def last_heartbeat(self) -> datetime | None: ...

    @abstractmethod
    def runs(self, gbm: str, fct: str, check: str, limit: int = 50) -> list[CheckOutcome]: ...

    @abstractmethod
    def prune_runs_before(self, before: datetime) -> int:
        """before 이전에 기록된 실행 이력을 전부 삭제하고 삭제 건수를 반환한다."""
        ...


class SendLedgerPort(ABC):
    """발송 2상 멱등 레저. 소비자: mail.send_report·retry_pending."""

    @abstractmethod
    def record_send(self, send_id: str, *, kind: str, target: str, at: datetime) -> bool:
        """send_id를 pending으로 기록한다. 이미 있으면 아무것도 하지 않고 False(중복 억제)."""
        ...

    @abstractmethod
    def mark_sent(self, send_id: str, at: datetime) -> None:
        """send_id를 발송 완료로 표시해 pending 목록에서 뺀다."""
        ...

    @abstractmethod
    def pending_sends(self, limit: int = 50) -> list[dict]:
        """아직 mark_sent되지 않은 발송 기록을 반환한다. 각 항목은
        {send_id, kind, target, at}."""
        ...

    @abstractmethod
    def prune_sends_before(self, before: datetime) -> int:
        """before 이전에 기록된 발송 이력(완료분 포함)을 전부 삭제하고 삭제 건수를 반환한다."""
        ...


class LedgerPort(CheckLedgerPort, SendLedgerPort):
    """두 책임을 다 쓰는 소비자(데몬 조립·retention 스윕)용 합집합.

    구현을 쪼개지 않는 이유: MongoLedger는 이미 ledger_runs/sends/ledger_meta로
    컬렉션이 갈라져 있고 retention knob도 ledger_d/sends_d로 분리돼 있다 —
    저장은 이미 갈라졌고 인터페이스만 융착돼 있었다. 실제 분리가 필요해지는
    시점(다른 채널이 발송만 쓰거나, 메트릭 sink가 붙을 때)에 구현을 나눈다.
    """
```

`InMemoryLedger(LedgerPort)`와 `MongoLedger(LedgerPort)`는 그대로 둔다.

소비자의 타입 힌트를 좁힌다:

- `src/patrol/selfcheck.py`의 `scan_self_check(..., ledger: LedgerPort ...)` → `ledger: CheckLedgerPort`
- `src/presentation/mail.py`의 `ledger: LedgerPort` → `ledger: SendLedgerPort`
- `src/application/worker.py`의 `ledger: LedgerPort` → `ledger: CheckLedgerPort`
- `src/infrastructure/retention.py`와 `src/patrol/daemon.py`는 둘 다 쓰므로 `LedgerPort` 유지

각 파일의 import를 맞춘다.

`docs/architecture.md`에서 `LedgerPort`의 두 책임 겸직을 한계로 적어 둔 대목을 갱신한다 — 이제 ABC는 갈라졌고 구현만 융착돼 있다는 사실로 고친다(`grep -n "LedgerPort" docs/architecture.md`).

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/patrol/ledger.py src/patrol/selfcheck.py src/presentation/mail.py src/application/worker.py src/infrastructure/retention.py docs/architecture.md tests/patrol/test_selfcheck.py
git commit -m "$(cat <<'EOF'
Split the ledger port along the boundary its storage already has

MongoLedger는 이미 ledger_runs/sends/ledger_meta로 컬렉션이 갈라져 있고
retention knob도 ledger_d/sends_d로 분리돼 있었다 — 저장은 갈라졌고 인터페이스만
융착돼 있었다. 그래서 ABC 분할이 거의 공짜다.

지금 하는 이유는 다음 계획이 MetricsSinkPort를 더하기 때문이다. ABC가 하나면
추상 메서드가 11개에서 15개가 되고 소비자 3종이 한 포트에 묶인다.

구현은 쪼개지 않는다. 워커와 게이트가 합성 check 이름으로 점검 레저를 범용
이벤트 로그처럼 쓰고 있고 벤치가 그 문자열을 단정하므로, 이주는 메트릭 sink가
생긴 뒤의 별도 작업이다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: 이벤트 보존

**Files:**
- Modify: `src/config/schema_app.py`, `src/infrastructure/retention.py`, `src/patrol/daemon.py`
- Test: `tests/infrastructure/test_retention.py`

**Interfaces:**
- Consumes: Task 1의 `EventStorePort.prune_before`
- Produces: `RetentionConfig.events_d: int`, `sweep_retention(..., events: EventStorePort | None = None)`의 `"events"` 카운트

이벤트를 저장하기 시작했으니 보존도 같이 정해야 한다. 안 하면 `case_events`가 영구 누적된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/infrastructure/test_retention.py` 끝에 추가:

```python
async def test_보존_스윕은_오래된_이벤트를_지운다():
    from datetime import timedelta
    from src.config.schema_app import RetentionConfig
    from src.domain.events import EngineEvent, InMemoryEventStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    events = InMemoryEventStore()
    events.append(EngineEvent(event="round_started", case_id="c-1", at=T - timedelta(days=40)))
    events.append(EngineEvent(event="round_started", case_id="c-1", at=T))

    counts = await sweep_retention(repo=repo, store=store, ledger=ledger, events=events,
                                   checkpointer=None, clock=lambda: T,
                                   retention=RetentionConfig(events_d=30))
    assert counts["events"] == 1
    assert [e.seq for e in events.since("c-1")] == [2]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_retention.py -q -k "이벤트"`
Expected: FAIL — `TypeError: sweep_retention() got an unexpected keyword argument 'events'`

- [ ] **Step 3: 최소 구현**

`src/config/schema_app.py`의 `RetentionConfig`에 추가:

```python
    events_d: int = 30          # case_events 보존기한 — 저장을 시작했으니 상한도 같이 정한다
```

`src/infrastructure/retention.py`의 시그니처와 카운트에 추가한다:

```python
async def sweep_retention(*, repo: CaseRepositoryPort, store: CaseStorePort,
                          ledger: LedgerPort, checkpointer: _CheckpointerPort | None,
                          clock: Clock, retention: RetentionConfig,
                          events: EventStorePort | None = None) -> dict[str, int]:
```
```python
    counts = {"closed_cases": 0, "ledger_runs": 0, "scratch_evidence": 0, "expired_threads": 0,
             "sends": 0, "events": 0}
```

레저 prune 옆에 규칙을 더한다(**개별 실패가 스윕 전체를 죽이지 않게** 기존 항목과 같은 방식으로 감싼다):

```python
    # ⑥ 오래된 이벤트 — events가 주입되지 않은 호출부(옛 테스트 등)는 건너뛴다
    if events is not None:
        try:
            counts["events"] = events.prune_before(now - timedelta(days=retention.events_d))
        except Exception:                                          # noqa: BLE001
            pass
```

`src/patrol/daemon.py`의 `sweep_job`에서 `events=`를 넘긴다. 데몬이 이벤트 스토어를 들고 있지 않으면 `__init__`에 `events` 인자를 더하고 `_run_patrol`이 넘기게 한다(Task 2에서 만든 4-튜플의 네 번째).

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add src/config/schema_app.py src/infrastructure/retention.py src/patrol/daemon.py tests/infrastructure/test_retention.py
git commit -m "$(cat <<'EOF'
Give the event log a retention bound

이벤트를 저장하기 시작했으니 상한도 같이 정한다 — 안 하면 case_events가 영구
누적되는데, 이 컬렉션은 조사 한 건마다 여러 행이 쌓이는 가장 빠르게 자라는
데이터다. 보존 삭제 후에도 seq가 재사용되지 않는 것은 스토어가 카운터를
길이와 분리해 들고 있기 때문이다(Task 1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 완료 기준

- [ ] `.venv/bin/python -m pytest tests/ -q`가 **전건 통과**하고 기준선(262)보다 테스트 수가 늘어 있다
- [ ] `grep -rn "acquire_lease" src/` 결과가 `lifecycle.py` 정의와 그 테스트뿐이다 — 워커의 lease 획득 경로에 남아 있지 않다
- [ ] `grep -rn "build_persistence(" src/ tests/`의 모든 호출부가 4-튜플을 받는다
- [ ] `patrol run`을 짧게 돌렸을 때(`--for-seconds 0`) 이벤트가 stdout에 나오고 `case_events`에도 적재된다
- [ ] `docs/architecture.md`의 `LedgerPort` 서술이 실제 코드와 일치한다

## 이 계획이 **하지 않는** 것

다음 계획으로 넘기는 것을 명시한다 — 실행자가 범위를 넓히지 않도록.

| 미포함 | 어디로 |
|---|---|
| `awaiting_human` 자동 재개 | P6(웹). 재개하려면 사람의 답이 필요한데 그 명령 채널이 아직 없다 |
| 전역(프로세스 교차) 동시 조사 상한 | P6. 지금 세마포어는 `run_forever` 안에 있어 직접 호출 경로가 무상한이지만, 정확한 상한은 슬롯 문서가 필요하고 실제 부하는 API가 열려야 온다 |
| `MetricsSinkPort` · `Ticker` | P8(관측성). 이 계획은 ABC 경계만 그어 둔다 |
| 합성 레저 이벤트(`worker:{id}`·`gate:{name}`) 이주 | P8. 벤치가 그 문자열을 단정하므로 메트릭 sink가 생긴 뒤에 |
| `EventStorePort`를 읽는 HTTP 엔드포인트 | P6 |
| `find_closed` 이력 조회 | P8 |
