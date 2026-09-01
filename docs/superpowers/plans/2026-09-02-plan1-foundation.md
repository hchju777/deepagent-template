# 계획 1: 기반 층 (config·지식·기동 검증) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스펙 §4.5~4.6·§3.1·§3.3의 기반 층 — 전역/사이트 config, deep-merge, env 해석, 토폴로지·deployment 지식 로더, 기동 검증, CLI 3종(`registry`, `config show`, `knowledge validate`) — 을 구현한다.

**Architecture:** 4계층 중 domain(스키마)과 config/knowledge 로딩, 그리고 얇은 CLI만 다룬다. 어댑터·그래프·순찰은 계획 2~5. 모든 로딩은 "merge → env 해석 → pydantic 강타입 검증(unknown key 거부)" 순서이고, 실패는 전부 기동 시점에 모아서 시끄럽게 보고한다.

**Tech Stack:** Python 3.11+, pydantic v2, PyYAML, python-dotenv, pytest. (LangGraph는 계획 3부터.)

**전체 로드맵**: 이 문서는 5개 순차 계획 중 1번이다. 2 어댑터 층 → 3 조사 엔진 → 4 순찰·큐 → 5 보고·채널·E2E. 각 계획은 단독으로 동작하는 소프트웨어를 낸다.

## Global Constraints (스펙에서 발췌 — 모든 태스크에 적용)

- **기동 거부 철학**: 검증 실패는 밤에 조용히 틀리는 대신 기동 시점에 시끄럽게 죽는다. 오류는 **첫 건에서 멈추지 않고 전부 모아** 보고한다.
- **unknown key 거부**: 모든 config·지식 pydantic 모델은 `extra="forbid"`.
- **비밀값은 `.env`에만**: config 파일에는 `${ENV_KEY}` 참조만. URL에 비밀번호 금지. 비밀 필드는 `SecretStr`로 표시 시 마스킹.
- **null 삭제 마커**: deep-merge에서 override 값 `null`은 해당 키 삭제를 뜻한다.
- **schedule**: 점검당 `interval` xor `cron` — 둘 다 또는 둘 다 없음은 검증 실패.
- **전역/사이트 스코프 분리**: 전역 키(`engine`, `investigations`, `llm`, `patrol.llm_budget`, `store`, `timezone`)가 사이트 계층에 나타나면 거부 (SiteConfig의 `extra="forbid"`가 자동으로 수행).
- **derivations는 map**: output locator 문자열을 키로 한다. 리스트 금지.
- **코드 주석·문서·오류 메시지는 한국어** (전작 컨벤션). 단, 라이브러리가 생성하는 오류 원문(pydantic 등)을 한국어 틀 안에 인용하는 것은 허용 — 번역 매핑은 유지비만 들고 정확성을 잃는다.
- 스펙 §4.6의 기동 검증 중 **7(deployment hash 실재 — git 필요)과 8(Mongo readonly 롤 — 어댑터 필요)은 계획 2로 이월**. 이 계획은 1~6을 구현한다.

## File Structure

```
requirements.txt / requirements-dev.txt / pytest.ini / .env.example
src/
├── __init__.py
├── __main__.py               # CLI 엔트리 (argparse, main(argv) -> int)
├── config/
│   ├── __init__.py
│   ├── merge.py              # deep_merge + provenance(출처 추적)
│   ├── envresolve.py         # ${KEY} 해석, 누락 수집
│   ├── schema_app.py         # AppConfig (전역)
│   ├── schema_site.py        # SiteConfig (사이트 계층)
│   └── loader.py             # 파일 배치 규약, load_app/registry/site
├── knowledge/
│   ├── __init__.py
│   ├── digest.py             # canonical_digest
│   ├── topology.py           # Topology 스키마·로드·정합성
│   └── deployment.py         # Deployment 스키마·로드
└── boot.py                   # validate_boot — §4.6 오케스트레이션
tests/
├── config/test_merge.py, test_envresolve.py, test_schema_app.py,
│          test_schema_site.py, test_loader.py
├── knowledge/test_topology.py, test_deployment.py
├── test_boot.py
└── test_cli.py
```

---

### Task 1: 스캐폴딩 + deep_merge(출처 추적·null 삭제)

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `src/__init__.py`, `src/config/__init__.py`, `src/config/merge.py`, `tests/__init__.py`, `tests/config/__init__.py`, `tests/knowledge/__init__.py`
- Test: `tests/config/test_merge.py`

**Interfaces:**
- Produces: `deep_merge(base: dict, override: dict, *, source: str, provenance: dict[str, str], prefix: str = "") -> dict` — base를 바꾸지 않는 새 dict 반환. `provenance`는 dotted path → source 라벨로 **제자리 갱신**. override 값 `None`은 키 삭제(하위 provenance도 제거).
- Produces: `record_provenance(data: dict, *, source: str, provenance: dict[str, str], prefix: str = "") -> None` — 베이스 레이어의 출처 시딩.

- [ ] **Step 1: 스캐폴딩 파일 작성**

`requirements.txt`:
```
# 계획 1 런타임 의존성. 계획 2~5에서 추가된다.
pydantic>=2.13,<3
PyYAML>=6.0,<7
python-dotenv>=1.0,<2
tzdata>=2024.1        # Windows에서 zoneinfo가 tz 데이터베이스를 찾도록
```

`requirements-dev.txt`:
```
pytest>=8.0,<9
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
```

`src/__init__.py`, `src/config/__init__.py`: 빈 파일.

`tests/__init__.py`, `tests/config/__init__.py`, `tests/knowledge/__init__.py`: 빈 파일 — tests를 패키지로 만들어 테스트 간 픽스처 import(`from tests.test_boot import ...`)가 동작하게 한다.

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/config/test_merge.py`

```python
from src.config.merge import deep_merge, record_provenance


def test_중첩_dict는_재귀_병합되고_스칼라는_덮어쓴다():
    base = {"target": {"redis": {"url": "a"}, "guards": {"timeout_s": 10}}}
    prov: dict[str, str] = {}
    record_provenance(base, source="gbm/mx", provenance=prov)
    merged = deep_merge(
        base, {"target": {"redis": {"url": "b"}}},
        source="factories/gumi/mx", provenance=prov,
    )
    assert merged["target"]["redis"]["url"] == "b"
    assert merged["target"]["guards"]["timeout_s"] == 10      # 보존
    assert prov["target.redis.url"] == "factories/gumi/mx"
    assert prov["target.guards.timeout_s"] == "gbm/mx"
    assert base["target"]["redis"]["url"] == "a"              # base 불변


