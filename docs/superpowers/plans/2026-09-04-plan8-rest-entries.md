# 계획 8 — 등재제 REST 프로버 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** POST와 인증이 필요한 REST 점검을 열되, "완전 읽기 전용"이 문서상 약속이 아니라 **메커니즘**으로 남게 한다.

**Architecture:** v1의 읽기 전용은 `RestProberPort`에 `get`밖에 없어서 쓰기가 물리적으로 불가능했다. POST를 열면 그 성질이 사라지므로, 잃은 것을 **등재제(entry registry)**로 대체한다: config에 손으로 쓴 `(method, path, 닫힌 body 스키마, 허용 쿼리 키)` 항목만 호출할 수 있고, **메서드는 호출자 인자가 아니라 등재 항목에서 어댑터가 고른다**. 포트에 제네릭 `post()`는 만들지 않는다 — "임의의 POST를 수행하라"는 호출이 타입 수준에서 표현 불가능해야 한다.

**Tech Stack:** Python 3.12 · pydantic 2 · httpx · pytest(`asyncio_mode=auto`)

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) 의 **P4 전반부**. §2-N1(읽기 전용 강제) · §2-N4(증거는 무엇을 물었는지 실어야 한다).

**선행:** 계획 7 완료 + 리뷰 픽스(커밋 `aa0bd65`, 327 tests).
**후속:** 계획 9가 파라미터 **값**의 해석기와 OpenAPI pin·드리프트 점검을 얹는다. 이 계획은 **칼럼(스키마)까지만** 다룬다.

## Global Constraints

- **무raise**: 어댑터·프로브 층은 예외를 던지지 않는다. 실패는 `ProbeResult(status="error")`로 흡수한다. 허용된 예외 셋(`get_evidence`의 `KeyError`, `KnownRuleError`, 워커 레저) 밖의 새 예외를 만들지 않는다.
- **시계 주입**: `src/__main__.py` 밖에서 `datetime.now()` 금지.
- **StrictModel**: 새 pydantic 모델은 `src/config/schema_app.py`의 `StrictModel` 상속.
- **비밀값은 `.env`만**: 토큰은 config에 리터럴로 쓰지 않는다. `${ENV}` 참조 + `SecretStr`(마스킹).
- **순수 판정은 `query_rules.py`에**: I/O 없는 함수로 두어 stub·real·boot가 **같은 규칙**을 공유한다. 이 모듈의 명시된 존재 이유다.
- **스텁도 진짜 계약을 진다**: `StubRest`가 `RealRest`와 같은 거부·같은 형태를 돌려줘야 한다. 테스트가 전부 스텁이므로 여기서 느슨해지면 그 계약을 검증하는 테스트가 무의미해진다(`tests/README.md`).
- **주석·문서는 한국어, WHY만.** **커밋 메시지는 영어 제목 + 한국어 본문**. 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **테스트**: `.venv/bin/python -m pytest tests/ -q` (기준선 **327 passed**). `output/`을 지우고도 통과해야 한다 — 잔재에 기대는 초록은 초록이 아니다(계획 7 리뷰에서 실제로 겪었다).
- **작업 디렉터리**: `/home/hchju777/langgraph_ws/deepagent-template`. 서브에이전트에게는 **절대 경로**로 지시한다.
- **브랜치**: 이 계획은 `feat/plan8-rest-entries`에서 구현하고, 리뷰 통과 후 `main`에 머지한다.

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `src/infrastructure/query_rules.py` | I/O 없는 판정 규칙 | `endpoint_allowed` URL 파싱 재작성, `entry_body_problems` 신설 |
| `src/config/schema_site.py` | 사이트 config 스키마 | `RestEntry`, `RestAuth`, `RestTarget.entries`/`auth` |
| `src/domain/ports.py` | 대상 시스템 포트 | `RestProberPort.query(entry, params)` 추가. `post`/`put`/`patch`/`delete`는 **만들지 않는다** |
| `src/infrastructure/rest_prober.py` | 실 REST 어댑터 | `query` 구현, 인증 헤더, 메서드는 항목에서 |
| `src/infrastructure/stubs.py` | 스텁 REST | `query` 구현(같은 거부 규칙) |
| `src/infrastructure/factory.py` | 어댑터 조립 | 등재 항목을 어댑터에 넘긴다 |
| `src/patrol/probes.py` | 프로브 레지스트리 | `rest_query` 신설, `resolve_probe`가 항목명을 라우팅 |
| `src/boot.py` | 기동 검증 | 점검 target ↔ 등재 항목 대조 |
| `src/patrol/runner.py` | 점검 실행·증거 박제 | 등재 항목 증거의 source에 body digest(N4) |

**새 모듈 없음.** 등재제는 새 서브시스템이 아니라 기존 어댑터 표면의 좁히기다.

---

## Task 1: 끝점 판정을 URL 파싱으로 다시 쓴다

**Files:**
- Modify: `src/infrastructure/query_rules.py`
- Test: `tests/infrastructure/test_query_rules.py`

**Interfaces:**
- Produces: `endpoint_allowed(endpoint, patterns, *, query_keys=frozenset()) -> bool`

