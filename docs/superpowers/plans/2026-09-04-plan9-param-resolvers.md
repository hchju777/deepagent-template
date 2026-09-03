# 계획 9 — 파라미터 값 해석기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 점검이 보낼 파라미터 **값**을 config에 적는 대신 실행 시점에 살아 있는 소스에서 해석한다.

**Architecture:** 계획 8은 **칼럼**(어떤 키를 어떤 타입으로 보낼 수 있는가)까지 닫았다. 남은 것은 **값**이다. `part_code`에 무엇을 넣을지는 명세에 없고, 사업부/법인마다 다르고, 매일 바뀐다 — **값을 config에 적는 어떤 설계도 즉시 썩는다.** 그래서 시나리오가 선언하는 것은 값이 아니라 **값이 어디서 오는지**이고, 실제 값은 점검 실행 시점에 해석된다.

**Tech Stack:** Python 3.12 · pydantic 2 · pytest(`asyncio_mode=auto`)

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) 의 **§2-N2**(값은 선언이 아니라 해석) · **§2-N3**(전부-또는-전무).

**선행:** 계획 8 머지(`246908d`, 365 tests).
**후속:** 계획 10이 pinned OpenAPI로 등재 항목을 검증하고 드리프트를 감시한다.

## Global Constraints

- **무raise**: 해석기·프로브·어댑터는 예외를 던지지 않는다. 실패는 `ProbeResult(status="error")`/`CheckOutcome(status="error")`로 흡수한다.
- **시계 주입**: `src/__main__.py` 밖에서 `datetime.now()` 금지. `clock` 해석기도 주입된 시계를 쓴다.
- **StrictModel**: 새 pydantic 모델은 `StrictModel` 상속.
- **판정과 전송이 갈리지 않게**: 계획 8이 세운 불변식("판정한 것 = 보내는 것")을 해석 결과에도 적용한다 — 해석된 body는 등재 스키마 검증(`entry_call_problems`)을 **반드시 다시** 통과해야 소켓에 나간다.
- **마법 키 냄새 맡기 금지**: 대상 시스템 데이터의 모양을 보고 분기하지 않는다(계획 8 리뷰가 `runner`에서 잡은 것). 해석기 종류는 config가 선언한다.
- **비밀값은 `params.body`에 못 들어간다**(계획 8이 `${ENV}`를 거부한다). 해석기 스펙에도 같은 제약이 적용되는지 확인하라.
- **주석·문서는 한국어, WHY만.** **커밋 메시지는 영어 제목 + 한국어 본문**. 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **테스트**: `rm -rf output/ && .venv/bin/python -m pytest tests/ -q` (기준선 **365 passed**). 잔재에 기대는 초록은 초록이 아니다.
- **브랜치**: `feat/plan9-param-resolvers`에서 구현하고 리뷰 통과 후 머지한다.

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `src/config/schema_site.py` | 사이트 config 스키마 | `ResolverSpec`, `CheckConfig.params`의 `resolve` 규약 |
| `src/patrol/resolvers.py` | **신설** — 값 해석기 레지스트리 | `resolve_params(specs, adapters, clock) -> ResolveResult` |
| `src/patrol/probes.py` | 프로브 | `rest_query`가 해석기를 태운다 |
| `src/boot.py` | 기동 검증 | 해석기 스펙 검증(참조 항목 실재·타입 정합) |
| `src/domain/envelope.py` | 결과 봉투 | 변화 없음 — `complete`/`truncated_reason`을 그대로 쓴다 |

**`resolvers.py`를 `patrol/`에 두는 이유**: 해석은 점검 실행의 일부이고 어댑터를 쓴다. `query_rules.py`(I/O 없는 순수 판정)와 성격이 다르다.

---

## Task 1: 해석기 스펙 스키마

**Files:** Modify `src/config/schema_site.py` · Test `tests/config/test_schema_site.py`

**Interfaces:** `ResolverSpec`(discriminated union), `CheckConfig.resolve: dict[str, ResolverSpec]`