def test_null은_키를_삭제하고_하위_출처도_지운다():
    base = {"patrol": {"checks": {"kafka.lag": {"judge": "rule"}}}}
    prov: dict[str, str] = {}
    record_provenance(base, source="gbm/mx", provenance=prov)
    merged = deep_merge(
        base, {"patrol": {"checks": {"kafka.lag": None}}},
        source="factories/gumi/mx", provenance=prov,
    )
    assert "kafka.lag" not in merged["patrol"]["checks"]
    assert not any(p.startswith("patrol.checks.kafka.lag") for p in prov)
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/config/test_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: src.config.merge`

- [ ] **Step 4: 구현** — `src/config/merge.py`

```python
"""config 레이어 deep-merge와 출처(provenance) 추적.

merge 규칙 (스펙 §4.5):
- dict끼리는 재귀 병합, 그 외는 override가 덮어쓴다.
- override 값 None은 "이 키를 삭제하라"는 마커다 (사이트별 점검 끄기 등).
"""


def record_provenance(data, *, source, provenance, prefix=""):
    """베이스 레이어 전체를 출처 dict에 시딩한다 (leaf 경로만)."""
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            record_provenance(value, source=source, provenance=provenance, prefix=path)
        else:
            provenance[path] = source


def _drop_subtree(provenance, path):
    for p in [p for p in provenance if p == path or p.startswith(path + ".")]:
        del provenance[p]


def deep_merge(base, override, *, source, provenance, prefix=""):
    out = dict(base)
    for key, value in override.items():
        path = f"{prefix}.{key}" if prefix else key
        if value is None:                       # null 마커: 삭제
            out.pop(key, None)
            _drop_subtree(provenance, path)
        elif isinstance(value, dict):
            # 기존 dict가 있든 없든 재귀 병합 (null 마커를 중첩에서도 처리)
            out[key] = deep_merge(out.get(key, {}), value, source=source,
                                  provenance=provenance, prefix=path)
        else:
            out[key] = value
            _drop_subtree(provenance, path)
            provenance[path] = source
    return out
```

- [ ] **Step 5: 통과 확인 후 커밋**

Run: `pytest tests/config/test_merge.py -v` → PASS

```bash
git add requirements.txt requirements-dev.txt pytest.ini src tests
git commit -m "Add deep-merge with provenance and null deletion marker"
```

---

### Task 2: env 해석기 + `.env.example`

**Files:**
- Create: `src/config/envresolve.py`, `.env.example`
- Test: `tests/config/test_envresolve.py`

**Interfaces:**
- Produces: `resolve_env_refs(data: object, *, env: Mapping[str, str]) -> tuple[object, list[str]]` — `"${KEY}"` 꼴 문자열(전체 일치만)을 env 값으로 치환한 사본과, **부재하거나 빈 값인** 키 이름 목록을 반환. 치환은 dict/list 재귀.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/config/test_envresolve.py`

```python
from src.config.envresolve import resolve_env_refs


def test_전체일치_참조만_치환하고_부분_문자열은_그대로():
    data = {"redis": {"url": "${MX_GUMI_REDIS_URL}", "note": "url is ${NOT_A_REF} ok"}}
    resolved, missing = resolve_env_refs(data, env={"MX_GUMI_REDIS_URL": "redis://g:6379"})
    assert resolved["redis"]["url"] == "redis://g:6379"
    assert resolved["redis"]["note"] == "url is ${NOT_A_REF} ok"   # 보간 미지원(의도)
    assert missing == []


def test_부재_또는_빈값_키는_missing에_모인다():
    data = {"mongo": {"url": "${A_URL}", "password": "${A_PW}"}}
    resolved, missing = resolve_env_refs(data, env={"A_URL": ""})
    assert sorted(missing) == ["A_PW", "A_URL"]
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/config/test_envresolve.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/config/envresolve.py`

```python
"""config 안의 ${ENV_KEY} 참조 해석.

- 문자열 전체가 참조일 때만 치환한다. 보간("a${X}b")은 지원하지 않는다 —
  비밀값이 더 큰 문자열에 섞여 로그로 새는 것을 구조적으로 막는다.
- 부재/빈 값은 모아서 반환한다. 호출자(기동 검증)가 전부 나열해 거부한다.
"""
import re

_ENV_REF = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


def resolve_env_refs(data, *, env):
    missing: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            m = _ENV_REF.match(node)
            if m:
                key = m.group(1)
                value = env.get(key, "")
                if value == "":
                    missing.append(key)
                    return node
                return value
        return node

    return walk(data), missing
```

- [ ] **Step 4: `.env.example` 작성**

```bash
# 실제 값은 .env에 (gitignore됨). 이 파일은 키 목록의 문서다.
# config가 참조하는 키가 비어 있거나 없으면 기동이 거부된다.

# ─── 에이전트 자신의 저장소 (계획 2부터 사용) ─────────────────
AGENT_MONGO_URL=mongodb://localhost:27017

# ─── LLM 게이트웨이 (계획 3부터 사용) ─────────────────────────
LLM_BASE_URL=https://gateway.example.com/v1
LLM_API_KEY=sk-example

# ─── 대상 시스템: mx/gumi (인증 없는 법인 — URL만) ─────────────
MX_GUMI_REDIS_URL=redis://gumi-redis:6379/0
MX_GUMI_MONGO_URL=mongodb://gumi-mongo:27017
MX_GUMI_KAFKA_BOOTSTRAP=gumi-kafka:9092
MX_GUMI_API_BASE=http://gumi-twin-api:8080

# ─── 대상 시스템: mx/suwon (인증 있는 법인 — 계정 키 추가) ──────
# MX_SUWON_REDIS_URL=redis://suwon-redis:6379/0
# MX_SUWON_REDIS_PASSWORD=change-me
# MX_SUWON_MONGO_URL=mongodb://suwon-mongo:27017
# MX_SUWON_MONGO_USER=twin_reader          # 읽기 전용 계정 권장
# MX_SUWON_MONGO_PASSWORD=change-me
```

- [ ] **Step 5: 통과 확인 후 커밋**

