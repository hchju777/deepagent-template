# 계획 2: 어댑터 층 (읽기 전용 포트·결과 봉투·가드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스펙 §4.1~4.4의 대상 시스템 어댑터 — 읽기 전용 포트 5종, 결과 봉투, 가드, in-memory 스텁, 실구현 — 과 계획 1에서 이월한 기동 검증 §4.6의 7(deployment hash 실재)·8(Mongo readonly 롤)을 구현한다.

**Architecture:** domain에 봉투·포트(추상), infrastructure에 스텁·실구현·가드·팩토리. 모든 어댑터 호출은 `ProbeResult`(status ok|error + Envelope)를 반환하고 **절대 raise하지 않는다**(스펙 §5.4 1층). I/O가 얇아지도록 판정 로직(연산 allowlist, 끝점 매칭, 롤 검사, as_of 폴백 계산)은 순수 함수로 분리해 단위 테스트한다 — 실 백엔드 통합 테스트는 YAGNI(스펙 리뷰 판정).

**Tech Stack:** 계획 1 위에 redis(asyncio), PyMongo(AsyncMongoClient), aiokafka, httpx, pytest-asyncio 추가. 어댑터는 async(코드 리더만 sync subprocess git).

## Global Constraints (스펙에서 발췌 — 모든 태스크에 적용)

- **읽기 전용을 메커니즘으로**: Mongo aggregate allowlist에서 `$out`/`$merge`/`$function`/`$where` 명시 배제. find filter도 연산자 allowlist. Kafka는 consumer group 미참여 `assign()` + 오프셋 커밋 금지 + admin 변경 API 미노출. Redis `KEYS *` 금지(SCAN+상한). REST는 토폴로지 등록 끝점 밖 거부. 코드 레포에 git 변경 명령 미노출.
- **결과 봉투**: 모든 포트 결과에 `observed_at`, `complete`(+`truncated_reason`), `requested_as_of`/`effective_as_of`(요청과 달성이 다르면 명시 — Kafka earliest 폴백이 대표). 스펙 §4.2.
- **어댑터는 절대 raise하지 않는다**: 타임아웃·접속 실패·잘못된 인자 전부 `status: "error"` + 원인 문자열로.
- **시계 주입**: 어댑터 코드에서 `datetime.now()` 직접 호출 금지 — `clock: Callable[[], datetime]`을 팩토리에서 주입 (as_of 규율 §2.5).
- **가드**: `target.guards.{timeout_s, max_rows, max_concurrent}` — 타임아웃은 호출 단위, max_rows는 결과 절단+봉투 마킹, max_concurrent는 사이트별 세마포어.
- **Redis 연산 폭 (스펙 §7-1 해소)**: 실물 스키마와 무관하게 안전한 상위집합으로 확정 — GET(TYPE 분기: string→str, hash→dict), SCAN(상한), TTL. 전부 읽기 전용.
- **코드 주석·문서·오류 메시지는 한국어** (라이브러리 오류 원문 인용 허용).
- 계획 1의 규약 유지: unknown key 거부(StrictModel), 기동 검증은 오류 전부 수집.

## File Structure

```
src/domain/
├── __init__.py
├── envelope.py            # Envelope, ProbeResult
└── ports.py               # 추상 포트 5종
src/infrastructure/
├── __init__.py
├── guards.py              # guarded_call — 타임아웃·세마포어·raise 금지 래퍼
├── stubs.py               # in-memory 4종 (redis/mongo/kafka/rest)
├── query_rules.py         # 순수 판정: aggregate/filter allowlist, 끝점 매칭, kafka as_of 폴백, mongo 롤 검사
├── redis_reader.py        # 실구현 (redis.asyncio)
├── mongo_reader.py        # 실구현 (pymongo AsyncMongoClient)
├── kafka_inspector.py     # 실구현 (aiokafka)
├── rest_prober.py         # 실구현 (httpx)
├── code_repo.py           # git subprocess (sync, 읽기 명령만)
└── factory.py             # SiteConfig → AdapterSet (stub|real)
src/boot.py                # 검사 7·8 추가 (수정)
src/config/schema_site.py  # target.adapters: stub|real 추가 (수정)
tests/domain/test_envelope.py
tests/infrastructure/test_guards.py, test_stubs.py, test_query_rules.py,
                     test_code_repo.py, test_factory.py
tests/test_boot.py         # 검사 7·8 테스트 추가 (수정)
```

---

### Task 1: 결과 봉투 + 도메인 포트

**Files:**
- Create: `src/domain/__init__.py`, `src/domain/envelope.py`, `src/domain/ports.py`, `tests/domain/__init__.py`, `tests/infrastructure/__init__.py`
- Test: `tests/domain/test_envelope.py`

**Interfaces:**
- Produces: `Envelope(observed_at: datetime, complete: bool = True, truncated_reason: str | None = None, requested_as_of: datetime | None = None, effective_as_of: datetime | None = None)` — `complete=False`면 `truncated_reason` 필수.
- Produces: `ProbeResult(status: Literal["ok","error"], envelope: Envelope, data: Any = None, error: str | None = None)` — `status="error"`면 `error` 필수, `"ok"`면 `error` 금지.
- Produces: `ports.py`의 추상 포트 5종 (아래 Step 3 코드가 정본). 이후 모든 스텁·실구현이 이를 상속.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/domain/test_envelope.py`

```python
from datetime import datetime

import pytest
from pydantic import ValidationError
from src.domain.envelope import Envelope, ProbeResult

T = datetime(2026, 9, 3, 8, 0, 0)


def test_불완전_결과는_사유가_필수다():
    Envelope(observed_at=T, complete=False, truncated_reason="max_rows")
    with pytest.raises(ValidationError):
        Envelope(observed_at=T, complete=False)


def test_error_결과는_원인이_필수고_ok는_원인_금지():
    env = Envelope(observed_at=T)
    ProbeResult(status="error", envelope=env, error="타임아웃")
    with pytest.raises(ValidationError):
        ProbeResult(status="error", envelope=env)
    with pytest.raises(ValidationError):
        ProbeResult(status="ok", envelope=env, error="이상한 조합")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/domain/test_envelope.py -v`
Expected: FAIL — `ModuleNotFoundError: src.domain.envelope`

- [ ] **Step 3: 구현**

`src/domain/envelope.py`:
```python
"""결과 봉투 — 스펙 §4.2. "요청한 것"과 "실제로 얻은 것"의 차이를 표현한다.

- complete=False: 상한(max_rows 등)에 잘렸다 — 부정 증거("없음")로 결론 금지(verify가 소비).
- effective_as_of: 요청 as_of와 실제 달성 as_of가 다르면 명시 — Kafka 보존 밖
  earliest 폴백이 조용히 "나중" 데이터를 T0 증거로 위장하는 것을 막는다.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from src.config.schema_app import StrictModel


class Envelope(StrictModel):
    observed_at: datetime
    complete: bool = True
    truncated_reason: str | None = None
    requested_as_of: datetime | None = None
    effective_as_of: datetime | None = None

    @model_validator(mode="after")
    def _incomplete_needs_reason(self):
        if not self.complete and not self.truncated_reason:
            raise ValueError("complete=False면 truncated_reason이 필요하다")
        return self


class ProbeResult(StrictModel):
    status: Literal["ok", "error"]
    envelope: Envelope
    data: Any = None
    error: str | None = None

    @model_validator(mode="after")
    def _error_needs_cause(self):
        if self.status == "error" and not self.error:
            raise ValueError("status=error면 error 원인이 필요하다")
        if self.status == "ok" and self.error:
            raise ValueError("status=ok면 error가 없어야 한다")
        return self
```

`src/domain/ports.py`:
```python
"""대상 시스템 읽기 전용 포트 — 스펙 §4.1. 쓰는 메서드는 존재하지 않는다.