`params.body`(정적 값)와 **별도 필드**로 둔다. 같은 dict에 섞고 `{"from": ...}` 모양으로 구별하면 마법 키 냄새 맡기가 된다 — 계획 8 리뷰가 `runner`에서 정확히 그걸 잡았다.

최종 body는 `{**params.body, **해석된 값}`이고, 키가 겹치면 **기동을 거부한다**(어느 쪽이 이기는지 사람이 헷갈리면 안 된다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_해석기_스펙은_종류별로_필요한_필드를_요구한다():
    from src.config.schema_site import CheckConfig
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge"},
        "resolve": {
            "part_code": {"from": "rest", "entry": "list_parts", "field": "part_code",
                          "cardinality": "all"},
            "line_code": {"from": "mongo", "collection": "lines", "field": "line_code",
                          "filter": {"active": True}, "cardinality": "first:10"},
            "date": {"from": "clock", "expr": "today"},
            "graph_type": {"from": "unfiltered"}}})
    assert check.resolve["part_code"].entry == "list_parts"
    assert check.resolve["line_code"].cardinality == "first:10"


def test_알_수_없는_해석기_종류는_거부된다():
    from src.config.schema_site import CheckConfig
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({
            "judge": "rule", "schedule": {"interval": "5m"},
            "resolve": {"x": {"from": "s3", "bucket": "b"}}})


def test_해석기_종류마다_필요한_필드가_강제된다():
    from src.config.schema_site import CheckConfig
    for bad in ({"from": "rest", "field": "x"},              # entry 없음
                {"from": "mongo", "field": "x"},             # collection 없음
                {"from": "clock"},                           # expr 없음
                {"from": "clock", "expr": "언젠가"}):         # 어휘 밖 expr
        with pytest.raises(ValidationError):
            CheckConfig.model_validate({
                "judge": "rule", "schedule": {"interval": "5m"}, "resolve": {"x": bad}})


def test_카디널리티_어휘_밖은_거부된다():
    from src.config.schema_site import CheckConfig
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({
            "judge": "rule", "schedule": {"interval": "5m"},
            "resolve": {"x": {"from": "rest", "entry": "e", "field": "f",
                              "cardinality": "무제한"}}})


def test_정적_값과_해석_키가_겹치면_거부된다():
    # 어느 쪽이 이기는지 사람이 헷갈리면 안 된다.
    from src.config.schema_site import CheckConfig
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({
            "judge": "rule", "schedule": {"interval": "5m"},
            "params": {"body": {"part_code": ["P001"]}},
            "resolve": {"part_code": {"from": "unfiltered"}}})
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/config/test_schema_site.py -q -k "해석기 or 카디널리티 or 정적_값과"`
Expected: FAIL — `Extra inputs are not permitted` (`CheckConfig`가 `resolve`를 모른다)

- [ ] **Step 3: 최소 구현**

`src/config/schema_site.py`에 추가:

```python
Cardinality = Literal["all"]          # "first:N"·"sample:N"은 정규식으로 따로 받는다
_CARDINALITY = re.compile(r"^(all|first:[1-9][0-9]*|sample:[1-9][0-9]*)$")
_CLOCK_EXPR = {"today", "yesterday", "now_iso"}


class _ResolverBase(StrictModel):
    cardinality: str = "all"

    @model_validator(mode="after")
    def _cardinality_is_known(self):
        if not _CARDINALITY.match(self.cardinality):
            raise ValueError(
                f"cardinality {self.cardinality!r}는 all·first:N·sample:N 중 하나여야 한다")
        return self


class RestResolver(_ResolverBase):
    """형제 조회 항목을 불러 값을 얻는다 — 대상 시스템 자신이 인정한 목록이라 가장 강하다."""
    from_: Literal["rest"] = Field(alias="from")
    entry: str
    field: str


class MongoResolver(_ResolverBase):
    from_: Literal["mongo"] = Field(alias="from")
    collection: str
    field: str
    filter: dict[str, Any] = {}


class RedisResolver(_ResolverBase):
    from_: Literal["redis"] = Field(alias="from")
    pattern: str                       # scan 패턴