Run: `pytest tests/config/test_envresolve.py -v` → PASS

```bash
git add src/config/envresolve.py tests/config/test_envresolve.py .env.example
git commit -m "Resolve whole-string env refs, collecting missing keys"
```

---

### Task 3: AppConfig — 전역 config 스키마

**Files:**
- Create: `src/config/schema_app.py`
- Test: `tests/config/test_schema_app.py`

**Interfaces:**
- Produces: `StrictModel` (pydantic BaseModel, `extra="forbid"` — 이후 모든 스키마의 베이스), `AppConfig` — 필드: `engine: EngineConfig`, `investigations: InvestigationsConfig`, `llm: LlmConfig`(필수), `patrol: AppPatrol`, `store: StoreConfig`, `timezone: str`.
- `EngineConfig`: `max_rounds:int=6`, `parallel_width:int=3`, `subagent_budgets: SubagentBudgets(data_prober=8, code_tracer=6, recompute_verifier=4)`, `autonomous_question_policy: Literal["default_and_log","park"]="default_and_log"`.
- `InvestigationsConfig`: `max_concurrent:int=2`, `awaiting_human_timeout_h:int=72`.
- `LlmConfig`: `profiles: LlmProfiles(judge:str, subagent:str, lead:str)` — 기본값 없음(모델명은 넘겨받아야 함).
- `AppPatrol`: `llm_budget: PatrolBudget(max_calls_per_hour:int=30)`.
- `StoreConfig`: `retention: RetentionConfig(closed_case_evidence_d:int=90, ledger_d:int=30, checkpoint_ttl_d:int=14)`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/config/test_schema_app.py`

```python
import pytest
from pydantic import ValidationError
from src.config.schema_app import AppConfig

MINIMAL = {"llm": {"profiles": {"judge": "m-s", "subagent": "m-m", "lead": "m-l"}}}


def test_최소_config로_기본값이_채워진다():
    cfg = AppConfig.model_validate(MINIMAL)
    assert cfg.engine.max_rounds == 6
    assert cfg.investigations.max_concurrent == 2
    assert cfg.patrol.llm_budget.max_calls_per_hour == 30
    assert cfg.store.retention.ledger_d == 30
    assert cfg.timezone == "Asia/Seoul"


def test_unknown_key는_거부된다():
    with pytest.raises(ValidationError, match="scheduel"):
        AppConfig.model_validate({**MINIMAL, "scheduel": {}})   # 오타 키


def test_llm_프로파일은_필수다():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({})
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/config/test_schema_app.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/config/schema_app.py`

```python
"""전역(app) config 스키마 — 스펙 §4.5-①.

전역 키가 사이트 계층에 섞이면 예산·상한이 사이트 수만큼 곱해진다.
전역은 이 모델로만, 사이트는 schema_site로만 검증한다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubagentBudgets(StrictModel):
    data_prober: int = 8
    code_tracer: int = 6
    recompute_verifier: int = 4


class EngineConfig(StrictModel):
    max_rounds: int = 6
    parallel_width: int = 3
    subagent_budgets: SubagentBudgets = SubagentBudgets()
    autonomous_question_policy: Literal["default_and_log", "park"] = "default_and_log"


class InvestigationsConfig(StrictModel):
    max_concurrent: int = 2
    awaiting_human_timeout_h: int = 72


class LlmProfiles(StrictModel):
    judge: str
    subagent: str
    lead: str


class LlmConfig(StrictModel):
    profiles: LlmProfiles


class PatrolBudget(StrictModel):
    max_calls_per_hour: int = 30


class AppPatrol(StrictModel):
    llm_budget: PatrolBudget = PatrolBudget()


class RetentionConfig(StrictModel):
    closed_case_evidence_d: int = 90
    ledger_d: int = 30
    checkpoint_ttl_d: int = 14


class StoreConfig(StrictModel):
    retention: RetentionConfig = RetentionConfig()


class AppConfig(StrictModel):
    engine: EngineConfig = EngineConfig()
    investigations: InvestigationsConfig = InvestigationsConfig()
    llm: LlmConfig
    patrol: AppPatrol = AppPatrol()
    store: StoreConfig = StoreConfig()
    timezone: str = "Asia/Seoul"
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `pytest tests/config/test_schema_app.py -v` → PASS

```bash
git add src/config/schema_app.py tests/config/test_schema_app.py
git commit -m "Add strict AppConfig schema for process-global keys"
```

---

### Task 4: SiteConfig — 사이트 계층 스키마 (인증 선택, schedule xor, 점검)

**Files:**
- Create: `src/config/schema_site.py`
- Test: `tests/config/test_schema_site.py`

**Interfaces:**
- Consumes: `StrictModel` (Task 3).
- Produces: `SiteConfig` — `target: TargetConfig`, `patrol: SitePatrol`, `knowledge: KnowledgeConfig`.
- `TargetConfig`: `redis: RedisTarget|None`, `mongo: MongoTarget|None`, `kafka: KafkaTarget|None`, `rest: RestTarget|None`, `code: CodeTarget|None`, `guards: Guards`.
- `RedisTarget(url:str, password:SecretStr|None)`, `MongoTarget(url:str, username:str|None, password:SecretStr|None)`, `KafkaTarget(bootstrap:str)`, `RestTarget(base_url:str)`, `CodeTarget(repos: list[RepoRef])`, `RepoRef(name:str, path:str)`, `Guards(timeout_s:float=10, max_rows:int=1000, max_concurrent:int=4)`.
- `Schedule(interval:str|None, cron:str|None)` — 정확히 하나만. interval은 `^\d+[smh]$`, cron은 공백 분리 5필드.
- `CheckConfig(judge: Literal["rule","llm","rule+llm"], schedule: Schedule, target: str|None, params: dict = {}, sample: int|None, on_budget_exhausted: Literal["skip","escalate"]="skip")` — `target`은 토폴로지 locator(예: `"rest:/api/v1/lines/{line}/oee"`), 해석 검증은 boot(Task 8).
- `SitePatrol(checks: dict[str, CheckConfig] = {})`, `KnowledgeConfig(root:str="knowledge")`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/config/test_schema_site.py`

```python
import pytest
from pydantic import ValidationError
from src.config.schema_site import Schedule, SiteConfig


def _site(**patrol_checks):
    return {
        "target": {"redis": {"url": "redis://g:6379"},
                   "mongo": {"url": "mongodb://g:27017",
                             "username": "reader", "password": "pw"}},
        "patrol": {"checks": patrol_checks},
    }


def test_인증은_선택이고_password는_마스킹된다():
    cfg = SiteConfig.model_validate(_site())
    assert cfg.target.redis.password is None                    # 없는 법인
    dumped = cfg.model_dump(mode="json")
    assert dumped["target"]["mongo"]["password"] == "**********"  # 있는 법인, 마스킹


def test_schedule은_interval_xor_cron():
    Schedule.model_validate({"interval": "5m"})
    Schedule.model_validate({"cron": "0 8,20 * * *"})
    with pytest.raises(ValidationError):
        Schedule.model_validate({"interval": "5m", "cron": "0 8 * * *"})
    with pytest.raises(ValidationError):
        Schedule.model_validate({})
    with pytest.raises(ValidationError):
        Schedule.model_validate({"interval": "5 minutes"})      # 형식 위반


def test_전역_키가_사이트_계층에_오면_거부():
    data = _site()
    data["engine"] = {"max_rounds": 99}
    with pytest.raises(ValidationError, match="engine"):
        SiteConfig.model_validate(data)


def test_점검_정의():
    cfg = SiteConfig.model_validate(_site(**{
        "api.oee_range": {"judge": "rule", "schedule": {"interval": "10m"},
                          "target": "rest:/api/v1/lines/{line}/oee",
                          "params": {"min": 0, "max": 100}},
    }))
    check = cfg.patrol.checks["api.oee_range"]
    assert check.on_budget_exhausted == "skip"                  # 기본값
    assert check.params["max"] == 100
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/config/test_schema_site.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/config/schema_site.py`

```python
"""사이트 계층 config 스키마 — 스펙 §4.5.

