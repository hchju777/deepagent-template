# 계획 4a: 순찰 층 (프로브·판정기·예산·레저·게이트·스케줄러) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스펙 §1.4(순찰 점검)·§1.1(케이스 게이트: 지문 중복 억제·스냅샷 가드레일)·§5.4-F5(하트비트) — config의 점검 정의가 실제로 돌아 Finding을 내고, 그 Finding이 케이스로 승격되어 조사 엔진(계획 3)에 넘겨질 준비가 되는 층을 구현한다. 케이스 큐·워커·수명주기 타임아웃·Mongo 저장소는 계획 4b.

**Architecture:** 점검 = 프로브(코드 레지스트리, 무엇을 뜨나) + 판정기(rule|llm|rule+llm). 프로브 스냅샷은 **뜨는 즉시** Store에 박제되고(순찰 스크래치 케이스 id 아래), Finding은 그 스냅샷 id를 인용한다 — LLM 판정기가 id를 지어낼 수 없도록 **코드가 id 목록을 주고 LLM은 그중에서 고른다**. 게이트는 인용 실재 → 지문 중복 → 열기/첨부를 결정하고, 새 케이스에는 스냅샷을 T0 증거로 복사한다. 스케줄러는 APScheduler(interval/cron, max_instances=1, coalesce)로 점검을 돌리고 틱마다 하트비트를 레저에 남긴다.

**Tech Stack:** 계획 3 위에 APScheduler>=3.11,<4 추가(전작 핀). LLM 판정기는 ScriptedLLM 호환 표면.

## Global Constraints (스펙에서 발췌 — 모든 태스크에 적용)

- **점검 결과는 3상 `ok | finding | error`** + 레저 전용 `skipped`(미점검 명시 — 예산 소진). 프로브 실패는 error지 finding이 아니다(네트워크 블립이 유령 케이스를 열지 않음). error는 버리지 않고 레저에 남긴다(§1.4).
- **점검 ≠ 조사**: LLM 판정기는 "이상하다/아니다 + 근거"까지. 원인 파기는 케이스를 열어 엔진이(§1.4) — 판정기 코드에 조사 도구를 주지 않는다.
- **스냅샷 박제**: 프로브 결과는 판정 전에 Store에 저장. Finding.evidence_ids는 저장된 id만. 게이트의 가드레일은 **저장본 대조**(라이브 재조회 금지)(§1.4·§1.1-⑥).
- **지문 중복 억제**: `fingerprint = sha256(gbm|fct|check|target)`. 같은 지문의 열린 케이스(open/investigating/awaiting_human)가 있으면 새로 열지 않고 첨부(§1.1).
- **LLM 예산**: 시간 창(1h) 슬라이딩 카운터, 전역. 소진 시 `on_budget_exhausted: skip`(레저 skipped) | `escalate`(rule+llm에서 룰 결과만으로 Finding)(§1.4·§4.5).
- **판정기 절대 raise 금지**, **시계 주입**(`datetime.now()` 금지), **한국어**, StrictModel, 기동 거부 철학(프로브 이름 실재는 기동 검증 9로).
- 큐·워커·owner/lease·타임아웃 종결·Mongo·CLI patrol 데몬은 **4b**.

## File Structure

```
requirements.txt                 # APScheduler 추가
src/config/schema_site.py        # (수정) CheckConfig.probe 필드
src/domain/patrol.py             # Finding, CheckOutcome, fingerprint
src/domain/cases.py              # CaseRecord, CaseStatus, CaseRepositoryPort + InMemory
src/patrol/__init__.py
src/patrol/probes.py             # PROBES 레지스트리 + 기본 프로브 4종 + resolve_probe
src/patrol/rules.py              # rule 판정기 (range/exists/freshness/max)
src/patrol/llm_judge.py          # LLM 판정기 + LlmBudget
src/patrol/ledger.py             # LedgerPort + InMemoryLedger (실행 기록·하트비트)
src/patrol/runner.py             # run_check — 3상 결과, rule+llm 합성, 예산 정책
src/patrol/gate.py               # admit_finding — 가드레일·지문·열기/첨부·Case 조립
src/patrol/scheduler.py          # build_scheduler — APScheduler 잡 등록·하트비트
src/boot.py                      # (수정) 검사 9: 점검 프로브 이름 실재
tests/domain/test_patrol.py, test_cases.py
tests/patrol/__init__.py, test_probes.py, test_rules.py, test_llm_judge.py,
              test_runner.py, test_gate.py, test_scheduler.py
```