지금은 `?`/`#`/`;`를 문자 단위로 거부한다(계획 6의 보안 픽스). 그 결과 **정상 쿼리 파라미터도 전면 거부**돼 MES 점검이 불가능하다. 파싱 기반으로 바꿔 path는 패턴에 `fullmatch`하고, query는 **키 allowlist**로 따로 판정한다. fragment는 여전히 무조건 거부한다(절단돼 다른 끝점이 된다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/infrastructure/test_query_rules.py` 끝에 추가:

```python
def test_허용된_쿼리_키는_통과하고_나머지는_거부된다():
    patterns = {"/api/v1/lines/{line}/oee"}
    ok = "/api/v1/lines/L1/oee?date=2026-09-04"
    assert not endpoint_allowed(ok, patterns)                       # 기본은 쿼리 불가
    assert endpoint_allowed(ok, patterns, query_keys={"date"})
    assert not endpoint_allowed(ok, patterns, query_keys={"line"})  # 미등록 키
    # 등록된 키라도 값에 경로가 섞이면 path 판정을 우회할 수 없어야 한다
    assert endpoint_allowed("/api/v1/lines/L1/oee?date=x&date=y", patterns,
                            query_keys={"date"})


def test_퍼센트_인코딩은_쿼리_값에서만_허용된다():
    patterns = {"/api/v1/lines/{line}/oee"}
    # path의 %는 여전히 거부한다 — %2e%2e 경로 순회 우회로가 있다
    assert not endpoint_allowed("/api/v1/lines/%2e%2e/oee", patterns, query_keys={"date"})
    # 쿼리 값의 인코딩은 정상이다(ISO 시각의 콜론 등)
    assert endpoint_allowed("/api/v1/lines/L1/oee?date=2026-09-04T00%3A00%3A00",
                            patterns, query_keys={"date"})


def test_프래그먼트는_쿼리를_허용해도_거부된다():
    # #은 절단돼 등록되지 않은 끝점이 된다 — 쿼리 허용과 무관하게 막는다.
    assert not endpoint_allowed("/api/v1/lines/L1#/oee", {"/api/v1/lines/{line}/oee"},
                                query_keys={"date"})


def test_기존_거부는_그대로_유지된다():
    # 계획 6이 막은 우회로가 파싱 재작성 후에도 막혀 있어야 한다.
    patterns = {"/api/v1/lines/{line}/oee"}
    assert not endpoint_allowed("/api/v1/lines/L1?_method=DELETE&/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/L1;x=y/oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/../oee", patterns)
    assert not endpoint_allowed("/api/v1/lines/7/oee\n", patterns)
    assert not endpoint_allowed("http://evil/api/v1/lines/7/oee", patterns)  # 절대 URL
    assert endpoint_allowed("/api/v1/lines/7/oee", patterns)                 # 정상은 통과
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_query_rules.py -q -k "쿼리_키 or 퍼센트 or 프래그먼트"`
Expected: FAIL — `TypeError: endpoint_allowed() got an unexpected keyword argument 'query_keys'`

- [ ] **Step 3: 최소 구현**

`src/infrastructure/query_rules.py`의 `endpoint_allowed`를 교체한다:

```python
def endpoint_allowed(endpoint, patterns, *, query_keys=frozenset()):
    """토폴로지/등재 패턴과의 전체 일치 판정.

    문자 단위 거부에서 URL 파싱으로 바꿨다. 이전에는 `?`/`#`/`;`를 통째로 막아
    정상 쿼리 파라미터(MES 점검이 요구한다)까지 못 쓰게 됐다 — 파싱하면 path와
    query를 갈라 각각에 맞는 규칙을 적용할 수 있다.

    거부 규칙:
    - scheme/netloc이 있으면(절대 URL) 거부 — base_url을 우회한다.
    - fragment는 무조건 거부 — 절단돼 등록되지 않은 끝점이 된다(실증됨).
    - path에 `%`가 있으면 거부 — `%2e%2e` 경로 순회 우회로.
    - path 세그먼트에 `.`/`..`/`;`가 있으면 거부 — 순회와 매트릭스 파라미터.
    - query 키가 query_keys에 없으면 거부. 기본값이 빈 집합이라 **등재하지 않은
      호출은 지금까지와 똑같이 쿼리를 쓸 수 없다.**

    `{자리표시자}`는 `[^/?#;]+`로 컴파일한다 — `[^/]+`는 구분자를 삼켜서
    `/lines/L1?_method=DELETE&/oee`가 패턴에 매치되는 우회로를 만든다.
    """
    parts = urlsplit(endpoint)
    if parts.scheme or parts.netloc or parts.fragment:
        return False
    path = parts.path
    if "%" in path or any(seg in (".", "..") or ";" in seg for seg in path.split("/")):
        return False
    if parts.query:
        keys = {k for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
        if not keys or not keys <= set(query_keys):
            return False
    for pattern in patterns:
        segs = re.split(r"\{[^/}]+\}", pattern)
        regex = "[^/?#;]+".join(re.escape(seg) for seg in segs)
        if re.fullmatch(regex, path):
            return True
    return False
```

파일 상단 import에 추가:

```python
from urllib.parse import parse_qsl, urlsplit
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q` — PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Judge endpoints by parsing the URL, not by banning characters

계획 6이 ?·#·;를 문자 단위로 막아 우회로를 닫았지만, 그 대가로 정상 쿼리
파라미터까지 전면 거부됐다 — MES 점검은 쿼리가 필수라 아예 불가능했다.

파싱하면 path와 query를 갈라 각각에 맞는 규칙을 쓸 수 있다: path는 패턴에
fullmatch하고 %·순회·매트릭스 파라미터를 거부하며, query는 키 allowlist로
따로 판정한다. query_keys 기본값이 빈 집합이라 등재하지 않은 호출의 동작은
지금과 똑같다.

자리표시자를 [^/?#;]+로 좁힌 것이 핵심이다 — [^/]+는 구분자를 삼켜서
파싱 이전에 패턴 매칭 자체가 뚫렸다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 등재 항목 스키마

**Files:**
- Modify: `src/config/schema_site.py`
- Test: `tests/config/test_schema_site.py`

**Interfaces:**
- Produces: `RestAuth`(header/value), `RestEntry`(method/path/body_schema/query_keys), `RestTarget.auth`/`RestTarget.entries`

**이것이 권한 결정이 사는 자리다.** 목록에 없으면 문이 열리지 않으므로 `read_only: true` 같은 플래그는 중복이 된다 — 사람이 손으로 쓰는 것은 플래그가 아니라 **항목 자체**다.

body 타입 어휘는 좁게 닫는다: `str`·`int`·`float`·`bool`·`list[str]`·`list[int]`. 넓히면 검증이 느슨해지고, 느슨한 검증은 없는 검증과 같다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/config/test_schema_site.py` 끝에 추가:

```python
def test_등재_항목은_메서드와_닫힌_body_스키마를_요구한다():
    from src.config.schema_site import RestTarget
    target = RestTarget.model_validate({
        "base_url": "http://x",
        "entries": {"summary_prod": {"method": "POST", "path": "/summary/prod",
                                     "body_schema": {"part_code": "list[str]",
                                                     "line_code": "str"}}}})
    entry = target.entries["summary_prod"]
    assert entry.method == "POST" and entry.query_keys == []


def test_쓰기_메서드는_등재할_수_없다():
    # 메서드를 등재 항목이 정하므로, 여기서 막지 않으면 config 한 줄로
    # 대상 시스템에 쓰기를 할 수 있게 된다.
    from pydantic import ValidationError
    from src.config.schema_site import RestTarget
    for method in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(ValidationError):
            RestTarget.model_validate({"base_url": "http://x",
                                       "entries": {"e": {"method": method, "path": "/x"}}})


def test_body_타입_어휘_밖은_거부된다():
    from pydantic import ValidationError
    from src.config.schema_site import RestTarget
    with pytest.raises(ValidationError):
        RestTarget.model_validate({
            "base_url": "http://x",
            "entries": {"e": {"method": "POST", "path": "/x",
                              "body_schema": {"f": "dict"}}}})


def test_GET_항목은_body를_가질_수_없다():
    # GET에 body를 실으면 프록시·서버마다 동작이 갈린다. 쿼리 키로 표현해야 한다.
    from pydantic import ValidationError
    from src.config.schema_site import RestTarget
    with pytest.raises(ValidationError):
        RestTarget.model_validate({
            "base_url": "http://x",
            "entries": {"e": {"method": "GET", "path": "/x",
                              "body_schema": {"f": "str"}}}})


def test_인증_토큰은_SecretStr로_마스킹된다():
    from src.config.schema_site import RestTarget
    target = RestTarget.model_validate({
        "base_url": "http://x",
        "auth": {"header": "x-dep-ticket", "value": "비밀토큰"}})
    assert "비밀토큰" not in repr(target)
    assert target.auth.value.get_secret_value() == "비밀토큰"
```

`tests/config/test_schema_site.py` 상단에 `import pytest`가 없으면 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/config/test_schema_site.py -q -k "등재_항목 or 쓰기_메서드 or body_타입 or GET_항목 or 인증_토큰"`
Expected: FAIL — `Extra inputs are not permitted [type=extra_forbidden]` (RestTarget이 `entries`를 모른다)

- [ ] **Step 3: 최소 구현**

`src/config/schema_site.py`의 `RestTarget`을 교체한다:

```python
# body 필드 타입 어휘. 좁게 닫는다 — 넓히면 검증이 느슨해지고, 느슨한 검증은
# 없는 검증과 같다. 실제로 필요해지면 그때 하나씩 연다.
BodyFieldType = Literal["str", "int", "float", "bool", "list[str]", "list[int]"]


class RestAuth(StrictModel):
    """대상 API가 요구하는 인증 헤더. 값은 반드시 ${ENV} 참조로 준다."""
    header: str
    value: SecretStr


class RestEntry(StrictModel):
    """호출을 허가받은 끝점 하나.

    **메서드가 여기 있는 것이 이 설계의 핵심이다** — 호출자는 항목 이름만 대고,
    어떤 HTTP 메서드로 나갈지는 어댑터가 이 값을 보고 정한다. 그래서 "임의의
    POST를 수행하라"는 호출이 코드에 표현될 수 없다.

    쓰기 메서드를 등재 어휘에서 빼는 이유: 메서드 결정권을 config로 옮긴 이상,
    여기서 막지 않으면 config 한 줄로 대상 시스템에 쓰기를 할 수 있다.
    """
    method: Literal["GET", "POST"] = "GET"
    path: str
    body_schema: dict[str, BodyFieldType] = {}
    query_keys: list[str] = []

    @model_validator(mode="after")
    def _get_has_no_body(self):
        # GET에 body를 실으면 프록시·서버마다 동작이 갈린다 — 쿼리 키로 표현한다.
        if self.method == "GET" and self.body_schema:
            raise ValueError("GET 항목에는 body_schema를 둘 수 없다 — query_keys를 쓰라")
        return self


class RestTarget(StrictModel):
    base_url: str
    auth: RestAuth | None = None
    entries: dict[str, RestEntry] = {}
```

파일 상단 import를 맞춘다:

```python
from pydantic import SecretStr, model_validator
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Put the authorization decision in config, as entries

POST를 열면 "포트에 get밖에 없어서 쓰기가 불가능하다"는 v1의 메커니즘이
사라진다. 그 자리를 등재제가 대신한다 — 목록에 있는 항목만 호출할 수 있고,
메서드는 호출자가 아니라 항목이 정한다.

read_only: true 같은 플래그를 두지 않는 이유: 목록에 없으면 문이 안 열리므로
플래그가 중복이다. 그리고 끝점 40개에 플래그를 적으라고 하면 사람은 기동
검증을 통과시키려고 전부 true로 적는다 — 아무도 생각하지 않는 체크박스의
안전 가치는 0이다. 손으로 쓰는 것을 항목 자체로 만들면 목록이 짧아져 실제로
읽고 생각하게 된다.

body 타입 어휘를 여섯 개로 닫은 것은 느슨한 검증이 없는 검증과 같기 때문이다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: body 검증 순수 함수

**Files:**
- Modify: `src/infrastructure/query_rules.py`
- Test: `tests/infrastructure/test_query_rules.py`

**Interfaces:**
- Produces: `entry_body_problems(body: dict, schema: dict[str, str]) -> list[str]`

메서드 수준에서 잃은 메커니즘의 **정직한 대체물이 body 수준의 닫힌 스키마**다. "POST는 쓰기 가능한 동사다"를 "이 항목은 이 키들을 이 타입으로만 실을 수 있다"로 되돌린다.

`query_rules.py`에 두는 이유는 그 모듈의 존재 이유 그대로다 — I/O가 없어 stub·real·boot가 같은 규칙을 공유하고 단위 테스트가 전부를 덮는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_body는_선언된_키와_타입만_허용한다():
    from src.infrastructure.query_rules import entry_body_problems
    schema = {"part_code": "list[str]", "line_code": "str", "limit": "int"}
    assert entry_body_problems({"part_code": ["P001"], "line_code": "L1", "limit": 10},
                               schema) == []
    # 스키마 밖 키 — LLM이나 해석기가 실수로 실을 수 있고, 대상이 그걸 해석하면
    # 우리가 의도하지 않은 동작이 된다
    assert any("save_as" in p for p in
               entry_body_problems({"line_code": "L1", "save_as": "x"}, schema))
    # 타입 불일치
    assert any("part_code" in p for p in
               entry_body_problems({"part_code": "P001"}, schema))
    assert any("limit" in p for p in entry_body_problems({"limit": "10"}, schema))
    # 리스트 원소 타입
    assert any("part_code" in p for p in
               entry_body_problems({"part_code": ["P001", 2]}, schema))


def test_body의_누락은_문제가_아니다():
    # 어떤 필드가 필수인지는 대상 API가 정하고 우리는 모른다(계획 9의 OpenAPI가
    # 답할 문제다). 여기서 강제하면 스키마를 우리 추측으로 좁히게 된다.
    from src.infrastructure.query_rules import entry_body_problems
    assert entry_body_problems({}, {"part_code": "list[str]"}) == []


def test_bool은_int로_통과하지_않는다():
    # 파이썬에서 bool은 int의 하위 타입이라 isinstance(True, int)가 참이다.
    # 그대로 두면 {"limit": True}가 통과해 대상에 1로 나간다.
    from src.infrastructure.query_rules import entry_body_problems
    assert entry_body_problems({"limit": True}, {"limit": "int"}) != []
    assert entry_body_problems({"flag": True}, {"flag": "bool"}) == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_query_rules.py -q -k "body는 or body의 or bool은"`
Expected: FAIL — `ImportError: cannot import name 'entry_body_problems'`

- [ ] **Step 3: 최소 구현**

`src/infrastructure/query_rules.py`에 추가:

```python
_SCALARS = {"str": str, "int": int, "float": float, "bool": bool}


def _type_problem(name: str, value, want: str) -> str | None:
    if want.startswith("list["):
        if not isinstance(value, list):
            return f"body 필드 {name!r}는 {want}여야 한다 (받은 타입: {type(value).__name__})"
        elem = _SCALARS[want[5:-1]]
        bad = [v for v in value if not _is_exact(v, elem)]
        return (f"body 필드 {name!r}의 원소 타입이 {want}와 다르다 (예: {bad[0]!r})"
                if bad else None)
    if not _is_exact(value, _SCALARS[want]):
        return f"body 필드 {name!r}는 {want}여야 한다 (받은 타입: {type(value).__name__})"
    return None


def _is_exact(value, want: type) -> bool:
    """bool을 int로 통과시키지 않는다 — 파이썬에서 bool은 int의 하위 타입이라
    isinstance(True, int)가 참이고, 그대로 두면 {"limit": True}가 1로 나간다."""
    if want is int and isinstance(value, bool):
        return False
    if want is float:                       # int는 float 자리에 허용한다(JSON 관례)
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, want)


def entry_body_problems(body: dict, schema: dict) -> list[str]:
    """등재 항목의 닫힌 body 스키마를 검증한다. 문제 목록을 돌려준다(빈 리스트면 통과).

    메서드 수준에서 잃은 "쓰기가 불가능하다"는 성질의 정직한 대체물이다 —
    "POST는 쓰기 가능한 동사다"를 "이 항목은 이 키들을 이 타입으로만 실을 수
    있다"로 되돌린다.

    필드 **누락**은 문제로 보지 않는다: 어떤 필드가 필수인지는 대상 API가 정하고
    우리는 모른다(계획 9의 OpenAPI가 답할 문제). 여기서 강제하면 스키마를 우리
    추측으로 좁히게 된다.
    """
    problems = []
    for name, value in body.items():
        want = schema.get(name)
        if want is None:
            problems.append(f"body 필드 {name!r}는 등재 스키마에 없다")
            continue
        problem = _type_problem(name, value, want)
        if problem:
            problems.append(problem)
    return problems
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Close the body schema so POST cannot smuggle anything

메서드 수준에서 잃은 "쓰기가 불가능하다"는 성질의 정직한 대체물이 body 수준의
닫힌 스키마다. "POST는 쓰기 가능한 동사다"를 "이 항목은 이 키들을 이 타입으로만
실을 수 있다"로 되돌린다.

필드 누락을 문제로 보지 않는 이유: 무엇이 필수인지는 대상 API가 정하고 우리는
모른다(계획 9의 OpenAPI가 답한다). 여기서 강제하면 스키마를 우리 추측으로 좁힌다.

bool을 int로 통과시키지 않는 것은 파이썬 특유의 함정이다 — isinstance(True, int)가
참이라 그대로 두면 {"limit": True}가 대상에 1로 나간다.

query_rules에 두는 이유는 그 모듈의 존재 이유 그대로다: I/O가 없어 stub·real·boot가
같은 규칙을 공유한다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 포트에 좁은 query를 연다

**Files:**
- Modify: `src/domain/ports.py`
- Test: `tests/domain/test_ports.py` (없으면 신설)

**Interfaces:**
- Produces: `RestProberPort.query(entry: str, params: dict) -> ProbeResult`

**`post()`를 만들지 않는다.** 호출자는 항목 이름만 대고, 메서드는 어댑터가 항목에서 고른다. 그리고 **포트 표면을 테스트가 지킨다** — 6개월 뒤 누군가 편의상 `post()`를 추가하면 v1의 성질이 조용히 사라지고, CLAUDE.md 산문은 읽지 않으면 무력하다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/domain/test_ports.py`를 만든다:

```python
from src.domain.ports import RestProberPort


def test_REST_포트에_쓰기_메서드가_없다():
    # v1의 "완전 읽기 전용"은 포트에 get밖에 없다는 물리적 사실이었다. POST를
    # 열면서 그 자리를 등재제가 대신하지만, 제네릭 쓰기 메서드가 생기는 순간
    # 등재제를 우회할 수 있다. 산문 규율은 읽지 않으면 무력하므로 테스트가 지킨다.
    surface = {name for name in vars(RestProberPort) if not name.startswith("_")}
    assert surface == {"get", "query"}
    for forbidden in ("post", "put", "patch", "delete", "request", "send"):
        assert not hasattr(RestProberPort, forbidden)


def test_query는_항목_이름을_받지_메서드를_받지_않는다():
    # 메서드가 인자면 호출자가 정하게 된다 — 등재 항목이 정해야 한다.
    import inspect
    params = list(inspect.signature(RestProberPort.query).parameters)
    assert params == ["self", "entry", "params"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/domain/test_ports.py -q`
Expected: FAIL — `AttributeError: type object 'RestProberPort' has no attribute 'query'`

- [ ] **Step 3: 최소 구현**

`src/domain/ports.py`의 `RestProberPort`를 교체한다:

```python
class RestProberPort(ABC):
    """대상 REST API 읽기 전용 접근.

    쓰기 메서드(post/put/patch/delete)를 **의도적으로 두지 않는다**. v1에서는
    get 하나뿐이라 쓰기가 물리적으로 불가능했고, POST가 필요해진 뒤에도 그 성질을
    잃지 않으려면 "임의의 메서드로 임의의 경로를 호출하라"가 표현 불가능해야 한다.
    query는 **등재 항목 이름**만 받고, 어떤 HTTP 메서드로 나갈지는 어댑터가 그
    항목의 선언을 보고 정한다.
    """

    @abstractmethod
    async def get(self, endpoint: str) -> ProbeResult: ...           # 토폴로지 등록 끝점만, GET 전용

    @abstractmethod
    async def query(self, entry: str, params: dict) -> ProbeResult:
        """등재 항목을 호출한다. GET 항목이면 params가 쿼리 문자열, POST면 body다.

        미등재 항목·스키마 밖 필드·허용되지 않은 쿼리 키는 소켓에 나가기 전에
        error ProbeResult로 거부한다.
        """
        ...
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: `StubRest`/`RealRest`가 아직 `query`를 구현하지 않아 인스턴스화가 실패한다 — **Task 5에서 함께 초록이 된다.** 이 태스크만으로 전체 스위트를 초록으로 만들려 하지 마라.

- [ ] **Step 5: 커밋하지 않는다**

Task 5와 한 커밋으로 묶는다 — 포트만 바꾸면 구현체가 추상 메서드 미구현으로 깨져 중간 상태가 성립하지 않는다.

---

## Task 5: 두 어댑터가 같은 규칙으로 query를 구현한다

**Files:**
- Modify: `src/infrastructure/rest_prober.py`, `src/infrastructure/stubs.py`, `src/infrastructure/factory.py`
- Test: `tests/infrastructure/test_stubs.py`, `tests/infrastructure/test_real_adapters_mocked.py`

**Interfaces:**
- Consumes: Task 2의 `RestEntry`, Task 3의 `entry_body_problems`, Task 4의 포트
- Produces: `RealRest(base_url, allowed, entries, auth, *, guards, semaphore, clock)`, `StubRest(responses, allowed, entries, *, clock)`

**스텁이 실구현과 같은 거부를 해야 한다.** 테스트가 전부 스텁이므로 여기서 느슨해지면 그 계약을 검증하는 테스트 자체가 무의미해진다 — 계획 7 리뷰에서 `claim`의 두 구현이 갈라진 것을 실제로 겪었다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/infrastructure/test_stubs.py` 끝에 추가:

```python
def _entries():
    from src.config.schema_site import RestEntry
    return {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                      body_schema={"part_code": "list[str]"}),
            "mes_plan": RestEntry(method="GET", path="/mes/plan",
                                  query_keys=["date"])}


async def test_스텁_query는_미등재_항목을_거부한다():
    from src.infrastructure.stubs import StubRest
    rest = StubRest({}, set(), _entries(), clock=lambda: T)
    result = await rest.query("없는항목", {})
    assert result.status == "error" and "등재" in result.error


async def test_스텁_query는_스키마_밖_필드를_거부한다():
    from src.infrastructure.stubs import StubRest
    rest = StubRest({"POST /summary/prod": {"ok": 1}}, set(), _entries(), clock=lambda: T)
    result = await rest.query("summary_prod", {"part_code": ["P001"], "save_as": "x"})
    assert result.status == "error" and "save_as" in result.error


async def test_스텁_query는_허용되지_않은_쿼리_키를_거부한다():
    from src.infrastructure.stubs import StubRest
    rest = StubRest({"GET /mes/plan": {"ok": 1}}, set(), _entries(), clock=lambda: T)
    assert (await rest.query("mes_plan", {"date": "2026-09-04"})).status == "ok"
    bad = await rest.query("mes_plan", {"line": "L1"})
    assert bad.status == "error" and "line" in bad.error


async def test_스텁_query는_등재_항목을_실제로_돌려준다():
    from src.infrastructure.stubs import StubRest
    rest = StubRest({"POST /summary/prod": {"badge": [0, 0, 0]}}, set(), _entries(),
                    clock=lambda: T)
    result = await rest.query("summary_prod", {"part_code": ["P001"]})
    assert result.status == "ok"
    assert result.data == {"status_code": 200, "body": {"badge": [0, 0, 0]}}
```

`tests/infrastructure/test_real_adapters_mocked.py`에 실구현 대조를 추가한다. **이 파일에는 아직 httpx 목킹이 없다**(직접 만든 페이크로 pymongo/aiokafka만 다룬다) — `httpx.MockTransport`는 새로 들이는 방식이지만, "라이브러리 표면만 흉내 내 어댑터가 그것을 올바르게 호출하는지 본다"는 파일의 철학과 같다. 상단 import에 `import httpx`를 추가한다:

```python
async def test_real_query는_항목의_메서드로_나가고_인증_헤더를_붙인다():
    # 메서드가 호출자 인자가 아니라 등재 항목에서 온다는 것을 실제 요청으로 확인한다.
    import httpx
    from pydantic import SecretStr

    from src.config.schema_site import Guards, RestAuth, RestEntry
    from src.infrastructure.rest_prober import RealRest
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["ticket"] = request.headers.get("x-dep-ticket")
        seen["body"] = request.content
        return httpx.Response(200, json={"badge": [0, 0, 0]})

    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"part_code": "list[str]"})}
    rest = RealRest("http://x", set(), entries,
                    RestAuth(header="x-dep-ticket", value=SecretStr("t0ken")),
                    guards=Guards(), semaphore=asyncio.Semaphore(1), clock=lambda: T)
    rest._client = httpx.AsyncClient(base_url="http://x",
                                     transport=httpx.MockTransport(handler))
    result = await rest.query("summary_prod", {"part_code": ["P001"]})
    assert result.status == "ok"
    assert seen["method"] == "POST" and seen["url"] == "http://x/summary/prod"
    assert seen["ticket"] == "t0ken"
    assert b"P001" in seen["body"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/infrastructure/ -q -k "query"`
Expected: FAIL — `TypeError: Can't instantiate abstract class StubRest with abstract method query`

- [ ] **Step 3: 최소 구현**

`src/infrastructure/rest_prober.py`:

```python
class RealRest(RestProberPort):
    def __init__(self, base_url, allowed, entries=None, auth=None, *,
                 guards, semaphore, clock):
        headers = {auth.header: auth.value.get_secret_value()} if auth is not None else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers)
        self._allowed = allowed
        self._entries = entries or {}
        self._guards, self._sem, self._clock = guards, semaphore, clock

    def _reject(self, message: str) -> ProbeResult:
        return ProbeResult(status="error", envelope=Envelope(observed_at=self._clock()),
                           error=message)

    async def query(self, entry, params):
        entry_spec = self._entries.get(entry)
        if entry_spec is None:
            return self._reject(f"항목 {entry!r}는 등재돼 있지 않다")
        if entry_spec.method == "GET":
            keys = set(params)
            unknown = keys - set(entry_spec.query_keys)
            if unknown:
                return self._reject(f"허용되지 않은 쿼리 키: {sorted(unknown)}")
        else:
            problems = entry_body_problems(params, entry_spec.body_schema)
            if problems:
                return self._reject("; ".join(problems))

        async def op():
            # 메서드는 항목이 정한다 — 호출자가 넘긴 값이 아니다.
            if entry_spec.method == "GET":
                response = await self._client.get(entry_spec.path, params=params)
            else:
                response = await self._client.post(entry_spec.path, json=params)
            try:
                body = response.json()
            except ValueError:
                body = response.text
            return {"status_code": response.status_code, "body": body}, \
                Envelope(observed_at=self._clock())
        return await self._call(op)
```

import에 `from src.infrastructure.query_rules import endpoint_allowed, entry_body_problems`.

`src/infrastructure/stubs.py`의 `StubRest`:

```python
class StubRest(RestProberPort):
    def __init__(self, responses, allowed, entries=None, *, clock):
        self._responses, self._allowed = responses, allowed
        self._entries = entries or {}
        self._clock = clock

    async def query(self, entry, params):
        # RealRest와 **같은 거부 규칙**을 쓴다. 테스트가 전부 스텁이므로 여기서
        # 느슨해지면 그 계약을 검증하는 테스트 자체가 무의미해진다.
        entry_spec = self._entries.get(entry)
        if entry_spec is None:
            return _err(f"항목 {entry!r}는 등재돼 있지 않다", self._clock)
        if entry_spec.method == "GET":
            unknown = set(params) - set(entry_spec.query_keys)
            if unknown:
                return _err(f"허용되지 않은 쿼리 키: {sorted(unknown)}", self._clock)
        else:
            problems = entry_body_problems(params, entry_spec.body_schema)
            if problems:
                return _err("; ".join(problems), self._clock)
        key = f"{entry_spec.method} {entry_spec.path}"
        if key not in self._responses:
            return _err("404: 스텁에 등록되지 않은 항목", self._clock)
        return _ok({"status_code": 200, "body": self._responses[key]},
                   Envelope(observed_at=self._clock()))
```

`src/infrastructure/factory.py`의 두 조립 지점에 `cfg.target.rest.entries`와 `cfg.target.rest.auth`를 넘긴다(`grep -n "StubRest\|RealRest" src/infrastructure/factory.py`로 찾는다). 스텁 응답은 기존 `StubSeeds.rest_responses`를 그대로 쓴다 — 등재 항목은 `"POST /summary/prod"`처럼 `"{method} {path}"` 키로 넣는다. 새 필드를 만들지 않는 이유: GET 경로 키(`"/oee"`)와 항목 키는 형태가 달라 충돌하지 않고, 시드가 한 자리에 모여 있는 편이 읽기 쉽다.

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Let the adapter choose the method from the registered entry

포트에 query(entry, params)만 열고 post()는 만들지 않는다. 호출자는 항목 이름만
대고, 어떤 HTTP 메서드로 나갈지는 어댑터가 그 항목의 선언을 보고 정한다 —
"임의의 POST를 수행하라"가 코드에 표현될 수 없어야 v1의 성질이 유지된다.

포트 표면을 테스트가 지킨다. 6개월 뒤 누군가 편의상 post()를 추가하면 등재제를
우회할 수 있고, 산문 규율은 읽지 않으면 무력하다.

스텁이 실구현과 같은 거부 규칙을 쓰는 것이 중요하다. 테스트가 전부 스텁이므로
여기서 느슨해지면 그 계약을 검증하는 테스트 자체가 무의미해진다 — 계획 7에서
claim의 두 구현이 갈라진 것을 실제로 겪었다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: rest_query 프로브

**Files:**
- Modify: `src/patrol/probes.py`
- Test: `tests/patrol/test_probes.py`

**Interfaces:**
- Produces: `rest_query(adapters, check, *, clock)`, `PROBES`에 `"rest_query"` 등록, `resolve_probe`가 `rest:<항목명>`을 `rest_query`로 라우팅

**target 규약**: `rest:` 뒤가 `/`로 시작하면 경로(기존 `rest_get`), 아니면 등재 항목 이름(`rest_query`). 새 구분자를 만들지 않아 `_split_target`이 그대로 동작한다.

점검이 보낼 params는 이 계획에서는 `check.params["body"]`(정적 dict)로 둔다 — **값을 어디서 해석할지는 계획 9의 몫**이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/patrol/test_probes.py` 상단에 `import asyncio`를 추가하고(현재 없다) 파일 끝에 붙인다:

```python
def test_경로가_아닌_target은_등재_항목_프로브로_간다():
    from src.config.schema_site import CheckConfig
    from src.patrol.probes import resolve_probe
    def _check(target):
        return CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                           "target": target, "params": {"rule": "exists"}})
    assert resolve_probe(_check("rest:/api/v1/oee")) == "rest_get"
    assert resolve_probe(_check("rest:summary_prod")) == "rest_query"