- 인증 필드는 선택: 없는 법인은 url만, 있는 법인은 username/password 추가.
- 비밀값은 SecretStr — 로그·config show에서 자동 마스킹된다.
- extra="forbid"가 전역 키의 사이트 계층 침입(§4.5-①)도 함께 거부한다.
"""
import re
from typing import Any, Literal

from pydantic import SecretStr, model_validator

from src.config.schema_app import StrictModel

_INTERVAL = re.compile(r"^\d+[smh]$")


class RedisTarget(StrictModel):
    url: str
    password: SecretStr | None = None


class MongoTarget(StrictModel):
    url: str
    username: str | None = None
    password: SecretStr | None = None


class KafkaTarget(StrictModel):
    bootstrap: str


class RestTarget(StrictModel):
    base_url: str


class RepoRef(StrictModel):
    name: str
    path: str


class CodeTarget(StrictModel):
    repos: list[RepoRef]


class Guards(StrictModel):
    timeout_s: float = 10
    max_rows: int = 1000
    max_concurrent: int = 4


class TargetConfig(StrictModel):
    redis: RedisTarget | None = None
    mongo: MongoTarget | None = None
    kafka: KafkaTarget | None = None
    rest: RestTarget | None = None
    code: CodeTarget | None = None
    guards: Guards = Guards()


class Schedule(StrictModel):
    interval: str | None = None
    cron: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.interval is None) == (self.cron is None):
            raise ValueError("schedule은 interval과 cron 중 정확히 하나만 선언한다")
        if self.interval is not None and not _INTERVAL.match(self.interval):
            raise ValueError(f"interval 형식 오류: {self.interval!r} (예: '30s', '5m', '1h')")
        if self.cron is not None and len(self.cron.split()) != 5:
            raise ValueError(f"cron은 5필드여야 한다: {self.cron!r}")
        return self


class CheckConfig(StrictModel):
    judge: Literal["rule", "llm", "rule+llm"]
    schedule: Schedule
    target: str | None = None          # 토폴로지 locator — 해석 검증은 boot에서
    params: dict[str, Any] = {}
    sample: int | None = None
    on_budget_exhausted: Literal["skip", "escalate"] = "skip"


class SitePatrol(StrictModel):
    checks: dict[str, CheckConfig] = {}


class KnowledgeConfig(StrictModel):
    root: str = "knowledge"


class SiteConfig(StrictModel):
    target: TargetConfig
    patrol: SitePatrol = SitePatrol()
    knowledge: KnowledgeConfig = KnowledgeConfig()
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `pytest tests/config/test_schema_site.py -v` → PASS

```bash
git add src/config/schema_site.py tests/config/test_schema_site.py
git commit -m "Add strict SiteConfig with optional auth and interval-xor-cron"
```

---

### Task 5: 로더 — 파일 배치 규약, registry, merge 순서, env 적용

**Files:**
- Create: `src/config/loader.py`
- Test: `tests/config/test_loader.py`

**Interfaces:**
- Consumes: Task 1~4 전부.
- Produces:
  - `SiteRef(gbm:str, fct:str, enabled:bool=True)`, `Registry(sites: list[SiteRef])`
  - `load_app_config(config_root: Path) -> AppConfig`
  - `load_registry(config_root: Path) -> Registry`
  - `load_site_config(config_root: Path, gbm: str, fct: str, *, env: Mapping[str,str]) -> tuple[SiteConfig, dict[str,str]]` — (검증된 config, provenance). env 누락 시 `ConfigError(missing=[...])`.
  - `ConfigError(Exception)` — `.problems: list[str]` (모든 문제를 모아 담는다).
- 파일 배치 규약: `config/app.json`, `config/registry.json`, `config/gbm/{gbm}.json`, `config/factories/{fct}/common.json`, `config/factories/{fct}/{gbm}.json`. 사이트 3파일은 각각 없어도 되며(빈 dict 취급) merge 순서는 나열 순서다(뒤가 이김).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/config/test_loader.py`

```python
import json

import pytest
from src.config.loader import ConfigError, load_registry, load_site_config


def _write(tmp_path, rel, data):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def _config_tree(tmp_path):
    _write(tmp_path, "config/registry.json",
           {"sites": [{"gbm": "mx", "fct": "gumi"},
                      {"gbm": "mx", "fct": "suwon", "enabled": False}]})
    _write(tmp_path, "config/gbm/mx.json",
           {"target": {"redis": {"url": "${MX_REDIS_URL}"}},
            "patrol": {"checks": {"api.freshness": {
                "judge": "rule", "schedule": {"interval": "5m"}}}}})
    _write(tmp_path, "config/factories/gumi/common.json",
           {"target": {"guards": {"max_rows": 500}}})
    _write(tmp_path, "config/factories/gumi/mx.json",
           {"patrol": {"checks": {"api.freshness": None}}})   # 사이트에서 점검 끔


def test_merge_순서와_null_삭제와_출처(tmp_path):
    _config_tree(tmp_path)
    cfg, prov = load_site_config(tmp_path / "config", "mx", "gumi",
                                 env={"MX_REDIS_URL": "redis://g:6379"})
    assert cfg.target.redis.url == "redis://g:6379"           # env 해석됨
    assert cfg.target.guards.max_rows == 500                  # common이 덮음
    assert "api.freshness" not in cfg.patrol.checks           # null로 꺼짐
    assert prov["target.guards.max_rows"] == "factories/gumi/common"


def test_env_누락은_키_이름을_전부_모아_거부(tmp_path):
    _config_tree(tmp_path)
    with pytest.raises(ConfigError) as exc:
        load_site_config(tmp_path / "config", "mx", "gumi", env={})
    assert any("MX_REDIS_URL" in p for p in exc.value.problems)


def test_registry_enabled_기본값(tmp_path):
    _config_tree(tmp_path)
    reg = load_registry(tmp_path / "config")
    assert [(s.gbm, s.fct, s.enabled) for s in reg.sites] == [
        ("mx", "gumi", True), ("mx", "suwon", False)]


def test_앞_계층이_없어도_null_마커는_삭제로_동작한다(tmp_path):
    # gbm/common 계층 없이 마지막 계층만 존재 — 스펙상 허용되는 배치
    _write(tmp_path, "config/factories/gumi/mx.json",
           {"target": {"redis": {"url": "redis://g:6379"}},
            "patrol": {"checks": {"api.freshness": None}}})
    cfg, prov = load_site_config(tmp_path / "config", "mx", "gumi", env={})
    assert "api.freshness" not in cfg.patrol.checks
    assert not any(p.startswith("patrol.checks.api.freshness") for p in prov)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/config/test_loader.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/config/loader.py`

```python
"""config 파일 배치 규약과 로드 파이프라인.