---

### Task 1: 도메인 — Finding·CheckOutcome·지문 + CaseRecord 저장소

**Files:**
- Modify: `requirements.txt`, `src/config/schema_site.py`
- Create: `src/domain/patrol.py`, `src/domain/cases.py`, `src/patrol/__init__.py`, `tests/patrol/__init__.py`
- Test: `tests/domain/test_patrol.py`, `tests/domain/test_cases.py`

**Interfaces:**
- `requirements.txt` 추가: `APScheduler>=3.11,<4` (주석: "4.x는 알파, API 상이 — 3.x 고정").
- `CheckConfig.probe: str | None = None` — 프로브 레지스트리 이름. None이면 target의 kind로 기본 프로브 선택(Task 2).
- `src/domain/patrol.py`:
  - `Finding(StrictModel)`: `id: str`, `gbm: str`, `fct: str`, `check: str`, `target: str | None`, `summary: str`, `evidence_ids: list[str]`, `scratch_case_id: str`(스냅샷이 저장된 순찰 스크래치 케이스 id), `observed_at: datetime`, `judge: Literal["rule","llm","rule+llm"]`.
  - `CheckOutcome(StrictModel)`: `status: Literal["ok","finding","error","skipped"]`, `observed_at: datetime`, `finding: Finding | None = None`, `error: str | None = None`, `skipped_reason: str | None = None`, `llm_calls: int = 0`. 검증자: finding→finding 필수, error→error 필수, skipped→skipped_reason 필수.
  - `fingerprint(gbm, fct, check, target) -> str` — `sha256("|".join([...]))[:16]`(target None은 "-").
  - `scratch_case_id(gbm, fct, check) -> str` — `f"patrol:{gbm}:{fct}:{check}"`.
- `src/domain/cases.py`:
  - `CaseStatus = Literal["open","investigating","awaiting_human","closed"]`, `OPEN_STATUSES = ("open","investigating","awaiting_human")`.
  - `CaseRecord(StrictModel)`: `id, gbm, fct, fingerprint, status: CaseStatus="open", created_at: datetime, updated_at: datetime, finding_ids: list[str]=[], thread_ids: list[str]=[], owner: str|None=None, lease_until: datetime|None=None`.
  - `CaseRepositoryPort(ABC)`: `save(record)`, `get(case_id) -> CaseRecord`(없으면 KeyError), `find_open_by_fingerprint(fp) -> CaseRecord | None`(OPEN_STATUSES만), `list_by_status(status) -> list[CaseRecord]`, `new_case_id() -> str`(`c-<n>` 증가).
  - `InMemoryCaseRepository`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/domain/test_patrol.py`:
```python
from datetime import datetime

import pytest
from pydantic import ValidationError
from src.domain.patrol import CheckOutcome, Finding, fingerprint, scratch_case_id

T = datetime(2026, 9, 3, 8, 0)


def test_지문은_사이트_점검_대상으로_결정되고_target_없음은_구분된다():
    a = fingerprint("mx", "gumi", "api.oee", "rest:/oee")
    assert a == fingerprint("mx", "gumi", "api.oee", "rest:/oee")
    assert a != fingerprint("mx", "suwon", "api.oee", "rest:/oee")
    assert fingerprint("mx", "gumi", "x", None) != fingerprint("mx", "gumi", "x", "-x")
    assert scratch_case_id("mx", "gumi", "api.oee") == "patrol:mx:gumi:api.oee"


def test_outcome_3상_검증자():
    f = Finding(id="f-1", gbm="mx", fct="gumi", check="api.oee", target="rest:/oee",
                summary="OEE 512%", evidence_ids=["ev-1"],
                scratch_case_id="patrol:mx:gumi:api.oee", observed_at=T, judge="rule")
    CheckOutcome(status="finding", observed_at=T, finding=f)
    CheckOutcome(status="error", observed_at=T, error="타임아웃")
    CheckOutcome(status="skipped", observed_at=T, skipped_reason="llm 예산 소진")
    with pytest.raises(ValidationError):
        CheckOutcome(status="finding", observed_at=T)
    with pytest.raises(ValidationError):
        CheckOutcome(status="error", observed_at=T)
```

`tests/domain/test_cases.py`:
```python
from datetime import datetime

