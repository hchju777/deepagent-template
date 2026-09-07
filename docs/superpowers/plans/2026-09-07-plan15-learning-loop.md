# 계획 15 — 학습 루프(P8): 관측성 · 이력 provider · 케이스 라벨 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 방향 문서의 P8(Wave 4) — 조사의 경과·실패를 측정해 보고서 푸터에 정직하게 싣고(`Ticker`·`MetricsSinkPort`), 과거 종결 케이스를 결정론 tier 검색으로 frame에 먹이고(`history_provider`), 사람이 실제 원인을 되먹이는 입구(`RootCauseLabel`·`case label`·`POST /cases/{id}/label`)를 연다.

**Architecture:** 세 갈래가 한 계획인 이유는 **셋이 같은 한 쌍을 공유하기 때문이다** — `VerdictSnapshot`(기계가 뭐라 했나)과 `RootCauseLabel`(실제로 뭐였나). 이력 검색이 frame에 무엇을 먹였는지(`history_shown`)를 스냅샷에 남기지 않으면 "이력이 도움이 됐나, 앵커링이었나"를 영원히 못 묻고, 관측치를 안 남기면 "느린 조사가 더 틀리나"를 못 묻는다. 셋 다 **종결 시점에만 공짜이고 나중엔 복구 불가**다(retention이 90일에 `Verdict`·증거·case_file을 지운다).

**계산은 이 계획의 범위가 아니다.** 라벨 `n ≥ 30` **그리고** 라벨률 > 50% 전에는 어떤 정확도도 내지 않는다 — 게이트를 코드로 박고, 게이트가 열리기 전에는 **건수만** 보고한다(맨 퍼센트 금지).

**Tech Stack:** pydantic StrictModel, `time.perf_counter`(CLI 경계 주입), mongomock 계약 테스트, FastAPI, 기존 `upstream_slice`.

## Global Constraints

- 무raise(규율 1): 이력 검색·관측치 기록·라벨 저장 어느 것도 조사나 발행을 막지 않는다. 실패는 반환값의 상태이거나 조용히 건너뛰되 **보고서에 그 사실이 보여야 한다**(조용한 생략 금지).
- 시계 주입(규율 2): `datetime.now()`는 `src/__main__.py`에서만. **`Ticker`는 시계가 아니다** — 경과 시간은 단조 소스(`time.perf_counter`)에서 오고, CLI 경계에서 별도 주입한다(방향 문서 N6).
- LLM 인용 불신(규율 3): **브리핑에 과거 케이스의 evidence id를 절대 렌더하지 않는다.** 과거 증거도 `ev-2` 형태이고 이번 케이스에도 `ev-2`가 있어, 리드가 과거 id를 인용하면 `verify`의 인용 우주(`state.evidence`)를 그대로 통과한다. 이 계획에서 가장 위험한 지점이고 렌더러 단위 테스트로 고정한다.
- 통제 경계(규율 6): tier 규칙·K건 상한·라벨 4분류·게이트 임계(30건/50%)는 **코드**가 쥔다. LLM은 이력을 읽고 무엇을 조사할지만 정한다.
- StrictModel(규율 5), 이벤트 어휘 6종 불변(규율 7 — 라벨은 이벤트가 아니다).
- 주석·문서 한국어(WHY만), 커밋 메시지 영어, 커밋 끝 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 테스트: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`. RED를 실제로 본 뒤 GREEN. 각 픽스는 되돌려 확인(**`-B` 또는 `__pycache__` 삭제** — `tests/README.md`의 pyc 함정).

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `src/application/lifecycle.py` | `Ticker` 타입 별칭 | 수정 |
| `src/patrol/ledger.py` | `MetricsSinkPort`(신설, 3분할 완성) + InMemory | 수정 |
| `src/infrastructure/mongo_store.py` | `MongoLedger.record_metric`, `metrics` 컬렉션·인덱스, `closed_by_*` 조회, 라벨 저장소 | 수정 |
| `src/domain/cases.py` | `closed_by_fingerprint` / `closed_by_locators` 포트 + InMemory | 수정 |
| `src/domain/label.py` | `RootCauseLabel`, `LabelStorePort`, InMemory | 신설 |
| `src/application/history.py` | tier 1~4 검색 + 렌더러(evidence id 금지) | 신설 |
| `src/application/deps.py` | `EngineDeps.history_provider` | 수정 |
| `src/application/nodes.py` | frame이 provider를 호출 | 수정 |
| `src/application/intake.py` | 접수 완료 시 chat 지문 재계산 | 수정 |
| `src/application/worker.py` | Ticker로 경과 측정 → case_file·MetricsSink·스냅샷(`history_shown`) | 수정 |
| `src/domain/snapshot.py` | `duration_s` | 수정 |
| `src/domain/report_model.py` | `Observability` + 라벨 유입구 데이터 | 수정 |
| `src/presentation/report.py`, `report_html.py` | 푸터(관측성 요약 + 라벨 명령 한 줄) | 수정 |
| `src/application/labels.py` | `submit_label`(CLI·API 공유) + `label_stats`(게이트) | 신설 |
| `src/api/routes_cases.py`, `models.py` | `POST /cases/{id}/label` | 수정 |
| `src/__main__.py` | `case label`, Ticker 주입, provider 배선 | 수정 |
| `src/patrol/daemon.py` | provider·Ticker·라벨 저장소 배선 | 수정 |
| `src/infrastructure/checkpointer.py` | `Persistence.labels` | 수정 |
| `src/boot.py` | (없음 — 새 config 없음) | — |

---

### Task 1: `Ticker` — 경과 시간은 시계가 아니다

**Files:** Modify `src/application/lifecycle.py`, `src/__main__.py`, `src/patrol/daemon.py` · Test `tests/application/test_lifecycle.py`

**Interfaces:** Produces `Ticker = Callable[[], float]`(단조 초). 프로덕션은 `time.perf_counter`, 테스트는 고정 리스트를 소비하는 가짜.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_Ticker는_시계와_다른_양이다():
    # 방향 문서 N6: 고정 시계에서 duration이 0이 나오는 것은 둘을 한 포트로 섞었기 때문이다.
    from src.application.lifecycle import Ticker
    ticks = iter([10.0, 12.5])
    tick: Ticker = lambda: next(ticks)
    assert round(tick() - tick(), 1) == -2.5
```