모든 async 메서드는 ProbeResult를 반환하고 절대 raise하지 않는다(§5.4 1층).
LLM에는 이 포트가 노출하는 파라미터화된 호출만 주어진다 — 원시 쿼리 금지.
"""
from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.envelope import ProbeResult


class RedisReaderPort(ABC):
    @abstractmethod
    async def get(self, key: str) -> ProbeResult: ...          # string→str, hash→dict (TYPE 분기)

    @abstractmethod
    async def scan(self, pattern: str) -> ProbeResult: ...     # 키 목록, max_rows 상한

    @abstractmethod
    async def ttl(self, key: str) -> ProbeResult: ...          # 초 단위, 없으면 -2/-1 규약 그대로


class MongoReaderPort(ABC):
    @abstractmethod
    async def find(self, collection: str, filter: dict, *,
                   sort: list[tuple[str, int]] | None = None,
                   limit: int | None = None) -> ProbeResult: ...

    @abstractmethod
    async def count(self, collection: str, filter: dict) -> ProbeResult: ...

    @abstractmethod
    async def aggregate(self, collection: str, pipeline: list[dict]) -> ProbeResult: ...


class KafkaInspectorPort(ABC):
    @abstractmethod
    async def group_offsets(self, group: str) -> ProbeResult: ...   # 파티션별 committed/end/lag

    @abstractmethod
    async def read(self, topic: str, *, start: datetime,
                   end: datetime) -> ProbeResult: ...                # 보존 내 메시지, earliest 폴백 시 봉투에 명시


class RestProberPort(ABC):
    @abstractmethod
    async def get(self, endpoint: str) -> ProbeResult: ...           # 토폴로지 등록 끝점만, GET 전용


class CodeRepoReaderPort(ABC):
    """유일한 sync 포트 — git subprocess. 읽기 명령만 노출한다."""

    @abstractmethod
    def hash_exists(self, repo: str, commit: str) -> bool: ...

    @abstractmethod
    def show(self, repo: str, commit: str, path: str) -> str: ...    # 실패 시 CodeRepoError

    @abstractmethod
    def head(self, repo: str) -> str: ...

    @abstractmethod
    def grep(self, repo: str, commit: str, pattern: str) -> list[str]: ...
```

`src/domain/__init__.py`, `tests/domain/__init__.py`, `tests/infrastructure/__init__.py`: 빈 파일.

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/domain/test_envelope.py -v` → PASS

```bash
git add src/domain tests/domain tests/infrastructure
git commit -m "Add result envelope and read-only adapter ports"
```

---

### Task 2: 의존성·config 확장 (`target.adapters`, pytest-asyncio)

**Files:**
- Modify: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `src/config/schema_site.py`
- Test: `tests/config/test_schema_site.py` (추가)

**Interfaces:**
- Produces: `TargetConfig.adapters: Literal["stub", "real"] = "stub"` — 개발·테스트는 스텁, 운영은 real. 팩토리(Task 8)가 소비.

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/config/test_schema_site.py` 끝에

```python
def test_adapters_모드는_stub이_기본이고_오타는_거부():
    cfg = SiteConfig.model_validate(_site())
    assert cfg.target.adapters == "stub"
    with pytest.raises(ValidationError):
        SiteConfig.model_validate({**_site(), "target": {**_site()["target"], "adapters": "rael"}})
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/config/test_schema_site.py -v`
Expected: 새 테스트 FAIL (`adapters` 필드 없음 → unknown key 거부로 두 번째 단언은 통과하고 첫 단언에서 AttributeError/ValidationError)

- [ ] **Step 3: 구현**

`src/config/schema_site.py`의 `TargetConfig`에 필드 추가:
```python
class TargetConfig(StrictModel):
    adapters: Literal["stub", "real"] = "stub"   # 스텁 ↔ 실구현 전환 (전작 패턴)
    redis: RedisTarget | None = None
    # ... (기존 필드 그대로)
```

`requirements.txt`에 추가:
```
# ── 어댑터 실구현 (계획 2) ── target.adapters="real"일 때만 실제 사용
redis>=5.0                 # redis.asyncio
pymongo>=4.12              # AsyncMongoClient (motor는 deprecated)
aiokafka>=0.14             # group 미참여 assign() 읽기 전용
httpx>=0.28,<1             # REST GET
```

`requirements-dev.txt`에 추가:
```
pytest-asyncio>=0.24,<2
```

`pytest.ini`를 다음으로 교체:
```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

새 의존성 설치: `.venv/bin/pip install -q "redis>=5.0" "pymongo>=4.12" "aiokafka>=0.14" "httpx>=0.28,<1" "pytest-asyncio>=0.24,<2"`

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/config/test_schema_site.py -v` → PASS, 이어서 전체 `.venv/bin/pytest` → PASS

```bash
git add requirements.txt requirements-dev.txt pytest.ini src/config/schema_site.py tests/config/test_schema_site.py
git commit -m "Add adapter mode switch and async test plumbing"
```

---

### Task 3: 가드 래퍼 — 타임아웃·세마포어·raise 금지

**Files:**
- Create: `src/infrastructure/__init__.py`, `src/infrastructure/guards.py`
- Test: `tests/infrastructure/test_guards.py`

**Interfaces:**
- Consumes: `Guards`(schema_site), `Envelope`/`ProbeResult`(Task 1).
- Produces: `guarded_call(op: Callable[[], Awaitable[tuple[Any, Envelope]]], *, timeout_s: float, semaphore: asyncio.Semaphore, clock: Callable[[], datetime]) -> ProbeResult` — op가 `(data, envelope)`를 돌려주면 ok로 감싸고, 타임아웃·예외는 error ProbeResult로 변환. **이 함수를 통과하지 않는 실구현 I/O는 없다.**

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/infrastructure/test_guards.py`

```python
import asyncio
from datetime import datetime

from src.domain.envelope import Envelope
from src.infrastructure.guards import guarded_call

T = datetime(2026, 9, 3, 8, 0, 0)
CLOCK = lambda: T


async def test_정상_호출은_ok로_감싼다():
    async def op():
        return {"v": 1}, Envelope(observed_at=T)
    sem = asyncio.Semaphore(1)
    result = await guarded_call(op, timeout_s=1, semaphore=sem, clock=CLOCK)
    assert result.status == "ok" and result.data == {"v": 1}


async def test_타임아웃은_raise가_아니라_error_결과다():
    async def slow():
        await asyncio.sleep(0.2)
        return None, Envelope(observed_at=T)
    sem = asyncio.Semaphore(1)
    result = await guarded_call(slow, timeout_s=0.01, semaphore=sem, clock=CLOCK)
    assert result.status == "error" and "타임아웃" in result.error


async def test_예외도_error_결과로_변환된다():
    async def boom():
        raise ConnectionError("connection refused")
    sem = asyncio.Semaphore(1)
    result = await guarded_call(boom, timeout_s=1, semaphore=sem, clock=CLOCK)
    assert result.status == "error" and "connection refused" in result.error
    assert "어댑터 호출 예외" in result.error


async def test_세마포어가_동시_실행을_제한한다():
    running, peak = 0, 0

    async def op():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return None, Envelope(observed_at=T)

    sem = asyncio.Semaphore(2)
    await asyncio.gather(*[
        guarded_call(op, timeout_s=1, semaphore=sem, clock=CLOCK) for _ in range(6)])
    assert peak <= 2
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/infrastructure/test_guards.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/infrastructure/guards.py`

```python
"""어댑터 공통 가드 — 스펙 §4.1 원칙 ③ "아픈 시스템을 더 아프게 하지 않는다".

- 타임아웃: 호출 단위 상한. 아픈 대상에 매달리지 않는다.
- 세마포어: 사이트별 동시 요청 상한 (팩토리가 사이트당 하나 생성).
- raise 금지: 모든 실패는 error ProbeResult로 — 그래프 superstep을 죽이지 않는다(§5.4).
"""
import asyncio

from src.domain.envelope import Envelope, ProbeResult


async def guarded_call(op, *, timeout_s, semaphore, clock) -> ProbeResult:
    try:
        async with semaphore:
            data, envelope = await asyncio.wait_for(op(), timeout=timeout_s)
        return ProbeResult(status="ok", envelope=envelope, data=data)
    except asyncio.TimeoutError:
        return ProbeResult(
            status="error", envelope=Envelope(observed_at=clock()),
            error=f"타임아웃({timeout_s}s 초과)")
    except Exception as exc:   # 어댑터 계약: 어떤 실패도 그래프 안으로 raise하지 않는다
        return ProbeResult(
            status="error", envelope=Envelope(observed_at=clock()),
            error=f"어댑터 호출 예외 — {type(exc).__name__}: {exc}")
```

`src/infrastructure/__init__.py`는 빈 파일로 생성.

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/infrastructure/test_guards.py -v` → PASS

```bash
git add src/infrastructure tests/infrastructure/test_guards.py
git commit -m "Guard every adapter call: timeout, semaphore, never raise"
```

---

### Task 4: 순수 판정 모듈 — allowlist·끝점 매칭·as_of 폴백·롤 검사

**Files:**
- Create: `src/infrastructure/query_rules.py`
- Test: `tests/infrastructure/test_query_rules.py`

**Interfaces:**
- Produces (전부 순수 함수 — I/O 없음, 실구현·스텁·boot이 공유):
  - `aggregate_problems(pipeline: list[dict]) -> list[str]` — 스테이지 allowlist: `$match $project $group $sort $limit $skip $count $unwind`. 그 밖(특히 `$out $merge $function $where $accumulator`)은 문제로 보고.
  - `filter_problems(filter: dict) -> list[str]` — 연산자 allowlist: `$eq $ne $gt $gte $lt $lte $in $nin $exists $regex $options $and $or`(`$options`는 `$regex`의 표준 짝, 중첩 재귀 검사). 그 밖(특히 `$where $expr $function`)은 문제.
  - `endpoint_allowed(endpoint: str, patterns: set[str]) -> bool` — 토폴로지 rest locator의 `{자리표시자}`를 `[^/]+`("/" 제외 1+)로 바꾼 전체 일치 매칭. 패턴 매칭 전에 `%`(퍼센트 인코딩)나 `.`/`..` 경로 세그먼트가 있으면 조기 거부한다 — `{자리표시자}`가 `[^/]+`라 `..`도 매치돼버리는데 httpx가 이를 재정규화해 실제로는 비허용 끝점에 도달하기 때문(실증됨).
  - `kafka_effective_start(requested: datetime, resolved_ts: int | None, earliest_ts: int | None) -> tuple[datetime, bool]` — offsets_for_times가 None(보존 밖)이면 earliest 타임스탬프로 폴백하고 `(달성 시각, 폴백 여부)` 반환. 폴백=True면 호출자가 봉투의 effective_as_of를 채운다.
  - `mongo_role_problems(conn_status: dict) -> list[str]` — `connectionStatus` 응답의 `authInfo.authenticatedUserRoles`에서 `{"read", "readAnyDatabase"}` 밖의 롤을 문제로 보고. 인증 사용자가 없으면(무인증 법인) 빈 목록.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/infrastructure/test_query_rules.py`

```python
from datetime import datetime, timezone

from src.infrastructure.query_rules import (
    aggregate_problems, endpoint_allowed, filter_problems,
    kafka_effective_start, mongo_role_problems)


def test_쓰기_스테이지와_JS_실행은_거부된다():
    assert aggregate_problems([{"$match": {"a": 1}}, {"$group": {"_id": "$b"}}]) == []
    assert any("$out" in p for p in aggregate_problems([{"$out": "evil"}]))
    assert any("$merge" in p for p in aggregate_problems([{"$merge": {"into": "evil"}}]))


def test_filter는_allowlist_연산자만():
    assert filter_problems({"line": 7, "ts": {"$lte": 5}, "$or": [{"a": 1}, {"b": {"$in": [1]}}]}) == []
    assert any("$where" in p for p in filter_problems({"$where": "sleep(1000)"}))
    assert any("$expr" in p for p in filter_problems({"x": {"$expr": {}}}))


def test_끝점은_토폴로지_패턴_전체일치만():
    patterns = {"/api/v1/lines/{line}/oee"}
    assert endpoint_allowed("/api/v1/lines/7/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/7/oee/../../admin", patterns)
    assert not endpoint_allowed("/api/v1/lines/7", patterns)


def test_kafka_보존_밖이면_earliest로_폴백하고_표시한다():
    req = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ts, fallback = kafka_effective_start(req, resolved_ts=1756701000000, earliest_ts=None)
    assert not fallback and ts == datetime.fromtimestamp(1756701000, tz=timezone.utc)
    ts2, fallback2 = kafka_effective_start(req, resolved_ts=None, earliest_ts=1756900000000)
    assert fallback2 and ts2 == datetime.fromtimestamp(1756900000, tz=timezone.utc)


def test_mongo_롤은_read계열만_허용():
    ok = {"authInfo": {"authenticatedUserRoles": [{"role": "read", "db": "twin"}]}}
    bad = {"authInfo": {"authenticatedUserRoles": [{"role": "readWrite", "db": "twin"}]}}
    anon = {"authInfo": {"authenticatedUserRoles": []}}
    assert mongo_role_problems(ok) == []
    assert any("readWrite" in p for p in mongo_role_problems(bad))
    assert mongo_role_problems(anon) == []


def test_중첩된_JS_실행_연산자도_잡는다():
    assert any("$function" in p for p in aggregate_problems(
        [{"$project": {"x": {"$function": {"body": "evil"}}}}]))
    assert any("$where" in p for p in aggregate_problems(
        [{"$match": {"$where": "sleep(1000)"}}]))
    assert any("$accumulator" in p for p in aggregate_problems(
        [{"$group": {"_id": "$a", "y": {"$accumulator": {}}}}]))


def test_끝점_메타문자와_개행_우회_차단():
    assert not endpoint_allowed("/api/v1/tags/x/PLCXLine7XValue",
                                {"/api/v1/tags/{tag}/PLC.Line7.Value"})
    assert endpoint_allowed("/api/v1/tags/x/PLC.Line7.Value",
                            {"/api/v1/tags/{tag}/PLC.Line7.Value"})
    assert not endpoint_allowed("/api/v1/lines/7/oee\n", {"/api/v1/lines/{line}/oee"})


def test_끝점_경로_순회와_퍼센트_인코딩_차단():
    patterns = {"/api/v1/lines/{line}/oee"}
    assert not endpoint_allowed("/api/v1/lines/../oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/./oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/%2e%2e/oee", patterns)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/infrastructure/test_query_rules.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/infrastructure/query_rules.py`

```python
"""읽기 전용을 선언이 아니라 메커니즘으로 만드는 순수 판정들 — 스펙 §4.1 원칙 ②.