import pytest
from src.domain.cases import CaseRecord, InMemoryCaseRepository

T = datetime(2026, 9, 3, 8, 0)


def test_열린_케이스만_지문으로_찾는다():
    repo = InMemoryCaseRepository()
    cid = repo.new_case_id()
    assert cid == "c-1" and repo.new_case_id() == "c-2"
    repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint="fp-a",
                         created_at=T, updated_at=T))
    assert repo.find_open_by_fingerprint("fp-a").id == cid
    closed = repo.get(cid).model_copy(update={"status": "closed"})
    repo.save(closed)
    assert repo.find_open_by_fingerprint("fp-a") is None
    assert [r.id for r in repo.list_by_status("closed")] == [cid]
    with pytest.raises(KeyError):
        repo.get("c-9")
```

- [ ] **Step 2: 실패 확인** — FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — 위 인터페이스대로. `CheckOutcome` 검증자 예:

```python
    @model_validator(mode="after")
    def _status_payload(self):
        need = {"finding": self.finding, "error": self.error, "skipped": self.skipped_reason}
        if self.status in need and need[self.status] is None:
            raise ValueError(f"status={self.status}에는 해당 필드가 필요하다")
        return self
```

`InMemoryCaseRepository.find_open_by_fingerprint`는 `status in OPEN_STATUSES` 필터. `new_case_id`는 인스턴스 카운터.

- [ ] **Step 4: 전체 PASS 후 커밋**

```bash
git add requirements.txt src/config/schema_site.py src/domain/patrol.py src/domain/cases.py src/patrol tests/patrol tests/domain/test_patrol.py tests/domain/test_cases.py
git commit -m "Add patrol findings, outcomes, fingerprints, and a case repository"
```

---

### Task 2: 프로브 레지스트리 + 기동 검증 9

**Files:**
- Create: `src/patrol/probes.py`
- Modify: `src/boot.py`
- Test: `tests/patrol/test_probes.py`, `tests/test_boot.py`(1건 추가)

**Interfaces:**
- 프로브 시그니처: `async def probe(adapters: AdapterSet, check: CheckConfig, *, clock) -> ProbeResult`. **절대 raise하지 않는다** — 어댑터 None이면 `ProbeResult(status="error", error="어댑터 미설정: {kind}")`.
- `PROBES: dict[str, ProbeFn]` 기본 4종:
  - `rest_get`: target `"rest:/path"` → `adapters.rest.get(path)`.
  - `redis_get`: target `"redis:key"` → `adapters.redis.get(key)`.
  - `mongo_recent`: target `"mongo:coll"` → `adapters.mongo.find(coll, {}, sort=[(params.get("ts_field","ts"), -1)], limit=check.sample or 20)`.
  - `kafka_lag`: `params["group"]` → `adapters.kafka.group_offsets(group)` (group 없으면 error 결과).
- `resolve_probe(check: CheckConfig) -> str | None` — `check.probe`가 있으면 그것, 없으면 target 접두사로 `{"rest": "rest_get", "redis": "redis_get", "mongo": "mongo_recent", "kafka": "kafka_lag"}`, 둘 다 없으면 None.
- 기동 검증 9 (`src/boot.py`): enabled 사이트의 각 점검에 대해 `resolve_probe(check)`가 None이거나 `PROBES`에 없으면 BootError(`"점검 {name!r}의 프로브를 해석할 수 없다"`).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/patrol/test_probes.py`

```python
from datetime import datetime, timezone

from src.config.schema_site import CheckConfig, SiteConfig
from src.infrastructure.factory import StubSeeds, build_adapters
from src.knowledge.topology import Topology
from src.patrol.probes import PROBES, resolve_probe

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {}})
SITE = SiteConfig.model_validate({"target": {
    "rest": {"base_url": "http://x"}, "redis": {"url": "redis://x"},
    "mongo": {"url": "mongodb://x:27017"}}})


def _adapters(seeds):
    return build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=seeds)


def _check(**kw):
    base = {"judge": "rule", "schedule": {"interval": "5m"}}
    base.update(kw)
    return CheckConfig.model_validate(base)


def test_target_kind로_기본_프로브가_정해지고_명시가_우선():
    assert resolve_probe(_check(target="rest:/oee")) == "rest_get"
    assert resolve_probe(_check(target="mongo:twin_state")) == "mongo_recent"
    assert resolve_probe(_check(target="rest:/oee", probe="kafka_lag")) == "kafka_lag"
    assert resolve_probe(_check()) is None


async def test_rest_get_프로브는_봉투와_본문을_돌려준다():
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 5.12}}))
    result = await PROBES["rest_get"](adapters, _check(target="rest:/oee"), clock=lambda: T)
    assert result.status == "ok" and result.data["body"] == {"oee": 5.12}
    assert result.envelope.observed_at == T


async def test_미설정_어댑터와_잘못된_target은_error_결과():
    adapters = _adapters(StubSeeds())
    kafka = await PROBES["kafka_lag"](adapters, _check(params={"group": "g"}), clock=lambda: T)
    assert kafka.status == "error" and "어댑터" in kafka.error
    nogroup = await PROBES["kafka_lag"](adapters, _check(), clock=lambda: T)
    assert nogroup.status == "error"
```