async def test_rest_query는_check의_body를_그대로_넘긴다():
    from src.config.schema_site import CheckConfig, RestEntry
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubRest
    from src.patrol.probes import rest_query
    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"part_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /summary/prod": {"badge": [0, 0, 0]}}, set(), entries,
                             clock=lambda: T)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge", "body": {"part_code": ["P001"]}}})
    result = await rest_query(adapters, check, clock=lambda: T)
    assert result.status == "ok" and result.data["body"] == {"badge": [0, 0, 0]}


async def test_rest_query는_어댑터가_없어도_raise하지_않는다():
    from src.config.schema_site import CheckConfig
    from src.infrastructure.factory import AdapterSet
    from src.patrol.probes import rest_query
    check = CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                        "target": "rest:summary_prod",
                                        "params": {"rule": "exists"}})
    result = await rest_query(AdapterSet(semaphore=asyncio.Semaphore(1)), check,
                              clock=lambda: T)
    assert result.status == "error" and "rest" in result.error
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/patrol/test_probes.py -q -k "등재_항목_프로브 or rest_query"`
Expected: FAIL — `ImportError: cannot import name 'rest_query'`

- [ ] **Step 3: 최소 구현**

`src/patrol/probes.py`에 추가:

```python
async def rest_query(adapters: AdapterSet, check: CheckConfig, *, clock) -> ProbeResult:
    """target "rest:<항목명>" → adapters.rest.query(항목명, params).

    보낼 params는 지금은 check.params["body"](정적 dict)다 — 값을 살아 있는
    소스에서 해석하는 것은 계획 9의 몫이고, 그때 이 자리가 해석기 호출로 바뀐다.
    """
    try:
        if adapters.rest is None:
            return _error("어댑터 미설정: rest", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "rest":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, entry = parts
        body = check.params.get("body", {})
        if not isinstance(body, dict):
            return _error(f"params.body는 dict여야 한다 (받은 타입: {type(body).__name__})",
                          clock)
        return await adapters.rest.query(entry, body)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)