순서: 파일 읽기 → deep-merge(출처 추적) → ${ENV} 해석 → pydantic 강타입 검증.
문제는 첫 건에서 멈추지 않고 ConfigError.problems에 모은다 (기동 거부 철학).
"""
import json
from pathlib import Path

from pydantic import ValidationError

from src.config.envresolve import resolve_env_refs
from src.config.merge import deep_merge
from src.config.schema_app import AppConfig, StrictModel
from src.config.schema_site import SiteConfig


class ConfigError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class SiteRef(StrictModel):
    gbm: str
    fct: str
    enabled: bool = True


class Registry(StrictModel):
    sites: list[SiteRef]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _validation_problems(exc: ValidationError, where: str) -> list[str]:
    return [f"{where}: {'.'.join(str(x) for x in e['loc'])} — {e['msg']}"
            for e in exc.errors()]


def load_app_config(config_root: Path) -> AppConfig:
    data = _read_json(config_root / "app.json")
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_problems(exc, "app.json")) from exc


def load_registry(config_root: Path) -> Registry:
    data = _read_json(config_root / "registry.json")
    try:
        return Registry.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_problems(exc, "registry.json")) from exc


_SITE_LAYERS = ("gbm/{gbm}.json", "factories/{fct}/common.json", "factories/{fct}/{gbm}.json")


def load_site_config(config_root: Path, gbm: str, fct: str, *, env):
    merged: dict = {}
    provenance: dict[str, str] = {}
    for template in _SITE_LAYERS:
        rel = template.format(gbm=gbm, fct=fct)
        layer = _read_json(config_root / rel)
        source = rel.removesuffix(".json")
        merged = deep_merge(merged, layer, source=source, provenance=provenance)

    resolved, missing = resolve_env_refs(merged, env=env)
    problems = [f"{gbm}/{fct}: env 키 부재 또는 빈 값 — {k}" for k in sorted(set(missing))]
    if problems:
        raise ConfigError(problems)
    try:
        return SiteConfig.model_validate(resolved), provenance
    except ValidationError as exc:
        raise ConfigError(_validation_problems(exc, f"{gbm}/{fct}")) from exc
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `pytest tests/config/test_loader.py -v` → PASS

```bash
git add src/config/loader.py tests/config/test_loader.py
git commit -m "Load app/registry/site config through merge, env, strict validation"
```

---

### Task 6: 토폴로지 — 스키마, 사이트 merge, 내부 정합성

**Files:**
- Create: `src/knowledge/__init__.py`, `src/knowledge/topology.py`
- Test: `tests/knowledge/test_topology.py`

**Interfaces:**
- Consumes: `deep_merge`(Task 1), `StrictModel`(Task 3).
- Produces:
  - `DataRef(kind: Literal["kafka","redis","mongo","rest"], topic/key/collection/endpoint: str|None)` — kind별 필수 필드 검증. `locator` property → `"kafka:edge.raw.{line}"` 꼴.
  - `Service(code: ServiceCode|None, reads: list[DataRef]=[], writes: list[DataRef]=[])`, `ServiceCode(repo:str, path:str)`
  - `Derivation(inputs: list[DataRef], via: str, key: str = "fan-in")` — `key`는 `"fan-in"` 또는 자리표시자 이름(per-key).
  - `Topology(services: dict[str, Service], derivations: dict[str, Derivation])`
  - `load_topology(knowledge_root: Path, gbm: str, fct: str) -> Topology` — `topology/common.yaml` + `topology/{gbm}/{fct}.yaml` deep-merge(null 삭제 동작 포함). 파일 규약은 이 함수가 소유.
  - `topology_problems(t: Topology) -> list[str]` — 내부 정합성: derivation의 `via`가 services에 실재, `locators()`에 중복 없음. (§4.6-4)
  - `Topology.locators() -> set[str]` — 모든 서비스 reads/writes locator + derivation 키의 합집합. boot의 룰 타깃 해석(Task 8)이 소비.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/knowledge/test_topology.py`

```python
import pytest
from pydantic import ValidationError
from src.knowledge.topology import DataRef, load_topology, topology_problems

COMMON = """
services:
  twin-aggregator:
    code: { repo: twin-services, path: services/aggregator }
    reads:  [ { kind: kafka, topic: "edge.raw.{line}" } ]
    writes: [ { kind: mongo, collection: twin_state } ]
  twin-api:
    reads:  [ { kind: mongo, collection: twin_state } ]