`tests/test_boot.py`에 추가:
```python
def test_해석_안되는_프로브는_기동_거부(tmp_path):
    _tree(tmp_path)
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["patrol"]["checks"]["c1"]["probe"] = "ghost_probe"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("프로브" in e.problem for e in errors)
```

- [ ] **Step 2~4**: FAIL → 구현(프로브 최외곽 try/except → error ProbeResult, `Envelope(observed_at=clock())`) → 전체 PASS → 커밋

```bash
git add src/patrol/probes.py src/boot.py tests/patrol/test_probes.py tests/test_boot.py
git commit -m "Register patrol probes and refuse boot on unresolvable ones"
```

---

### Task 3: rule 판정기

**Files:**
- Create: `src/patrol/rules.py`
- Test: `tests/patrol/test_rules.py`

**Interfaces:**
- `RuleVerdict(StrictModel)`: `status: Literal["ok","finding"]`, `reason: str`.
- `judge_by_rule(result: ProbeResult, params: dict, *, clock) -> RuleVerdict` — `params["rule"]` 종류:
  - `range`: `field`(점 경로, 예 `"body.oee"`), `min`/`max` 중 하나 이상. 값이 범위 밖이면 finding. 값 부재/비수치면 **finding**(reason "필드 부재/비수치" — 룰이 기대한 형태가 아니라는 것 자체가 이상).
  - `exists`: 데이터가 None/빈 컨테이너면 finding(reason "대상 부재").
  - `freshness`: `field`(ISO 문자열 또는 datetime), `max_age_s` — `clock() - ts > max_age_s`면 finding. 필드 부재는 finding.
  - `max`: `field`, `max` — 값 > max면 finding(lag 등).
  - 미지의 rule 이름(또는 `rule` 키 부재)은 config 오류다. 조용히 ok로 통과시키면 안 되므로 `judge_by_rule`은 `KnownRuleError(Exception)`를 던진다 — 판정기 중 **유일하게 허용되는 예외**이며, runner가 잡아 `error` 3상("rule 설정 오류 — ...")으로 레저에 남긴다.
- `get_path(data, dotted) -> Any | None` — dict/list 점 경로 조회.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/patrol/test_rules.py`

```python
from datetime import datetime, timedelta, timezone

import pytest
from src.domain.envelope import Envelope, ProbeResult
from src.patrol.rules import KnownRuleError, judge_by_rule

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _ok(data):
    return ProbeResult(status="ok", envelope=Envelope(observed_at=T), data=data)


def test_range_rule():
    params = {"rule": "range", "field": "body.oee", "min": 0, "max": 100}
    assert judge_by_rule(_ok({"body": {"oee": 87}}), params, clock=lambda: T).status == "ok"
    bad = judge_by_rule(_ok({"body": {"oee": 512}}), params, clock=lambda: T)
    assert bad.status == "finding" and "512" in bad.reason
    assert judge_by_rule(_ok({"body": {}}), params, clock=lambda: T).status == "finding"


def test_exists_freshness_max():
    assert judge_by_rule(_ok(None), {"rule": "exists"}, clock=lambda: T).status == "finding"
    assert judge_by_rule(_ok("480"), {"rule": "exists"}, clock=lambda: T).status == "ok"
    fresh = {"rule": "freshness", "field": "ts", "max_age_s": 60}
    old = (T - timedelta(seconds=300)).isoformat()
    assert judge_by_rule(_ok({"ts": old}), fresh, clock=lambda: T).status == "finding"
    assert judge_by_rule(_ok({"ts": T.isoformat()}), fresh, clock=lambda: T).status == "ok"
    assert judge_by_rule(_ok({"lag": 1500}), {"rule": "max", "field": "lag", "max": 1000},
                         clock=lambda: T).status == "finding"