```

`resolve_probe`를 고친다:

```python
def resolve_probe(check: CheckConfig) -> str | None:
    """check.probe가 있으면 그것, 없으면 target 접두사로 기본 프로브를 고른다.

    rest는 접두사만으로 갈리지 않는다: `rest:/path`는 토폴로지 등록 끝점의 GET,
    `rest:<이름>`은 등재 항목이다. 새 구분자를 만들지 않아 _split_target이 그대로
    동작한다.
    """
    if check.probe is not None:
        return check.probe
    parts = _split_target(check.target)
    if parts is None:
        return None
    kind, rest = parts
    if kind == "rest":
        return "rest_get" if rest.startswith("/") else "rest_query"
    return _TARGET_PREFIX_TO_PROBE.get(kind)
```

`PROBES` 레지스트리에 `"rest_query": rest_query`를 더한다(`grep -n "^PROBES" -A8 src/patrol/probes.py`로 위치 확인).

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Route entry-name targets to the registered-entry probe

target의 rest: 뒤가 /로 시작하면 토폴로지 등록 끝점의 GET, 아니면 등재 항목이다.
새 구분자를 만들지 않아 _split_target이 그대로 동작하고, 기존 rest:/path 점검은
한 글자도 안 바뀐다.

보낼 params를 지금은 check.params["body"] 정적 dict로 둔다 — 값을 살아 있는
소스에서 해석하는 것은 계획 9의 몫이고, 그때 이 자리가 해석기 호출로 바뀐다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 기동 검증 — 점검이 참조하는 항목이 실재하는가

**Files:**
- Modify: `src/boot.py`
- Test: `tests/test_boot.py`

**Interfaces:**
- Consumes: Task 2의 `RestTarget.entries`, Task 6의 `resolve_probe`

지금 boot 검사 6은 `check.target not in known`(토폴로지 locator)만 본다. 등재 항목 target은 토폴로지에 없으므로 **전부 거부당한다.** 항목 이름은 별도로 대조해야 한다.

**기동 거부 철학**: 발견 즉시 죽지 않고 전부 모아서 보고한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_boot.py` 끝에 추가(파일의 기존 트리 픽스처 `_tree`/`_write`를 그대로 쓴다):