derivations:
  "rest:/api/v1/lines/{line}/oee":
    inputs: [ { kind: mongo, collection: twin_state } ]
    via: twin-api
    key: line
"""


def _tree(tmp_path, common=COMMON, site=""):
    d = tmp_path / "knowledge" / "topology"
    (d / "mx").mkdir(parents=True)
    (d / "common.yaml").write_text(common, encoding="utf-8")
    (d / "mx" / "gumi.yaml").write_text(site, encoding="utf-8")
    return tmp_path / "knowledge"


def test_kind별_필수_필드():
    DataRef.model_validate({"kind": "kafka", "topic": "t"})
    with pytest.raises(ValidationError):
        DataRef.model_validate({"kind": "kafka", "collection": "c"})  # 잘못된 필드


def test_로드와_사이트_오버라이드(tmp_path):
    root = _tree(tmp_path, site="""
services:
  twin-aggregator:
    reads: [ { kind: kafka, topic: "edge.raw.gumi.{line}" } ]
""")
    topo = load_topology(root, "mx", "gumi")
    assert topo.services["twin-aggregator"].reads[0].locator == "kafka:edge.raw.gumi.{line}"
    assert topo.derivations["rest:/api/v1/lines/{line}/oee"].key == "line"
    assert "mongo:twin_state" in topo.locators()


def test_끊긴_via는_정합성_오류(tmp_path):
    root = _tree(tmp_path, site="""
derivations:
  "rest:/api/v1/lines/{line}/oee": { inputs: [ { kind: mongo, collection: twin_state } ],
                                     via: ghost-service }
""")
    topo = load_topology(root, "mx", "gumi")
    problems = topology_problems(topo)
    assert any("ghost-service" in p for p in problems)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/knowledge/test_topology.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/knowledge/topology.py`

```python
"""토폴로지 명세 — 스펙 §3.1. 파이프라인 이분탐색의 지도.

derivations는 output locator를 키로 하는 map이다. 리스트가 아닌 이유:
리스트 deep-merge는 통째 대체 아니면 append라 사이트별 편집에 틀린 의미가 된다.
"""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import model_validator

from src.config.merge import deep_merge, record_provenance
from src.config.schema_app import StrictModel

_KIND_FIELD = {"kafka": "topic", "redis": "key", "mongo": "collection", "rest": "endpoint"}


class DataRef(StrictModel):
    kind: Literal["kafka", "redis", "mongo", "rest"]
    topic: str | None = None
    key: str | None = None
    collection: str | None = None
    endpoint: str | None = None

    @model_validator(mode="after")
    def _field_matches_kind(self):
        want = _KIND_FIELD[self.kind]
        set_fields = [f for f in _KIND_FIELD.values() if getattr(self, f) is not None]
        if set_fields != [want]:
            raise ValueError(f"kind={self.kind}에는 {want}만 선언한다 (선언됨: {set_fields})")
        return self

    @property
    def locator(self) -> str:
        return f"{self.kind}:{getattr(self, _KIND_FIELD[self.kind])}"


class ServiceCode(StrictModel):
    repo: str
    path: str


class Service(StrictModel):
    code: ServiceCode | None = None
    reads: list[DataRef] = []
    writes: list[DataRef] = []


class Derivation(StrictModel):
    inputs: list[DataRef]
    via: str
    key: str = "fan-in"     # "fan-in" 또는 자리표시자 이름(per-key)


class Topology(StrictModel):
    services: dict[str, Service] = {}
    derivations: dict[str, Derivation] = {}

    def locators(self) -> set[str]:
        out = set(self.derivations)
        for svc in self.services.values():
            out |= {ref.locator for ref in svc.reads + svc.writes}
        return out


def load_topology(knowledge_root: Path, gbm: str, fct: str) -> Topology:
    base_path = knowledge_root / "topology" / "common.yaml"
    site_path = knowledge_root / "topology" / gbm / f"{fct}.yaml"
    base = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {} \
        if base_path.exists() else {}
    site = yaml.safe_load(site_path.read_text(encoding="utf-8")) or {} \
        if site_path.exists() else {}
    provenance: dict[str, str] = {}
    record_provenance(base, source="common", provenance=provenance)
    merged = deep_merge(base, site, source=f"{gbm}/{fct}", provenance=provenance)
    return Topology.model_validate(merged)


def topology_problems(topo: Topology) -> list[str]:
    problems = []
    for output, deriv in topo.derivations.items():
        if deriv.via not in topo.services:
            problems.append(f"derivation {output!r}: via {deriv.via!r}가 services에 없다")
    return problems
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `pytest tests/knowledge/test_topology.py -v` → PASS

```bash
git add src/knowledge tests/knowledge
git commit -m "Add topology schema with site merge and integrity check"
```

---

### Task 7: deployment.yaml + canonical digest

**Files:**
- Create: `src/knowledge/deployment.py`, `src/knowledge/digest.py`
- Test: `tests/knowledge/test_deployment.py`

**Interfaces:**
- Consumes: `StrictModel`(Task 3).
- Produces:
  - `canonical_digest(obj: object) -> str` — 파싱된 객체의 sha256 hex. 정렬된 JSON 직렬화 기반이라 **공백·키 순서에 불변** (지식 as_of 박제 §2.5-3용).
  - `DeployedVersion(repo: str, commit: str)`, `Deployment(services: dict[str, DeployedVersion])`
  - `load_deployment(knowledge_root: Path, gbm: str, fct: str) -> Deployment | None` — `deployment/{gbm}/{fct}.yaml`, 없으면 None (없는 사이트는 "배포 버전 미검증" 경로 — 계획 3에서 소비).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/knowledge/test_deployment.py`

```python
from src.knowledge.deployment import load_deployment
from src.knowledge.digest import canonical_digest


def test_digest는_키순서와_공백에_불변():
    a = {"b": 1, "a": [1, 2]}
    b = {"a": [1, 2], "b": 1}
    assert canonical_digest(a) == canonical_digest(b)
    assert canonical_digest(a) != canonical_digest({"a": [2, 1], "b": 1})


def test_deployment_로드와_부재(tmp_path):
    d = tmp_path / "knowledge" / "deployment" / "mx"
    d.mkdir(parents=True)
    (d / "gumi.yaml").write_text(
        "services:\n  twin-aggregator: { repo: twin-services, commit: a3f9c2 }\n",
        encoding="utf-8")
    dep = load_deployment(tmp_path / "knowledge", "mx", "gumi")
    assert dep.services["twin-aggregator"].commit == "a3f9c2"
    assert load_deployment(tmp_path / "knowledge", "mx", "suwon") is None
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/knowledge/test_deployment.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현**

`src/knowledge/digest.py`:
```python
"""지식 산출물의 content digest — 케이스 T0 박제(스펙 §2.5-3)에 쓴다.