class ClockResolver(StrictModel):
    """주입된 시계로 값을 만든다 — datetime.now()를 직접 부르지 않는다(규율 2)."""
    from_: Literal["clock"] = Field(alias="from")
    expr: Literal["today", "yesterday", "now_iso"]


class UnfilteredResolver(StrictModel):
    """**의도한 전체 조회**를 명시한다(§2-N3).

    해석 실패로 우연히 전체 조회에 도달하는 경로와 처음부터 전체를 보려는 의도를
    코드가 구별할 수 있어야 한다 — 빈 필터는 endpoint에 따라 0/0/0(거짓 경보)이
    되기도 하고 전체 조회(거짓 안심, 조용해서 더 위험)가 되기도 한다.
    """
    from_: Literal["unfiltered"] = Field(alias="from")


ResolverSpec = Annotated[
    RestResolver | MongoResolver | RedisResolver | ClockResolver | UnfilteredResolver,
    Field(discriminator="from_")]
```

`CheckConfig`에 필드를 더하고 겹침을 막는다:

```python
    resolve: dict[str, ResolverSpec] = {}

    @model_validator(mode="after")
    def _static_and_resolved_keys_are_disjoint(self):
        static = self.params.get("body") if isinstance(self.params, dict) else None
        overlap = sorted(set(static or {}) & set(self.resolve))
        if overlap:
            raise ValueError(
                f"params.body와 resolve에 같은 키가 있다: {overlap} — 어느 쪽이 이기는지 "
                f"사람이 헷갈리면 안 되므로 한 곳에만 둔다")
        return self
```

import에 `from typing import Annotated`, `from pydantic import Field`를 맞춘다.

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/ -q`
- [ ] **Step 5: 커밋**

```
Declare where values come from, not what they are

part_code에 무엇을 넣을지는 명세에 없고 사업부/법인마다 다르고 매일 바뀐다 —
값을 config에 적는 어떤 설계도 즉시 썩는다. 그래서 선언하는 것은 값이 아니라
값이 어디서 오는지다.

params.body(정적)와 별도 필드로 둔 이유: 같은 dict에 섞고 {"from": ...} 모양으로
구별하면 마법 키 냄새 맡기가 되는데, 계획 8 리뷰가 runner에서 정확히 그것을 잡았다.
키가 겹치면 기동을 거부한다 — 어느 쪽이 이기는지 사람이 헷갈리면 안 된다.

unfiltered를 어휘에 넣은 이유(§2-N3): 해석 실패로 우연히 전체 조회에 도달하는
경로와 처음부터 전체를 보려는 의도를 코드가 구별할 수 있어야 한다.
```

---

## Task 2: 해석 결과 모델과 전부-또는-전무

**Files:** Create `src/patrol/resolvers.py` · Test `tests/patrol/test_resolvers.py`(신설)

**Interfaces:** `ResolveResult(params: dict, omitted: list[str], problems: list[str], truncated: list[str])`, `resolve_params(specs, *, adapters, clock) -> ResolveResult`

**§2-N3이 이 태스크의 전부다.** 해석기가 하나라도 값을 못 내면 **호출 자체를 하지 않는다.**

"불러놓고 판정만 안 한다"로는 부족하다 — 전체 조회로 돌아온 응답이 **증거로 Store에 박제되면** 나중에 서브에이전트가 "정상 확인됨"의 근거로 인용한다. 잘못된 범위의 응답은 증거가 아니라 오염원이므로 애초에 만들지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from datetime import datetime, timezone

from src.config.schema_site import CheckConfig
from src.infrastructure.factory import AdapterSet
from src.patrol.resolvers import resolve_params

T = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)


def _specs(**raw):
    check = CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                        "resolve": raw})
    return check.resolve


async def test_clock_해석기는_주입된_시계를_쓴다():
    out = await resolve_params(_specs(d={"from": "clock", "expr": "today"}),
                               adapters=AdapterSet(semaphore=asyncio.Semaphore(1)),
                               clock=lambda: T)
    assert out.problems == [] and out.params == {"d": "2026-09-04"}