I/O가 없어 실구현·스텁·기동 검증이 같은 규칙을 공유하고, 단위 테스트가 전부를 덮는다.
"""
import re
from datetime import datetime, timezone

_AGG_ALLOW = {"$match", "$project", "$group", "$sort", "$limit", "$skip", "$count", "$unwind"}
_AGG_BANNED_NESTED = {"$function", "$accumulator", "$where"}
_FILTER_ALLOW = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin",
                 "$exists", "$regex", "$options", "$and", "$or"}
_READONLY_ROLES = {"read", "readAnyDatabase"}


def aggregate_problems(pipeline):
    """스테이지 이름은 허용 목록으로, 스테이지 내부는 금지 연산자 재귀 탐색으로 검사한다.

    $function/$accumulator/$where는 스테이지 최상위가 아니라 허용된 스테이지 안에
    중첩되어 나타나므로(실제 MongoDB 문법), 내부까지 걸어야 JS 실행을 막는다.
    """
    problems = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _AGG_BANNED_NESTED:
                    problems.append(f"aggregate 내부 연산자 {key!r}는 금지된다 (JS 실행 차단)")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for stage in pipeline:
        for op, body in stage.items():
            if op not in _AGG_ALLOW:
                problems.append(f"aggregate 스테이지 {op!r}는 허용 목록에 없다 (쓰기/JS 실행 차단)")
            walk(body)
    return problems


def filter_problems(filter):
    problems = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("$") and key not in _FILTER_ALLOW:
                    problems.append(f"filter 연산자 {key!r}는 허용 목록에 없다")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(filter)
    return problems


def endpoint_allowed(endpoint, patterns):
    """토폴로지 패턴과의 전체 일치 판정. 리터럴 구간은 이스케이프, {자리표시자}는 [^/]+.

    경로 순회(.., .)와 퍼센트 인코딩은 패턴 매칭 전에 조기 거부한다. `{자리표시자}`가
    `[^/]+`로 컴파일되므로 `/api/v1/lines/../oee`가 자리표시자 구간과 매치되어
    통과해버리는데, httpx가 이를 다시 `/api/v1/oee`로 정규화해 실제로는 비허용
    끝점에 도달한다(실증됨) — `%2e%2e` 같은 퍼센트 인코딩도 동일하게 우회로 쓰인다.
    """
    if "%" in endpoint:
        return False
    if any(segment in (".", "..") for segment in endpoint.split("/")):
        return False
    for pattern in patterns:
        parts = re.split(r"\{[^/}]+\}", pattern)
        regex = "[^/]+".join(re.escape(part) for part in parts)
        if re.fullmatch(regex, endpoint):
            return True
    return False


def kafka_effective_start(requested, resolved_ts, earliest_ts):
    """offsets_for_times 결과로 달성 시작 시각을 정한다.

    resolved_ts가 None이면 요청 시각이 보존 밖 — earliest로 폴백하고 True를 돌려
    호출자가 봉투 effective_as_of에 명시하게 한다 (조용한 폴백 금지, 스펙 §4.2).
    둘 다 None이면 빈 파티션 — 달성 시각을 정할 수 없어 요청 시각을 그대로, 폴백 표시.
    """
    if resolved_ts is not None:
        return datetime.fromtimestamp(resolved_ts / 1000, tz=timezone.utc), False
    if earliest_ts is not None:
        return datetime.fromtimestamp(earliest_ts / 1000, tz=timezone.utc), True
    return requested, True


def mongo_role_problems(conn_status):
    roles = conn_status.get("authInfo", {}).get("authenticatedUserRoles", [])
    return [f"Mongo 계정 롤 {r.get('role')!r}(db={r.get('db')})는 읽기 전용이 아니다"
            for r in roles if r.get("role") not in _READONLY_ROLES]
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/infrastructure/test_query_rules.py -v` → PASS

```bash
git add src/infrastructure/query_rules.py tests/infrastructure/test_query_rules.py
git commit -m "Enforce read-only access as pure, testable rules"
```

**Fix round (전체 브랜치 리뷰):** `endpoint_allowed`가 `{자리표시자}` 구간을 `[^/]+`로 컴파일해 `..`/`.`도 매치해버려 `/api/v1/lines/../oee`가 통과했다(httpx가 이를 재정규화해 실제로는 비허용 끝점에 도달 — 실증됨). `%2e%2e` 같은 퍼센트 인코딩도 동일 우회였다. 패턴 매칭 전에 `%` 존재나 `.`/`..` 세그먼트를 조기 거부하도록 고쳤다. 곁들여 `$options`를 `_FILTER_ALLOW`에 추가했다(`$regex`의 표준 짝인데 allowlist에 없어 `$regex`와 함께 쓰면 filter 전체가 거부되고 있었다).

Run: `.venv/bin/pytest tests/infrastructure/test_query_rules.py -v` → PASS, 전체 `.venv/bin/pytest` → PASS

```bash
git add src/infrastructure/query_rules.py tests/infrastructure/test_query_rules.py
git commit -m "Close the retention, traversal, and injection gaps the branch review found"
```

---

### Task 5: in-memory 스텁 4종

**Files:**
- Create: `src/infrastructure/stubs.py`
- Test: `tests/infrastructure/test_stubs.py`

**Interfaces:**
- Consumes: 포트(Task 1), query_rules(Task 4), Guards 값.
- Produces (전부 시드 데이터를 생성자로 받고, 봉투 규칙은 실구현과 동일):
  - `StubRedis(data: dict[str, str | dict], ttls: dict[str, int] = {}, *, max_rows: int, clock)` — get은 값 타입 그대로(str→string, dict→hash), scan은 fnmatch 패턴, max_rows 초과 시 절단+`complete=False`.
  - `StubMongo(collections: dict[str, list[dict]], *, max_rows: int, clock)` — find는 `$eq/$gt/$gte/$lt/$lte/$in` 지원 필터 평가 + filter_problems 검사(위반 시 error), count, aggregate는 `aggregate_problems` 검사만 하고 `$match`+`$count` 정도의 최소 평가.
  - `StubKafka(messages: dict[str, list[dict]], offsets: dict[str, dict[int, dict]] = {}, *, max_rows: int, clock)` — messages의 각 원소는 `{"ts": datetime, "value": ...}`. read는 [start, end) 필터+상한, 요청 start가 가장 이른 ts보다 과거면 earliest 폴백을 봉투에 명시. group_offsets는 offsets 시드 반환.
  - `StubRest(responses: dict[str, Any], allowed: set[str], *, clock)` — endpoint_allowed 검사(위반 시 error "토폴로지 밖"), 등록된 응답 반환, 미등록이면 error "404".

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/infrastructure/test_stubs.py`

```python
from datetime import datetime, timezone

from src.infrastructure.stubs import StubKafka, StubMongo, StubRedis, StubRest

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CLOCK = lambda: T


async def test_redis_스텁_TYPE분기와_scan_절단():
    stub = StubRedis({"plan:7": "480", "equip:7": {"state": "RUN"}},
                     ttls={"plan:7": 3600}, max_rows=1, clock=CLOCK)
    assert (await stub.get("plan:7")).data == "480"
    assert (await stub.get("equip:7")).data == {"state": "RUN"}
    assert (await stub.get("ghost")).data is None
    assert (await stub.ttl("plan:7")).data == 3600
    scan = await stub.scan("*:7")
    assert scan.envelope.complete is False and scan.envelope.truncated_reason == "max_rows"
    assert len(scan.data) == 1


async def test_mongo_스텁_필터_평가와_위험_연산_거부():
    stub = StubMongo({"twin_state": [{"line": 7, "oee": 5.12}, {"line": 6, "oee": 0.87}]},
                     max_rows=10, clock=CLOCK)
    found = await stub.find("twin_state", {"line": 7})
    assert found.status == "ok" and found.data == [{"line": 7, "oee": 5.12}]
    assert (await stub.count("twin_state", {"oee": {"$gt": 1}})).data == 1
    bad = await stub.find("twin_state", {"$where": "1"})
    assert bad.status == "error" and "$where" in bad.error


async def test_kafka_스텁_보존밖_요청은_earliest_폴백_명시():
    msgs = {"edge.raw.7": [{"ts": datetime(2026, 9, 2, tzinfo=timezone.utc), "value": {"n": 1}}]}
    stub = StubKafka(msgs, max_rows=10, clock=CLOCK)
    res = await stub.read("edge.raw.7",
                          start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                          end=T)
    assert res.status == "ok" and len(res.data) == 1
    assert res.envelope.effective_as_of == datetime(2026, 9, 2, tzinfo=timezone.utc)


async def test_rest_스텁_토폴로지_밖_끝점_거부():
    stub = StubRest({"/api/v1/lines/7/oee": {"oee": 5.12}},
                    allowed={"/api/v1/lines/{line}/oee"}, clock=CLOCK)
    assert (await stub.get("/api/v1/lines/7/oee")).data == {"oee": 5.12}
    outside = await stub.get("/admin/drop")
    assert outside.status == "error" and "토폴로지" in outside.error


async def test_mongo_스텁_regex_평가():
    stub = StubMongo({"c": [{"name": "apple"}, {"name": "banana"}]}, max_rows=10, clock=CLOCK)
    found = await stub.find("c", {"name": {"$regex": "^a"}})
    assert found.data == [{"name": "apple"}]


async def test_mongo_스텁_aggregate_절단_표시():
    stub = StubMongo({"c": [{"i": n} for n in range(5)]}, max_rows=2, clock=CLOCK)
    res = await stub.aggregate("c", [{"$match": {}}])
    assert res.envelope.complete is False and res.envelope.truncated_reason == "max_rows"


async def test_mongo_스텁_잘못된_구조는_error_결과():
    stub = StubMongo({"c": [{"a": 1}]}, max_rows=10, clock=CLOCK)
    res = await stub.find("c", {"$and": {"a": 1}})
    assert res.status == "error" and "구조 오류" in res.error


async def test_mongo_스텁_sort_필드_부재도_error_결과():
    stub = StubMongo({"c": [{"a": 1}, {"b": 2}]}, max_rows=10, clock=CLOCK)
    res = await stub.find("c", {}, sort=[("a", 1)])
    assert res.status == "error"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/infrastructure/test_stubs.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/infrastructure/stubs.py`

```python
"""개발·테스트용 in-memory 스텁 — 전작 패턴. 봉투·읽기 전용 규칙은 실구현과 동일하다.

