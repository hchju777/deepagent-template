# 계획 16 — Fleet 집계(P7): 선언적 시나리오 · 팬아웃 · MetricRollup · HTML 발행

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 방향 문서의 P7 — 사이트를 가로질러 지표를 모아 HTML 리포트로 발송한다. v2의 마지막 축이고, 세 타깃 중 **유일하게 모양이 다른 것**이다(증상도 조사도 판정도 없다).

**Architecture:** 집계는 `Case`가 아니다. 방향 문서 §31~34가 왜 안 되는지 네 가지로 못박았다 — `requeue_open`이 집계 레코드마다 LLM 그래프를 돌리고, `Verdict`가 전부 "조사 실패" 낙인을 찍고, `symptom`/`t0`/`fingerprint`에 정직한 값이 없어 발명해야 하고, 사이트 없는 지문이 실제 finding과 충돌한다. 그래서 **별도 1급 개념**: `config/scenarios/{name}.json`(층 병합 없음) → 팬아웃 → `MetricRollup`(정직성을 **타입이** 강제) → HTML → 메일.

**정직성이 이 계획의 전부다.** "알람 12% 감소"가 실은 "3개 법인 누락"인 사고를 막는 것이 목적이고, 그것을 문서가 아니라 pydantic validator가 막는다. 커버리지는 **숫자보다 반드시 앞에** 렌더된다.

**Tech Stack:** pydantic StrictModel, APScheduler(기존 `build_scheduler`에 전역 잡 추가), 기존 프로브·해석기·`get_path`·`Schedule`·메일 2상 레저 재사용.

## Global Constraints

- 무raise(규율 1): 사이트 하나의 실패는 그 사이트의 **커버리지 항목**이지 집계의 죽음이 아니다. 팬아웃·수집·감축·렌더·발행 어디도 raise하지 않는다.
- 시계 주입(규율 2): `datetime.now()` 금지. `window_from`/`window_to`는 주입된 시계로 찍는다.
- 상한은 코드가 쥔다(규율 6): `max_parallel_sites`(config가 값을 주되 코드가 **반드시** 세마포어로 강제), `reduce` 6종 Literal, `sample` 상한. 사용자 표현식 DSL 금지(감사 불가능).
- 이벤트 어휘 6종 불변(규율 7): **집계는 `EngineEvent`를 내지 않는다** — 엔진 산출물이 아니다. 관측은 레저(`CheckLedgerPort`)와 stdout.
- 읽기 전용(규율 9): 집계도 등재 항목·기존 프로버만 쓴다. 새 메서드를 포트에 만들지 않는다.
- StrictModel(규율 5), 주석·문서 한국어(WHY만), 커밋 영어 + `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 테스트: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`. RED 먼저, 각 픽스는 되돌려 확인(`-B` 필수 — pyc 함정).

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `src/config/schema_scenario.py` | `ScenarioConfig`·`ScenarioScope`·`MetricSpec`·`OutputSpec` | 신설 |
| `src/config/loader.py` | `load_scenarios(config_root, env)` — 단독 검증, 층 병합 없음 | 수정 |
| `src/config/schema_site.py` | `SitePatrol.scenarios: dict[str, SiteScenarioOverride]` | 수정 |
| `src/domain/rollup.py` | `MetricRollup`(4 validator)·`SiteCoverage`·`FleetReport`·`DigestStorePort` | 신설 |
| `src/fleet/reduce.py` | `extract`(get_path 재사용) + `reduce` 6종 | 신설 |
| `src/fleet/collect.py` | 사이트 하나에서 표본 하나 — 기존 프로브·해석기 재사용 | 신설 |
| `src/fleet/run.py` | 팬아웃(`max_parallel_sites`)·창·커버리지·롤업 조립·digest | 신설 |
| `src/presentation/fleet_report.py` | HTML·MD — 커버리지가 숫자보다 앞 | 신설 |
| `src/infrastructure/mongo_store.py` | `MongoDigestStore` + 인덱스 | 수정 |
| `src/patrol/daemon.py` | 시나리오 잡 등록(전역 1회)·발행 배선 | 수정 |
| `src/boot.py` | 시나리오 검증 항목 | 수정 |
| `src/__main__.py` | `scenario run` / `scenario list` | 수정 |
| `src/api/routes_reads.py` | `GET /digests/{scenario}` | 수정 |