파싱된 객체 기준이라 파일의 공백·키 순서 변경은 digest를 바꾸지 않는다.
"""
import hashlib
import json


def canonical_digest(obj) -> str:
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

`src/knowledge/deployment.py`:
```python
"""사이트×서비스 → 배포 커밋 매핑 — 스펙 §3.3.

로컬 체크아웃의 HEAD는 배포 진실이 아니다. 이 파일이 진실이고,
운영 절차로 수동 유지된다. 없으면 None — 그 사이트의 코드 증거에는
"배포 버전 미검증" 플래그가 강제된다(계획 3에서 소비).
"""
from pathlib import Path

import yaml

from src.config.schema_app import StrictModel


class DeployedVersion(StrictModel):
    repo: str
    commit: str


class Deployment(StrictModel):
    services: dict[str, DeployedVersion] = {}


def load_deployment(knowledge_root: Path, gbm: str, fct: str) -> Deployment | None:
    path = knowledge_root / "deployment" / gbm / f"{fct}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Deployment.model_validate(data)
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `pytest tests/knowledge/test_deployment.py -v` → PASS

```bash
git add src/knowledge/deployment.py src/knowledge/digest.py tests/knowledge/test_deployment.py
git commit -m "Add deployment mapping and canonical content digest"
```

---

### Task 8: 기동 검증 오케스트레이터 (§4.6의 1~6)

**Files:**
- Create: `src/boot.py`
- Test: `tests/test_boot.py`

**Interfaces:**
- Consumes: Task 5~7의 로더 전부.
- Produces:
  - `BootError(where: str, problem: str)` (dataclass)
  - `validate_boot(config_root: Path, *, env: Mapping[str, str], repo_root: Path) -> list[BootError]` — 빈 리스트면 통과. 검사 항목: ① app config ② registry ③ enabled 사이트별: 사이트 config(스키마+env) ④ 토폴로지 정합성 ⑤ 점검 `target`이 `topology.locators()`로 해석 ⑥ 토폴로지 `code.repo`가 config `target.code.repos[].name`에 실재. `knowledge_root`는 사이트 config의 `knowledge.root`를 `repo_root` 기준 상대 경로로 해석.
  - 스펙 §4.6의 7(deployment hash — git)·8(Mongo 롤)은 계획 2에서 이 리스트에 추가된다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_boot.py`

```python
import json

from src.boot import validate_boot

ENV = {"MX_REDIS_URL": "redis://g:6379"}


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(tmp_path, *, check_target="rest:/oee", repo_name="twin-services"):
    _write(tmp_path, "config/app.json", json.dumps(
        {"llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}}}))
    _write(tmp_path, "config/registry.json", json.dumps(
        {"sites": [{"gbm": "mx", "fct": "gumi"},
                   {"gbm": "mx", "fct": "off", "enabled": False}]}))
    _write(tmp_path, "config/gbm/mx.json", json.dumps(
        {"target": {"redis": {"url": "${MX_REDIS_URL}"},
                    "code": {"repos": [{"name": "twin-services", "path": "/r"}]}},
         "patrol": {"checks": {"c1": {"judge": "rule",
                                      "schedule": {"interval": "5m"},
                                      "target": check_target}}}}))
    _write(tmp_path, "knowledge/topology/common.yaml", f"""
services:
  twin-api:
    code: {{ repo: {repo_name}, path: api }}
    writes: [ {{ kind: rest, endpoint: /oee }} ]
derivations:
  "rest:/oee": {{ inputs: [ {{ kind: mongo, collection: twin_state }} ], via: twin-api }}
""")
    return tmp_path


def test_정상_트리는_통과하고_disabled_사이트는_건너뛴다(tmp_path):
    _tree(tmp_path)   # mx/off 사이트는 config 파일이 없지만 disabled라 검사 안 함
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []


def test_해석_안되는_룰_타깃은_거부(tmp_path):
    _tree(tmp_path, check_target="rest:/ghost")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("rest:/ghost" in e.problem for e in errors)


def test_토폴로지의_미등록_repo는_거부(tmp_path):
    _tree(tmp_path, repo_name="ghost-repo")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("ghost-repo" in e.problem for e in errors)


def test_오류는_전부_모인다(tmp_path):
    _tree(tmp_path, check_target="rest:/ghost", repo_name="ghost-repo")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert len(errors) >= 2
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_boot.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/boot.py`

```python
"""기동 검증 — 스펙 §4.6.