스텁이 규칙(query_rules)을 실구현과 공유하므로, 스텁으로 도는 테스트가
"위험 연산 거부·절단 마킹·as_of 폴백 명시"라는 계약 자체를 검증한다.
"""
import fnmatch
import re
from datetime import datetime

from src.domain.envelope import Envelope, ProbeResult
from src.domain.ports import (KafkaInspectorPort, MongoReaderPort,
                              RedisReaderPort, RestProberPort)
from src.infrastructure.query_rules import (aggregate_problems, endpoint_allowed,
                                            filter_problems)


def _ok(data, envelope):
    return ProbeResult(status="ok", envelope=envelope, data=data)


def _err(msg, clock):
    return ProbeResult(status="error", envelope=Envelope(observed_at=clock()), error=msg)


class StubRedis(RedisReaderPort):
    def __init__(self, data, ttls=None, *, max_rows, clock):
        self._data, self._ttls = data, ttls or {}
        self._max_rows, self._clock = max_rows, clock

    async def get(self, key):
        return _ok(self._data.get(key), Envelope(observed_at=self._clock()))

    async def scan(self, pattern):
        keys = sorted(k for k in self._data if fnmatch.fnmatch(k, pattern))
        truncated = len(keys) > self._max_rows
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None)
        return _ok(keys[: self._max_rows], env)

    async def ttl(self, key):
        ttl = self._ttls.get(key, -1 if key in self._data else -2)
        return _ok(ttl, Envelope(observed_at=self._clock()))


def _match(doc, filter):
    for field, cond in filter.items():
        if field in ("$and", "$or"):
            results = [_match(doc, c) for c in cond]
            if field == "$and" and not all(results):
                return False
            if field == "$or" and not any(results):
                return False
        elif isinstance(cond, dict):
            value = doc.get(field)
            for op, rhs in cond.items():
                if op == "$eq" and not value == rhs: return False
                if op == "$ne" and not value != rhs: return False
                if op == "$gt" and not (value is not None and value > rhs): return False
                if op == "$gte" and not (value is not None and value >= rhs): return False
                if op == "$lt" and not (value is not None and value < rhs): return False
                if op == "$lte" and not (value is not None and value <= rhs): return False
                if op == "$in" and value not in rhs: return False
                if op == "$nin" and value in rhs: return False
                if op == "$exists" and (field in doc) != bool(rhs): return False
                if op == "$regex" and not (isinstance(value, str) and re.search(rhs, value)): return False
                # $options는 query_rules의 allowlist에 있지만(표준 $regex 짝) 여기서는
                # 평가하지 않는다 — re.search에 플래그를 안 넘겨도 매치 결과 자체는
                # 안전 쪽(더 좁게 매치)이라 no-raise 계약을 깨지 않는다.
        elif doc.get(field) != cond:
            return False
    return True


class StubMongo(MongoReaderPort):
    def __init__(self, collections, *, max_rows, clock):
        self._cols, self._max_rows, self._clock = collections, max_rows, clock

    async def find(self, collection, filter, *, sort=None, limit=None):
        problems = filter_problems(filter)
        if problems:
            return _err("; ".join(problems), self._clock)
        try:
            docs = [d for d in self._cols.get(collection, []) if _match(d, filter)]
            # sort도 try 안에서: 정렬 필드가 문서마다 없거나 타입이 섞이면 None과
            # 다른 타입 비교로 TypeError가 날 수 있다 — no-raise 계약(§5.4)을 지키려면
            # 여기서 잡아 error 결과로 돌려야 한다.
            if sort:
                for field, direction in reversed(sort):
                    docs.sort(key=lambda d: d.get(field), reverse=direction < 0)
        except Exception as exc:
            return _err(f"filter 구조 오류 — {type(exc).__name__}: {exc}", self._clock)
        cap = min(limit, self._max_rows) if limit else self._max_rows
        truncated = len(docs) > cap
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None)
        return _ok(docs[:cap], env)

    async def count(self, collection, filter):
        problems = filter_problems(filter)
        if problems:
            return _err("; ".join(problems), self._clock)
        try:
            n = sum(1 for d in self._cols.get(collection, []) if _match(d, filter))
        except Exception as exc:
            return _err(f"filter 구조 오류 — {type(exc).__name__}: {exc}", self._clock)
        return _ok(n, Envelope(observed_at=self._clock()))

    async def aggregate(self, collection, pipeline):
        problems = aggregate_problems(pipeline)
        if problems:
            return _err("; ".join(problems), self._clock)
        docs = list(self._cols.get(collection, []))
        for stage in pipeline:                      # 최소 평가: $match·$count만
            if "$match" in stage:
                try:
                    docs = [d for d in docs if _match(d, stage["$match"])]
                except Exception as exc:
                    return _err(f"filter 구조 오류 — {type(exc).__name__}: {exc}", self._clock)
            elif "$count" in stage:
                docs = [{stage["$count"]: len(docs)}]
        truncated = len(docs) > self._max_rows
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None)
        return _ok(docs[: self._max_rows], env)


class StubKafka(KafkaInspectorPort):
    def __init__(self, messages, offsets=None, *, max_rows, clock):
        self._msgs, self._offsets = messages, offsets or {}
        self._max_rows, self._clock = max_rows, clock

    async def group_offsets(self, group):
        return _ok(self._offsets.get(group, {}), Envelope(observed_at=self._clock()))

    async def read(self, topic, *, start, end):
        msgs = sorted(self._msgs.get(topic, []), key=lambda m: m["ts"])
        effective = None
        if msgs and start < msgs[0]["ts"]:          # 보존 밖 → earliest 폴백 명시
            effective = msgs[0]["ts"]
        effective_start = effective or start
        window = [m for m in msgs if effective_start <= m["ts"] < end]
        truncated = len(window) > self._max_rows
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None,
                       requested_as_of=start, effective_as_of=effective)
        return _ok(window[: self._max_rows], env)


class StubRest(RestProberPort):
    def __init__(self, responses, allowed, *, clock):
        self._responses, self._allowed, self._clock = responses, allowed, clock

    async def get(self, endpoint):
        if not endpoint_allowed(endpoint, self._allowed):
            return _err(f"끝점 {endpoint!r}는 토폴로지에 등록돼 있지 않다", self._clock)
        if endpoint not in self._responses:
            return _err("404: 스텁에 등록되지 않은 끝점", self._clock)
        return _ok(self._responses[endpoint], Envelope(observed_at=self._clock()))
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/infrastructure/test_stubs.py -v` → PASS

```bash
git add src/infrastructure/stubs.py tests/infrastructure/test_stubs.py
git commit -m "Add in-memory stubs sharing the real adapters' contracts"
```

**Fix round (전체 브랜치 리뷰):** `StubMongo.find`의 sort가 try 블록 밖에 있어 정렬 필드가 문서마다 없거나(`None` vs 값 비교) 타입이 섞이면 `TypeError`가 그대로 새어나가 no-raise 계약(§5.4)을 깼다 — sort를 try 안으로 옮겨 filter 구조 오류와 동일하게 error 결과로 감싸도록 고쳤다.

Run: `.venv/bin/pytest tests/infrastructure/test_stubs.py -v` → PASS, 전체 `.venv/bin/pytest` → PASS

```bash
git add src/infrastructure/stubs.py tests/infrastructure/test_stubs.py
git commit -m "Close the retention, traversal, and injection gaps the branch review found"
```

---

### Task 6: 실구현 — Redis·Mongo·Kafka·REST (얇은 I/O + 공유 규칙)

**Files:**
- Create: `src/infrastructure/redis_reader.py`, `src/infrastructure/mongo_reader.py`, `src/infrastructure/kafka_inspector.py`, `src/infrastructure/rest_prober.py`
- Test: 없음(신규) — I/O 래퍼는 실 백엔드 없이 테스트 불가(YAGNI 판정). 판정 로직은 전부 Task 4·5에서 검증됨. **단, 각 파일이 import 가능하고 포트를 구현하는지 확인하는 스모크 테스트 1개**를 `tests/infrastructure/test_real_adapters_smoke.py`에 둔다. 스모크가 못 잡는 라이브러리 API 오용(await 누락, 메타데이터 미페치)은 `tests/infrastructure/test_real_adapters_mocked.py`에서 pymongo/aiokafka의 async 표면만 흉내 낸 페이크로 검증한다.

**Interfaces:**
- Consumes: 포트, guards.guarded_call, query_rules, config의 접속 정보.
- Produces: `RealRedis(url, password, *, guards, semaphore, clock)`, `RealMongo(url, username, password, db, *, guards, semaphore, clock)`, `RealKafka(bootstrap, *, guards, semaphore, clock)`, `RealRest(base_url, allowed: set[str], *, guards, semaphore, clock)` — 전부 해당 포트 구현. 모든 공개 메서드는 `guarded_call`로 감싼 내부 op를 실행.

- [ ] **Step 1: 스모크 테스트 작성** — `tests/infrastructure/test_real_adapters_smoke.py`, `tests/infrastructure/test_real_adapters_mocked.py`

```python
"""실구현은 실 백엔드 없이 동작 검증이 불가하다(통합 환경 YAGNI — 스펙 리뷰 판정).
여기서는 포트 구현 여부와 읽기 전용 표면만 검사한다."""
import inspect

from src.domain import ports
from src.infrastructure.kafka_inspector import RealKafka
from src.infrastructure.mongo_reader import RealMongo
from src.infrastructure.redis_reader import RealRedis
from src.infrastructure.rest_prober import RealRest


def test_포트_구현과_읽기전용_표면():
    assert issubclass(RealRedis, ports.RedisReaderPort)
    assert issubclass(RealMongo, ports.MongoReaderPort)
    assert issubclass(RealKafka, ports.KafkaInspectorPort)
    assert issubclass(RealRest, ports.RestProberPort)
    # 쓰기 냄새가 나는 공개 메서드가 없어야 한다
    for cls in (RealRedis, RealMongo, RealKafka, RealRest):
        public = [n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                  if not n.startswith("_")]
        assert not [n for n in public if n in
                    {"set", "delete", "insert", "update", "write", "commit", "produce", "post", "put"}]
```

`tests/infrastructure/test_real_adapters_mocked.py` — 스모크가 못 잡는 라이브러리 API 오용(await 누락, 메타데이터 미페치)을 pymongo/aiokafka의 async 표면만 흉내 낸 페이크로 검증한다:

```python
"""라이브러리 표면만 흉내 낸 페이크로 실구현의 API 사용을 검증한다(실 백엔드 없이)."""
import asyncio
from datetime import datetime, timezone

from src.infrastructure.kafka_inspector import RealKafka
from src.infrastructure.mongo_reader import RealMongo

T = datetime(2026, 9, 2, tzinfo=timezone.utc)
CLOCK = lambda: T


class _Guards:
    timeout_s = 1
    max_rows = 10


# ---- Mongo: aggregate()는 코루틴을 반환하므로 await 없이 async for 하면 TypeError ----

class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for doc in self._docs:
            yield doc


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    async def aggregate(self, pipeline, **kwargs):   # pymongo AsyncCollection.aggregate처럼 코루틴
        return _FakeCursor(self._docs)                # maxTimeMS 등 커맨드 옵션은 무시하고 받기만


class _FakeDB:
    def __init__(self, docs):
        self._docs = docs

    def __getitem__(self, name):
        return _FakeCollection(self._docs)


async def test_mongo_aggregate는_커서를_await해서_얻는다():
    mongo = RealMongo("mongodb://localhost:1", db="test",
                      guards=_Guards(), semaphore=asyncio.Semaphore(1), clock=CLOCK)
    mongo._db = _FakeDB([{"i": 1}, {"i": 2}])   # 실 연결 없이 표면만 교체

    res = await mongo.aggregate("c", [{"$match": {}}])

    # await 누락 버그가 있으면 "어댑터 호출 예외 — TypeError: ..."로 잡혀 status=error가 된다.
    assert res.status == "ok"
    assert res.data == [{"i": 1}, {"i": 2}]


# ---- Kafka: topic을 안 넘긴 fresh consumer는 topics()로 메타데이터를 먼저 페치해야
#      partitions_for_topic이 파티션을 찾는다. 페치 전엔 None, 페치 후엔 채워진다. ----

class _FakeOffsetAndTimestamp:
    def __init__(self, offset, timestamp=None):
        self.offset = offset
        self.timestamp = timestamp


class _FakeRecord:
    def __init__(self, topic, partition, offset, timestamp, key, value):
        self.topic, self.partition, self.offset = topic, partition, offset
        self.timestamp, self.key, self.value = timestamp, key, value


class _FakeConsumer:
    def __init__(self, *, bootstrap_servers, group_id, enable_auto_commit):
        assert group_id is None, "consumer group에 참여하면 안 된다"
        assert enable_auto_commit is False, "커밋 계열 설정이 있으면 안 된다"
        self._topics_fetched = False
        self._getmany_calls = 0

    async def start(self):
        pass

    async def topics(self):
        self._topics_fetched = True   # 전체 메타데이터 페치가 일어났음을 기록
        return {"edge.raw.7"}

    def partitions_for_topic(self, topic):
        # topics()를 먼저 부르지 않으면(버그가 있으면) 여기서 항상 None을 돌려준다.
        return {0} if self._topics_fetched else None

    def assign(self, partitions):
        self._assigned = list(partitions)

    async def offsets_for_times(self, timestamps):
        return {tp: _FakeOffsetAndTimestamp(offset=0) for tp in timestamps}

    async def beginning_offsets(self, partitions):
        return dict.fromkeys(partitions, 0)

    def seek(self, tp, offset):
        pass

    async def getmany(self, *partitions, timeout_ms=0, max_records=None):
        self._getmany_calls += 1
        if self._getmany_calls == 1:
            tp = partitions[0]
            ts = int(T.timestamp() * 1000)
            rec = _FakeRecord(tp.topic, tp.partition, 0, ts, None, {"n": 1})
            return {tp: [rec]}
        return {}       # 이후 호출은 빈 배치 — 수집 루프를 종료시킨다

    async def stop(self):
        pass


async def test_kafka_read는_topics를_먼저_페치해서_파티션을_얻는다(monkeypatch):
    monkeypatch.setattr("src.infrastructure.kafka_inspector.AIOKafkaConsumer", _FakeConsumer)
    kafka = RealKafka("localhost:9092", guards=_Guards(),
                      semaphore=asyncio.Semaphore(1), clock=CLOCK)

    res = await kafka.read("edge.raw.7", start=T, end=datetime(2026, 9, 3, tzinfo=timezone.utc))

    # topics()를 안 부르면 partitions_for_topic이 항상 None이라 빈 ok로 끝난다(가짜 음성).
    assert res.status == "ok"
    assert len(res.data) == 1
    assert res.data[0]["value"] == {"n": 1}


async def test_kafka_read는_메타데이터_없는_토픽을_error로_구분한다(monkeypatch):
    class _NoSuchTopicConsumer(_FakeConsumer):
        def partitions_for_topic(self, topic):
            return None   # topics() 이후에도 계속 없음 — 진짜 존재하지 않는 토픽

    monkeypatch.setattr("src.infrastructure.kafka_inspector.AIOKafkaConsumer", _NoSuchTopicConsumer)
    kafka = RealKafka("localhost:9092", guards=_Guards(),
                      semaphore=asyncio.Semaphore(1), clock=CLOCK)

    res = await kafka.read("ghost.topic", start=T, end=datetime(2026, 9, 3, tzinfo=timezone.utc))

    assert res.status == "error"
    assert "토픽 메타데이터 없음" in res.error


# ---- offsets_for_times는 "start 이후 메시지가 없을 때"만 None을 준다. start가
#      보존 범위보다 오래됐으면 None이 아니라 earliest 오프셋 + 그보다 나중인
#      실제 타임스탬프를 정상 반환한다 — 이 폴백을 놓치면 오염된 evidence를
#      T0 evidence로 위장해 봉투에 내보내게 된다. ----

class _RetentionFallbackConsumer(_FakeConsumer):
    async def offsets_for_times(self, timestamps):
        # None이 아니라 earliest(=beginning_offsets와 같은 offset)와 요청보다
        # 나중인 타임스탬프를 준다 — 진짜 aiokafka의 보존-밖 응답 모양.
        return {tp: _FakeOffsetAndTimestamp(offset=0, timestamp=ts + 5000)
                for tp, ts in timestamps.items()}


async def test_kafka_read는_None이_아닌_보존_밖_폴백도_감지한다(monkeypatch):
    monkeypatch.setattr("src.infrastructure.kafka_inspector.AIOKafkaConsumer",
                        _RetentionFallbackConsumer)
    kafka = RealKafka("localhost:9092", guards=_Guards(),
                      semaphore=asyncio.Semaphore(1), clock=CLOCK)

    res = await kafka.read("edge.raw.7", start=T, end=datetime(2026, 9, 3, tzinfo=timezone.utc))

    assert res.status == "ok"
    start_ms = int(T.timestamp() * 1000)
    assert res.envelope.effective_as_of == datetime.fromtimestamp(
        (start_ms + 5000) / 1000, tz=timezone.utc)


# ---- 라이브 토픽은 end 이후 레코드를 계속 내놓을 수 있다 — 파티션별로 완료
#      처리를 안 하면 empty_polls가 매번 리셋돼 수집 루프가 안 끝난다. ----

class _LiveTopicConsumer(_FakeConsumer):
    async def getmany(self, *partitions, timeout_ms=0, max_records=None):
        self._getmany_calls += 1
        tp = partitions[0] if partitions else None
        if tp is None:
            return {}
        end_ms = int(datetime(2026, 9, 3, tzinfo=timezone.utc).timestamp() * 1000)
        if self._getmany_calls == 1:
            ts = int(T.timestamp() * 1000)
            rec = _FakeRecord(tp.topic, tp.partition, 0, ts, None, {"n": 1})
            return {tp: [rec]}
        # end 이후 레코드를 영원히 내놓는다(라이브 트래픽 시뮬레이션) — 파티션별
        # 완료 처리가 없으면 이 fixture로 수집 루프가 결코 끝나지 않는다.
        rec = _FakeRecord(tp.topic, tp.partition, self._getmany_calls,
                          end_ms + 100_000, None, {"n": "late"})
        return {tp: [rec]}


async def test_kafka_read는_라이브_토픽에서_파티션별로_확정_종료한다(monkeypatch):
    monkeypatch.setattr("src.infrastructure.kafka_inspector.AIOKafkaConsumer", _LiveTopicConsumer)
    kafka = RealKafka("localhost:9092", guards=_Guards(),
                      semaphore=asyncio.Semaphore(1), clock=CLOCK)

    # 고침 전에는 empty_polls가 매번 리셋돼 guarded_call의 타임아웃(1s)으로만
    # 끝나 status="error"가 된다 — 고친 뒤에는 파티션이 완료 처리되어 곧바로 ok.
    res = await asyncio.wait_for(
        kafka.read("edge.raw.7", start=T, end=datetime(2026, 9, 3, tzinfo=timezone.utc)),
        timeout=5)

    assert res.status == "ok"
    assert len(res.data) == 1
    assert res.data[0]["value"] == {"n": 1}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/infrastructure/test_real_adapters_smoke.py tests/infrastructure/test_real_adapters_mocked.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** (4파일 — 구조가 같으므로 Redis를 정본으로 보이고, 나머지는 동일 패턴에 각자의 클라이언트·규칙 적용)

`src/infrastructure/redis_reader.py`:
```python
"""Redis 실구현 — redis.asyncio. TYPE 분기 읽기(string/hash), SCAN+상한, TTL. 쓰기 명령 미노출."""
import redis.asyncio as aioredis

from src.domain.envelope import Envelope
from src.domain.ports import RedisReaderPort
from src.infrastructure.guards import guarded_call


class RealRedis(RedisReaderPort):
    def __init__(self, url, password=None, *, guards, semaphore, clock):
        self._client = aioredis.from_url(url, password=password, decode_responses=True)
        self._guards, self._sem, self._clock = guards, semaphore, clock

    def _call(self, op):
        return guarded_call(op, timeout_s=self._guards.timeout_s,
                            semaphore=self._sem, clock=self._clock)

    async def get(self, key):
        async def op():
            kind = await self._client.type(key)
            if kind == "hash":
                value = await self._client.hgetall(key)
            elif kind == "string":
                value = await self._client.get(key)
            elif kind == "none":
                value = None
            else:
                raise ValueError(f"지원하지 않는 Redis 타입 {kind!r} — string/hash만 읽는다")
            return value, Envelope(observed_at=self._clock())
        return await self._call(op)

    async def scan(self, pattern):
        async def op():
            keys, cursor = [], 0
            while True:
                cursor, batch = await self._client.scan(cursor, match=pattern, count=200)
                keys.extend(batch)
                if cursor == 0 or len(keys) > self._guards.max_rows:
                    break
            truncated = len(keys) > self._guards.max_rows
            env = Envelope(observed_at=self._clock(), complete=not truncated,
                           truncated_reason="max_rows" if truncated else None)
            return sorted(keys)[: self._guards.max_rows], env
        return await self._call(op)

    async def ttl(self, key):
        async def op():
            return await self._client.ttl(key), Envelope(observed_at=self._clock())
        return await self._call(op)
```

`src/infrastructure/mongo_reader.py` — 같은 패턴으로:
- `AsyncMongoClient(url, username=..., password=...)`; `find`/`count`/`aggregate` 전에 `filter_problems`/`aggregate_problems` 검사(위반 시 즉시 error ProbeResult — DB에 안 나감), find는 `limit=min(limit or max_rows, max_rows)+1`로 읽어 절단 여부 판단 후 봉투 마킹.
- `aggregate`는 pymongo 4.17에서 `AsyncCollection.aggregate()`가 코루틴을 반환하므로 **반드시 `cursor = await self._db[collection].aggregate(pipeline)`으로 커서를 얻은 뒤 `async for doc in cursor`로 순회한다**(`await` 없이 바로 `async for`하면 TypeError — 스모크로 못 잡혀 모킹 테스트로 검증).
- `find`/`count_documents`/`aggregate` 전부 서버측 시간 상한을 건다(원칙 ③ — 클라이언트가 guarded_call 타임아웃으로 취소한 뒤에도 서버가 계속 도는 것을 막는다): `find`는 Cursor 파라미터라 `max_time_ms=int(guards.timeout_s * 1000)`(snake_case), `count_documents`/`aggregate`는 커맨드 옵션이라 `maxTimeMS=int(guards.timeout_s * 1000)`(camelCase) — pymongo가 인자를 그대로 커맨드로 전달하므로 이름을 틀리면 조용히 무시되거나 서버가 거부한다.
- `connection_status()` 메서드 추가(포트 밖, boot 전용): `db.command({"connectionStatus": 1, "showPrivileges": True})` 결과 dict 반환 — Task 9의 롤 검사가 소비.
- `db`는 기본값 없는 필수 키워드 인자(`*, db, guards, semaphore, clock`) — username/password만 인증 선택 필드라 기본값을 가진다.

`src/infrastructure/kafka_inspector.py` — 같은 패턴으로:
- `read`: `AIOKafkaConsumer(bootstrap_servers=..., group_id=None, enable_auto_commit=False)` 생성 시 **topic을 넘기지 않는다**(auto-subscribe가 이후 수동 `assign()`과 충돌). `consumer.start()` 직후 `partitions_for_topic`을 바로 호출하면 per-topic 메타데이터가 없어 항상 `None`이다 — **`await consumer.topics()`로 전체 클러스터 메타데이터를 먼저 강제 페치한 뒤** `partitions_for_topic` → `assign()` → 모든 파티션에 대해 `beginning_offsets`와 `offsets_for_times`를 함께 조회한다. `topics()` 이후에도 파티션이 비면 진짜 존재하지 않는 토픽이므로 빈 ok가 아니라 error ProbeResult("토픽 메타데이터 없음")로 구분한다. **commit 계열 호출 없음.**
- **보존-폴백 판정(리뷰 수정)**: aiokafka의 `offsets_for_times`는 "start 이후 메시지가 없을 때"(빈 파티션·미래 시각)만 None을 준다 — start가 보존 범위보다 오래됐으면 None이 아니라 earliest 오프셋과 그 오프셋의(요청보다 나중인) 실제 타임스탬프를 정상 반환한다. 그래서 폴백 여부를 `resolved[tp] is None`으로 보면 안 되고 `resolved[tp].offset == beginning_offset(tp) and resolved[tp].timestamp > start_ms`로 판정해야 한다. 봉투 `effective_as_of`는 폴백 파티션들의 `resolved[tp].timestamp` 중 **최솟값(earliest)**을 `kafka_effective_start`에 넘겨 명시한다(수집된 레코드의 타임스탬프에서 역산하지 않는다 — max_rows 절단으로 폴백 파티션의 레코드가 아예 안 뽑힐 수도 있어서다).
- **수집 루프 종결(리뷰 수정)**: `getmany`로 end 시각/상한까지 수집하되, 파티션별로 end 이후 레코드를 처음 본 순간 그 파티션을 완료 처리해 다음 `getmany` 호출 대상에서 뺀다. 그냥 레코드를 버리기만 하면(`ts >= end_ms`) 살아있는 토픽에서 새 레코드가 계속 들어와 `empty_polls`가 매번 리셋되어 수집 루프가 결코 끝나지 않는다(실측: 모킹 테스트로 재현 시 이벤트 루프를 완전히 점유하는 tight loop가 되어 `guarded_call`의 타임아웃조차 못 걸린다). 전 파티션 완료 시, 또는 max_rows 도달 시(기존 동작 유지) 즉시 종료한다.
- `group_offsets`: `AIOKafkaConsumer`의 admin 경유 없이 `AIOKafkaAdminClient.list_consumer_group_offsets(group)` + `end_offsets`로 파티션별 `{committed, end, lag}` 계산. **변경 API 미노출.**

`src/infrastructure/rest_prober.py` — 같은 패턴으로:
- `httpx.AsyncClient(base_url=...)`; `get(endpoint)`은 먼저 `endpoint_allowed(endpoint, self._allowed)` 검사(위반 시 error, 네트워크에 안 나감) → `client.get(endpoint)` → JSON 파싱 시도, 실패 시 text. GET 외 메서드 미노출.
- 응답 데이터는 `{"status_code": response.status_code, "body": <json 또는 text>}`로 반환한다(리뷰 수정) — status_code를 폐기하지 않는다. 4xx/5xx도 `status="ok"`로 유지한 채 `status_code`로 판별하게 한다: 이 프로버는 모니터링 목적이라 오류 응답 자체가 유효한 관측 증거이지, 어댑터 실패(`guarded_call`의 error)가 아니다.

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/infrastructure/test_real_adapters_smoke.py -v` → PASS, 전체 `.venv/bin/pytest` → PASS

```bash
git add src/infrastructure/redis_reader.py src/infrastructure/mongo_reader.py \
        src/infrastructure/kafka_inspector.py src/infrastructure/rest_prober.py \
        tests/infrastructure/test_real_adapters_smoke.py
git commit -m "Add real adapters as thin guarded IO over shared read-only rules"
```

**Fix round (리뷰):** `partitions_for_topic`을 `topics()` 없이 호출하면 항상 빈 결과라 모든 토픽이 "메시지 없음"으로 보이는 가짜 음성, `AsyncCollection.aggregate()`가 코루틴인데 await 없이 `async for`해 TypeError가 나는 버그를 각각 수정하고 `tests/infrastructure/test_real_adapters_mocked.py`로 라이브러리 표면 모킹 검증을 추가했다. 곁들여 `RealMongo.db`를 기본값 없는 필수 키워드 인자로, `RealRedis.get`이 string/hash/none 외 타입을 명시적 error ProbeResult로 바꾸도록 강화했다.

Run: `.venv/bin/pytest tests/infrastructure/test_real_adapters_mocked.py tests/infrastructure/test_real_adapters_smoke.py -v` → PASS, 전체 `.venv/bin/pytest` → PASS

```bash
git add src/infrastructure/redis_reader.py src/infrastructure/mongo_reader.py \
        src/infrastructure/kafka_inspector.py tests/infrastructure/test_real_adapters_mocked.py
git commit -m "Fetch topic metadata and await aggregate cursors"
```

**Fix round 2 (전체 브랜치 리뷰):** Kafka `read`의 보존-폴백 판정이 `resolved[tp] is None`만 봐서 실제로는 폴백되지 않았다(aiokafka는 start가 보존 밖이어도 None이 아니라 earliest+나중 타임스탬프를 정상 반환) — 모든 파티션의 `beginning_offsets`를 함께 조회해 `offset == beginning and timestamp > start_ms`로 재판정하고, 봉투 `effective_as_of`는 폴백 파티션들의 resolved 타임스탬프 중 최솟값으로 고쳤다(이전엔 `max()`를 썼는데 이것도 오류였다). 같은 함수의 수집 루프가 `ts >= end_ms` 레코드를 버리기만 하고 `empty_polls`를 리셋해 라이브 토픽에서 결코 끝나지 않던 것도 파티션별 완료 처리(대상에서 제외)로 고쳤다 — 모킹 테스트로 재현하면 이벤트 루프를 통째로 점유하는 tight loop가 되어 `guarded_call`의 타임아웃조차 걸리지 않는 걸 확인했다. `RealRest.get`이 status_code를 버리고 항상 `status="ok"`로 body만 내보내던 것을 `{"status_code", "body"}` 구조로 바꿔 4xx/5xx를 관측 가능하게 했다. `RealMongo`의 find/count_documents/aggregate에 서버측 시간 상한(`max_time_ms`/`maxTimeMS`)을 추가해 클라이언트 타임아웃 이후에도 서버가 계속 도는 것을 막았다. `query_rules.endpoint_allowed`가 `.`/`..` 세그먼트와 `%` 인코딩을 패턴 매칭 전에 조기 거부하도록(경로 순회·퍼센트 우회 차단), `code_repo.grep`이 `-e`로 패턴을 분리하도록(대시로 시작하는 패턴의 옵션 주입 차단) 각각 고쳤다. `CodeRepoReader._run`에 `timeout=30`을 걸어 멈춘 git 프로세스가 무한정 붙잡지 않게 했다. `stubs.StubMongo.find`의 sort를 try 블록 안으로 옮겨(필드 부재·타입 혼재 시 TypeError를 no-raise 계약대로 error 결과로) 고쳤다. `$options`를 `_FILTER_ALLOW`에 추가했다(`$regex`의 표준 짝).

Run: `.venv/bin/pytest tests/infrastructure -v` → PASS, 전체 `.venv/bin/pytest` → PASS

```bash
git add src/infrastructure/kafka_inspector.py src/infrastructure/rest_prober.py \
        src/infrastructure/mongo_reader.py src/infrastructure/query_rules.py \
        src/infrastructure/code_repo.py src/infrastructure/stubs.py \
        tests/infrastructure/test_real_adapters_mocked.py tests/infrastructure/test_query_rules.py \
        tests/infrastructure/test_code_repo.py tests/infrastructure/test_stubs.py \
        docs/superpowers/plans/2026-09-03-plan2-adapters.md
git commit -m "Close the retention, traversal, and injection gaps the branch review found"
```

---

### Task 7: CodeRepoReader — git subprocess (읽기 명령만)

**Files:**
- Create: `src/infrastructure/code_repo.py`
- Test: `tests/infrastructure/test_code_repo.py`

**Interfaces:**
- Consumes: `CodeRepoReaderPort`(Task 1), config의 `target.code.repos`.
- Produces: `CodeRepoReader(repos: dict[str, Path])` — name→경로. `hash_exists(repo, commit) -> bool`, `show(repo, commit, path) -> str`, `head(repo) -> str`, `grep(repo, commit, pattern) -> list[str]`(`파일:줄번호:내용` 형식). 미등록 repo나 git 실패는 `CodeRepoError`(메시지 한국어) — **이 포트만은 예외를 던진다**(sync·boot/서브에이전트 내부용이라 봉투 불필요, 호출자가 잡는다. 계획 3의 서브에이전트 최외곽 catch-all이 방어).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/infrastructure/test_code_repo.py`

```python
import subprocess

import pytest
from src.infrastructure.code_repo import CodeRepoError, CodeRepoReader


@pytest.fixture()
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "svc.py").write_text("OEE = output / planned_time\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    return tmp_path


def test_head와_hash_exists와_show(repo):
    reader = CodeRepoReader({"twin-services": repo})
    head = reader.head("twin-services")
    assert reader.hash_exists("twin-services", head)
    assert not reader.hash_exists("twin-services", "0" * 40)
    assert "planned_time" in reader.show("twin-services", head, "svc.py")


def test_grep과_미등록_repo(repo):
    reader = CodeRepoReader({"twin-services": repo})
    head = reader.head("twin-services")
    hits = reader.grep("twin-services", head, "planned_time")
    assert hits and "svc.py" in hits[0]
    with pytest.raises(CodeRepoError, match="등록"):
        reader.head("ghost-repo")


def test_대시로_시작하는_패턴도_안전(repo):
    reader = CodeRepoReader({"twin-services": repo})
    head = reader.head("twin-services")
    assert reader.grep("twin-services", head, "-v") == []   # 옵션이 아니라 리터럴 패턴
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/infrastructure/test_code_repo.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/infrastructure/code_repo.py`

```python
"""코드 레포 리더 — git subprocess, 읽기 명령만 노출한다 (스펙 §4.3).