async def test_unfiltered는_키를_아예_생략한다():
    # 빈 리스트를 보내는 것과 키를 안 보내는 것은 대상에서 다르게 동작한다.
    out = await resolve_params(_specs(g={"from": "unfiltered"}),
                               adapters=AdapterSet(semaphore=asyncio.Semaphore(1)),
                               clock=lambda: T)
    assert out.params == {} and out.omitted == ["g"] and out.problems == []


async def test_해석기가_비면_문제로_보고한다():
    # 빈 값을 그대로 보내면 대상에 따라 0/0/0(거짓 경보) 또는 전체 조회(거짓 안심)가
    # 된다. 어느 쪽인지 OpenAPI로도 알 수 없으므로 구별이 필요 없는 규율을 세운다.
    from src.infrastructure.stubs import StubMongo
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.mongo = StubMongo({"lines": []}, max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=adapters, clock=lambda: T)
    assert out.params == {} and any("line" in p for p in out.problems)


async def test_어댑터가_없으면_문제로_보고하고_raise하지_않는다():
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code"}),
        adapters=AdapterSet(semaphore=asyncio.Semaphore(1)), clock=lambda: T)
    assert out.params == {} and out.problems


async def test_카디널리티는_잘라내고_그_사실을_남긴다():
    # "5,000개 중 50개만 봤다"를 안 적으면 조용한 생략이다.
    from src.infrastructure.stubs import StubMongo
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.mongo = StubMongo({"lines": [{"line_code": f"L{i}"} for i in range(10)]},
                               max_rows=100, clock=lambda: T)
    out = await resolve_params(
        _specs(line={"from": "mongo", "collection": "lines", "field": "line_code",
                     "cardinality": "first:3"}),
        adapters=adapters, clock=lambda: T)
    assert out.params["line"] == ["L0", "L1", "L2"]
    assert any("line" in t and "10" in t for t in out.truncated)
```

- [ ] **Step 2: 실패를 확인한다** — `ModuleNotFoundError: src.patrol.resolvers`

- [ ] **Step 3: 최소 구현**

`src/patrol/resolvers.py`:

```python
"""파라미터 값 해석기 — 스펙 §2-N2·§2-N3.

값을 config에 적으면 즉시 썩는다(사업부/법인마다 다르고 매일 바뀐다). 그래서
config는 **값이 어디서 오는지**만 선언하고, 실제 값은 점검 실행 시점에 살아 있는
소스에서 읽는다.

**전부-또는-전무**(§2-N3): 해석기가 하나라도 값을 못 내면 호출 자체를 하지 않는다.
"불러놓고 판정만 안 한다"로는 부족한 이유 — 빈 필터로 나간 요청은 endpoint에 따라
0/0/0(거짓 경보)이 되기도 하고 전체 조회(거짓 안심, 조용해서 더 위험)가 되기도
하는데 어느 쪽인지 알 방법이 없다. 게다가 전체 조회로 돌아온 응답이 증거로 박제되면
나중에 서브에이전트가 "정상 확인됨"의 근거로 인용한다 — 잘못된 범위의 응답은
증거가 아니라 오염원이므로 애초에 만들지 않는다.

이 모듈은 절대 raise하지 않는다. 모든 실패는 ResolveResult.problems로 흡수한다.
"""
from collections.abc import Callable
from datetime import datetime, timedelta

from src.config.schema_app import StrictModel
from src.infrastructure.factory import AdapterSet


class ResolveResult(StrictModel):
    params: dict = {}
    omitted: list[str] = []       # unfiltered로 의도적으로 뺀 키
    problems: list[str] = []      # 하나라도 있으면 호출하지 않는다
    truncated: list[str] = []     # 카디널리티로 잘라낸 사실(보고서가 적어야 한다)