- [ ] **Step 2: RED**(ImportError) → **Step 3:** `lifecycle.py`에 `Ticker = Callable[[], float]`와 WHY 주석(단조·재현 불가가 정상, `datetime.now()` 금지의 목적은 *기록 시점*의 감사이지 경과가 아니다).
- [ ] **Step 4: GREEN** → **Step 5: 커밋** `"Name the monotonic elapsed-time source, apart from the clock"`.

---

### Task 2: `MetricsSinkPort` — LedgerPort 3분할의 마지막 조각

**Files:** Modify `src/patrol/ledger.py`, `src/infrastructure/mongo_store.py`(`ensure_indexes`, `MongoLedger`) · Test `tests/patrol/test_ledger.py`, `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
```python
class MetricsSinkPort(ABC):
    @abstractmethod
    def record_metric(self, name: str, value: float, *, tags: dict[str, str], at: datetime) -> None: ...
    @abstractmethod
    def metrics(self, name: str, *, limit: int = 200) -> list[dict]: ...
    @abstractmethod
    def prune_metrics_before(self, before: datetime) -> int: ...
class LedgerPort(CheckLedgerPort, SendLedgerPort, MetricsSinkPort): ...
```

- [ ] **Step 1: 실패하는 테스트** — InMemory: 기록·조회 왕복, 이름별 분리, `limit`. Mongo(mongomock): 같은 왕복 + `prune_metrics_before`가 오래된 것만 지운다. **`record_metric`은 raise하지 않는다**(sink 장애가 조사를 막지 않는다 — Mongo 구현이 내부에서 삼키고 세는 게 아니라, 호출부가 감싼다는 계약을 여기 주석으로 못 박는다: 포트는 정직하게 던지고 **워커가 감싼다**).
- [ ] **Step 2: RED** → **Step 3:** 구현. `ensure_indexes`에 `db.metrics.create_index([("name", 1), ("at", -1)])`, `db.metrics.create_index("at")`. retention의 `ledger_d`를 metrics에도 적용(`sweep_retention`에 `metrics` 카운트 추가).
- [ ] **Step 4: GREEN + 돌연변이**(이름 필터 제거, prune 경계 뒤집기) → **Step 5: 커밋** `"Split the metrics sink out of the ledger port"`.

---

### Task 3: 조사 관측치 — 워커가 재고, 보고서 푸터가 말한다

**Files:** Modify `src/application/worker.py`, `src/domain/report_model.py`, `src/domain/snapshot.py`, `src/presentation/report.py`, `report_html.py` · Test `tests/application/test_worker.py`, `tests/domain/test_report_model.py`, `tests/presentation/*`

**Interfaces:**
```python
class Observability(StrictModel):
    duration_s: float | None       # None = 미측정(옛 스냅샷·Ticker 미주입)
    rounds: int | None
    tool_failures: int             # 태스크 status="error" 수
    unmeasured: list[str]          # 예: ["토큰"] — 못 잰 것을 이름으로 말한다
ReportModel.observability: Observability
VerdictSnapshot.duration_s: float | None = None
InvestigationWorker(..., ticker: Ticker | None = None, metrics: MetricsSinkPort | None = None)
```

- [ ] **Step 1: 실패하는 테스트**
  - 워커: 가짜 ticker(`[100.0, 103.5]`)를 주입하고 조사를 완주 → case_file에 `duration_s == 3.5`, 스냅샷 `duration_s == 3.5`, MetricsSink에 `investigation.duration_s` 1건(tags에 gbm/fct/outcome).
  - ticker 미주입 → `duration_s is None`, 보고서 푸터가 "경과: 미측정".
  - **MetricsSink가 던져도 조사는 종결된다**(무raise) — 던지는 sink 주입 후 `run_once == "closed"`.
  - 보고서 md/html 푸터: `관측성: 경과 3.5s · 라운드 2 · 도구 실패 1 · 미측정: 토큰`.
  - **토큰을 "0"으로 적지 않는다** — 안 잰 것을 0으로 적으면 나중에 분모가 거짓이 된다.
- [ ] **Step 2: RED** → **Step 3:** 구현. 측정 구간은 그래프 호출 전후(`_invoke_with_keepalive`를 감싸는 자리)이고 `_fail` 경로도 같은 구간을 재야 한다(실패한 조사가 분모에서 빠지면 생존 편향).
- [ ] **Step 4: GREEN + 돌연변이**(sink 예외 흡수 제거, 미측정→0, `_fail` 경로 측정 생략) → **Step 5: 커밋** `"Measure how long an investigation took, and say what was not measured"`.

---

### Task 4: chat 지문 정정 — 이력 매칭의 전제

**Files:** Modify `src/application/intake.py`(`_finish`), `src/application/open_case.py`(주석) · Test `tests/application/test_intake_turn.py`

계획 12가 남긴 알려진 결함: `fingerprint(gbm, fct, "chat", case_id)`라 사람이 연 케이스는 **서로 절대 같은 지문을 갖지 않는다**. 이대로 tier 1 검색을 얹으면 human 케이스는 영원히 안 맞는다.

- [ ] **Step 1: 실패하는 테스트**

```python
async def test_접수가_끝나면_지문이_대상_기준으로_다시_계산된다():
    # 같은 대상에 대한 두 human 케이스가 같은 지문을 갖는다 — tier 1 이력 매칭의 전제.
    # 개설 시점엔 target_locator가 없어 case_id로 지문을 만들 수밖에 없었다(계획 12).
    ...
    assert repo.get(c1).fingerprint == repo.get(c2).fingerprint
    assert repo.get(c1).fingerprint == fingerprint("mx", "gumi", "chat", "rest:/oee")

async def test_대상을_못_정한_접수는_지문을_그대로_둔다():
    # _give_up 경로 — locator가 없으면 재계산의 재료가 없다. case_id 지문이 남는다.
```

- [ ] **Step 2: RED** → **Step 3:** `_finish`에서 `target_locator`가 정해졌을 때만 재계산. **"chat" 성분을 유지한다** — 순찰 지문은 점검 이름을 쓰므로 네임스페이스가 갈라져 있고, 게이트의 `find_open_by_fingerprint`가 human 케이스에 순찰 finding을 붙이는 일이 생기지 않는다. 사람이 연 두 케이스가 같은 지문을 갖는 것은 이제 **의도**다(`open_case`는 지문 중복 억제를 하지 않는다).
- [ ] **Step 4: GREEN + 전체**(게이트 억제 테스트가 안 깨지는지) → **Step 5: 커밋** `"Recompute a chat case's fingerprint once intake knows the target"`.

---

### Task 5: 저장소의 이력 조회 표면

**Files:** Modify `src/domain/cases.py`, `src/infrastructure/mongo_store.py`(+ `ensure_indexes`) · Test `tests/domain/test_cases.py`, `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
```python
def closed_by_fingerprint(self, fp: str, *, exclude_case_id: str, limit: int = 10) -> list[CaseRecord]
def closed_by_locators(self, locators: list[str], *, exclude_case_id: str, limit: int = 20) -> list[CaseRecord]
```
둘 다 `status == "closed"`만, `closed_at`(없으면 `updated_at`) 최신순. 빈 `locators`는 빈 목록(전체 조회로 번지지 않게).

- [ ] **Step 1: 실패하는 테스트** — 인메모리·Mongo 같은 계약 테스트: 최신순, `exclude_case_id` 제외, 열린 케이스 제외, `limit`, 빈 입력 → `[]`. Mongo는 `(status, target_locator)` 인덱스가 생기는지도.
- [ ] **Step 2: RED** → **Step 3:** 구현 + `db.cases.create_index([("status", 1), ("target_locator", 1)])`(`(status, fingerprint)`는 이미 있다).
- [ ] **Step 4: GREEN + 돌연변이**(정렬 제거, exclude 제거, 빈 locators가 전체를 긁음) → **Step 5: 커밋** `"Query closed cases by fingerprint and by target locator"`.

---

### Task 6: 이력 검색과 렌더러 — tier 1~4, evidence id 금지

**Files:** Create `src/application/history.py` · Test `tests/application/test_history.py`

**Interfaces:**
```python
class HistoryHit(StrictModel):
    case_id: str; tier: int; reason: str          # 왜 매칭됐는지(사람이 읽는 한 줄)
    verdict_type: str | None; component: str | None; summary: str | None

def find_history(case, *, repo, snapshots, topology, limit: int = 3) -> list[HistoryHit]
def render_history(hits: list[HistoryHit]) -> str
```

tier 규칙(방향 문서 §4.5 표):

| tier | 조건 | reason 문구 |
|---|---|---|
| 1 | `fingerprint` 일치(같은 사이트 함의) | "같은 점검이 같은 대상에서 전에도" |
| 2 | `target_locator` 일치, 같은 사이트 | "다른 점검이 같은 대상을" |
| 3 | `target_locator` 일치, 다른 사이트 | "같은 대상이 다른 공장에서" |
| 4 | 과거 locator ∈ `upstream_slice(이번 locator)` | "상류에서 전에" |

- [ ] **Step 1: 실패하는 테스트**
  - tier 순서대로 걸어 K건에서 멈춘다(tier 1이 3건이면 tier 2를 안 본다).
  - 같은 케이스가 두 tier에 걸리면 **낮은 tier로 한 번만**.
  - `degraded` 판정과 `verdict_summary` 없는 케이스는 제외(워커 실패로 닫힌 케이스의 판정은 잡음).
  - **렌더러가 evidence id를 절대 내지 않는다** — 스냅샷/요약에 `ev-2`가 섞여 있어도 출력에 `ev-`가 없다. 이 테스트가 이 계획의 안전 앵커다.
  - 렌더는 **tier 사유를 행마다** 싣는다(없으면 리드가 tier 4를 tier 1처럼 과신한다).
  - `target_locator`가 없는 케이스(접수 실패)는 tier 2~4를 건너뛴다.
  - repo가 던져도 raise하지 않고 빈 목록(무raise).
- [ ] **Step 2: RED** → **Step 3:** 구현. 판정 재료는 `VerdictSnapshot`(retention 뒤에도 산다) + `CaseRecord.verdict_summary`. `store.get_verdict`는 **쓰지 않는다** — 90일 뒤 사라지므로 이력이 시간에 따라 조용히 비는 것을 피한다.
- [ ] **Step 4: GREEN + 돌연변이**(tier 순서 뒤집기, 중복 제거 제거, degraded 필터 제거, **렌더러에 evidence id 추가**) → **Step 5: 커밋** `"Find past cases by deterministic tiers, and never quote their evidence ids"`.

---

### Task 7: `history_provider` 배선 — frame 시점에, 케이스마다

**Files:** Modify `src/application/deps.py`, `src/application/nodes.py`(frame), `src/patrol/daemon.py`(조립), `src/__main__.py` · Test `tests/application/test_nodes_frame.py`, `tests/patrol/test_daemon.py`, `tests/test_cli.py`

**Interfaces:** `EngineDeps.history_provider: Callable[[Case], str] | None = None`. frame이 있으면 부르고 없으면 `deps.history_text`(기존 정적 필드, 테스트용)로 폴백.

- [ ] **Step 1: 실패하는 테스트**
  - frame이 provider의 반환을 `[유사 이력]`에 싣는다(브리핑 문자열로 확인).
  - **provider가 던져도 frame은 계속한다**(무raise) — 이력은 힌트지 조사의 전제가 아니다. 던지면 "이력 조회 실패"를 브리핑에 명시(조용한 생략 금지).
  - 데몬 조립이 provider를 **실제로 넘긴다**(`assemble_sites`가 만든 `EngineDeps.history_provider is not None`) — "함수는 되는데 호출부가 안 넘긴다"가 이 리포의 반복 실패 유형이라 조립부마다 테스트를 둔다.
- [ ] **Step 2: RED** → **Step 3:** 구현. provider는 `partial(find_history, repo=..., snapshots=..., topology=...)`를 렌더러로 감싼 클로저다(사이트별 topology를 클로저가 쥔다).
- [ ] **Step 4: GREEN + 돌연변이**(provider 무시, 예외 흡수 제거, 데몬 조립에서 `history_provider=` 제거) → **Step 5: 커밋** `"Feed the lead what happened before, resolved per case at frame time"`.

---

### Task 8: `history_shown` — 무엇을 먹였는지 스냅샷에 남긴다

**Files:** Modify `src/application/worker.py`, `src/application/deps.py`(provider가 hits를 노출) · Test `tests/application/test_worker.py`

이력이 frame에 무엇을 먹였는지 남기지 않으면 "도움이 됐나, 앵커링이었나"를 영원히 못 묻는다. **지금은 공짜, 나중엔 복구 불가.**

- [ ] **Step 1: 실패하는 테스트** — 이력 2건이 걸린 케이스를 완주 → `snapshots.get(cid).history_shown == [{"case_id": "c-old", "tier": 1}, ...]`. 이력이 없으면 `[]`. `_fail` 경로도 남긴다.
- [ ] **Step 2: RED** → **Step 3:** 구현. provider가 렌더 문자열만 돌려주면 워커가 무엇이 실렸는지 모른다 — provider를 `HistoryProvider`(호출 가능 + 마지막 hits 보관)로 만들지 말고(상태를 든 콜러블은 동시 조사에서 섞인다), **케이스별로 한 번 계산해 워커가 들고 다니는** 모양으로 한다: 워커가 `find_history`를 부르고, 그 결과를 렌더해 `deps`의 사본(`model_copy`가 아니라 `dataclasses.replace`)에 실어 그래프에 넘긴다. 그러면 `history_shown`도 워커가 안다.
- [ ] **Step 4: GREEN + 돌연변이**(스냅샷 필드 제거, `_fail` 경로 생략) → **Step 5: 커밋** `"Record which past cases the lead was shown"`.

---

### Task 9: `RootCauseLabel` — 사람이 실제 원인을 되먹인다

**Files:** Create `src/domain/label.py` · Modify `src/infrastructure/mongo_store.py`, `src/infrastructure/checkpointer.py`(`Persistence.labels`), `src/infrastructure/retention.py` · Test `tests/domain/test_label.py`, `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
```python
class RootCauseLabel(StrictModel):
    case_id: str
    agreement: Literal["correct", "partially_correct", "wrong", "unknown"]
    resolution: Literal["fixed", "not_reproducible", "wont_fix", "false_positive"] | None = None
    actual_root_cause_component: str | None = None
    actual_verdict_type: str | None = None
    saw_report: bool = False          # 앵커링 탐지 — 보고서를 보고 라벨했나
    labeled_by: str | None = None
    labeled_at: datetime

class LabelStorePort(ABC):
    def append(self, label) -> None          # append-only, 케이스당 복수 허용
    def list_for(self, case_id) -> list[RootCauseLabel]
    def count(self) -> int
    def labeled_case_ids(self) -> set[str]
```

`agreement`가 component 문자열 비교보다 중요하다 — 자유 문자열은 절대 정확히 일치하지 않고("plan-sync" vs "plan sync 서비스"), 자동 비교는 자기 정규화기를 측정하는 짓이다. `resolution`은 "에이전트가 틀렸다"와 "실은 아무것도 안 고장났다"를 분리한다.

- [ ] **Step 1: 실패하는 테스트** — append-only(같은 케이스에 두 번 → 둘 다 남는다, 순서 보존), `list_for` 격리, `count`/`labeled_case_ids`, Mongo 왕복. **retention이 라벨을 지우지 않는다**(스냅샷과 짝이다 — 한쪽만 남으면 대조 불가).
- [ ] **Step 2: RED** → **Step 3:** 구현 + `db.labels.create_index([("case_id", 1), ("labeled_at", 1)])`.
- [ ] **Step 4: GREEN + 돌연변이**(덮어쓰기, retention이 지움) → **Step 5: 커밋** `"Store what the humans said the cause actually was"`.

---

### Task 10: 라벨 유입구 — `case label`, `POST /cases/{id}/label`, 푸터 한 줄

**Files:** Create `src/application/labels.py` · Modify `src/__main__.py`, `src/api/routes_cases.py`, `src/api/models.py`, `src/domain/report_model.py`, `src/presentation/*` · Test `tests/application/test_labels.py`, `tests/test_cli.py`, `tests/api/test_routes_cases.py`, `tests/presentation/*`

**Interfaces:**
```python
def submit_label(case_id, *, agreement, resolution=None, actual_component=None,
                 actual_verdict_type=None, saw_report=False, labeled_by=None,
                 repo, labels, clock) -> Literal["recorded", "not_found", "error"]
def label_stats(*, repo, labels) -> LabelStats   # closed_total, labeled, rate, gate_open, why
```

게이트: `labeled >= 30 and rate > 0.5`. 열리기 전에는 **건수만**(`gate_open=False`, `why="라벨 12건 / 종결 40건 (30건·50% 필요)"`). 어떤 정확도도 계산하지 않는다 — 대상이 범주형이라 상관계수는 범주 오류이고, 열린 뒤에도 낼 것은 `confidence`별 적중이지 맨 퍼센트가 아니다.

- [ ] **Step 1: 실패하는 테스트**
  - `submit_label`: 없는 케이스 → `not_found`, 저장소 장애 → `error`(계획 13의 어휘와 같다), 성공 → `recorded` + 저장.
  - **닫히지 않은 케이스에도 라벨을 받는다**(사람이 조사 중에 원인을 알 수 있다) — 다만 `label_stats`의 분모는 종결 케이스다.
  - CLI `case label c-1 --agreement wrong --actual-component plan-sync --resolution fixed --saw-report`.
  - API `POST /cases/{id}/label` → 202 `{"result": "recorded"}`, 404, 503. **접근 검사는 다른 쓰기와 같다**(`visible_record`).
  - `case label --stats`가 게이트 닫힘 상태에서 퍼센트를 내지 않는다(출력에 `%` 없음, 건수는 있음).
  - 보고서 푸터에 라벨 명령 한 줄이 있고, **이미 라벨된 케이스는 그 사실을 보인다**(`라벨: wrong (fixed)`).
- [ ] **Step 2: RED** → **Step 3:** 구현.
- [ ] **Step 4: GREEN + 돌연변이**(게이트 상수 30→3, 퍼센트 출력, CLI 플래그가 저장까지 안 감, 푸터 한 줄 제거) → **Step 5: 커밋** `"Open the label intake in the CLI, the api and the report footer"`.

---

### Task 11: 문서와 인계

**Files:** `docs/architecture.md`(P8 층 — 학습 루프의 한 쌍, Ticker vs Clock, tier 표, 라벨 게이트), `docs/glossary.md`(**Ticker**, **tier 검색**, **RootCauseLabel**, **관측성 푸터**), `docs/howto.md`(`case label` 예시, `POST /label` curl), `docs/config-reference.md`(retention의 metrics), `CLAUDE.md` 코드 지도(`history.py`·`label.py`·`labels.py`), `tests/README.md`, 이 계획서 끝 인계.

- [ ] **Step 1:** 문서가 어떤 함수를 부른다고 쓰면 `grep`으로 호출부를 확인한 뒤 쓴다(CLAUDE.md).
- [ ] **Step 2:** 전체 통과 → 커밋 `"Document the learning loop: what it measures, shows and asks for"`.

---

## 자기 검토

- **커버리지**: 방향 문서 §128(N6 Ticker) → Task 1·3; §203(LedgerPort 3분할) → Task 2; §301(푸터) → Task 3·10; §405(스냅샷 담는 것) → Task 3·8; §411(RootCauseLabel) → Task 9·10; §415(계산 유예) → Task 10; §417~430(이력 tier·안전 제약·배선 형태) → Task 5·6·7; 계획 12 인계(chat 지문) → Task 4; §518(인덱스) → Task 5.
- **의도적으로 뺀 것(YAGNI)**: 캘리브레이션 계산 자체(게이트가 닫혀 있다 — 열리면 그때), 토큰 계측(LLM 콜백 배선이 필요하고 오늘 그 숫자에 붙는 행동이 없다 — "미측정"으로 정직하게 적는다), 벡터 검색(세 게이트 전부 미달, 방향 문서가 명시적으로 막아둠), OpenTelemetry/Prometheus(같은 이유 + 오프라인 규율).
- **타입 일관성**: `Ticker`(Task 1) → 워커 생성자(Task 3). `HistoryHit`(Task 6) → provider(Task 7) → `history_shown`(Task 8). `LabelStorePort`(Task 9) → `submit_label`/`label_stats`(Task 10). `Observability`(Task 3) → 푸터(Task 3·10).
- **가장 위험한 지점**: 브리핑의 evidence id 누출(규율 3의 가드레일을 통째로 무력화한다). Task 6의 렌더러 테스트가 유일한 방어선이므로 그 테스트를 먼저 쓴다.

## 인계(계획 15 이후)

1. **캘리브레이션 계산이 없다** — 게이트(`label_stats`)만 있다. 열리면 낼 것은
   `confidence`별 적중이고, 재료는 `VerdictSnapshot`(기계) × `RootCauseLabel`(사람)의
   조인이다. 항상 건수와 함께, 맨 퍼센트 금지.
2. **`history_shown`의 소비자가 아직 없다** — "이력을 보여준 케이스가 더 정확했나(도움)
   vs 보여준 후보로만 답했나(앵커링)"는 (1)과 같은 조인에서 나온다.
3. **Ticker는 CLI 경계에서 주입한다** — `patrol run`·`chat`·`case resume`이
   `time.perf_counter`를 넘기고 데몬이 워커까지 전달한다(집행 중 배선 완료). `api`는
   조사를 하지 않으므로 대상이 아니다.
4. **`MetricsSinkPort`의 소비자가 sink뿐이다** — 읽는 쪽(`metrics()`)을 쓰는 코드가
   없다. P7 Fleet 집계나 관측 대시보드가 첫 소비자가 된다.
5. **tier 4의 상류는 `upstream_slice(max_depth=3)`에 묶인다** — 더 먼 상류는 안 본다.
   깊이를 늘리면 tier 4가 사실상 "이 사이트의 아무 케이스나"가 된다.
6. **`_strip_evidence_ids`는 `ev-\d+` 형태만 지운다** — 증거 id 형식이 바뀌면 이 정규식도
   같이 바뀌어야 한다(`InMemoryCaseStore`가 `ev-N`을 만든다).
7. **접수 `_save`의 TOCTOU는 그대로다**(계획 13 인계 #1). 지문 재계산도 그 저장을 탄다.