hash 지정 읽기가 기본이다: 사이트 조사는 워크트리가 아니라 deployment.yaml의
커밋으로 읽는다(§2.5-2). 레포 변경 명령(pull, checkout 등)은 존재하지 않는다.
"""
import subprocess
from pathlib import Path

from src.domain.ports import CodeRepoReaderPort


class CodeRepoError(Exception):
    pass


class CodeRepoReader(CodeRepoReaderPort):
    def __init__(self, repos: dict[str, Path]):
        self._repos = {name: Path(p) for name, p in repos.items()}

    def _run(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        if repo not in self._repos:
            raise CodeRepoError(f"레포 {repo!r}는 config에 등록돼 있지 않다")
        try:
            return subprocess.run(["git", "-C", str(self._repos[repo]), *args],
                                  capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise CodeRepoError(f"{repo}: git 명령 시간 초과(30s) — {' '.join(args)}") from exc

    def hash_exists(self, repo, commit):
        return self._run(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0

    def show(self, repo, commit, path):
        proc = self._run(repo, "show", f"{commit}:{path}")
        if proc.returncode != 0:
            raise CodeRepoError(f"{repo}@{commit[:7]}:{path} 읽기 실패 — {proc.stderr.strip()}")
        return proc.stdout

    def head(self, repo):
        proc = self._run(repo, "rev-parse", "HEAD")
        if proc.returncode != 0:
            raise CodeRepoError(f"{repo}의 HEAD 조회 실패 — {proc.stderr.strip()}")
        return proc.stdout.strip()

    def grep(self, repo, commit, pattern):
        # -e로 패턴을 명시적으로 구분한다 — 아니면 "-v" 같은 패턴이 git grep 옵션으로
        # 파싱돼(예: -v는 매치 반전) 인자 주입이 된다(실증됨).
        proc = self._run(repo, "grep", "-n", "-e", pattern, commit)
        if proc.returncode > 1:                      # 1 = 매치 없음(정상), >1 = 오류
            raise CodeRepoError(f"{repo}@{commit[:7]} grep 실패 — {proc.stderr.strip()}")
        return [line.split(":", 1)[1] if ":" in line else line
                for line in proc.stdout.splitlines()]
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/infrastructure/test_code_repo.py -v` → PASS

```bash
git add src/infrastructure/code_repo.py tests/infrastructure/test_code_repo.py
git commit -m "Read code repos through git plumbing, hashes first"
```

**Fix round (전체 브랜치 리뷰):** `grep`이 패턴을 옵션 자리에 그대로 넘겨 `-`로 시작하는 패턴(예: `-v`)이 git grep 옵션으로 파싱되는 인자 주입이 가능했다 — `-e`로 패턴 자리를 명시해 리터럴로 고정했다. `_run`에 `timeout=30`을 걸고 `TimeoutExpired`를 `CodeRepoError`로 변환해, 응답 없는 git 프로세스가 호출자를 무한정 붙잡지 않게 했다.

Run: `.venv/bin/pytest tests/infrastructure/test_code_repo.py -v` → PASS

```bash
git add src/infrastructure/code_repo.py tests/infrastructure/test_code_repo.py
git commit -m "Close the retention, traversal, and injection gaps the branch review found"
```

---

### Task 8: 팩토리 — SiteConfig → AdapterSet

**Files:**
- Create: `src/infrastructure/factory.py`
- Test: `tests/infrastructure/test_factory.py`

**Interfaces:**
- Consumes: SiteConfig, Topology(REST allowlist용 locators), 스텁·실구현 전부.
- Produces:
  - `AdapterSet` (dataclass): `redis / mongo / kafka / rest` (각 포트 또는 None — config에 접속 정보 없으면 None), `code: CodeRepoReader | None`, `semaphore: asyncio.Semaphore`.
  - `build_adapters(cfg: SiteConfig, topology: Topology, *, clock, stub_seeds: StubSeeds | None = None) -> AdapterSet` — `cfg.target.adapters`에 따라 스텁/실구현 조립. REST allowlist는 `topology.locators()` 중 `rest:` 접두사에서 추출. 세마포어는 `guards.max_concurrent`로 사이트당 1개 생성해 모든 어댑터가 공유.
  - `StubSeeds` (dataclass): `redis_data / redis_ttls / mongo_collections / kafka_messages / kafka_offsets / rest_responses` — stub 모드에서 시드 주입(기본 전부 빈 값).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/infrastructure/test_factory.py`

```python
from datetime import datetime, timezone

from src.config.schema_site import SiteConfig
from src.infrastructure.factory import StubSeeds, build_adapters
from src.infrastructure.stubs import StubMongo, StubRedis
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CLOCK = lambda: T

TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/api/v1/lines/{line}/oee"}]}},
    "derivations": {}})

SITE = SiteConfig.model_validate({
    "target": {"redis": {"url": "redis://x:6379"},
               "mongo": {"url": "mongodb://x:27017"},
               "guards": {"max_concurrent": 2}}})     # adapters 기본값 = stub


async def test_stub_모드_조립과_시드_주입():
    seeds = StubSeeds(redis_data={"plan:7": "480"},
                      mongo_collections={"twin_state": [{"line": 7}]})
    adapters = build_adapters(SITE, TOPO, clock=CLOCK, stub_seeds=seeds)
    assert isinstance(adapters.redis, StubRedis)
    assert isinstance(adapters.mongo, StubMongo)
    assert adapters.kafka is None                     # config에 kafka 없음 → None
    assert adapters.code is None                      # config에 code 없음 → None
    assert (await adapters.redis.get("plan:7")).data == "480"
    assert adapters.semaphore._value == 2             # guards.max_concurrent


async def test_rest_allowlist는_토폴로지에서_온다():
    site = SiteConfig.model_validate({
        "target": {"rest": {"base_url": "http://x"}}})
    seeds = StubSeeds(rest_responses={"/api/v1/lines/7/oee": {"oee": 0.9}})
    adapters = build_adapters(site, TOPO, clock=CLOCK, stub_seeds=seeds)
    assert (await adapters.rest.get("/api/v1/lines/7/oee")).status == "ok"
    assert (await adapters.rest.get("/admin")).status == "error"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/infrastructure/test_factory.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/infrastructure/factory.py`

```python
"""SiteConfig → 어댑터 세트 조립 — stub|real 전환의 유일한 지점.