```python
def test_등재_항목_target은_토폴로지가_아니라_entries로_해석된다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {
            "base_url": "http://x",
            "entries": {"summary_prod": {"method": "POST", "path": "/summary/prod",
                                         "body_schema": {"part_code": "list[str]"}}}}},
        "patrol": {"checks": {"prod.badge": {
            "judge": "rule", "schedule": {"interval": "5m"},
            "target": "rest:summary_prod",
            "params": {"rule": "exists", "field": "body.badge"}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert not [e for e in errors if "summary_prod" in e.problem]


def test_미등재_항목을_참조하는_점검은_기동을_거부한다(tmp_path):
    # 오타나 삭제된 항목을 참조하면 매 순찰이 error를 내고 끝난다 — 밤에 조용히
    # 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {"base_url": "http://x", "entries": {}}},
        "patrol": {"checks": {"prod.badge": {
            "judge": "rule", "schedule": {"interval": "5m"},
            "target": "rest:summary_prod",
            "params": {"rule": "exists", "field": "body.badge"}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("summary_prod" in e.problem for e in errors)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_boot.py -q -k "등재_항목_target or 미등재_항목"`
Expected: 첫 테스트 FAIL — 등재 항목 target이 "토폴로지로 해석되지 않는다"로 거부된다

- [ ] **Step 3: 최소 구현**