하나라도 실패하면 기동 거부. 오류는 전부 모아 보고한다 —
밤에 조용히 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.
(§4.6의 7 deployment hash 실재·8 Mongo readonly 롤은 계획 2에서 추가.)
"""
from dataclasses import dataclass
from pathlib import Path

from src.config.loader import ConfigError, load_app_config, load_registry, load_site_config
from src.knowledge.topology import load_topology, topology_problems


@dataclass
class BootError:
    where: str
    problem: str


def validate_boot(config_root: Path, *, env, repo_root: Path) -> list[BootError]:
    errors: list[BootError] = []

    try:
        load_app_config(config_root)
    except ConfigError as exc:
        errors += [BootError("app", p) for p in exc.problems]

    try:
        registry = load_registry(config_root)
    except ConfigError as exc:
        return errors + [BootError("registry", p) for p in exc.problems]

    for site in registry.sites:
        if not site.enabled:
            continue
        where = f"{site.gbm}/{site.fct}"
        try:
            cfg, _ = load_site_config(config_root, site.gbm, site.fct, env=env)
        except ConfigError as exc:
            errors += [BootError(where, p) for p in exc.problems]
            continue

        knowledge_root = repo_root / cfg.knowledge.root
        topo = load_topology(knowledge_root, site.gbm, site.fct)
        errors += [BootError(where, p) for p in topology_problems(topo)]

        known = topo.locators()
        for name, check in cfg.patrol.checks.items():
            if check.target is not None and check.target not in known:
                errors.append(BootError(
                    where, f"점검 {name!r}의 target {check.target!r}이 토폴로지로 해석되지 않는다"))

        repo_names = {r.name for r in cfg.target.code.repos} if cfg.target.code else set()
        for svc_name, svc in topo.services.items():
            if svc.code is not None and svc.code.repo not in repo_names:
                errors.append(BootError(
                    where, f"서비스 {svc_name!r}의 repo {svc.code.repo!r}가 config에 없다"))

    return errors
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `pytest tests/test_boot.py -v` → PASS

```bash
git add src/boot.py tests/test_boot.py
git commit -m "Validate boot across app, registry, sites, topology, rule targets, repos"
```

---

### Task 9: CLI — `registry`, `config show`, `knowledge validate`

**Files:**
- Create: `src/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 5~8 전부.
- Produces: `main(argv: list[str] | None = None) -> int` — argparse 서브커맨드 3종. `.env`는 python-dotenv `load_dotenv()`로 `os.environ`에 올린 뒤 그것을 env로 쓴다. 공통 옵션 `--config-root`(기본 `config`), `--repo-root`(기본 `.`).
  - `registry`: 사이트 목록과 enabled를 한 줄씩 출력, exit 0.
  - `config show --gbm G --fct F`: 병합 결과를 JSON(마스킹: `model_dump(mode="json")` — SecretStr은 `**********`)으로, 이어서 `# 출처` 섹션에 `path = layer` 나열. ConfigError면 문제를 stderr에 출력, exit 1.
  - `knowledge validate`: `validate_boot` 실행. 통과면 "OK" exit 0, 실패면 `[where] problem` 나열 exit 1 (CI용).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cli.py`

```python
import json

from src.__main__ import main
from tests.test_boot import ENV, _tree   # 트리 픽스처 재사용


def test_registry_출력(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["registry", "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out
    assert code == 0 and "mx/gumi" in out and "mx/off" in out


def test_config_show는_비밀을_마스킹한다(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ",
                        {**ENV, "MX_REDIS_PW": "hunter2"})
    # redis에 password 참조 추가
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["redis"]["password"] = "${MX_REDIS_PW}"
    gbm.write_text(json.dumps(data), encoding="utf-8")

    code = main(["config", "show", "--gbm", "mx", "--fct", "gumi",
                 "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out
    assert code == 0
    assert "hunter2" not in out and "**********" in out
    assert "gbm/mx" in out                       # 출처 표시


def test_knowledge_validate_실패는_exit_1(tmp_path, capsys, monkeypatch):
    _tree(tmp_path, check_target="rest:/ghost")
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["knowledge", "validate",
                 "--config-root", str(tmp_path / "config"),
                 "--repo-root", str(tmp_path)])
    assert code == 1
    assert "rest:/ghost" in capsys.readouterr().err
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_cli.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/__main__.py`

```python
"""CLI 엔트리 — 계획 1 범위: registry / config show / knowledge validate.

계획 4~5에서 patrol, chat, case 서브커맨드가 추가된다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.boot import validate_boot
from src.config.loader import ConfigError, load_registry, load_site_config


def _add_common(parser):
    parser.add_argument("--config-root", default="config")
    parser.add_argument("--repo-root", default=".")


def main(argv=None) -> int:
    load_dotenv()
    env = os.environ

    parser = argparse.ArgumentParser(prog="python -m src")
    sub = parser.add_subparsers(dest="command", required=True)

    p_registry = sub.add_parser("registry", help="사이트 목록")
    _add_common(p_registry)

    p_config = sub.add_parser("config")
    config_sub = p_config.add_subparsers(dest="config_command", required=True)
    p_show = config_sub.add_parser("show", help="병합 config와 값의 출처")
    p_show.add_argument("--gbm", required=True)
    p_show.add_argument("--fct", required=True)
    _add_common(p_show)

    p_knowledge = sub.add_parser("knowledge")
    knowledge_sub = p_knowledge.add_subparsers(dest="knowledge_command", required=True)
    p_validate = knowledge_sub.add_parser("validate", help="기동 검증 단독 실행 (CI용)")
    _add_common(p_validate)

    args = parser.parse_args(argv)
    config_root = Path(args.config_root)

    if args.command == "registry":
        registry = load_registry(config_root)
        for site in registry.sites:
            flag = "enabled" if site.enabled else "disabled"
            print(f"{site.gbm}/{site.fct}  [{flag}]")
        return 0

    if args.command == "config":
        try:
            cfg, provenance = load_site_config(config_root, args.gbm, args.fct, env=env)
        except ConfigError as exc:
            for problem in exc.problems:
                print(problem, file=sys.stderr)
            return 1
        print(json.dumps(cfg.model_dump(mode="json"), ensure_ascii=False, indent=2))
        print("\n# 출처")
        for path, source in sorted(provenance.items()):
            print(f"{path} = {source}")
        return 0

    if args.command == "knowledge":
        errors = validate_boot(config_root, env=env, repo_root=Path(args.repo_root))
        if not errors:
            print("OK")
            return 0
        for e in errors:
            print(f"[{e.where}] {e.problem}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `pytest -v`
Expected: 전체 PASS (Task 1~9의 모든 테스트)

- [ ] **Step 5: 커밋**

```bash
git add src/__main__.py tests/test_cli.py
git commit -m "Add CLI: registry, config show with masking, knowledge validate"
```

---

## 완료 기준 (계획 1)

- `pytest` 전체 통과.
- 실제 `config/`·`knowledge/` 예시 트리를 만들어 `python -m src registry`, `python -m src config show --gbm mx --fct gumi`, `python -m src knowledge validate`가 문서대로 동작.
- 비밀값이 어떤 출력에도 평문으로 나타나지 않음.

## 계획 2 예고 (이 계획이 끝나면)

읽기 전용 포트 5종 + 결과 봉투 + guards + in-memory 스텁 + 실구현, 그리고 §4.6의 7(deployment hash — CodeRepoReader의 git으로)·8(Mongo readonly 롤)을 `validate_boot`에 추가.