def test_미지의_rule은_KnownRuleError():
    with pytest.raises(KnownRuleError):
        judge_by_rule(_ok({}), {"rule": "ghost"}, clock=lambda: T)
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/patrol/rules.py tests/patrol/test_rules.py
git commit -m "Judge probe snapshots with range, exists, freshness, and max rules"
```

---

### Task 4: LLM 판정기 + 예산

**Files:**
- Create: `src/patrol/llm_judge.py`
- Test: `tests/patrol/test_llm_judge.py`

**Interfaces:**
- `LlmBudget(max_calls_per_hour: int, *, clock)`: `try_acquire() -> bool` — 최근 3600초 내 호출 수가 상한 미만이면 기록하고 True. `remaining() -> int`.
- `LlmJudgeOutput(StrictModel)`: `status: Literal["ok","finding"]`, `summary: str`, `evidence_ids: list[str] = []`.
- `judge_by_llm(snapshot_ids: list[str], snapshot_texts: dict[str, str], check_name: str, question: str, *, llm) -> tuple[LlmJudgeOutput | None, str | None]` — 프롬프트(한국어): 점검 이름, 질문(`params.get("question")` 또는 기본 "이 데이터에 운영상 이상이 있는가?"), 스냅샷을 `[증거 ev-N] <텍스트(최대 2000자)>`로 나열, 지시 "finding이면 근거로 쓴 증거 id를 evidence_ids에 그대로 적어라. JSON만: {...}". `parse_structured` 재사용, **재시도 없음**(예산 절약 — 파싱 실패는 error 3상). **절대 raise 금지**: ainvoke 예외도 `(None, "LLM 호출 실패 — ...")`.
- **인용 가드레일**(코드): `judge_by_llm` 반환 후 runner가 검사하지만, 판정기 자체도 `evidence_ids`를 `snapshot_ids`와 교집합으로 **정제**해 돌려준다(지어낸 id 소멸). finding인데 정제 후 인용이 비면 → `(None, "환각 인용 — 근거 없는 finding 기각")`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/patrol/test_llm_judge.py`

```python
from datetime import datetime, timedelta, timezone

from src.infrastructure.llm import ScriptedLLM
from src.patrol.llm_judge import LlmBudget, judge_by_llm

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_예산은_시간_창_슬라이딩():
    now = [T]
    budget = LlmBudget(2, clock=lambda: now[0])
    assert budget.try_acquire() and budget.try_acquire() and not budget.try_acquire()
    now[0] = T + timedelta(hours=1, seconds=1)
    assert budget.try_acquire()          # 창이 지나면 회복
    assert budget.remaining() == 1


async def test_finding은_실재_id만_인용하고_환각은_기각():
    llm = ScriptedLLM(['{"status": "finding", "summary": "멈춘 라인이 생산 중", '
                       '"evidence_ids": ["ev-1", "ev-99"]}'])
    out, err = await judge_by_llm(["ev-1"], {"ev-1": "line12 STOP, output +60/h"},
                                  "twin.consistency", "모순이 있는가?", llm=llm)
    assert err is None and out.status == "finding" and out.evidence_ids == ["ev-1"]

    ghost = ScriptedLLM(['{"status": "finding", "summary": "x", "evidence_ids": ["ev-99"]}'])
    out2, err2 = await judge_by_llm(["ev-1"], {"ev-1": "..."}, "c", "q", llm=ghost)
    assert out2 is None and "환각" in err2


async def test_파싱_실패와_호출_실패는_raise가_아니라_오류_반환():
    out, err = await judge_by_llm(["ev-1"], {"ev-1": "..."}, "c", "q", llm=ScriptedLLM(["말로만"]))
    assert out is None and err
    out2, err2 = await judge_by_llm(["ev-1"], {"ev-1": "..."}, "c", "q", llm=ScriptedLLM([]))
    assert out2 is None and "실패" in err2
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/patrol/llm_judge.py tests/patrol/test_llm_judge.py
git commit -m "Judge snapshots with a budgeted LLM that can only cite real ids"
```

---

### Task 5: 실행 레저 + run_check 러너