`src/boot.py`의 **기존 루프 안**(`for name, check in cfg.patrol.checks.items():`, 현재 94행 부근)에서 target 조건만 교체한다 — 루프를 새로 만들지 마라. 같은 루프의 `judge` 검사는 그대로 둔다. 루프 **앞에** `entries`를 한 번 계산한다:

```python
        known = topo.locators()
        entries = set(cfg.target.rest.entries) if cfg.target.rest else set()
        for name, check in cfg.patrol.checks.items():
            # rest:<이름>은 토폴로지가 아니라 등재 항목에서 해석된다 — 두 이름공간을
            # 섞어 보면 정상 설정이 거부당한다.
            if check.target is not None:
                kind, _, rest = check.target.partition(":")
                if kind == "rest" and not rest.startswith("/"):
                    if rest not in entries:
                        errors.append(BootError(
                            where, f"점검 {name!r}의 target {check.target!r}이 "
                                   f"target.rest.entries에 등재돼 있지 않다"))
                elif check.target not in known:
                    errors.append(BootError(
                        where, f"점검 {name!r}의 target {check.target!r}이 "
                               f"토폴로지로 해석되지 않는다"))
            if check.judge in ("llm", "rule+llm"):
                needs_judge_llm = True
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Resolve entry targets against the registry, not the topology

boot 검사 6이 모든 target을 토폴로지 locator로만 해석해서, 등재 항목을 가리키는
정상 설정이 전부 거부당했다. 두 이름공간을 갈라 각자의 출처에서 확인한다.

미등재 항목 참조를 기동 거부로 올린 이유는 기동 거부 철학 그대로다 — 오타나
삭제된 항목을 참조하면 매 순찰이 error를 내고 끝나는데, 밤에 조용히 틀리는 것보다
배포 시점에 시끄럽게 죽는 게 낫다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 증거는 무엇을 물었는지 실어야 한다

**Files:**
- Modify: `src/infrastructure/query_rules.py`, `src/patrol/runner.py`
- Test: `tests/infrastructure/test_query_rules.py`, `tests/patrol/test_runner.py`

**Interfaces:**
- Produces: 증거 `source`가 `rest:POST:/summary/prod#<body digest 8자>` 형태