---

### Task 1: `MetricRollup` — 정직성을 타입이 강제한다

**Files:** Create `src/domain/rollup.py` · Test `tests/domain/test_rollup.py`

**Interfaces:**
```python
class SiteCoverage(StrictModel):
    gbm: str; fct: str
    status: Literal["covered", "missing", "fallback"]
    reason: str | None = None          # missing/fallback이면 필수
    last_success_at: datetime | None = None

class MetricRollup(StrictModel):
    metric: str
    value: float | None
    reduce: Literal["sum", "avg", "max", "min", "count", "count_nonzero"]
    expected_sites: int
    covered_sites: int
    complete: bool
    coverage_note: str | None = None
    unit: str | None = None

class FleetReport(StrictModel):
    scenario: str; title: str; concern: Concern
    scenario_digest: str
    window_from: datetime; window_to: datetime
    coverage: list[SiteCoverage]
    rollups: list[MetricRollup]
    groups: dict[str, list[MetricRollup]] = {}     # group_by 축 분해
    previous_digest: str | None = None             # 추세 비교 대상
    trend: dict[str, float | None] = {}            # metric → 전회 대비 차이
    trend_caveat: str | None = None                # digest 불일치면 비교 불가
    generated_at: datetime
```

- [ ] **Step 1: 실패하는 테스트** — 방향 문서 §327의 네 validator를 각각:

```python
def test_누락이_있으면_complete가_강제로_False다():
    r = MetricRollup(metric="alarms", value=12.0, reduce="sum",
                     expected_sites=30, covered_sites=27, complete=True,
                     coverage_note="3개 사이트 미확인")
    assert r.complete is False           # 입력이 True여도 강제된다

def test_불완전은_사유를_요구한다():
    with pytest.raises(ValidationError):
        MetricRollup(..., covered_sites=27, expected_sites=30, complete=False)  # note 없음

def test_커버가_0이면_값은_None이다():
    # 0이 아니다 — "아무 데서도 못 읽었다"와 "전부 0이었다"는 다른 주장이다.
    with pytest.raises(ValidationError):
        MetricRollup(..., covered_sites=0, value=0.0, ...)

def test_표본에_불완전이_하나라도_있으면_전체가_불완전이다():
    # AND-fold — fold_complete([True, False, True]) is False
```

- [ ] **Step 2: RED** → **Step 3:** 구현. `SiteCoverage`도 `_missing_needs_reason`. `DigestStorePort`(`put`/`latest(scenario)`/`list(scenario, limit)`/`prune_before`) + InMemory.
- [ ] **Step 4: GREEN + 돌연변이**(각 validator 제거) → **Step 5: 커밋** `"Let the type refuse a rollup that hides a gap"`.

---

### Task 2: `extract`와 `reduce` — 순수 함수 6종

**Files:** Create `src/fleet/reduce.py` · Test `tests/fleet/test_reduce.py`

**Interfaces:** `extract(payload, dotted) -> list[float]`(리스트면 각 항목에서, 스칼라면 하나), `reduce_values(values, how) -> float | None`, `REDUCERS: dict[str, ...]`.

- [ ] **Step 1: 실패하는 테스트** — 6종 각각의 값, 빈 목록은 `None`(0이 아니다), 숫자가 아닌 값은 **건너뛰지 말고** 세어서 호출부가 불완전으로 판정하게(`extract`가 `(values, skipped)`를 돌려준다), 점 경로는 `rules.get_path` 재사용, `count_nonzero`와 `count`의 차이.
- [ ] **Step 2: RED** → **Step 3:** 구현. DSL 금지 — `Literal` 6종뿐이고 새 종류는 스펙 갱신 뒤에.
- [ ] **Step 4: GREEN + 돌연변이**(빈 목록→0, 비수치 조용히 스킵) → **Step 5: 커밋** `"Reduce a sample six ways, and never turn an empty one into zero"`.