세마포어는 사이트당 하나를 모든 어댑터가 공유한다: "이 사이트에 대한 동시
요청 상한"이지 어댑터별 상한이 아니다 (스펙 §4.1 guards.max_concurrent).
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.config.schema_site import SiteConfig
from src.infrastructure.code_repo import CodeRepoReader
from src.infrastructure.kafka_inspector import RealKafka
from src.infrastructure.mongo_reader import RealMongo
from src.infrastructure.redis_reader import RealRedis
from src.infrastructure.rest_prober import RealRest
from src.infrastructure.stubs import StubKafka, StubMongo, StubRedis, StubRest
from src.knowledge.topology import Topology


@dataclass
class StubSeeds:
    redis_data: dict[str, Any] = field(default_factory=dict)
    redis_ttls: dict[str, int] = field(default_factory=dict)
    mongo_collections: dict[str, list[dict]] = field(default_factory=dict)
    kafka_messages: dict[str, list[dict]] = field(default_factory=dict)
    kafka_offsets: dict[str, dict] = field(default_factory=dict)
    rest_responses: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterSet:
    redis: Any = None
    mongo: Any = None
    kafka: Any = None
    rest: Any = None
    code: CodeRepoReader | None = None
    semaphore: asyncio.Semaphore | None = None


def _rest_allowlist(topology: Topology) -> set[str]:
    return {loc.removeprefix("rest:") for loc in topology.locators()
            if loc.startswith("rest:")}