**Files:**
- Create: `src/patrol/ledger.py`, `src/patrol/runner.py`
- Test: `tests/patrol/test_runner.py`

**Interfaces:**
- `LedgerPort(ABC)`: `record_run(gbm, fct, check, outcome: CheckOutcome) -> None`, `last_run(gbm, fct, check) -> CheckOutcome | None`, `consecutive_errors(gbm, fct, check) -> int`, `heartbeat(at: datetime) -> None`, `last_heartbeat() -> datetime | None`, `runs(gbm, fct, check, limit=50) -> list[CheckOutcome]`. `InMemoryLedger`.
- `run_check(gbm, fct, name, check: CheckConfig, *, adapters, store, clock, llm=None, budget: LlmBudget | None = None) -> CheckOutcome` — **절대 raise 금지**:
  1. 프로브 해석(`resolve_probe`)·실행 → `status=error`면 outcome error(원인 전달).
  2. 스냅샷 박제: `store.put_evidence(scratch_case_id(...), source=check.target or name, body=result.data, as_of=envelope.observed_at, complete=..., effective_as_of=...)` → `snap_id`.
  3. 판정:
     - `rule`: `judge_by_rule(result, check.params, clock)`; `KnownRuleError` → error outcome("rule 설정 오류 — ..."). finding이면 Finding(evidence_ids=[snap_id], judge="rule").
     - `llm`: `budget.try_acquire()` 실패 → `skipped`(reason "llm 예산 소진") — `on_budget_exhausted="escalate"`는 llm 단독엔 의미 없으므로 무시하고 skipped. 성공 시 `judge_by_llm([snap_id], {snap_id: repr(data)[:2000]}, ...)` → err면 error outcome; finding이면 Finding(judge="llm", evidence_ids=out.evidence_ids); `llm_calls=1`.
     - `rule+llm`: 룰 먼저. 룰 ok → ok. 룰 finding → 예산 획득 시 LLM 2차: finding 확정이면 Finding(judge="rule+llm", summary=LLM 요약), ok면 ok(reason 기록 없음 — outcome ok). 예산 소진: `skip`→skipped, `escalate`→룰 결과만으로 Finding(judge="rule+llm", summary=룰 reason + " (LLM 미확인 — 예산 소진)").
  4. Finding.id = `f"{name}@{observed_at.isoformat()}"`, observed_at = envelope.observed_at.
  - `llm`/`rule+llm` 인데 `llm is None` → error outcome("LLM 미주입").

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/patrol/test_runner.py`

```python
from datetime import datetime, timezone

from src.config.schema_site import CheckConfig
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds
from src.infrastructure.llm import ScriptedLLM
from src.patrol.ledger import InMemoryLedger
from src.patrol.llm_judge import LlmBudget
from src.patrol.runner import run_check
from tests.patrol.test_probes import _adapters

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _check(**kw):
    base = {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/oee",
            "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}}
    base.update(kw)
    return CheckConfig.model_validate(base)


async def test_rule_점검은_스냅샷을_먼저_박제하고_finding을_낸다():
    store = InMemoryCaseStore()
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    out = await run_check("mx", "gumi", "api.oee", _check(), adapters=adapters,
                          store=store, clock=lambda: T)
    assert out.status == "finding" and out.finding.judge == "rule"
    snap = out.finding.evidence_ids[0]
    assert store.has_evidence(out.finding.scratch_case_id, snap)
    assert store.get_evidence(out.finding.scratch_case_id, snap)["body"] == {"oee": 512}


async def test_프로브_실패는_finding이_아니라_error():
    out = await run_check("mx", "gumi", "k", _check(judge="rule", target=None,
                                                    probe="kafka_lag", params={"rule": "exists"}),
                          adapters=_adapters(StubSeeds()), store=InMemoryCaseStore(),
                          clock=lambda: T)
    assert out.status == "error" and out.finding is None


async def test_rule_llm은_룰_통과면_LLM을_안_부르고_소진시_정책을_따른다():
    adapters_ok = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 87}}))
    llm = ScriptedLLM([])                                  # 호출되면 RuntimeError → 테스트 실패
    out = await run_check("mx", "gumi", "c", _check(judge="rule+llm"), adapters=adapters_ok,
                          store=InMemoryCaseStore(), clock=lambda: T, llm=llm,
                          budget=LlmBudget(5, clock=lambda: T))
    assert out.status == "ok" and out.llm_calls == 0

    adapters_bad = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    exhausted = LlmBudget(0, clock=lambda: T)
    skipped = await run_check("mx", "gumi", "c", _check(judge="rule+llm"), adapters=adapters_bad,
                              store=InMemoryCaseStore(), clock=lambda: T, llm=llm, budget=exhausted)
    assert skipped.status == "skipped"
    escalated = await run_check("mx", "gumi", "c",
                                _check(judge="rule+llm", on_budget_exhausted="escalate"),
                                adapters=adapters_bad, store=InMemoryCaseStore(),
                                clock=lambda: T, llm=llm, budget=exhausted)
    assert escalated.status == "finding" and "예산" in escalated.finding.summary