---

### Task 3: 시나리오 스키마와 로더

**Files:** Create `src/config/schema_scenario.py` · Modify `src/config/loader.py`, `src/config/schema_site.py` · Test `tests/config/test_scenario_config.py`, `tests/config/test_loader.py`

**Interfaces:**
```python
class MetricSpec(StrictModel):
    target: str | None = None; probe: str | None = None
    params: dict[str, Any] = {}; body: dict[str, Any] = {}
    resolve: dict[str, ResolverSpec] = {}
    sample: int | None = None
    extract: str                       # 점 경로
    reduce: Literal[6종]
    window: str | None = None          # "24h" 등 — 표본이 무엇을 물었는지 보고서에 적는다
    required: bool = True              # False면 이 지표의 누락이 커버리지에서 빠진다
    unit: str | None = None

class ScenarioScope(StrictModel):
    sites: Literal["all"] | list[str] = "all"     # "gbm/fct" 문자열
    exclude: list[str] = []
    max_parallel_sites: int = 4                   # 코드가 세마포어로 강제한다

class OutputSpec(StrictModel):
    format: Literal["html", "md"] = "html"
    output_dir: str = "output/fleet"
    mail: bool = False

class ScenarioConfig(StrictModel):
    kind: Literal["aggregate"]
    concern: Concern
    enabled: bool = True
    title: str
    schedule: Schedule                 # 기존 재사용 — interval xor cron 검증이 공짜
    scope: ScenarioScope = ScenarioScope()
    metrics: dict[str, MetricSpec]     # 이름→스펙(list 아님 — 사이트별 편집이 가능해야)
    group_by: list[str] = []
    output: OutputSpec = OutputSpec()

def load_scenarios(config_root: Path, env: dict) -> dict[str, ScenarioConfig]
class SiteScenarioOverride(StrictModel):
    enabled: bool | None = None        # 사이트별 옵트아웃만. 지표 재정의는 하지 않는다
```

- [ ] **Step 1: 실패하는 테스트** — `config/scenarios/*.json` 각각 단독 검증(오류 메시지에 파일명), 알 수 없는 키 거부(StrictModel), `metrics`가 비면 거부, `max_parallel_sites <= 0` 거부, 디렉터리가 없으면 빈 dict(시나리오는 선택 기능), env 참조 치환(`${...}`)이 실제로 일어남(계획 1의 반복 실패 유형), `SitePatrol.scenarios`의 `enabled: false`가 그 사이트를 제외.
- [ ] **Step 2: RED** → **Step 3:** 구현. 층 병합 없음(`registry.json`과 같은 형태).
- [ ] **Step 4: GREEN + 돌연변이**(env 미치환, 빈 metrics 허용) → **Step 5: 커밋** `"Declare a fleet scenario in its own file, validated alone"`.

---

### Task 4: 사이트 하나에서 표본 — 기존 프로브를 그대로 쓴다

**Files:** Create `src/fleet/collect.py` · Test `tests/fleet/test_collect.py`

**Interfaces:** `async collect_site(spec, *, gbm, fct, adapters, clock) -> SiteSample`
`SiteSample(values: list[float], skipped: int, status: Literal["covered","missing","fallback"], reason: str | None, effective_as_of: datetime | None)`.

- [ ] **Step 1: 실패하는 테스트**
  - 프로브 성공 → `covered`, 값이 `extract`로 뽑힌다.
  - 프로브 error → `missing` + 사유(예외 문구가 아니라 `ProbeResult.error`).
  - 해석기가 값을 못 내면(전부-또는-전무) 호출 자체를 안 하고 `missing`.
  - `effective_as_of`가 요청 창보다 오래됐으면 `fallback`(방향 문서의 커버리지 표 셋째 줄).
  - **절대 raise하지 않는다** — 어댑터가 던져도 `missing`.
  - `sample` 상한이 실제로 자른다.