def build_adapters(cfg: SiteConfig, topology: Topology, *, clock,
                   stub_seeds: StubSeeds | None = None) -> AdapterSet:
    guards = cfg.target.guards
    sem = asyncio.Semaphore(guards.max_concurrent)
    seeds = stub_seeds or StubSeeds()
    allowed = _rest_allowlist(topology)
    out = AdapterSet(semaphore=sem)

    if cfg.target.adapters == "stub":
        if cfg.target.redis:
            out.redis = StubRedis(seeds.redis_data, seeds.redis_ttls,
                                  max_rows=guards.max_rows, clock=clock)
        if cfg.target.mongo:
            out.mongo = StubMongo(seeds.mongo_collections,
                                  max_rows=guards.max_rows, clock=clock)
        if cfg.target.kafka:
            out.kafka = StubKafka(seeds.kafka_messages, seeds.kafka_offsets,
                                  max_rows=guards.max_rows, clock=clock)
        if cfg.target.rest:
            out.rest = StubRest(seeds.rest_responses, allowed, clock=clock)
    else:
        if cfg.target.redis:
            pw = cfg.target.redis.password.get_secret_value() if cfg.target.redis.password else None
            out.redis = RealRedis(cfg.target.redis.url, pw,
                                  guards=guards, semaphore=sem, clock=clock)
        if cfg.target.mongo:
            m = cfg.target.mongo
            pw = m.password.get_secret_value() if m.password else None
            out.mongo = RealMongo(m.url, username=m.username, password=pw, db=m.db,
                                  guards=guards, semaphore=sem, clock=clock)
        if cfg.target.kafka:
            out.kafka = RealKafka(cfg.target.kafka.bootstrap,
                                  guards=guards, semaphore=sem, clock=clock)
        if cfg.target.rest:
            out.rest = RealRest(cfg.target.rest.base_url, allowed,
                                guards=guards, semaphore=sem, clock=clock)

    if cfg.target.code:
        out.code = CodeRepoReader({r.name: r.path for r in cfg.target.code.repos})
    return out