async def test_llm_판정은_실재_id를_인용하고_레저가_기록한다():
    store, ledger = InMemoryCaseStore(), InMemoryLedger()
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 60}}))
    llm = ScriptedLLM(['{"status": "finding", "summary": "패턴 이상", "evidence_ids": ["ev-1"]}'])
    out = await run_check("mx", "gumi", "c", _check(judge="llm", params={}), adapters=adapters,
                          store=store, clock=lambda: T, llm=llm, budget=LlmBudget(5, clock=lambda: T))
    assert out.status == "finding" and out.llm_calls == 1 and out.finding.evidence_ids == ["ev-1"]
    ledger.record_run("mx", "gumi", "c", out)
    ledger.heartbeat(T)
    assert ledger.last_run("mx", "gumi", "c").status == "finding"
    assert ledger.last_heartbeat() == T and ledger.consecutive_errors("mx", "gumi", "c") == 0
```

- [ ] **Step 2~4**: FAIL → 구현(runner 최외곽 try/except → error outcome) → 전체 PASS → 커밋

```bash
git add src/patrol/ledger.py src/patrol/runner.py tests/patrol/test_runner.py
git commit -m "Run a check end to end: probe, snapshot, judge, budget, ledger"
```

---

### Task 6: 게이트 + 스케줄러

**Files:**
- Create: `src/patrol/gate.py`, `src/patrol/scheduler.py`
- Test: `tests/patrol/test_gate.py`, `tests/patrol/test_scheduler.py`

**Interfaces:**
- `AdmitResult(StrictModel)`: `action: Literal["opened","attached","rejected"]`, `case_id: str | None`, `reason: str | None`, `case: Case | None`(opened일 때 엔진에 넘길 도메인 Case).
- `admit_finding(finding: Finding, *, repo: CaseRepositoryPort, store: CaseStorePort, clock) -> AdmitResult`:
  1. **가드레일**: `finding.evidence_ids` 각각 `store.has_evidence(finding.scratch_case_id, id)` — 하나라도 없으면 `rejected("인용 스냅샷 부재: ...")`. 빈 인용도 rejected.
  2. `fp = fingerprint(...)`; `existing = repo.find_open_by_fingerprint(fp)`.
  3. existing → 스냅샷을 `existing.id`로 복사(`store.get_evidence_record`+`get_evidence` → `put_evidence(existing.id, ...)` 메타 보존), `finding_ids` 추가·`updated_at=clock()` 저장 → `attached`.
  4. 없음 → `case_id = repo.new_case_id()`, 스냅샷 복사, `CaseRecord(status="open", ...)` 저장, `Case(id, gbm, fct, origin="patrol", symptom=finding.summary, t0=finding.observed_at, target_locator=finding.target)` → `opened`.
- `interval_seconds(spec: str) -> int` — `"30s"/"5m"/"1h"`.
- `build_trigger(schedule: Schedule)` — `IntervalTrigger(seconds=...)` | `CronTrigger.from_crontab(cron)`.
- `build_scheduler(sites: list[tuple[str, str, SiteConfig]], *, run_one: Callable, heartbeat: Callable, heartbeat_seconds: int = 60, timezone: str) -> AsyncIOScheduler` — enabled 사이트의 점검마다 `add_job(run_one, trigger, args=[gbm, fct, name, check], id=f"{gbm}/{fct}/{name}", max_instances=1, coalesce=True)` + 하트비트 잡(`id="heartbeat"`). **시작하지 않는다**(start는 4b 데몬이). `run_one`/`heartbeat`는 async 콜러블.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/patrol/test_gate.py`:
```python
from datetime import datetime, timezone

from src.domain.cases import InMemoryCaseRepository
from src.domain.patrol import Finding
from src.domain.store import InMemoryCaseStore
from src.patrol.gate import admit_finding

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _finding(store, summary="OEE 512%"):
    snap = store.put_evidence("patrol:mx:gumi:api.oee", "rest:/oee", {"oee": 512}, as_of=T)
    return Finding(id=f"api.oee@{T.isoformat()}", gbm="mx", fct="gumi", check="api.oee",
                   target="rest:/oee", summary=summary, evidence_ids=[snap],
                   scratch_case_id="patrol:mx:gumi:api.oee", observed_at=T, judge="rule")


def test_첫_finding은_케이스를_열고_스냅샷을_T0_증거로_복사한다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    result = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    assert result.action == "opened" and result.case.origin == "patrol"
    assert result.case.t0 == T and result.case.target_locator == "rest:/oee"
    assert store.list_evidence(result.case_id)[0].as_of == T       # 메타 보존 복사


def test_같은_지문의_열린_케이스에는_첨부한다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    first = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    second = admit_finding(_finding(store, "OEE 530%"), repo=repo, store=store, clock=lambda: T)
    assert second.action == "attached" and second.case_id == first.case_id
    assert len(repo.get(first.case_id).finding_ids) == 2
    assert len(store.list_evidence(first.case_id)) == 2


def test_인용_스냅샷이_없으면_기각():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    f = _finding(store).model_copy(update={"evidence_ids": ["ev-99"]})
    result = admit_finding(f, repo=repo, store=store, clock=lambda: T)
    assert result.action == "rejected" and repo.list_by_status("open") == []
```