def _pluck(rows, field: str) -> list:
    """행 목록에서 field를 뽑아 중복을 없앤다(등장 순서 보존)."""
    seen, out = set(), []
    for row in rows if isinstance(rows, list) else []:
        value = row.get(field) if isinstance(row, dict) else None
        if value is not None and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _apply_cardinality(name: str, values: list, cardinality: str,
                       truncated: list[str]) -> list:
    if cardinality == "all" or len(values) <= int(cardinality.split(":")[1]):
        return values
    limit = int(cardinality.split(":")[1])
    kept = values[:limit] if cardinality.startswith("first:") else values[::max(
        1, len(values) // limit)][:limit]
    truncated.append(f"{name}: {len(values)}개 중 {len(kept)}개만 사용({cardinality})")
    return kept


async def resolve_params(specs: dict, *, adapters: AdapterSet,
                         clock: Callable[[], datetime]) -> ResolveResult:
    """해석기 스펙들을 실제 값으로 바꾼다. 절대 raise하지 않는다."""
    params, omitted, problems, truncated = {}, [], [], []
    for name, spec in specs.items():
        kind = spec.from_
        try:
            if kind == "unfiltered":
                # 빈 리스트를 보내는 것과 키를 안 보내는 것은 대상에서 다르게 동작한다.
                omitted.append(name)
                continue
            if kind == "clock":
                now = clock()
                params[name] = {"today": now.date().isoformat(),
                                "yesterday": (now - timedelta(days=1)).date().isoformat(),
                                "now_iso": now.isoformat()}[spec.expr]
                continue
            values = await _read_values(name, spec, adapters=adapters, problems=problems)
            if values is None:
                continue
            if not values:
                problems.append(f"해석기 {name!r}가 빈 결과를 냈다 — 빈 값을 보내면 "
                                f"대상에 따라 거짓 경보 또는 거짓 안심이 된다")
                continue
            params[name] = _apply_cardinality(name, values, spec.cardinality, truncated)
        except Exception as exc:                                   # noqa: BLE001 — 무raise 계약
            problems.append(f"해석기 {name!r} 실패 — {type(exc).__name__}: {exc}")
    return ResolveResult(params=params, omitted=omitted, problems=problems,
                         truncated=truncated)


async def _read_values(name: str, spec, *, adapters: AdapterSet, problems: list) -> list | None:
    """어댑터에서 값을 읽는다. 어댑터 미설정·조회 실패는 problems에 남기고 None."""
    kind = spec.from_
    if kind == "rest":
        if adapters.rest is None:
            problems.append(f"해석기 {name!r}: rest 어댑터 미설정")
            return None
        result = await adapters.rest.query(spec.entry, {})
    elif kind == "mongo":
        if adapters.mongo is None:
            problems.append(f"해석기 {name!r}: mongo 어댑터 미설정")
            return None
        result = await adapters.mongo.find(spec.collection, spec.filter)
    else:                                    # redis
        if adapters.redis is None:
            problems.append(f"해석기 {name!r}: redis 어댑터 미설정")
            return None
        result = await adapters.redis.scan(spec.pattern)
    if result.status == "error":
        problems.append(f"해석기 {name!r} 조회 실패 — {result.error}")
        return None
    data = result.data
    if kind == "redis":
        return list(data) if isinstance(data, list) else []
    rows = data.get("body") if kind == "rest" and isinstance(data, dict) else data
    return _pluck(rows, spec.field)
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/ -q`
- [ ] **Step 5: 커밋**

```
Resolve parameter values, all or nothing

해석기가 하나라도 값을 못 내면 호출 자체를 하지 않는다. "불러놓고 판정만 안
한다"로는 부족하다 — 빈 필터로 나간 요청은 endpoint에 따라 0/0/0(거짓 경보)이
되기도 하고 전체 조회(거짓 안심, 조용해서 더 위험)가 되기도 하는데 어느 쪽인지
알 방법이 없다.

더 나쁜 것은 증거 오염이다. 전체 조회로 돌아온 응답이 Store에 박제되면 나중에
서브에이전트가 "정상 확인됨"의 근거로 인용한다 — 잘못된 범위의 응답은 증거가
아니라 오염원이므로 애초에 만들지 않는다.

unfiltered는 키를 아예 생략한다. 빈 리스트를 보내는 것과 키를 안 보내는 것은
대상에서 다르게 동작하고, 의도한 전체 조회는 명시적이어야 한다.

카디널리티로 잘라낸 사실을 truncated에 남긴다 — "5,000개 중 50개만 봤다"를
안 적으면 조용한 생략이다.
```

---

## Task 3: rest_query가 해석기를 태운다

**Files:** Modify `src/patrol/probes.py` · Test `tests/patrol/test_probes.py`

**Interfaces:** Consumes Task 2의 `resolve_params`

해석 결과는 등재 스키마 검증(`entry_call_problems`)을 **다시** 통과해야 한다 — 계획 8의 불변식("판정한 것 = 보내는 것")이 해석 경로에도 적용된다.

잘라낸 사실은 `Envelope.complete=False` + `truncated_reason`으로 싣는다. **기존 정직성 메커니즘을 그대로 쓴다** — verify 노드가 "불완전 증거의 부정 결론 금지"를 이미 강제하므로, 잘린 표본으로 "이상 없음"을 단정하는 것이 자동으로 막힌다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
async def test_rest_query가_해석된_값을_보낸다():
    from src.config.schema_site import RestEntry
    from src.infrastructure.stubs import StubMongo, StubRest
    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"line_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /summary/prod": {"badge": [1]}}, set(), entries,
                             clock=lambda: T)
    adapters.mongo = StubMongo({"lines": [{"line_code": "L1"}, {"line_code": "L2"}]},
                               max_rows=100, clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge"},
        "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                  "field": "line_code"}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "ok"
    assert result.data["request"]["params"] == {"line_code": ["L1", "L2"]}


async def test_해석_실패면_호출하지_않고_error다():
    from src.config.schema_site import RestEntry
    from src.infrastructure.stubs import StubMongo, StubRest
    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"line_code": "list[str]"})}
    called = []

    class SpyRest(StubRest):
        async def query(self, entry, params):
            called.append(params)
            return await super().query(entry, params)

    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = SpyRest({"POST /summary/prod": {"badge": [1]}}, set(), entries,
                            clock=lambda: T)
    adapters.mongo = StubMongo({"lines": []}, max_rows=100, clock=lambda: T)   # 빈 결과
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge"},
        "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                  "field": "line_code"}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "error" and "line_code" in result.error
    assert called == [], "해석에 실패했는데 대상을 호출했다"


async def test_잘라낸_표본은_불완전으로_표시된다():
    from src.config.schema_site import RestEntry
    from src.infrastructure.stubs import StubMongo, StubRest
    entries = {"e": RestEntry(method="POST", path="/x",
                              body_schema={"line_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /x": {"ok": 1}}, set(), entries, clock=lambda: T)
    adapters.mongo = StubMongo({"lines": [{"line_code": f"L{i}"} for i in range(10)]},
                               max_rows=100, clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body.ok"},
        "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                  "field": "line_code", "cardinality": "first:3"}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "ok"
    assert result.envelope.complete is False
    assert "10" in (result.envelope.truncated_reason or "")
```

- [ ] **Step 2: 실패를 확인한다**
- [ ] **Step 3: 최소 구현**

`src/patrol/probes.py`의 `rest_query`를 고친다:

```python
async def rest_query(adapters: AdapterSet, check: CheckConfig, *, clock) -> ProbeResult:
    """target "rest:<항목명>" → 해석기로 params를 만들어 adapters.rest.query 호출.

    해석 결과는 등재 스키마 검증을 **다시** 통과해야 소켓에 나간다(어댑터가 한다) —
    계획 8의 불변식("판정한 것 = 보내는 것")이 해석 경로에도 그대로 적용된다.
    """
    try:
        if adapters.rest is None:
            return _error("어댑터 미설정: rest", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "rest":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, entry = parts
        static = check.params.get("body", {})
        if not isinstance(static, dict):
            return _error(f"params.body는 dict여야 한다 (받은 타입: {type(static).__name__})",
                          clock)
        resolved = await resolve_params(check.resolve, adapters=adapters, clock=clock)
        if resolved.problems:
            # 전부-또는-전무(§2-N3): 하나라도 못 내면 호출하지 않는다. finding이
            # 아니라 error다 — 우리 쪽 실패가 "현장 이상"으로 둔갑하면 안 된다.
            return _error("파라미터 해석 실패 — " + "; ".join(resolved.problems), clock)
        result = await adapters.rest.query(entry, {**static, **resolved.params})
        if resolved.truncated and result.status == "ok":
            # 잘린 표본으로 "이상 없음"을 단정하는 것을 verify가 자동으로 막는다
            # (불완전 증거의 부정 결론 금지) — 기존 메커니즘을 그대로 쓴다.
            envelope = result.envelope.model_copy(update={
                "complete": False,
                "truncated_reason": "; ".join(resolved.truncated)})
            return result.model_copy(update={"envelope": envelope})
        return result
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)
```

import에 `from src.patrol.resolvers import resolve_params`.

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/ -q`
- [ ] **Step 5: 커밋**

```
Send resolved values, and say when the sample was cut

해석 결과가 등재 스키마 검증을 다시 통과해야 소켓에 나간다 — 계획 8의 불변식
("판정한 것 = 보내는 것")이 해석 경로에도 적용된다.

해석 실패를 error로 두는 것이 핵심이다. finding으로 삼키면 우리 쪽 실패가 "현장
이상"으로 둔갑해 매 순찰마다 거짓 경보가 된다 — KnownRuleError가 존재하는 이유와
같은 논리다.

잘라낸 표본을 complete=False로 표시하면 verify가 "불완전 증거의 부정 결론 금지"를
이미 강제하므로, 잘린 표본으로 "이상 없음"을 단정하는 것이 자동으로 막힌다.
새 메커니즘을 만들지 않았다.
```

---

## Task 4: 기동 검증 — 해석기가 가리키는 것이 실재하는가

**Files:** Modify `src/boot.py` · Test `tests/test_boot.py`

해석기가 없는 항목·컬렉션을 가리키면 매 순찰이 error를 내고 끝난다. **밤에 조용히 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.**

검증할 것:
- `rest` 해석기의 `entry`가 `target.rest.entries`에 실재하는가
- 그 항목이 **GET**인가(조회용 항목이 POST면 부작용 위험)
- 해석 대상 키가 점검 target 항목의 스키마(`body_schema`/`query_schema`)에 있는가
- `unfiltered` 키도 스키마에 있는가(없으면 애초에 보낼 수 없는 키를 생략한다고 선언한 것)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_해석기가_없는_항목을_가리키면_기동을_거부한다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {
            "base_url": "http://x",
            "entries": {"e": {"method": "POST", "path": "/x",
                              "body_schema": {"part_code": "list[str]"}}}}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
            "params": {"rule": "exists", "field": "body"},
            "resolve": {"part_code": {"from": "rest", "entry": "없는항목",
                                      "field": "part_code"}}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("없는항목" in e.problem for e in errors)


def test_스키마에_없는_키를_해석하면_기동을_거부한다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {
            "base_url": "http://x",
            "entries": {"e": {"method": "POST", "path": "/x",
                              "body_schema": {"part_code": "list[str]"}}}}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
            "params": {"rule": "exists", "field": "body"},
            "resolve": {"없는키": {"from": "unfiltered"}}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("없는키" in e.problem for e in errors)


def test_조회용_해석기_항목이_POST면_기동을_거부한다(tmp_path):
    # 값을 얻으려고 부수효과 가능성이 있는 메서드를 쓰면 안 된다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {
            "base_url": "http://x",
            "entries": {"e": {"method": "POST", "path": "/x",
                              "body_schema": {"part_code": "list[str]"}},
                        "lister": {"method": "POST", "path": "/list",
                                   "body_schema": {"q": "str"}}}}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
            "params": {"rule": "exists", "field": "body"},
            "resolve": {"part_code": {"from": "rest", "entry": "lister",
                                      "field": "part_code"}}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("lister" in e.problem and "GET" in e.problem for e in errors)
```

- [ ] **Step 2: 실패를 확인한다**
- [ ] **Step 3: 최소 구현**

`src/boot.py`의 등재 항목 분기(계획 8이 만든 `entry is None` else 절)에 이어 붙인다:

```python
                    else:
                        body = check.params.get("body", {})
                        for problem in entry_call_problems(entry, body):
                            errors.append(BootError(where, f"점검 {name!r}: {problem}"))
                        schema = entry.body_schema or entry.query_schema
                        for key, spec in check.resolve.items():
                            if key not in schema:
                                errors.append(BootError(
                                    where, f"점검 {name!r}의 resolve 키 {key!r}가 "
                                           f"항목 {rest!r}의 스키마에 없다"))
                            if spec.from_ != "rest":
                                continue
                            source = entries.get(spec.entry)
                            if source is None:
                                errors.append(BootError(
                                    where, f"점검 {name!r}의 해석기 {key!r}가 가리키는 "
                                           f"항목 {spec.entry!r}이 등재돼 있지 않다"))
                            elif source.method != "GET":
                                # 값을 얻으려고 부수효과 가능성이 있는 메서드를 쓰지 않는다.
                                errors.append(BootError(
                                    where, f"점검 {name!r}의 해석기 {key!r}가 가리키는 "
                                           f"항목 {spec.entry!r}은 GET이어야 한다"))
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/ -q`
- [ ] **Step 5: 커밋**

```
Refuse to boot when a resolver points at nothing

해석기가 없는 항목·스키마에 없는 키를 가리키면 매 순찰이 error를 내고 끝난다 —
밤에 조용히 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.

조회용 항목이 GET이어야 한다는 것도 강제한다. 값을 얻으려고 부수효과 가능성이
있는 메서드를 쓰는 것은 등재제가 막으려던 바로 그 형태다.
```

---

## Task 5: 예시와 문서

**Files:** `config.example/gbm/mx.json`, `docs/config-reference.md`, `docs/howto.md`, `docs/going-live.md`, `CLAUDE.md`

- [ ] **Step 1** 예시 config의 `prod.badge_nonzero` 점검에 해석기를 넣는다(`line_code`를 Mongo에서). 스텁 시드도 함께.
- [ ] **Step 2** 예시가 실제로 도는지 확인한다:
```bash
set -a; source .env.example; set +a
.venv/bin/python -m src knowledge validate --config-root config.example --repo-root .
.venv/bin/python -m src patrol run --for-seconds 0 --config-root config.example --repo-root .
```
- [ ] **Step 3** 문서:
  - `config-reference.md`에 `resolve.*` 행(종류별 필드·`cardinality` 어휘)
  - `howto.md`에 "값이 매일 바뀌는 파라미터를 쓰고 싶다" 항목
  - `going-live.md` 승인 절차에 "해석기가 가리키는 조회 항목도 읽기 전용인지 확인" 한 줄
  - `CLAUDE.md` 코드 지도에 `src/patrol/resolvers.py`
- [ ] **Step 4** `rm -rf output/ && pytest tests/ -q`
- [ ] **Step 5** 커밋

---

## 완료 기준

- [ ] `rm -rf output/ && pytest tests/ -q` 전건 통과, 365보다 증가
- [ ] 해석 실패 시 **대상을 호출하지 않는다**(스파이 어댑터로 확인)
- [ ] 잘라낸 표본이 `complete=False`로 나가고 보고서 §4에 "⚠ 불완전"로 렌더된다
- [ ] `config.example`로 `knowledge validate`·`patrol run` 둘 다 exit 0
- [ ] 해석기가 `datetime.now()`를 직접 부르지 않는다(`grep`)

## 이 계획이 **하지 않는** 것

| 미포함 | 어디로 |
|---|---|
| OpenAPI pin·드리프트 점검·`target_api` digest | 계획 10 |
| 응답 필드 경로(`field: "body.oee"`) 검증 | 계획 10. 응답 스키마가 있어야 한다 |
| 코드 grep 해석기 | 정적 분석 난제. OpenAPI enum(계획 10)이 먼저 답할 수 있는지 본다 |
| 해석 결과 캐시 | 먼저 실제로 느린지 측정한다. 캐시는 "언제 무효화하나"를 새로 만든다 |
| concern 축·rule 확장 | 계획 11 |