- [ ] **Step 2: RED** → **Step 3:** 구현(`StubSeeds`로 테스트).
- [ ] **Step 4: GREEN + 돌연변이** → **Step 5: 커밋** `"Take one site's sample, and say why when there is none"`.

---

### Task 5: 팬아웃과 롤업 — 상한은 코드가 쥔다

**Files:** Create `src/fleet/run.py` · Test `tests/fleet/test_run.py`

**Interfaces:** `async run_scenario(name, scenario, *, sites, adapters_for_site, clock, digests=None) -> FleetReport`
`scenario_digest(scenario) -> str`(canonical JSON의 sha256 앞 16자 — `knowledge` digest와 같은 관용구).

- [ ] **Step 1: 실패하는 테스트**
  - **동시 사이트 수가 `max_parallel_sites`를 넘지 않는다**(진행 중 카운터의 최대값을 재는 가짜 어댑터). 규율 6의 핵심.
  - 사이트 하나가 죽어도 나머지가 집계된다(무raise).
  - `expected_sites`는 scope로 좁힌 뒤의 수, `covered_sites`는 실제 성공 수.
  - 전부 실패 → `value is None`, `complete=False`, 사유가 커버리지에 사이트별로.
  - `window_from`/`window_to`가 첫·마지막 표본의 시각(고정 시계면 같고, 진행하는 시계면 벌어진다).
  - `group_by`가 축 분해를 만든다(사업부별 표).
  - digest가 시나리오 내용에 반응하고(extract 한 글자만 바꿔도 달라진다) `enabled`·`title` 같은 표현 필드에는 반응하지 않는다.
  - 이전 실행 digest가 다르면 `trend_caveat`이 붙고 `trend`는 비어 있다.
- [ ] **Step 2: RED** → **Step 3:** 구현. `asyncio.Semaphore(max_parallel_sites)`.
- [ ] **Step 4: GREEN + 돌연변이**(세마포어 제거, expected를 covered로, digest에 title 포함, caveat 생략) → **Step 5: 커밋** `"Fan out across sites under a bound the code holds"`.

---

### Task 6: 리포트 — 커버리지가 숫자보다 앞에 온다

**Files:** Create `src/presentation/fleet_report.py` · Test `tests/presentation/test_fleet_report.py`

- [ ] **Step 1: 실패하는 테스트**
  - HTML·MD 둘 다: **커버리지 블록이 지표 표보다 먼저** 나온다(`index()` 비교로 단정).
  - 미확인 사이트가 사유·마지막 성공 시각과 함께 나온다.
  - `complete=False`인 지표에 경고 표식과 `coverage_note`.
  - `value is None`이면 "—"이지 `0`이 아니다.
  - 창(`window_from ~ window_to`)과 최대 편차, `scenario_digest`가 헤더에.
  - `trend_caveat`이 있으면 추세 표 대신 그 문장.
  - 대상 문자열 이스케이프(HTML).
- [ ] **Step 2: RED** → **Step 3:** 구현(`report_html`의 `_e`·`_table` 관용구 재사용).
- [ ] **Step 4: GREEN + 돌연변이**(순서 뒤집기, None→0) → **Step 5: 커밋** `"Render coverage before the numbers it qualifies"`.

---

### Task 7: 발행과 스케줄 — 파일 먼저, 그다음 메일

**Files:** Modify `src/patrol/daemon.py` · Test `tests/patrol/test_daemon.py`

- [ ] **Step 1: 실패하는 테스트**
  - 시나리오 잡이 **전역 1회** 등록된다(사이트 수와 무관 — 방향 문서 §344의 근거).
  - 실행하면 파일이 먼저 쓰이고, 메일이 켜져 있으면 2상 레저(`record_send`→`mark_sent`)를 탄다.
  - 메일 실패가 파일을 되돌리지 않는다.
  - 레저에 실행 기록(`CheckOutcome`)이 남는다 — **`EngineEvent`는 안 낸다**(규율 7).
  - `enabled=false` 시나리오는 잡이 없다.
  - 실행이 던져도 데몬은 산다(무raise).