`tests/patrol/test_scheduler.py`:
```python
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config.schema_site import Schedule, SiteConfig
from src.patrol.scheduler import build_scheduler, build_trigger, interval_seconds


def test_interval_파싱과_트리거_생성():
    assert (interval_seconds("30s"), interval_seconds("5m"), interval_seconds("1h")) == (30, 300, 3600)
    assert isinstance(build_trigger(Schedule(interval="5m")), IntervalTrigger)
    assert isinstance(build_trigger(Schedule(cron="0 8,20 * * *")), CronTrigger)


def test_점검마다_잡이_등록되고_하트비트가_붙는다():
    site = SiteConfig.model_validate({"target": {"rest": {"base_url": "http://x"}}, "patrol": {"checks": {
        "a": {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/oee"},
        "b": {"judge": "llm", "schedule": {"cron": "0 8 * * *"}, "target": "rest:/oee"}}}})

    async def run_one(gbm, fct, name, check): ...
    async def heartbeat(): ...

    sched = build_scheduler([("mx", "gumi", site)], run_one=run_one, heartbeat=heartbeat,
                            timezone="Asia/Seoul")
    ids = {job.id for job in sched.get_jobs()}
    assert ids == {"mx/gumi/a", "mx/gumi/b", "heartbeat"}
    job = sched.get_job("mx/gumi/a")
    assert job.max_instances == 1 and job.coalesce is True
    assert not sched.running
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/patrol/gate.py src/patrol/scheduler.py tests/patrol/test_gate.py tests/patrol/test_scheduler.py
git commit -m "Admit findings through the fingerprint gate and schedule the checks"
```

---

## 완료 기준 (계획 4a)

- `.venv/bin/pytest` 전체 통과.
- config의 점검 정의 → 스케줄러 잡 등록 → `run_check`가 스텁 위에서 3상 결과 → Finding → `admit_finding`이 케이스를 열고(중복은 첨부) 엔진용 `Case`를 돌려주는 사슬이 결정론으로 검증됨.
- LLM 판정기가 코드가 준 스냅샷 id 밖의 인용을 할 수 없고, 예산 소진이 조용히 생략되지 않고 `skipped`로 남음.

## 계획 4b 예고

케이스 큐·동시 조사 워커(owner/lease, 단일 실행자), 수명주기 전이(open→investigating→awaiting_human→closed, 타임아웃 종결 = 케이스 닫기+스레드 폐기 — 3b 이월), Mongo 케이스 Store·케이스 저장소·레저·체크포인터(pymongo<4.17 핀 확인, 스키마 버전 스탬프·재개 실패 정책 F3), 보존 정리 잡, `patrol` 데몬 CLI(`patrol status` 하트비트), 내장 자기 감시 점검(연속 error N회).