```

주의: 테스트의 팩토리 시드 주입에서 rest 케이스는 config에 rest만 있으면 된다. StubRest 생성은 `cfg.target.rest`가 있을 때만 — 테스트 두 번째 케이스가 이를 확인한다.

**Task 8 deviation:** `MongoTarget`에 `db: str = "twin"` 필드를 추가했다 — RealMongo 시그니처의 `db` 키워드 필수 인자에 대응하기 위해. `src/config/schema_site.py` 수정 시 기존 테스트 호환성 확인 완료.

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/infrastructure/test_factory.py -v` → PASS

```bash
git add src/infrastructure/factory.py tests/infrastructure/test_factory.py
git commit -m "Assemble per-site adapter sets behind one stub-or-real switch"
```

---

### Task 9: 기동 검증 7·8 — deployment hash 실재, Mongo readonly 롤

**Files:**
- Modify: `src/boot.py`
- Test: `tests/test_boot.py` (추가)

**Interfaces:**
- Consumes: `load_deployment`(계획 1 Task 7), `CodeRepoReader`(Task 7), `mongo_role_problems`(Task 4).
- Produces: `validate_boot`에 추가되는 검사 —
  - **검사 7**: 사이트에 deployment.yaml이 있으면, 기재된 각 (repo, commit)에 대해 repo가 config에 등록되어 있고 `hash_exists`인지. deployment.yaml이 없으면 건너뜀(그 사이트는 "배포 버전 미검증" 경로).
  - **검사 8**: `cfg.target.adapters == "real"`이고 mongo에 username이 설정된 사이트만 — 접속해 `connection_status()`를 얻어 `mongo_role_problems`로 검사. 무인증·스텁 사이트는 건너뜀. 접속 실패는 검사 실패가 아니라 문제로 수집("롤 확인 불가"). live 접속이 필요하므로 **`validate_boot(..., check_live: bool = False)` 파라미터** 뒤에서만 수행 — CLI `knowledge validate --live` 플래그가 켠다(기본은 정적 검증만: "죽은 사이트가 기동을 막으면 역효과" 원칙과 양립).
- CLI: `knowledge validate`에 `--live` 플래그 추가 (`src/__main__.py` 수정 — validate 서브커맨드에 `p_validate.add_argument("--live", action="store_true")` 한 줄과 전달).

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_boot.py` 끝에

```python
def test_deployment의_hash가_레포에_없으면_거부(tmp_path):
    _tree(tmp_path)
    # 실제 git repo를 만들어 config가 가리키게 한다
    import subprocess
    repo = tmp_path / "repos" / "twin-services"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    # config의 repo path를 실제 경로로 교체
    import json
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["code"]["repos"][0]["path"] = str(repo)
    gbm.write_text(json.dumps(data), encoding="utf-8")

    # 실재하는 hash → 통과
    _write(tmp_path, "knowledge/deployment/mx/gumi.yaml",
           f"services:\n  twin-api: {{ repo: twin-services, commit: {head} }}\n")
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []

    # 유령 hash → 거부
    _write(tmp_path, "knowledge/deployment/mx/gumi.yaml",
           "services:\n  twin-api: { repo: twin-services, commit: deadbeef }\n")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("deadbeef" in e.problem for e in errors)


def test_deployment이_없으면_검사7은_건너뛴다(tmp_path):
    _tree(tmp_path)
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []
```

(`_write`는 기존 헬퍼 — 문자열을 그대로 쓴다. deployment yaml은 f-string으로 조립.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_boot.py -v` → 새 테스트 FAIL (검사 7 미구현 — 유령 hash가 통과해버림)

- [ ] **Step 3: 구현** — `src/boot.py`에 추가

사이트 루프 안(토폴로지 검사 뒤)에 검사 7:
```python
        # 검사 7: deployment.yaml의 (repo, commit)이 실재하는가 (§4.6-7)
        deployment = load_deployment(knowledge_root, site.gbm, site.fct)
        if deployment is not None:
            reader = CodeRepoReader(
                {r.name: r.path for r in cfg.target.code.repos}
                if cfg.target.code else {})
            for svc_name, ver in deployment.services.items():
                try:
                    if not reader.hash_exists(ver.repo, ver.commit):
                        errors.append(BootError(
                            where, f"deployment: {svc_name}의 커밋 {ver.commit!r}이 "
                                   f"레포 {ver.repo!r}에 없다 (fetch 누락 또는 오타)"))
                except CodeRepoError as exc:
                    errors.append(BootError(where, f"deployment: {exc}"))
```

검사 8은 `validate_boot(config_root, *, env, repo_root, check_live=False)`로 시그니처 확장:
```python
        # 검사 8: Mongo 계정이 readonly 롤인가 (§4.6-8) — live 접속이 필요해 opt-in
        if check_live and cfg.target.adapters == "real" and \
                cfg.target.mongo and cfg.target.mongo.username:
            try:
                status = asyncio.run(_fetch_conn_status(cfg))
                errors.extend(BootError(where, p) for p in mongo_role_problems(status))
            except Exception as exc:
                errors.append(BootError(where, f"Mongo 롤 확인 불가 — {exc}"))
```
구현 세부는 구현자가 다듬되 계약은 고정: **check_live=False(기본)면 어떤 네트워크 접속도 하지 않는다.** `_fetch_conn_status`는 RealMongo를 만들어 `connection_status()`를 부르는 짧은 헬퍼로 boot.py 안에 둔다. import는 함수 안(지연) — stub 전용 환경에서 실구현 의존성이 없어도 정적 검증이 돌게.

CLI: `knowledge validate`에 `--live` 플래그를 추가해 `check_live`로 전달.

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest -v` → 전체 PASS

```bash
git add src/boot.py src/__main__.py tests/test_boot.py
git commit -m "Verify deployment hashes and Mongo roles at boot"
```

---

## 완료 기준 (계획 2)

- `.venv/bin/pytest` 전체 통과.
- stub 모드로 `build_adapters` → 4포트 + code 리더가 조립되고, 봉투·절단·as_of 폴백·읽기 전용 거부가 스텁 테스트로 검증됨.
- `python -m src knowledge validate`가 deployment hash 검사를 포함해 동작 (검사 8은 `--live`에서만).
- 실구현 4종은 import·포트 준수·쓰기 표면 부재가 스모크로 확인됨 (동작 검증은 실 백엔드 연결 시점 — 스펙 going-live 단계).

## 계획 3 예고

조사 엔진: CaseState(계획·케이스 파일·Verdict 인과 사슬), frame/select/integrate/verify 노드, create_agent 기반 서브에이전트 3종(이 계획의 AdapterSet을 도구로 노출), 체크포인터. LangGraph·langchain 의존성이 이때 추가된다.