스펙 §2-N4다. `0/0/0`이라는 응답만 보관하면 보고서를 읽는 사람이 그것이 "P001이 멈췄다"인지 "질문을 잘못했다"인지 **구별할 수 없다.** GET 프로브는 URL이 곧 질문이라 이 문제가 없었다 — POST를 열면서 처음 생기는 요구다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

증거 source를 만드는 자리는 `src/patrol/runner.py:51`의 `source=check.target or name`이다 — 등재 항목 target(`rest:summary_prod`)은 보낸 body를 식별하지 못한다. 먼저 순수 함수를 만든다:

```python
def test_등재_항목_증거의_출처는_보낸_body를_식별한다():
    # 같은 끝점에 다른 필터를 보낸 두 증거가 §4 표에서 구별돼야 한다.
    # 구별되지 않으면 0/0/0이 "멈췄다"인지 "잘못 물었다"인지 알 수 없다.
    from src.infrastructure.query_rules import entry_evidence_source
    a = entry_evidence_source("POST", "/summary/prod", {"part_code": ["P001"]})
    b = entry_evidence_source("POST", "/summary/prod", {"part_code": ["P002"]})
    assert a.startswith("rest:POST:/summary/prod#") and a != b
    # 키 순서가 달라도 같은 질문이면 같은 출처여야 한다(canonical_digest 규약)
    c = entry_evidence_source("POST", "/summary/prod",
                              {"part_code": ["P001"], "line_code": None})
    d = entry_evidence_source("POST", "/summary/prod",
                              {"line_code": None, "part_code": ["P001"]})
    assert c == d
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest -q -k "등재_항목_증거"`
Expected: FAIL — `ImportError: cannot import name 'entry_evidence_source'`

- [ ] **Step 3: 최소 구현**

`src/infrastructure/query_rules.py`에 추가:

```python
def entry_evidence_source(method: str, path: str, params: dict) -> str:
    """등재 항목 호출의 증거 출처 문자열.

    body digest를 붙이는 이유(스펙 §2-N4): 응답만 보관하면 "0/0/0"이 "현장이
    멈췄다"인지 "질문을 잘못했다"인지 구별할 수 없다. GET은 URL이 곧 질문이라
    이 문제가 없었고, POST를 열면서 처음 생기는 요구다.

    canonical_digest를 쓰므로 키 순서가 달라도 같은 질문이면 같은 출처가 된다.
    """
    return f"rest:{method}:{path}#{canonical_digest(params)[:8]}"
```

import에 `from src.knowledge.digest import canonical_digest`.

`src/patrol/runner.py`의 `store.put_evidence(...)` 호출이 등재 항목 점검일 때 이 함수로 source를 만들게 한다. `check.params.get("body")`가 보낸 것이므로 runner가 그 값을 안다. 응답만이 아니라 요청도 본문에 남긴다:

```python
        entry_body = check.params.get("body") if isinstance(check.params, dict) else None
        if entry_body is not None and check.target and not check.target.startswith("rest:/"):
            entry_spec = ...  # 어댑터가 아는 값이므로 runner는 target에서 method/path를 알 수 없다
```

**주의**: runner는 등재 항목의 `method`/`path`를 모른다(어댑터만 안다). 그러므로 **어댑터가 `ProbeResult.envelope`이 아니라 `data`에 자기가 무엇을 물었는지 실어 보내야 한다** — `RealRest.query`/`StubRest.query`의 반환 `data`를 `{"status_code", "body", "request": {"method", "path", "params"}}`로 넓히고, runner는 그것을 읽어 source를 만든다. 두 어댑터를 같이 고쳐야 계약이 갈라지지 않는다(Task 5의 반환 형태도 함께 갱신하라).

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Record what we asked, not just what came back

응답만 보관하면 "0/0/0"이 "현장이 멈췄다"인지 "질문을 잘못했다"인지 보고서를
읽는 사람이 구별할 수 없다. GET은 URL이 곧 질문이라 이 문제가 없었고, POST를
열면서 처음 생기는 요구다(스펙 §2-N4).

canonical_digest를 쓰므로 키 순서가 달라도 같은 질문이면 같은 출처가 된다 —
증거가 질문 단위로 모이고 흩어지지 않는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 예시 config와 문서

**Files:**
- Modify: `config.example/gbm/mx.json`, `docs/config-reference.md`, `docs/architecture.md`, `CLAUDE.md`
- Test: 없음(예시 config는 `test_boot`가 이미 로드한다)

문서가 코드보다 뒤처지는 것이 이 리포에서 반복된 실패 양식이다. **같은 계획 안에서 갚는다.**

- [ ] **Step 1: 예시 config에 등재 항목을 넣는다**

`config.example/gbm/mx.json`의 `target.rest`에 추가:

```json
"rest": {
  "base_url": "${MX_GUMI_API_BASE}",
  "entries": {
    "summary_prod": {
      "method": "POST",
      "path": "/summary/prod",
      "body_schema": {"part_code": "list[str]", "line_code": "str"}
    }
  }
}
```

- [ ] **Step 2: 예시가 실제로 기동 검증을 통과하는지 확인한다**

```bash
MX_GUMI_API_BASE=http://x MX_GUMI_MONGO_URL=mongodb://x MX_GUMI_REDIS_URL=redis://x \
  .venv/bin/python -m src knowledge validate --config-root config.example --repo-root .
```
Expected: exit 0

- [ ] **Step 3: 문서를 갱신한다**

- `docs/config-reference.md`: `target.rest.auth.header`/`auth.value`/`entries.*.method`/`path`/`body_schema`/`query_keys` 행 추가
- `docs/architecture.md`: "대상 시스템 포트" 설명에 **왜 `post()`가 없는지**와 등재제를 한 문단으로 추가
- `CLAUDE.md` 코드 지도에 `등재 항목 판정(순수 함수) | src/infrastructure/query_rules.py` 행이 이미 있는지 확인하고 없으면 추가
- `CLAUDE.md` 절대 규율에 **읽기 전용 강제**를 명문화한다:

```markdown
### 9. 대상 시스템 읽기 전용은 등재제로 강제한다

`RestProberPort`에 `post`/`put`/`patch`/`delete`를 **만들지 않는다**. v1에서는
`get` 하나뿐이라 쓰기가 물리적으로 불가능했고, POST가 필요해진 뒤에도 그 성질을
잃지 않으려면 "임의의 메서드로 임의의 경로를 호출하라"가 표현 불가능해야 한다.

호출자는 `query(entry, params)`로 **등재 항목 이름**만 대고, 어떤 HTTP 메서드로
나갈지는 어댑터가 그 항목의 선언(`target.rest.entries`)을 보고 정한다. body는
항목의 닫힌 스키마를 통과해야 소켓에 나간다 — 메서드 수준에서 잃은 메커니즘의
정직한 대체물이 body 수준의 닫힌 스키마다.

**config가 권한이고, 대상의 자기 서술(OpenAPI 등)은 증거일 뿐이다.** 런타임에
명세를 읽어 등재 목록을 넓히는 코드를 만들지 마라 — 대상이 새 POST를 배포하면
우리 허용 범위가 자동으로 넓어지는 fail-open이 된다.

`tests/domain/test_ports.py`가 포트 표면을 단정한다. 산문 규율은 읽지 않으면
무력하므로 테스트가 지킨다.
```

- [ ] **Step 4: 통과를 확인한다**

```bash
rm -rf output/ && .venv/bin/python -m pytest tests/ -q
```

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Write down why the port has no post()

문서가 코드보다 뒤처지는 것이 이 리포에서 반복된 실패 양식이라 같은 계획 안에서
갚는다. 등재제는 새 기능이 아니라 v1의 절대 규율("대상 시스템에 완전 읽기 전용")을
POST 시대에도 유지하는 방법이므로 CLAUDE.md의 절대 규율에 들어가야 한다.

예시 config에 실제로 도는 등재 항목을 넣어, README의 빠른 시작이 새 기능까지
포함해 그대로 복사-실행되게 했다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 완료 기준

- [ ] `rm -rf output/ && .venv/bin/python -m pytest tests/ -q` 전건 통과, 기준선(327)보다 증가
- [ ] `grep -rn "def post\|def put\|def patch\|def delete" src/domain/ports.py src/infrastructure/rest_prober.py src/infrastructure/stubs.py` → 0건
- [ ] `StubRest`와 `RealRest`가 같은 입력에 같은 거부 사유를 돌려준다(직접 실행해 대조)
- [ ] `config.example`로 `knowledge validate`·`patrol run --for-seconds 0`이 exit 0
- [ ] `docs/config-reference.md`에 새 키가 전부 있다

## 이 계획이 **하지 않는** 것

| 미포함 | 어디로 |
|---|---|
| 파라미터 **값**의 해석기(`{"from": "mongo", ...}`) | 계획 9. 이 계획은 칼럼(스키마)까지만 |
| 전부-또는-전무 규율(해석 실패 시 호출 안 함) | 계획 9. 해석기가 없으면 규율의 대상이 없다 |
| OpenAPI pin·드리프트 점검·`target_api` digest | 계획 9 |
| 응답 필드 경로(`field: "body.oee"`) 검증 | 계획 9. 응답 스키마가 있어야 검증할 수 있다 |
| 읽기 전용 스코프 토큰 | 대상 시스템에 존재하지 않는다(확인됨). `auth`는 MES 접근용이지 권한 축소용이 아니다 |
| `concern` 축·rule 확장(`all_zero`·`expected_state`) | 계획 10(S0의 P5) |