- [ ] **Step 2: RED** → **Step 3:** 구현. `DigestStorePort.put`으로 실행 기록.
- [ ] **Step 4: GREEN + 돌연변이**(사이트마다 등록, 메일 먼저) → **Step 5: 커밋** `"Schedule the rollup once for the fleet, and publish the file first"`.

---

### Task 8: CLI와 API

**Files:** Modify `src/__main__.py`, `src/api/routes_reads.py`, `src/api/assembly.py`, `src/boot.py` · Test `tests/test_cli.py`, `tests/api/test_routes_reads.py`, `tests/test_boot.py`

- [ ] **Step 1: 실패하는 테스트**
  - `scenario list` — 이름·제목·스케줄·사이트 수.
  - `scenario run <name>` — 한 번 돌리고 보고서 경로를 출력, 없는 이름은 exit 1.
  - `GET /digests/{scenario}` — 실행 기록(최신순, digest·창·커버리지 요약). 접근 검사는 `sites_for`가 아니라 **시나리오 단위**(사이트를 가로지르므로) — 주체가 시나리오 scope의 사이트를 **하나라도** 못 보면 404.
  - boot: 시나리오의 `target`이 등재 항목·토폴로지에 실재하는지, `scope.sites`가 registry에 있는지, `extract`가 비지 않았는지. 문제는 전부 모아서 보고(기동 거부 철학).
- [ ] **Step 2: RED** → **Step 3:** 구현.
- [ ] **Step 4: GREEN + 돌연변이** → **Step 5: 커밋** `"Run a scenario from the CLI, and read its runs over HTTP"`.

---

### Task 9: 예시 시나리오와 벤치

**Files:** Create `config/scenarios/alarm_trend.json`(예시) · Modify `tests/test_bench_scenarios.py` · Test 위와 같음

- [ ] **Step 1:** 방향 문서의 타깃 (1)을 실제 시나리오로 적는다(Alarm Trend: 사이트별 알람 수 → sum, 사업부 분해). 벤치에 **집계 시나리오 하나**를 추가: 3 사이트 중 하나가 죽은 상태로 돌려 `complete=False`·커버리지 2/3·값이 나머지 둘의 합인지 구조화 필드로 채점(텍스트 매칭 금지).
- [ ] **Step 2~5:** RED → GREEN → 커밋 `"Score a fleet rollup with one site down"`.

---

### Task 10: 문서

`docs/architecture.md`(Fleet 층 — 왜 Case가 아닌가, 타입이 강제하는 정직성, 상한), `docs/config-reference.md`(`scenarios/*.json` 전 필드), `docs/howto.md`(`scenario run`, 리포트 읽는 법), `docs/glossary.md`(**MetricRollup**·**커버리지**·**scenario_digest**), `CLAUDE.md` 코드 지도, `tests/README.md`, 계획서 인계.

- [ ] 문서가 부른다고 적은 함수는 `grep`으로 호출부 확인 뒤 쓴다. → 커밋 `"Document the fleet rollup and what its types refuse"`.

---

## 자기 검토

- **커버리지**: 방향 문서 §309(리포트 형태) → Task 6; §327(4 validator) → Task 1; §340(digest·추세) → Task 5·6; §342~346(스키마·배치·근거) → Task 3; §364(`max_parallel_sites`) → Task 5; §378(`GET /digests`) → Task 8; §204(`DigestStorePort`) → Task 1·7; §31~34(Case가 아닌 이유) → 전체 구조.
- **뺀 것(YAGNI)**: 범용 집계 DSL(감사 불가능), 집계 결과의 LLM 서술(결정론 가드레일을 새로 발명해야 한다), 집계 전용 이벤트 종류(규율 7), 사이트 간 클럭 스큐 보정(보정이 아니라 **표시**가 이 시스템의 방식이다).
- **가장 위험한 지점**: 커버리지가 숫자보다 뒤에 오거나, `covered_sites == 0`인데 값이 `0`으로 나가는 것. 둘 다 "12% 감소"가 "3개 법인 누락"인 사고의 형태다 — validator와 렌더 순서 테스트가 방어선이다.

## 인계(계획 16 이후)

1. **축 분해는 `group_by == ["gbm"]`만 지원한다** — 다른 값(`["fct"]`, `["gbm","fct"]`)은
   **조용히 빈 dict**가 된다. boot 검증도 없다. 축을 늘릴 때 `_groups`를 일반화하고
   축 조합의 카디널리티 상한을 코드가 쥐어야 한다.
2. **카디널리티 정직성이 절반이다** — `sample`은 프로브에 전달되지만 "5,000개 중 50개를
   봤다"를 리포트에 적는 데까지 가지 않았다(방향 문서 §110). `skipped`는 사유에 적힌다.
3. **retention이 `fleet_runs`를 안 걷는다** — `DigestStorePort.prune_before`는 있는데
   `sweep_retention`이 호출하지 않는다. knob도 없다.
4. **`scenario run`은 기동 검증을 안 탄다** — scope의 오타난 사이트가 분모를 조용히
   줄인다(`patrol run`·`api`는 boot이 잡는다).
5. **`/digests`는 실행 기록이 없으면 접근 검사를 건너뛴다** — 미인가 주체가 "기록 있음"과
   "없음"을 404/200으로 구별할 수 있다(미세 누출).
6. **`api`가 시나리오 선언 자체를 안 낸다** — 웹 UI가 목록을 그리려면 `GET /scenarios`가
   필요하다.
7. **`gather(return_exceptions=True)`가 삼킨 예외의 내용이 어디에도 안 남는다** — 사이트는
   커버리지에 `missing`으로 드러나지만 원인 문자열은 없다. 실질 대상은
   `adapters_for_site`뿐이다(`collect_site`는 이미 무raise).
8. **`MongoDigestStore.prune_before`는 naive datetime에 `TypeError`다** — 호출부가 없어
   지금은 도달 불가.
9. **`extract`의 숫자 세그먼트는 리스트 길이에 따라 의미가 바뀐다** — 유효 범위면
   인덱스, 아니면 dict 키 팬아웃이다. 실 config에 숫자 경로가 없어 지금은 도달 불가지만,
   쓰기 시작하면 행 수가 다른 두 사이트가 **같은 경로를 다르게 계산**하고 리포트가 그
   사실을 말하지 않는다. 정직한 종착점은 명시 문법(`rows[0].n`)이거나 인덱스 지원 제거다.
10. **전부-선택 시나리오에서 죽은 사이트가 `covered`로 렌더된다** — "필수가 없으면 gap이라
    부를 것이 없다"는 논리는 맞지만 "전 사이트 확인됨"은 도달 가능성 주장으로 읽힌다.
    `required`의 기본값이 `true`라 모든 지표를 명시적으로 꺼야 도달한다.

## 집행 중 검증 리뷰가 잡은 것(기록)

- **블로커 2건**: `patrol run`이 시나리오·실행 기록을 데몬에 안 넘겨 **집계가 프로덕션에서
  한 번도 안 돌았다**(CLAUDE.md가 이름 붙인 `resume_once` 유형의 재발). 커밋된 예시
  시나리오가 기본 config 루트의 기동을 막았다(`config.example/`로 이동).
- **선택 지표가 커버리지를 거꾸로 만들었다** — 한 리포트가 "커버리지 0/3"과 "지표 3/3
  완전"을 동시에 말했다.
- **"완전"과 "—"가 나란히 섰다** — 전 사이트가 답했고 행이 0건인 흔한 경우에 대시를
  설명하는 문장이 없었다. 합·건수는 관측된 0으로, 평균·최대·최소는 사유와 함께 None으로.
- `digest`가 `max_parallel_sites`에 반응해 부하 knob을 올린 운영자가 영구히 "추세 비교
  불가"를 받았다. 사이트 옵트아웃(`patrol.scenarios`)은 파싱만 되고 소비자가 0이었다.
- `extract`의 인덱스 세그먼트 분기가 죽은 코드였고 `get_path`는 import만 돼 있었다.
