# 계획 7 — 보고 모델과 산출물 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 보고서의 데이터 유도와 렌더링을 갈라, 같은 `ReportModel`에서 마크다운과 HTML이 나오게 하고, 실패로 종결된 케이스도 조사 흔적을 잃지 않게 한다.

**Architecture:** `render_report`가 지금 데이터 유도와 마크다운 조립을 한 함수에서 한다. 이걸 `(record, verdict, evidence, case_file) → ReportModel → render_md | render_html` 2단으로 쪼갠다. 단계 체크리스트·이모지·요청 스펙·Timeline이 전부 같은 데이터를 원하므로 **한 번만 계산하고 렌더러만 갈린다.** 여기에 두 가지 영속화를 더한다: `_fail` 경로가 체크포인트에서 조사 흔적을 구제해 케이스 파일로 박제하고, 종결 시점에 `VerdictSnapshot`을 남겨 retention이 `Verdict`를 지운 뒤에도 판정을 대조할 수 있게 한다.

**Tech Stack:** Python 3.12 · pydantic 2 · pymongo(동기) · aiosmtplib · pytest(`asyncio_mode=auto`) · mongomock

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) 의 **P3**. §2-N5(ReportModel 2단) · §4.1(조사 보고서 형태) · §4.5(학습 루프 산출물).

**선행:** [계획 6](2026-09-04-plan6-process-boundary.md) 완료(커밋 `9b498bf`, 279 tests).

## Global Constraints

프로젝트 전역 규율이다. **모든 태스크의 요구사항에 암묵적으로 포함된다.**

- **무raise**: 어댑터·프로브·판정기·게이트·서브에이전트·워커·순찰·발행 전 층은 예외를 던지지 않는다. 실패는 반환값의 상태로 흡수하고, 최외곽 `try/except Exception`이 마지막 방어선이다. **보고서 조립은 특히 그렇다** — 조립 실패가 조사 종결을 막아서는 안 된다.
- **시계 주입**: `src/__main__.py`(CLI 경계) 밖에서 `datetime.now()`를 직접 부르지 않는다.
- **StrictModel**: 새 pydantic 모델은 `src/config/schema_app.py`의 `StrictModel`(`extra="forbid"`)을 상속한다.
- **조용한 생략 금지**: 각 하위 항목이 비면 값을 지우는 대신 "없음"을 적는다. "무엇을 확인 안 했나"의 명시가 신뢰의 조건이다.
- **벤치를 텍스트로 채점하지 않는다**: `tests/test_bench_scenarios.py`는 `Verdict`의 구조화 필드만 본다. 보고서 문자열을 단정하는 테스트를 새로 만들지 마라 — 단, `## N.` 절 제목 5개는 이미 단정하고 있으니 **절 구조를 깨면 안 된다**.
- **주석·문서는 한국어, WHY만.** **커밋 메시지는 영어 제목 + 한국어 본문**(WHY). 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **테스트 명령**: `.venv/bin/python -m pytest tests/ -q` (기준선 **279 passed**).
- **작업 디렉터리**: `/home/hchju777/langgraph_ws/deepagent-template`. 서브에이전트에게는 **절대 경로**로 지시한다.

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `src/infrastructure/checkpointer.py` | 영속 조립 | `build_persistence`가 이름 있는 `Persistence`를 돌려준다 |
| `src/domain/snapshot.py` | **신설** — 종결 시점 판정 스냅샷 | `VerdictSnapshot`, `VerdictSnapshotPort`, 인메모리 구현 |
| `src/presentation/report_model.py` | **신설** — 보고서 데이터 유도(순수) | `ReportModel`, `StageCheck`, `build_report_model` |
| `src/presentation/report.py` | 마크다운 렌더 + 파일 쓰기 | 모델을 받아 렌더, §1 체크리스트, §5 스냅샷 출처, 확장자 인자화 |
| `src/presentation/report_html.py` | **신설** — HTML 렌더 | `render_html(model)` |
| `src/presentation/mail.py` | 메일 발송 | HTML 대체 본문 |
| `src/application/worker.py` | 조사 실행 | `_fail` 스냅샷 구제, 종결 시 `VerdictSnapshot` |
| `src/infrastructure/mongo_store.py` | Mongo 어댑터 | `MongoVerdictSnapshotStore`, 인덱스 |
| `src/infrastructure/retention.py` | 보존 스윕 | 스냅샷 보존(다른 것보다 길다) |
| `src/patrol/daemon.py` | 데몬 조립 | 렌더 포맷 선택, 스냅샷 스토어 주입 |
| `src/config/schema_app.py` | config | `report.format`, `retention.snapshots_d` |
| `src/__main__.py` | CLI 경계 | `Persistence` 속성 접근, `case show --report` 확장자 |

**렌더러를 파일로 가르는 이유**: `report.py`가 이미 249줄이고 HTML을 같은 파일에 넣으면 400줄을 넘는다. 데이터 유도(`report_model.py`)와 두 렌더러가 서로 다른 이유로 바뀐다 — 유도는 State 모양이 바뀔 때, 렌더러는 표현이 바뀔 때다.

---

## Task 1: build_persistence가 이름으로 돌려준다

**Files:**
- Modify: `src/infrastructure/checkpointer.py`, `src/__main__.py`, `tests/infrastructure/test_checkpointer.py`, `tests/test_cli.py`
- Test: `tests/infrastructure/test_checkpointer.py`

**Interfaces:**
- Produces: `Persistence` NamedTuple — `.store`, `.repo`, `.ledger`, `.events`, `.snapshots`

계획 6에서 3-튜플을 4-튜플로 바꾸느라 7개 호출부를 손댔다. 이 계획이 다섯 번째(`snapshots`)를 더한다. **이름으로 접근하면 앞으로 필드를 더해도 호출부가 안 깨진다.** 지금 한 번만 갚는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/infrastructure/test_checkpointer.py` 끝에 추가:

```python
def test_build_persistence는_이름으로_접근된다():
    from src.config.schema_app import StoreConfig
    from src.infrastructure.checkpointer import build_persistence
    p = build_persistence(StoreConfig(backend="memory"))
    assert p.store is not None and p.repo is not None
    assert p.ledger is not None and p.events is not None
    assert p.snapshots is not None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_checkpointer.py -q -k "이름으로"`
Expected: FAIL — `AttributeError: 'tuple' object has no attribute 'store'`

- [ ] **Step 3: 최소 구현**

Task 6이 `InMemoryVerdictSnapshotStore`를 만들기 전이므로, **이 태스크에서는 `snapshots` 자리에 `None`을 넣지 않고** 먼저 Task 6을 하거나 — 순서를 지켜 **Task 6을 먼저 실행하고 이 태스크로 돌아온다.** 실행 순서는 아래 "태스크 순서" 절을 따른다.

`src/infrastructure/checkpointer.py`:

```python
class Persistence(NamedTuple):
    """영속 3종 + 이벤트 로그 + 판정 스냅샷.

    NamedTuple인 이유: 필드를 더할 때마다 튜플 언패킹 호출부를 전부 고쳐야 했다
    (계획 6에서 3→4로 늘리며 7곳을 손댔다). 이름으로 접근하면 추가가 호출부를
    깨지 않는다.
    """
    store: CaseStorePort
    repo: CaseRepositoryPort
    ledger: LedgerPort
    events: EventStorePort
    snapshots: VerdictSnapshotPort


def build_persistence(cfg: StoreConfig) -> Persistence:
    """cfg.backend에 따라 영속 계층 일습을 만든다."""
    if cfg.backend == "memory":
        return Persistence(InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger(),
                           InMemoryEventStore(), InMemoryVerdictSnapshotStore())
    db = MongoClient(cfg.mongo_url)[cfg.mongo_db]
    ensure_indexes(db)
    return Persistence(MongoCaseStore(db), MongoCaseRepository(db), MongoLedger(db),
                       MongoEventStore(db), MongoVerdictSnapshotStore(db))
```

import에 `from typing import NamedTuple`과 필요한 포트·구현체를 더한다.

`src/__main__.py`의 6개 호출부를 속성 접근으로 바꾼다(`grep -n "build_persistence(" src/__main__.py`로 찾는다). 예:

```python
    p = build_persistence(app.store)
    store, repo, ledger, events = p.store, p.repo, p.ledger, p.events
```

`tests/test_cli.py`의 monkeypatch 4곳도 `Persistence`를 돌려주게 고친다:

```python
    monkeypatch.setattr("src.__main__.build_persistence",
                        lambda cfg: Persistence(store, repo, ledger, InMemoryEventStore(),
                                                InMemoryVerdictSnapshotStore()))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q` — PASS 전건

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Name the persistence bundle instead of unpacking a tuple

계획 6에서 3-튜플을 4-튜플로 늘리며 호출부 7곳을 손댔고, 이 계획이 다섯 번째
필드를 더한다. 위치 언패킹은 필드를 더할 때마다 같은 세금을 물린다 — 이름으로
접근하면 추가가 호출부를 깨지 않는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 케이스 파일 스냅샷을 관용적으로, verify_attempts까지

**Files:**
- Modify: `src/application/worker.py`
- Test: `tests/application/test_worker.py`

**Interfaces:**
- Produces: `_case_file_snapshot(result)`가 `verify_attempts`를 포함하고, 모델이 아닌 값(체크포인트에서 온 dict 등)에도 raise하지 않는다

`verify_attempts`는 §1 체크리스트의 검증 단계가 "그냥 통과"와 "재작성 후 통과"를 구별하는 유일한 구조적 신호다. 그리고 Task 5가 이 함수에 **체크포인트 값**을 먹이는데, 그 값이 pydantic 모델이 아닐 수 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/application/test_worker.py` 끝에 추가:

```python
def test_케이스_파일_스냅샷은_모델이_아닌_값에도_견딘다():
    # Task 5가 체크포인트에서 읽은 값을 그대로 먹인다 — 역직렬화 결과가 pydantic
    # 모델이 아닐 수 있는데, model_dump를 무조건 부르면 AttributeError로 터진다.
    from src.application.worker import _case_file_snapshot
    snap = _case_file_snapshot({
        "plan_tasks": [{"id": "t1", "status": "ok"}],      # 모델이 아니라 dict
        "hypotheses": [], "round": 2, "qa_log": [], "verify_problems": [],
        "verify_attempts": 1})
    assert snap["plan_tasks"] == [{"id": "t1", "status": "ok"}]
    assert snap["round"] == 2 and snap["verify_attempts"] == 1


def test_케이스_파일_스냅샷은_빠진_키를_기본값으로_채운다():
    from src.application.worker import _case_file_snapshot
    snap = _case_file_snapshot({})
    assert snap["verify_attempts"] == 0 and snap["round"] == 0 and snap["plan_tasks"] == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/application/test_worker.py -q -k "스냅샷은"`
Expected: FAIL — `AttributeError: 'dict' object has no attribute 'model_dump'` / `KeyError: 'verify_attempts'`

- [ ] **Step 3: 최소 구현**

`src/application/worker.py`의 `_case_file_snapshot`을 바꾼다:

```python
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
    """
    return {
        "plan_tasks": [_dump_item(t) for t in result.get("plan_tasks", [])],
        "hypotheses": [_dump_item(h) for h in result.get("hypotheses", [])],
        "round": result.get("round", 0),
        "qa_log": result.get("qa_log", []),
        "verify_problems": result.get("verify_problems", []),
        "verify_attempts": result.get("verify_attempts", 0),
    }
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Carry verify_attempts and tolerate non-model values in the case file

보고서 §1의 검증 단계가 "그냥 통과"와 "재작성 후 강등 통과"를 구별하려면 구조적
신호가 필요한데, 지금은 caveat 문자열을 냄새 맡는 것 말고는 방법이 없었다.
verify_attempts가 그 신호다.

관용적 dump는 다음 태스크의 전제다 — 체크포인트에서 구제한 값은 역직렬화 방식에
따라 pydantic 모델이 아닐 수 있고, 무raise 규율상 거기서 터지면 안 된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: ReportModel과 단계 체크리스트

**Files:**
- Create: `src/presentation/report_model.py`
- Test: `tests/presentation/test_report_model.py` (신설)

**Interfaces:**
- Produces: `StageCheck`, `ReportModel`, `build_report_model(record, *, verdict, evidence, case_file, clock, evidence_summaries=None) -> ReportModel`

**단계 유도 규칙** — 문자열 냄새 맡기 없이 구조로만 판정한다:

| 단계 | `ok` | `fail` | `warn` | `skip` |
|---|---|---|---|---|
| 가설 수립 | `hypotheses` 있음 | 없는데 verdict 있음(frame 파싱 실패) | — | 없고 verdict도 없음 |
| 조사 계획 | `plan_tasks` 있음 | — | — | 없음 |
| 조사 실행 | 태스크 있고 `error` 0 | `error` ≥ 1 | — | 태스크 없음 또는 전부 미실행 |
| 결과 통합 | `round` ≥ 1 | `qa_log`에 `integrate_parse_failure` | — | `round` 0 |
| 판정 | verdict 있고 `degraded` 아님 | `degraded` | — | verdict 없음 |
| 검증 | verdict 있고 문제 없고 `verify_attempts` 0 | `verify_problems` 있음 | 문제 없고 `verify_attempts` ≥ 1 | verdict 없음 |

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/presentation/test_report_model.py`를 새로 만든다:

```python
from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.presentation.report_model import build_report_model

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _record(**kw):
    base = dict(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="OEE 512%",
                t0=T, created_at=T, updated_at=T)
    base.update(kw)
    return CaseRecord(**base)


def _verdict(**kw):
    base = dict(verdict_type="data_loss", confidence="high",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                narrative="계획 동기화 누락")
    base.update(kw)
    return Verdict(**base)


def _marks(model):
    return {s.stage: s.mark for s in model.stages}


def test_정상_종결은_여섯_단계가_전부_통과다():
    case_file = {"hypotheses": [{"id": "h1"}], "plan_tasks": [{"id": "t1", "status": "ok"}],
                 "round": 2, "qa_log": [], "verify_problems": [], "verify_attempts": 0}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert set(_marks(model).values()) == {"ok"}


def test_태스크_에러는_조사_실행을_실패로_표시한다():
    case_file = {"hypotheses": [{"id": "h1"}],
                 "plan_tasks": [{"id": "t1", "status": "ok"}, {"id": "t2", "status": "error"}],
                 "round": 1, "qa_log": [], "verify_problems": [], "verify_attempts": 0}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert _marks(model)["execute"] == "fail"
    assert model.task_error_rate == "1/2"


def test_재작성_후_통과는_경고로_구별한다():
    # verify_attempts >= 1인데 문제가 비었다 = 재작성했거나 강등 통과다.
    # ok로 뭉개면 "깨끗하게 통과한 판정"과 구별되지 않는다.
    case_file = {"hypotheses": [{"id": "h1"}], "plan_tasks": [{"id": "t1", "status": "ok"}],
                 "round": 1, "qa_log": [], "verify_problems": [], "verify_attempts": 1}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert _marks(model)["verify"] == "warn"


def test_판정이_없으면_판정과_검증이_미도달이다():
    model = build_report_model(_record(), verdict=None, evidence=[],
                               case_file={"hypotheses": [{"id": "h1"}], "round": 0},
                               clock=lambda: T)
    marks = _marks(model)
    assert marks["conclude"] == "skip" and marks["verify"] == "skip"
    assert marks["integrate"] == "skip"     # round 0


def test_가설이_없는데_판정만_있으면_가설_수립_실패다():
    # frame 파싱 실패는 hypotheses 없이 degraded verdict만 만든다.
    model = build_report_model(
        _record(), verdict=_verdict(verdict_type="degraded", confidence="low",
                                    root_cause=None, narrative="frame 출력 파싱 실패"),
        evidence=[], case_file={"round": 0}, clock=lambda: T)
    marks = _marks(model)
    assert marks["frame"] == "fail" and marks["conclude"] == "fail"


def test_옛_스냅샷에_verify_attempts가_없으면_통과로_본다():
    # 계획 7 이전에 쓰인 케이스 파일에는 이 키가 없다 — 없다고 경고를 띄우면
    # 과거 보고서가 전부 의심스러워 보인다.
    case_file = {"hypotheses": [{"id": "h1"}], "plan_tasks": [{"id": "t1", "status": "ok"}],
                 "round": 1, "qa_log": [], "verify_problems": []}
    model = build_report_model(_record(), verdict=_verdict(), evidence=[],
                               case_file=case_file, clock=lambda: T)
    assert _marks(model)["verify"] == "ok"


def test_케이스_파일이_없어도_모델이_만들어진다():
    model = build_report_model(_record(), verdict=None, evidence=[], case_file=None,
                               clock=lambda: T)
    assert len(model.stages) == 6 and model.task_error_rate == "없음"
    assert model.partial is False and model.salvage_error is None


def test_실패_시점_부분_스냅샷은_모델에_표시된다():
    model = build_report_model(_record(), verdict=None, evidence=[],
                               case_file={"partial": True, "salvage_error": "RuntimeError: x"},
                               clock=lambda: T)
    assert model.partial is True and model.salvage_error == "RuntimeError: x"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/presentation/test_report_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.presentation.report_model'`

- [ ] **Step 3: 최소 구현**

`src/presentation/report_model.py`를 만든다:

```python
"""보고서 데이터 유도 — 스펙 §2-N5(ReportModel 2단).

렌더러(마크다운·HTML)가 공유하는 유일한 데이터 소스다. 단계 체크리스트·이모지·
증거 표·Timeline이 전부 같은 값을 원하므로 여기서 한 번만 계산한다 — 렌더러마다
따로 유도하면 마크다운과 HTML이 다른 말을 하게 된다.

단계 판정은 **구조로만** 한다: caveat 문자열이나 narrative를 냄새 맡지 않는다.
그런 판정은 프롬프트를 손볼 때마다 조용히 틀려진다.

case_file은 Store에 박제된 원시 dict라 옛 스냅샷이거나 키가 빠져도 이 모듈은
절대 raise하지 않는다 — 보고서 조립 실패가 조사 종결을 막아서는 안 된다.
"""
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel
from src.domain.case import Verdict
from src.domain.cases import CaseRecord
from src.domain.store import EvidenceRecord

Clock = Callable[[], datetime]

Mark = Literal["ok", "fail", "warn", "skip"]

_UNINVESTIGATED = {"pending", "cancelled"}   # §5: 미조사로 명시할 태스크 상태
_STAGE_LABELS = [("frame", "가설 수립"), ("select", "조사 계획"), ("execute", "조사 실행"),
                 ("integrate", "결과 통합"), ("conclude", "판정"), ("verify", "검증")]


class StageCheck(StrictModel):
    """조사 단계 하나의 통과 여부. mark는 렌더러가 기호로 바꾼다."""
    stage: str
    label: str
    mark: Mark
    note: str = ""


class ReportModel(StrictModel):
    """보고서 한 건의 데이터 전부. 렌더러는 이것만 보고 문자열을 만든다."""
    record: CaseRecord
    verdict: Verdict | None
    evidence: list[EvidenceRecord]
    evidence_summaries: dict[str, str] | None
    stages: list[StageCheck]
    round_no: int | None
    plan_tasks: list[dict]
    hypotheses: list[dict]
    verify_problems: list[str]
    qa_log: list[dict]
    task_error_rate: str
    partial: bool = False            # 실패 시점 부분 스냅샷인가
    salvage_error: str | None = None  # 구제 자체가 실패했으면 그 사유
    generated_at: datetime


def _as_list(value: object) -> list:
    """컨테이너 타입 가드 — `value or []`는 5 같은 truthy 비-리스트를 통과시킨다."""
    return value if isinstance(value, list) else []


def _dicts(value: object) -> list[dict]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _task_error_rate(plan_tasks: list[dict]) -> str:
    if not plan_tasks:
        return "없음"
    errors = sum(1 for t in plan_tasks if t.get("status") == "error")
    return f"{errors}/{len(plan_tasks)}"


def _frame_stage(hypotheses, verdict) -> tuple[Mark, str]:
    if hypotheses:
        return "ok", f"가설 {len(hypotheses)}건"
    # 가설 없이 판정만 있는 유일한 경로가 frame 출력 파싱 실패다.
    if verdict is not None:
        return "fail", "가설 없이 판정으로 직행(frame 파싱 실패)"
    return "skip", "미도달"


def _select_stage(plan_tasks) -> tuple[Mark, str]:
    if plan_tasks:
        return "ok", f"태스크 {len(plan_tasks)}건"
    return "skip", "미도달"


def _execute_stage(plan_tasks) -> tuple[Mark, str]:
    if not plan_tasks:
        return "skip", "미도달"
    errors = [t for t in plan_tasks if t.get("status") == "error"]
    done = [t for t in plan_tasks if t.get("status") == "ok"]
    if errors:
        return "fail", f"{len(done)} ok / {len(errors)} error"
    if not done:
        return "skip", "실행된 태스크 없음"
    return "ok", f"{len(done)} ok"


def _integrate_stage(round_no, qa_log) -> tuple[Mark, str]:
    if any(e.get("kind") == "integrate_parse_failure" for e in qa_log):
        return "fail", "통합 출력 파싱 실패"
    if isinstance(round_no, int) and round_no >= 1:
        return "ok", f"라운드 {round_no}"
    return "skip", "미도달"


def _conclude_stage(verdict) -> tuple[Mark, str]:
    if verdict is None:
        return "skip", "미도달"
    if verdict.verdict_type == "degraded":
        return "fail", "degraded — 판정 불가"
    return "ok", verdict.verdict_type


def _verify_stage(verdict, verify_problems, verify_attempts) -> tuple[Mark, str]:
    if verdict is None:
        return "skip", "미도달"
    if verify_problems:
        return "fail", f"미해결 문제 {len(verify_problems)}건"
    # verify_attempts가 없는 옛 스냅샷은 0으로 본다 — 과거 보고서를 전부 경고로
    # 물들이지 않기 위해서다.
    if isinstance(verify_attempts, int) and verify_attempts >= 1:
        return "warn", "재작성 후 통과(확신 강등 가능)"
    return "ok", "인용 검증 통과"


def build_report_model(record: CaseRecord, *, verdict: Verdict | None,
                       evidence: list[EvidenceRecord], case_file: dict | None,
                       clock: Clock,
                       evidence_summaries: dict[str, str] | None = None) -> ReportModel:
    """보고서 데이터를 유도한다. 순수 함수이고 절대 raise하지 않는다."""
    case_file = case_file if isinstance(case_file, dict) else {}
    plan_tasks = _dicts(case_file.get("plan_tasks"))
    hypotheses = _dicts(case_file.get("hypotheses"))
    qa_log = _dicts(case_file.get("qa_log"))
    verify_problems = [str(p) for p in _as_list(case_file.get("verify_problems"))]
    round_raw = case_file.get("round")
    round_no = round_raw if isinstance(round_raw, int) else None
    verify_attempts = case_file.get("verify_attempts", 0)

    outcomes = {
        "frame": _frame_stage(hypotheses, verdict),
        "select": _select_stage(plan_tasks),
        "execute": _execute_stage(plan_tasks),
        "integrate": _integrate_stage(round_no, qa_log),
        "conclude": _conclude_stage(verdict),
        "verify": _verify_stage(verdict, verify_problems, verify_attempts),
    }
    stages = [StageCheck(stage=key, label=label, mark=outcomes[key][0], note=outcomes[key][1])
              for key, label in _STAGE_LABELS]

    salvage_error = case_file.get("salvage_error")
    return ReportModel(
        record=record, verdict=verdict, evidence=evidence,
        evidence_summaries=evidence_summaries, stages=stages, round_no=round_no,
        plan_tasks=plan_tasks, hypotheses=hypotheses, verify_problems=verify_problems,
        qa_log=qa_log, task_error_rate=_task_error_rate(plan_tasks),
        partial=bool(case_file.get("partial")),
        salvage_error=str(salvage_error) if salvage_error else None,
        generated_at=clock())
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Derive report data once, for every renderer

render_report가 데이터 유도와 마크다운 조립을 한 함수에서 하고 있어, HTML을
더하면 같은 유도를 두 번 쓰게 된다 — 그러면 두 렌더러가 언젠가 다른 말을 한다.

단계 체크리스트를 구조로만 판정하는 것이 핵심이다. caveat 문자열이나 narrative를
냄새 맡는 판정은 프롬프트를 손볼 때마다 조용히 틀려진다. verify_attempts가
"깨끗한 통과"와 "재작성 후 강등 통과"를 가르는 유일한 구조적 신호다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 마크다운 렌더러가 모델을 쓴다 + §1 체크리스트

**Files:**
- Modify: `src/presentation/report.py`
- Test: `tests/presentation/test_report.py`

**Interfaces:**
- Consumes: Task 3의 `build_report_model`, `ReportModel`
- Produces: `render_report(...)` 시그니처 유지(호출부 무변경), `render_md(model) -> str` 신설

기존 보고서 테스트가 전부 그대로 통과해야 한다 — 5절 구조와 "없음" 관례는 계약이다. 새로 붙는 것은 §1의 체크리스트 표뿐이다.

**GFM 함정**: 표를 앞의 불릿에서 빈 줄로 떼어내지 않으면 리스트 항목의 느슨한 계속으로 흡수돼 평문으로 렌더된다. §5 태스크 표에서 이미 겪은 문제이고([report.py](../../../src/presentation/report.py) 참고), §1 체크리스트 표에도 그대로 적용된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/presentation/test_report.py` 끝에 추가:

```python
def test_요약절에_단계_체크리스트가_기호로_나온다():
    from src.domain.case import CauseLink, Verdict
    verdict = Verdict(verdict_type="data_loss", confidence="high",
                      root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                      narrative="계획 동기화 누락")
    case_file = {"hypotheses": [{"id": "h1"}],
                 "plan_tasks": [{"id": "t1", "role": "data_prober", "status": "ok"},
                                {"id": "t2", "role": "code_tracer", "status": "error"}],
                 "round": 2, "qa_log": [], "verify_problems": [], "verify_attempts": 0}
    text = render_report(RECORD, verdict=verdict, evidence=[], case_file=case_file,
                         clock=lambda: T)
    assert "| 단계 | 상태 | 비고 |" in text
    assert "| 가설 수립 | ✅ |" in text
    assert "| 조사 실행 | ❌ |" in text          # error 태스크가 있다
    # 표 앞에 빈 줄이 없으면 GFM이 앞 불릿의 계속으로 흡수해 평문이 된다.
    assert "\n\n| 단계 | 상태 | 비고 |" in text


def test_미도달_단계는_빈칸_기호로_구별된다():
    text = render_report(RECORD, verdict=None, evidence=[], case_file={"round": 0},
                         clock=lambda: T)
    assert "| 판정 | ⬜ |" in text and "| 검증 | ⬜ |" in text
```

`RECORD`는 `tests/presentation/test_report.py` 상단에 이미 있는 모듈 상수다(별도 헬퍼가 없다).

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/presentation/test_report.py -q -k "체크리스트 or 미도달"`
Expected: FAIL — `assert '| 단계 | 상태 | 비고 |' in text`

- [ ] **Step 3: 최소 구현**

`src/presentation/report.py`에 기호 표와 렌더 함수를 더하고, `_render`가 모델을 쓰게 바꾼다:

```python
_MARKS = {"ok": "✅", "fail": "❌", "warn": "⚠", "skip": "⬜"}


def _stage_table(stages) -> list[str]:
    """§1의 조사 단계 체크리스트.

    표 앞뒤에 빈 줄을 둔다 — 빈 줄 없이 앞 불릿에 붙으면 GFM이 리스트 항목의
    느슨한 계속으로 흡수해 표가 아니라 평문으로 렌더한다(§5 태스크 표에서 겪은
    것과 같은 함정, mistune으로 실측 확인).
    """
    lines = ["", "| 단계 | 상태 | 비고 |", "|---|---|---|"]
    for s in stages:
        lines.append(f"| {s.label} | {_MARKS.get(s.mark, '?')} | {s.note or '없음'} |")
    lines.append("")
    return lines
```

`_section1`이 모델을 받아 체크리스트를 붙이게 고친다:

```python
def _section1(model) -> str:
    record = model.record
    confidence = model.verdict.confidence if model.verdict is not None else "없음"
    rows = [
        f"- 케이스 id: {record.id}",
        f"- 스코프: {record.gbm}/{record.fct}",
        f"- 개설 경로: {record.origin}",
        f"- 증상: {record.symptom}",
        f"- T0: {record.t0.isoformat()}",
        f"- 판정: {_verdict_headline(record, model.verdict)}",
        f"- 신뢰도: {confidence}",
        f"- 태스크 에러율: {model.task_error_rate}",
        "- 조사 단계:",
    ]
    return "## 1. 요약\n" + "\n".join(rows + _stage_table(model.stages))
```

`_render`를 모델 기반으로 바꾼다. **나머지 절(2~5)의 출력은 바꾸지 않는다** — 기존 인자를 모델 필드에서 꺼내 그대로 넘긴다:

```python
def render_md(model) -> str:
    """ReportModel에서 5절 마크다운을 조립한다."""
    sections = [
        f"# 케이스 {model.record.id} 보고서",
        "",
        f"작성 시각: {model.generated_at.isoformat()}",
        "",
        _section1(model),
        "",
        _section2(model.record, model.verdict),
        "",
        _section3(model.verdict),
        "",
        _section4(model.evidence, model.evidence_summaries),
        "",
        _section5(model),
    ]
    return "\n".join(sections) + "\n"


def render_report(RECORD, *, verdict, evidence, case_file, clock, evidence_summaries=None) -> str:
    """스펙 §5.1의 5절 보고서를 md로 조립한다(호출부 호환 유지).

    데이터 유도는 report_model.build_report_model이 한다 — 이 함수는 그 결과를
    마크다운으로 옮길 뿐이다. 어느 단계에서 실패해도 raise하지 않는다.
    """
    try:
        model = build_report_model(record, verdict=verdict, evidence=evidence,
                                   case_file=case_file, clock=clock,
                                   evidence_summaries=evidence_summaries)
        return render_md(model)
    except Exception as exc:            # noqa: BLE001 — 최후의 그물(계약)
        return (f"# 케이스 {getattr(record, 'id', '?')} 보고서\n\n"
                f"보고서 조립 실패: {type(exc).__name__}: {exc}\n")
```

`_section5`도 모델을 받게 바꾼다(내용은 그대로, 인자만 모델에서 꺼낸다):

```python
def _section5(model) -> str:
    lines = ["## 5. 조사 경위",
             f"- 라운드: {model.round_no if model.round_no is not None else '없음'}",
             "- 태스크 현황:"]
    ...  # 이하 기존 로직에서 plan_tasks/hypotheses/verify_problems/qa_log를 model에서 꺼내 쓴다
```

`_task_error_rate`와 `_as_list`는 `report_model.py`로 옮겨갔으므로 `report.py`에서 제거하고, 남은 사용처를 모델 필드로 대체한다.

import에 `from src.presentation.report_model import build_report_model`을 더한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ -q` — 기존 보고서 테스트가 **하나도 깨지지 않아야 한다.**

- [ ] **Step 5: 실제 렌더러로 표를 검증한다**

CLAUDE.md의 "실제 소비자로 직접 검증하라"를 따른다. mistune은 커밋하지 않는다(임시 설치):

```bash
.venv/bin/pip install -q mistune
.venv/bin/python - <<'EOF'
import mistune
from datetime import datetime, timezone
from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.presentation.report import render_report

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
record = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                    t0=T, created_at=T, updated_at=T)
verdict = Verdict(verdict_type="data_loss", confidence="high",
                  root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                  narrative="n")
case_file = {"hypotheses": [{"id": "h1"}],
             "plan_tasks": [{"id": "t1", "role": "data_prober", "status": "ok"}],
             "round": 1, "qa_log": [], "verify_problems": [], "verify_attempts": 0}
html = mistune.create_markdown(plugins=["table"])(
    render_report(RECORD, verdict=verdict, evidence=[], case_file=case_file, clock=lambda: T))
tables = html.count("<table>")
print(f"<table> 개수: {tables}")
assert tables >= 2, "§1 체크리스트와 §5 태스크 표가 둘 다 표로 렌더돼야 한다"
print("OK")
EOF
.venv/bin/pip uninstall -y -q mistune
```

- [ ] **Step 6: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Show which investigation stages actually passed

보고서가 "무엇이 실패했는지"는 말했지만 "어디까지 갔는지"는 말하지 않았다.
§5의 다섯 항목이 전부 "없음"일 때 그것이 "조사한 게 없다"인지 "조사 흔적을
잃었다"인지 구별할 수 없었고, 정상 종결에서도 어느 단계가 깨끗했는지 알 수
없었다.

렌더러가 모델을 경유하게 바꾼 것은 HTML을 더하기 위한 준비다 — 유도가 한 곳에
있어야 두 렌더러가 같은 말을 한다.

표 앞뒤의 빈 줄은 장식이 아니다. 없으면 GFM이 앞 불릿의 느슨한 계속으로 흡수해
평문으로 렌더한다 — §5 태스크 표에서 겪은 것과 같은 함정이라 mistune으로 실측
확인했다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 실패 종결의 조사 흔적 구제

**Files:**
- Modify: `src/application/worker.py`
- Test: `tests/application/test_worker.py`

**Interfaces:**
- Consumes: Task 2의 관용적 `_case_file_snapshot`
- Produces: `InvestigationWorker._salvage_case_file(record, case_id)` — `_fail`이 `close_case` **앞에서** 부른다

`_fail`은 `put_case_file`을 아예 부르지 않고, 직후 `close_case(discard_threads=True)`가 스레드를 지운다. 그래서 실패 종결 보고서의 §5가 통째로 "없음"이 된다. **순서가 계약이다** — 스레드를 지운 뒤에는 읽을 것이 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/application/test_worker.py` 끝에 추가:

```python
async def test_실패_종결도_조사_흔적을_케이스_파일로_남긴다(monkeypatch):
    # _fail은 put_case_file을 부르지 않고 close_case가 스레드를 지워, 실패 종결
    # 보고서의 §5가 통째로 "없음"이 됐다 — discard_threads=True와 겹쳐 완전 유실.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    # _finish에서 터뜨려 그래프는 정상 완주하되 종결만 실패시킨다 — 체크포인트에
    # 진짜 조사 흔적이 남은 상태에서 _fail이 도는 유일한 방법이다.
    async def boom(self, record, result):
        raise RuntimeError("종결 실패")
    monkeypatch.setattr(InvestigationWorker, "_finish", boom)

    assert await worker.run_once("c-1") == "failed"
    case_file = store.get_case_file("c-1")
    assert case_file is not None
    assert case_file["partial"] is True
    assert case_file["plan_tasks"], "체크포인트에서 태스크를 구제했어야 한다"
    assert repo.get("c-1").status == "closed"


async def test_구제가_불가능해도_케이스_파일에_사유가_남고_워커는_raise하지_않는다():
    # 엔진이 캐시에 없는 실패(build_engine 전에 터짐)는 읽을 체크포인트가 없다.
    # "조사한 게 없다"와 "흔적을 잃었다"를 보고서가 구별할 수 있어야 한다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    def broken_deps(g, f):
        raise RuntimeError("deps 조립 실패")
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=broken_deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "failed"
    case_file = store.get_case_file("c-1")
    assert case_file["partial"] is True and case_file["salvage_error"]
    assert case_file["plan_tasks"] == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/application/test_worker.py -q -k "흔적 or 구제가"`
Expected: FAIL — `assert case_file is not None` (`get_case_file`이 `None`을 준다)

- [ ] **Step 3: 최소 구현**

`src/application/worker.py`에 구제 메서드를 더한다:

```python
    async def _salvage_case_file(self, record, case_id: str) -> None:
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
            for thread_id in reversed(list(record.thread_ids)):
                state = await engine.aget_state({"configurable": {"thread_id": thread_id}})
                values = getattr(state, "values", None)
                if isinstance(values, dict) and values:
                    snapshot.update(_case_file_snapshot(values))
                    break
        except Exception as exc:                                   # noqa: BLE001
            snapshot["salvage_error"] = f"{type(exc).__name__}: {exc}"
        snapshot.setdefault("plan_tasks", [])
        snapshot.setdefault("hypotheses", [])
        snapshot.setdefault("qa_log", [])
        snapshot.setdefault("verify_problems", [])
        try:
            self._store.put_case_file(case_id, snapshot)
        except Exception:                                          # noqa: BLE001
            pass
```

`_fail`에서 **`close_case` 앞에** 부른다:

```python
        self._log_failure(record, case_id, exc)
        await self._salvage_case_file(record, case_id)   # close_case가 스레드를 지우기 전에
        reason = f"워커 실패 — {type(exc).__name__}: {exc}"
        try:
            await close_case(case_id, repo=self._repo, checkpointer=self._checkpointer,
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Salvage the investigation trail before discarding the threads

_fail은 put_case_file을 부르지 않고 곧장 close_case(discard_threads=True)로
스레드를 지웠다. 그래서 실패로 닫힌 케이스의 보고서는 §5가 통째로 "없음"이었고,
조사가 어디까지 갔는지 영영 알 수 없었다.

순서가 계약이다 — 스레드를 지운 뒤에는 읽을 것이 없다. 그래서 구제를 close_case
앞에 둔다. 구제 실패도 케이스 파일에 남기는 이유는 보고서가 "조사한 게 없다"와
"흔적을 잃었다"를 구별할 수 있어야 하기 때문이다. 구별하지 못하면 조용한 생략이
된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 판정 스냅샷 — retention보다 오래 사는 기록

**Files:**
- Create: `src/domain/snapshot.py`
- Modify: `src/infrastructure/mongo_store.py`, `src/config/schema_app.py`, `src/infrastructure/retention.py`
- Test: `tests/domain/test_snapshot.py` (신설), `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
- Produces: `VerdictSnapshot`, `VerdictSnapshotPort`(`put`/`get`/`prune_before`), `InMemoryVerdictSnapshotStore`, `MongoVerdictSnapshotStore`, `RetentionConfig.snapshots_d`

**이것이 v2의 유일한 일방향 문이다.** `sweep_retention` ①이 90일에 `store.purge_case`로 `Verdict`·증거·케이스 파일을 전부 지운다. 그때까지 남기지 않은 것은 나중에 어떤 상관도 계산할 수 없다 — 100일 뒤 사람이 실제 원인을 알려줘도 대조할 대상이 없다.

`history_shown`은 지금 비어 있다(이력 검색은 P8이 만든다). **필드는 지금 열어 둔다** — 나중에 추가하면 그 사이 종결된 케이스는 영영 답을 못 준다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/domain/test_snapshot.py`를 새로 만든다:

```python
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.snapshot import InMemoryVerdictSnapshotStore, VerdictSnapshot

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _snap(case_id="c-1", **kw):
    base = dict(case_id=case_id, closed_at=T, gbm="mx", fct="gumi", fingerprint="fp",
                origin="patrol", outcome="closed", verdict_type="data_loss",
                root_cause_component="plan-sync", confidence="high", rounds=2,
                evidence_count=3, task_error_rate="0/2", verify_demoted=False,
                knowledge_digests={"topology": "d1"})
    base.update(kw)
    return VerdictSnapshot(**base)


def test_스냅샷은_케이스당_하나로_덮어쓴다():
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap(confidence="high"))
    store.put(_snap(confidence="low"))
    assert store.get("c-1").confidence == "low"
    assert store.get("없는-케이스") is None


def test_history_shown은_기본이_비어있고_기록할_수_있다():
    # P8의 이력 검색이 아직 없다. 필드를 지금 열어 두지 않으면 그 사이 종결된
    # 케이스는 "이력을 보여준 게 도움이 됐나"를 영영 답하지 못한다.
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap())
    assert store.get("c-1").history_shown == []
    store.put(_snap(history_shown=[{"case_id": "c-0", "tier": 1}]))
    assert store.get("c-1").history_shown[0]["tier"] == 1


def test_스냅샷_보존은_종결_시각으로_거른다():
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap("c-old", closed_at=T - timedelta(days=800)))
    store.put(_snap("c-new", closed_at=T))
    assert store.prune_before(T - timedelta(days=730)) == 1
    assert store.get("c-old") is None and store.get("c-new") is not None


def test_실패_종결도_스냅샷을_남길_수_있다():
    # 실패 종결을 빼면 분모에 생존 편향이 생긴다 — 잘 끝난 케이스만 세게 된다.
    store = InMemoryVerdictSnapshotStore()
    store.put(_snap(outcome="failed", verdict_type=None, root_cause_component=None,
                    confidence=None))
    assert store.get("c-1").outcome == "failed"


def test_알_수_없는_필드는_거부한다():
    with pytest.raises(Exception):
        VerdictSnapshot(**{**_snap().model_dump(mode="json"), "새필드": 1})
```

`tests/infrastructure/test_mongo_store.py` 끝에 추가:

```python
def test_mongo_스냅샷은_케이스당_하나다(db):
    from src.domain.snapshot import VerdictSnapshot
    from src.infrastructure.mongo_store import MongoVerdictSnapshotStore
    store = MongoVerdictSnapshotStore(db)
    base = dict(case_id="c-1", closed_at=T, gbm="mx", fct="gumi", fingerprint="fp",
                origin="patrol", outcome="closed", verdict_type="data_loss",
                root_cause_component="plan-sync", confidence="high", rounds=2,
                evidence_count=3, task_error_rate="0/2", verify_demoted=False,
                knowledge_digests={"topology": "d1"})
    store.put(VerdictSnapshot(**base))
    store.put(VerdictSnapshot(**{**base, "confidence": "low"}))
    assert store.get("c-1").confidence == "low"
    assert db.verdict_snapshots.count_documents({"case_id": "c-1"}) == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/domain/test_snapshot.py tests/infrastructure/test_mongo_store.py -q -k "스냅샷 or history_shown"`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.domain.snapshot'`

- [ ] **Step 3: 최소 구현**

`src/domain/snapshot.py`를 만든다:

```python
"""종결 시점 판정 스냅샷 — 스펙 §4.5.

retention ①이 closed_case_evidence_d(기본 90일)에 store.purge_case로 Verdict·
증거·케이스 파일을 전부 지운다. 살아남는 것은 CaseRecord.verdict_summary 200자뿐이라,
100일 뒤 사람이 실제 원인을 알려줘도 대조할 구조화 데이터가 없다 — **일방향 문이다.**
그래서 종결 시점에 별도로 박제한다. 보존기한도 다른 것들보다 훨씬 길다.

history_shown은 "이력 검색이 frame에 무엇을 먹였는가"다. 지금은 항상 비어 있지만
(이력 검색은 P8) 필드를 나중에 더하면 그 사이 종결된 케이스는 "이력을 보여준 게
도움이 됐나, 앵커링이었나"를 영영 답하지 못한다.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel


class VerdictSnapshot(StrictModel):
    """케이스가 닫힌 시점의 기계 판정 — 나중에 사람 라벨과 대조할 대상."""
    case_id: str
    closed_at: datetime
    gbm: str
    fct: str
    fingerprint: str
    target_locator: str | None = None
    origin: Literal["human", "patrol"] = "patrol"
    outcome: Literal["closed", "failed"]     # failed도 남긴다 — 빼면 분모에 생존 편향
    verdict_type: str | None = None
    root_cause_component: str | None = None
    alternates: list[str] = []               # 다중 RCA 후보(P6에서 채운다)
    confidence: str | None = None
    rounds: int = 0
    evidence_count: int = 0
    task_error_rate: str = "없음"
    verify_demoted: bool = False
    knowledge_digests: dict[str, str] = {}
    history_shown: list[dict] = []           # [{"case_id": ..., "tier": ...}] — P8이 채운다


class VerdictSnapshotPort(ABC):
    @abstractmethod
    def put(self, snapshot: VerdictSnapshot) -> None:
        """케이스당 하나로 덮어쓴다."""
        ...

    @abstractmethod
    def get(self, case_id: str) -> VerdictSnapshot | None: ...

    @abstractmethod
    def prune_before(self, before: datetime) -> int:
        """closed_at이 before 이전인 스냅샷을 삭제하고 건수를 반환한다."""
        ...


class InMemoryVerdictSnapshotStore(VerdictSnapshotPort):
    def __init__(self):
        self._snapshots: dict[str, VerdictSnapshot] = {}

    def put(self, snapshot):
        self._snapshots[snapshot.case_id] = snapshot

    def get(self, case_id):
        return self._snapshots.get(case_id)

    def prune_before(self, before):
        stale = [cid for cid, s in self._snapshots.items() if s.closed_at < before]
        for cid in stale:
            del self._snapshots[cid]
        return len(stale)
```

`src/infrastructure/mongo_store.py`에 구현을 더한다:

```python
class MongoVerdictSnapshotStore(VerdictSnapshotPort):
    """종결 판정 스냅샷 — purge_case가 건드리지 않는 별도 컬렉션이다."""

    def __init__(self, db: Database):
        self._db = db

    def put(self, snapshot: VerdictSnapshot) -> None:
        doc = snapshot.model_dump(mode="json")
        self._db.verdict_snapshots.update_one({"case_id": snapshot.case_id},
                                              {"$set": doc}, upsert=True)

    def get(self, case_id):
        doc = self._db.verdict_snapshots.find_one({"case_id": case_id})
        if doc is None:
            return None
        return VerdictSnapshot.model_validate({k: v for k, v in doc.items() if k != "_id"})

    def prune_before(self, before):
        # closed_at은 ISO 문자열이라 DB $lt로 거르면 마이크로초 유무로 순서가
        # 어긋난다(모듈 docstring) — Python에서 판정한다.
        stale = [doc["_id"] for doc in
                 self._db.verdict_snapshots.find({}, {"_id": 1, "closed_at": 1})
                 if datetime.fromisoformat(doc["closed_at"]) < before]
        if stale:
            self._db.verdict_snapshots.delete_many({"_id": {"$in": stale}})
        return len(stale)
```

`ensure_indexes`에 더한다:

```python
    db.verdict_snapshots.create_index("case_id", unique=True)
```

`src/config/schema_app.py`의 `RetentionConfig`에 더한다:

```python
    # 판정 스냅샷 보존기한. 다른 것들보다 훨씬 길다 — 사람 라벨은 몇 달 뒤에
    # 오는데 그때 대조할 대상이 없으면 스냅샷을 남긴 의미가 사라진다.
    snapshots_d: int = 730
```

`src/infrastructure/retention.py`의 스윕에 규칙을 더한다(이벤트와 같은 방식):

```python
    # ⑦ 오래된 판정 스냅샷 — snapshots가 주입되지 않은 호출부는 건너뛴다
    if snapshots is not None:
        try:
            counts["snapshots"] = snapshots.prune_before(
                now - timedelta(days=retention.snapshots_d))
        except Exception:                                          # noqa: BLE001
            pass
```

시그니처와 `counts` 초기값에 `snapshots`를 더한다.

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Snapshot the verdict before retention erases it

retention이 90일에 purge_case로 Verdict·증거·케이스 파일을 전부 지운다. 살아남는
것은 verdict_summary 200자뿐이라, 100일 뒤 사람이 실제 원인을 알려줘도 대조할
구조화 데이터가 없다 — 이건 되돌릴 수 없는 일방향 문이다.

history_shown을 지금 열어 두는 이유도 같다. 이력 검색은 아직 없지만(P8), 필드를
나중에 더하면 그 사이 종결된 모든 케이스가 "이력을 보여준 게 도움이 됐나,
앵커링이었나"를 영영 답하지 못한다.

실패 종결도 남긴다. 빼면 분모에 생존 편향이 생겨 잘 끝난 케이스만 세게 된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 종결 시 스냅샷을 남긴다

**Files:**
- Modify: `src/application/worker.py`, `src/patrol/daemon.py`
- Test: `tests/application/test_worker.py`

**Interfaces:**
- Consumes: Task 6의 `VerdictSnapshotPort`, Task 3의 `build_report_model`(단계·에러율 유도 재사용)
- Produces: `InvestigationWorker(..., snapshots=None)`

Task 6이 그릇을 만들었고 이 태스크가 **채운다.** 나눈 이유는 리뷰어가 "스냅샷 모델이 옳은가"와 "워커가 옳은 값을 넣는가"를 따로 판정할 수 있어야 하기 때문이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/application/test_worker.py` 끝에 추가:

```python
async def test_정상_종결은_판정_스냅샷을_남긴다():
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {"topology": "d1"},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "closed"
    snap = snapshots.get("c-1")
    assert snap is not None and snap.outcome == "closed"
    assert snap.verdict_type and snap.root_cause_component
    assert snap.knowledge_digests == {"topology": "d1"}
    assert snap.history_shown == []          # P8 전까지는 비어 있다


async def test_실패_종결도_판정_스냅샷을_남긴다():
    # 실패 종결을 빼면 분모에 생존 편향이 생긴다.
    from src.domain.snapshot import InMemoryVerdictSnapshotStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    snapshots = InMemoryVerdictSnapshotStore()
    _open_case(repo, store)
    def broken_deps(g, f):
        raise RuntimeError("deps 조립 실패")
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=broken_deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 snapshots=snapshots)
    assert await worker.run_once("c-1") == "failed"
    snap = snapshots.get("c-1")
    assert snap is not None and snap.outcome == "failed" and snap.verdict_type is None


async def test_스냅샷_스토어가_없어도_종결은_그대로_된다():
    # snapshots=None인 호출부(옛 테스트·CLI 일부)가 깨지면 안 된다.
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {})
    assert await worker.run_once("c-1") == "closed"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/application/test_worker.py -q -k "판정_스냅샷"`
Expected: FAIL — `TypeError: unexpected keyword argument 'snapshots'`

- [ ] **Step 3: 최소 구현**

`src/application/worker.py`의 `__init__`에 인자를 더한다:

```python
                max_wall_clock_s: float | None = None,
                snapshots=None):
```
```python
        self._snapshots = snapshots      # VerdictSnapshotPort | None
```

기록 헬퍼를 더한다:

```python
    def _record_snapshot(self, case_id: str, *, outcome: str) -> None:
        """종결 시점의 기계 판정을 박제한다 — retention이 Verdict를 지운 뒤에도 남는다.

        기록 실패가 이미 끝난 종결을 뒤집지 않는다(발행 훅과 같은 계약). 순서도
        같은 이유로 종결 뒤다 — 스냅샷을 남기려다 케이스가 안 닫히면 본말전도다.
        """
        if self._snapshots is None:
            return
        try:
            record = self._repo.get(case_id)
            verdict = self._store.get_verdict(case_id)
            case_file = self._store.get_case_file(case_id) or {}
            model = build_report_model(record, verdict=verdict,
                                       evidence=self._store.list_evidence(case_id),
                                       case_file=case_file, clock=self._clock)
            verify_stage = next((s for s in model.stages if s.stage == "verify"), None)
            self._snapshots.put(VerdictSnapshot(
                case_id=case_id, closed_at=self._clock(), gbm=record.gbm, fct=record.fct,
                fingerprint=record.fingerprint, target_locator=record.target_locator,
                origin=record.origin, outcome=outcome,
                verdict_type=verdict.verdict_type if verdict else None,
                root_cause_component=(verdict.root_cause.component
                                      if verdict and verdict.root_cause else None),
                confidence=verdict.confidence if verdict else None,
                rounds=model.round_no or 0, evidence_count=len(model.evidence),
                task_error_rate=model.task_error_rate,
                verify_demoted=bool(verify_stage and verify_stage.mark == "warn"),
                knowledge_digests=self._knowledge_digests_for_site(record.gbm, record.fct)))
        except Exception:                                          # noqa: BLE001
            pass
```

`_finish`의 closed 경로에서 `_emit_status` 뒤, `_emit_closed` **앞**에 부른다:

```python
        self._emit_status(record.id, "closed")
        self._record_snapshot(record.id, outcome="closed")
        await self._emit_closed(record.id)
```

`_fail`의 `close_case` 성공 뒤에도 부른다:

```python
            self._emit_status(case_id, "closed", reason=reason)
            self._record_snapshot(case_id, outcome="failed")
            await self._emit_closed(case_id)
```

import을 더한다:

```python
from src.domain.snapshot import VerdictSnapshot
from src.presentation.report_model import build_report_model
```

`src/patrol/daemon.py`의 워커 생성에 스토어를 넘긴다(`__init__`에 `snapshots=None` 인자를 더하고 `_run_patrol`이 `p.snapshots`를 넘긴다):

```python
            max_wall_clock_s=self.app.investigations.max_wall_clock_s,
            snapshots=self.snapshots,
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Record what the agent concluded, at the moment it concluded

스냅샷 기록을 종결 뒤에 두는 것은 순서가 중요해서다 — 기록하려다 케이스가 안
닫히면 본말전도다. 그래서 발행 훅과 같은 계약으로 실패를 삼킨다.

verify_demoted를 ReportModel의 단계 판정에서 가져오는 이유는 그 규칙이 이미
한 곳에 있기 때문이다. 여기서 verify_attempts를 다시 해석하면 보고서와 스냅샷이
언젠가 다른 말을 한다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: §5에 스냅샷 출처를 적는다

**Files:**
- Modify: `src/presentation/report.py`
- Test: `tests/presentation/test_report.py`

Task 5가 `partial`/`salvage_error`를 케이스 파일에 넣고 Task 3이 모델에 실었다. 보고서가 **그것을 말해야** 조용한 생략이 아니게 된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_실패_스냅샷은_조사_경위에_출처를_밝힌다():
    text = render_report(RECORD, verdict=None, evidence=[],
                         case_file={"partial": True, "round": 2,
                                    "plan_tasks": [{"id": "t1", "role": "data_prober",
                                                    "status": "running"}]},
                         clock=lambda: T)
    assert "실패 시점 부분 스냅샷" in text


def test_구제_실패는_흔적_유실을_명시한다():
    text = render_report(RECORD, verdict=None, evidence=[],
                         case_file={"partial": True, "salvage_error": "RuntimeError: 엔진 없음"},
                         clock=lambda: T)
    assert "조사 흔적 구제 실패" in text and "RuntimeError" in text


def test_정상_종결에는_출처_줄이_없다():
    text = render_report(RECORD, verdict=None, evidence=[],
                         case_file={"round": 1}, clock=lambda: T)
    assert "부분 스냅샷" not in text
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/presentation/test_report.py -q -k "스냅샷 or 구제"`
Expected: FAIL — `assert '실패 시점 부분 스냅샷' in text`

- [ ] **Step 3: 최소 구현**

`_section5`의 첫 줄들에 출처를 붙인다:

```python
def _section5(model) -> str:
    lines = ["## 5. 조사 경위"]
    # 부분 스냅샷임을 숨기면 "라운드 2"가 완결된 조사처럼 읽힌다 — 조용한 생략이다.
    if model.partial:
        lines.append("- 스냅샷: 실패 시점 부분 스냅샷(조사 미완)")
        if model.salvage_error:
            lines.append(f"- 조사 흔적 구제 실패: {model.salvage_error}")
    lines.append(f"- 라운드: {model.round_no if model.round_no is not None else '없음'}")
    lines.append("- 태스크 현황:")
    ...
```

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Say when the trail is only a partial snapshot

구제한 흔적을 그냥 보여주면 "라운드 2"가 완결된 조사처럼 읽힌다. 실패로 중단된
스냅샷이라는 사실과, 구제 자체가 실패한 경우 그 사유를 §5 첫머리에 밝힌다 —
"무엇을 확인 안 했나"의 명시가 이 시스템의 신뢰 조건이다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: HTML 렌더러

**Files:**
- Create: `src/presentation/report_html.py`
- Modify: `src/presentation/report.py`, `src/config/schema_app.py`, `src/patrol/daemon.py`, `src/__main__.py`
- Test: `tests/presentation/test_report_html.py` (신설)

**Interfaces:**
- Consumes: Task 3의 `ReportModel`
- Produces: `render_html(model) -> str`, `write_report(text, *, output_dir, case_id, suffix="md")`, `ReportConfig.format: Literal["md","html"]`

**템플릿 엔진을 쓰지 않는다**(스펙 §6 YAGNI). 내장 템플릿 하나 + f-string이고, 사용자 템플릿은 v2 범위 밖이다. HTML 이스케이프는 반드시 한다 — 증상·narrative·오류 메시지에 `<`가 들어올 수 있고, 그중 일부는 LLM이 쓴 텍스트다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/presentation/test_report_html.py`를 새로 만든다:

```python
from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.presentation.report_html import render_html
from src.presentation.report_model import build_report_model

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _model(**kw):
    record = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp",
                        symptom=kw.pop("symptom", "OEE 512%"), t0=T,
                        created_at=T, updated_at=T)
    return build_report_model(record, verdict=kw.pop("verdict", None), evidence=[],
                              case_file=kw.pop("case_file", {"round": 1}), clock=lambda: T)


def test_다섯_절이_전부_나온다():
    html = render_html(_model())
    for heading in ("1. 요약", "2. 판정", "3. 조치 권고", "4. 증거", "5. 조사 경위"):
        assert heading in html
    assert html.startswith("<!DOCTYPE html>")


def test_단계_체크리스트가_표로_나온다():
    html = render_html(_model())
    assert "<table" in html and "가설 수립" in html


def test_사람이_쓰지_않은_텍스트도_이스케이프한다():
    # 증상·narrative·오류 메시지에는 LLM이 쓴 텍스트가 섞인다. 그대로 넣으면
    # 보고서를 여는 것만으로 임의 마크업이 실행된다.
    verdict = Verdict(verdict_type="data_loss", confidence="high",
                      root_cause=CauseLink(component="<script>alert(1)</script>",
                                           evidence_ids=["ev-1"]),
                      narrative="n")
    html = render_html(_model(symptom="<img src=x onerror=alert(1)>", verdict=verdict))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_렌더링이_실패해도_raise하지_않는다():
    class Broken:
        stages = property(lambda self: (_ for _ in ()).throw(RuntimeError("깨짐")))
    out = render_html(Broken())
    assert "보고서 조립 실패" in out
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/presentation/test_report_html.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.presentation.report_html'`

- [ ] **Step 3: 최소 구현**

`src/presentation/report_html.py`를 만든다:

```python
"""HTML 보고서 렌더러 — 스펙 §4.1.

ReportModel만 보고 문자열을 만든다(report.py의 마크다운 렌더러와 같은 소스).
템플릿 엔진을 쓰지 않는 이유는 스펙 §6이 YAGNI로 기각했기 때문이다 — 내장
템플릿 하나면 충분하고, 사용자 템플릿은 v2 범위 밖이다.

이스케이프는 선택이 아니다: 증상·narrative·component·오류 메시지에는 LLM이 쓴
텍스트와 대상 시스템의 응답이 섞여 들어온다. 그대로 넣으면 보고서를 여는 것만으로
임의 마크업이 실행된다.
"""
from html import escape

_MARKS = {"ok": "✅", "fail": "❌", "warn": "⚠", "skip": "⬜"}

_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 60rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; }
th { background: #f5f5f5; }
h2 { border-bottom: 1px solid #eee; padding-bottom: 0.3rem; margin-top: 2rem; }
.partial { background: #fff8e1; border-left: 4px solid #ffb300; padding: 0.6rem 1rem; }
"""


def _e(value) -> str:
    return escape(str(value), quote=True)


def _rows(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _bullets(items: list[str]) -> str:
    if not items:
        return "<p>없음</p>"
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def render_html(model) -> str:
    """ReportModel에서 HTML 보고서를 조립한다. 절대 raise하지 않는다."""
    try:
        return _render(model)
    except Exception as exc:            # noqa: BLE001 — 마크다운 렌더러와 같은 계약
        return (f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                f"<h1>보고서 조립 실패</h1><p>{_e(f'{type(exc).__name__}: {exc}')}</p>"
                f"</body></html>")


def _render(model) -> str:
    record = model.record
    verdict = model.verdict
    stage_rows = [[_e(s.label), _MARKS.get(s.mark, "?"), _e(s.note or "없음")]
                  for s in model.stages]
    summary_rows = [
        ["케이스 id", _e(record.id)], ["스코프", _e(f"{record.gbm}/{record.fct}")],
        ["개설 경로", _e(record.origin)], ["증상", _e(record.symptom)],
        ["T0", _e(record.t0.isoformat())],
        ["판정", _e(f"{verdict.verdict_type}" if verdict else
                  f"판정 없음(종결 사유: {record.closed_reason or '미상'})")],
        ["신뢰도", _e(verdict.confidence if verdict else "없음")],
        ["태스크 에러율", _e(model.task_error_rate)],
    ]

    cause_items = []
    if verdict is not None and verdict.root_cause is not None:
        ids = ", ".join(verdict.root_cause.evidence_ids) or "없음"
        cause_items.append(f"근본 원인: {_e(verdict.root_cause.component)} (증거: {_e(ids)})")
    for c in (verdict.contributing if verdict else []):
        ids = ", ".join(c.evidence_ids) or "없음"
        rel = f" — {_e(c.relation)}" if c.relation else ""
        cause_items.append(f"기여 요인: {_e(c.component)} (증거: {_e(ids)}){rel}")

    evidence_rows = [[_e(ev.id), _e(ev.source),
                      _e(ev.as_of.isoformat() if ev.as_of else "-"),
                      "완전" if ev.complete else "⚠ 불완전",
                      _e(ev.effective_as_of.isoformat() if ev.effective_as_of else "-"),
                      _e((model.evidence_summaries or {}).get(ev.id,
                                                              (ev.body_digest or "")[:12]))]
                     for ev in model.evidence]

    task_rows = [[_e(t.get("id", "?")), _e(t.get("role", "?")), _e(t.get("status", "?")),
                  _e("미조사" if t.get("status") in ("pending", "cancelled")
                     else (t.get("error") or ""))]
                 for t in model.plan_tasks]

    refuted = [f"{_e(h.get('id', '?'))}: {_e(h.get('statement', ''))} "
               f"(반박 증거: {_e(', '.join(h.get('refuting_ids') or []) or '없음')})"
               for h in model.hypotheses if h.get("status") == "refuted"]

    partial_note = ""
    if model.partial:
        extra = (f"<br>조사 흔적 구제 실패: {_e(model.salvage_error)}"
                 if model.salvage_error else "")
        partial_note = (f"<p class='partial'>실패 시점 부분 스냅샷(조사 미완){extra}</p>")

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>케이스 {_e(record.id)} 보고서</title><style>{_STYLE}</style></head><body>
<h1>케이스 {_e(record.id)} 보고서</h1>
<p>작성 시각: {_e(model.generated_at.isoformat())}</p>
<h2>1. 요약</h2>
{_rows(["항목", "값"], summary_rows)}
<h3>조사 단계</h3>
{_rows(["단계", "상태", "비고"], stage_rows)}
<h2>2. 판정</h2>
{_bullets(cause_items)}
<h3>caveat</h3>
{_bullets([_e(c) for c in (verdict.caveats if verdict else [])])}
<h2>3. 조치 권고</h2>
{_bullets([_e(r) for r in (verdict.recommendations if verdict else [])])}
<h2>4. 증거</h2>
{_rows(["id", "출처", "as_of", "완전성", "effective_as_of", "요지"], evidence_rows)
 if evidence_rows else "<p>없음</p>"}
<h2>5. 조사 경위</h2>
{partial_note}
<p>라운드: {_e(model.round_no if model.round_no is not None else "없음")}</p>
{_rows(["id", "역할", "status", "비고"], task_rows) if task_rows else "<p>태스크 없음</p>"}
<h3>기각된 가설</h3>
{_bullets(refuted)}
<h3>검증 문제</h3>
{_bullets([_e(p) for p in model.verify_problems])}
</body></html>
"""
```

`src/presentation/report.py`의 `write_report`에 확장자 인자를 더한다:

```python
def write_report(text: str, *, output_dir: str, case_id: str, suffix: str = "md") -> str:
    """`{output_dir}/{case_id}.{suffix}`에 UTF-8로 쓰고 경로를 반환한다."""
    try:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{case_id}.{suffix}"
        path.write_text(text, encoding="utf-8")
        return str(path)
    except OSError:
        return ""
```

`src/config/schema_app.py`의 `ReportConfig`에 더한다:

```python
class ReportConfig(StrictModel):
    output_dir: str = "output"
    format: Literal["md", "html"] = "html"   # 기본 산출물은 HTML(스펙 §4.1)
    mail: MailConfig = MailConfig()
```

`src/patrol/daemon.py`의 `_render_case_report`가 포맷을 고르게 한다:

```python
    def _render_case_report(self, case_id: str) -> str:
        ...
        model = build_report_model(record, verdict=verdict, evidence=evidence,
                                   case_file=case_file, clock=self.clock,
                                   evidence_summaries=evidence_summaries)
        return (render_html(model) if self.report_cfg.format == "html" else render_md(model))
```

`_publish_report`의 `write_report`에 `suffix=self.report_cfg.format`을 넘긴다.

`src/__main__.py`의 `case show --report`가 두 확장자를 다 찾게 한다(`.{format}` 우선, 없으면 즉석 렌더).

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 실제 브라우저 파서로 검증한다**

```bash
.venv/bin/python - <<'EOF'
from datetime import datetime, timezone
from html.parser import HTMLParser
from src.domain.cases import CaseRecord
from src.presentation.report_html import render_html
from src.presentation.report_model import build_report_model

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
record = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp",
                    symptom="<script>bad</script>", t0=T, created_at=T, updated_at=T)
html = render_html(build_report_model(record, verdict=None, evidence=[],
                                      case_file={"partial": True, "round": 2},
                                      clock=lambda: T))
class P(HTMLParser):
    def __init__(self): super().__init__(); self.tags = []
    def handle_starttag(self, tag, attrs): self.tags.append(tag)
p = P(); p.feed(html)
assert "script" not in p.tags, "이스케이프 실패 — script 태그가 실렸다"
assert p.tags.count("table") >= 2
print(f"태그 파싱 OK — table {p.tags.count('table')}개, script 0개")
EOF
```

- [ ] **Step 6: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Render the report as HTML from the same model

기본 산출물을 HTML로 바꾼다. 마크다운 렌더러와 같은 ReportModel을 보므로 두
렌더러가 다른 말을 할 수 없다 — 그게 유도를 먼저 분리한 이유다.

이스케이프는 선택이 아니다. 증상·narrative·component·오류 메시지에는 LLM이 쓴
텍스트와 대상 시스템의 응답이 섞여 들어오고, 그대로 넣으면 보고서를 여는
것만으로 임의 마크업이 실행된다. HTMLParser로 실측 확인했다.

템플릿 엔진은 넣지 않는다(스펙 §6) — 내장 템플릿 하나면 충분하고 사용자 템플릿은
v2 범위 밖이다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: HTML 메일

**Files:**
- Modify: `src/presentation/mail.py`, `src/patrol/daemon.py`
- Test: `tests/presentation/test_mail.py`

**Interfaces:**
- Produces: `MailSenderPort.send(subject, body, *, recipients, html=None)`

`SmtpSender.send`가 `message.set_content(body)`뿐이라 **text/plain만 발송된다.** HTML 보고서를 그대로 넣으면 수신자가 태그 원문을 본다 — 요구 6이 여기서 막힌다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/presentation/test_mail.py` 끝에 추가:

```python
def test_HTML_본문은_대체_파트로_실린다(monkeypatch):
    # set_content만 쓰면 text/plain 하나뿐이라 수신자가 태그 원문을 본다.
    from src.config.schema_app import MailConfig
    from src.presentation.mail import SmtpSender
    sent = {}

    async def fake_send(message, **kw):
        sent["message"] = message

    import sys, types
    fake_mod = types.ModuleType("aiosmtplib")
    fake_mod.send = fake_send
    monkeypatch.setitem(sys.modules, "aiosmtplib", fake_mod)

    cfg = MailConfig(enabled=True, host="smtp.x", sender="a@x", recipients=["b@x"])
    import asyncio
    asyncio.run(SmtpSender(cfg).send("제목", "평문 본문",
                                     recipients=["b@x"], html="<p>본문</p>"))
    types_seen = {part.get_content_type() for part in sent["message"].walk()}
    assert "text/plain" in types_seen and "text/html" in types_seen


def test_html이_없으면_평문만_보낸다(monkeypatch):
    from src.config.schema_app import MailConfig
    from src.presentation.mail import SmtpSender
    sent = {}

    async def fake_send(message, **kw):
        sent["message"] = message

    import sys, types
    fake_mod = types.ModuleType("aiosmtplib")
    fake_mod.send = fake_send
    monkeypatch.setitem(sys.modules, "aiosmtplib", fake_mod)

    cfg = MailConfig(enabled=True, host="smtp.x", sender="a@x", recipients=["b@x"])
    import asyncio
    asyncio.run(SmtpSender(cfg).send("제목", "평문", recipients=["b@x"]))
    types_seen = {part.get_content_type() for part in sent["message"].walk()}
    assert "text/html" not in types_seen
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/presentation/test_mail.py -q -k "HTML_본문 or html이_없으면"`
Expected: FAIL — `TypeError: send() got an unexpected keyword argument 'html'`

- [ ] **Step 3: 최소 구현**

`src/presentation/mail.py`의 포트와 두 구현을 고친다:

```python
class MailSenderPort(ABC):
    @abstractmethod
    async def send(self, subject: str, body: str, *, recipients: list[str],
                   html: str | None = None) -> None: ...
```

```python
class NullSender(MailSenderPort):
    async def send(self, subject, body, *, recipients, html=None) -> None:
        logger.info("메일 발송 생략(NullSender): subject=%s recipients=%d건",
                    subject, len(recipients))
```

```python
    async def send(self, subject, body, *, recipients, html=None) -> None:
        ...
        message.set_content(body)
        # HTML은 대체 파트로 얹는다 — set_content만 쓰면 text/plain 하나뿐이라
        # 수신자가 태그 원문을 본다. 평문 파트를 남겨 두는 이유는 HTML을 못 읽는
        # 클라이언트와 검색 인덱스가 여전히 내용을 읽을 수 있어야 하기 때문이다.
        if html is not None:
            message.add_alternative(html, subtype="html")
```

`send_report`가 `html`을 받아 넘기게 한다:

```python
async def send_report(case_id: str, subject: str, body: str, *, sender: MailSenderPort,
                      ledger: SendLedgerPort, cfg: MailConfig, clock: Clock,
                      html: str | None = None) -> str:
    ...
    await sender.send(subject, body, recipients=cfg.recipients, html=html)
```

`src/patrol/daemon.py`의 `_publish_report`가 포맷에 맞춰 넘긴다 — `report_cfg.format == "html"`이면 본문을 `html=`로 보내고 평문 자리에는 안내 한 줄을 넣는다:

```python
            if self.report_cfg.format == "html":
                await send_report(case_id, subject,
                                  f"HTML 보고서는 첨부/대체 본문을 참고하라: {path}",
                                  sender=self._mail_sender(), ledger=self.ledger,
                                  cfg=self.report_cfg.mail, clock=self.clock, html=text)
            else:
                await send_report(case_id, subject, text, sender=self._mail_sender(),
                                  ledger=self.ledger, cfg=self.report_cfg.mail,
                                  clock=self.clock)
```

`_render_pending`(재시도 경로)도 같은 분기를 타야 한다 — 재시도가 평문으로만 나가면 첫 발송과 다른 것이 간다.

- [ ] **Step 4: 통과를 확인한다** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "$(cat <<'EOF'
Send the HTML report as an HTML mail

SmtpSender가 set_content(body)뿐이라 text/plain만 나갔다 — HTML 보고서를 그대로
넣으면 수신자가 태그 원문을 본다.

평문 파트를 남기는 것은 HTML을 못 읽는 클라이언트와 검색 인덱스를 위해서다.
재시도 경로도 같은 분기를 타야 한다 — 첫 발송과 다른 것이 가면 2상 멱등의
의미가 없다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 태스크 순서

의존이 한 곳에서 역행한다: **Task 1이 `InMemoryVerdictSnapshotStore`를 필요로 하므로 Task 6을 먼저 한다.**

```
6 → 1 → 2 → 3 → 4 → 5 → 7 → 8 → 9 → 10
```

`6`(스냅샷 그릇) → `1`(Persistence 이름화) → `2`(관용적 스냅샷) → `3`(모델) → `4`(md 렌더러) → `5`(구제) → `7`(스냅샷 기록) → `8`(§5 출처) → `9`(HTML) → `10`(HTML 메일).

## 완료 기준

- [ ] `.venv/bin/python -m pytest tests/ -q` 전건 통과, 기준선(279)보다 테스트 수 증가
- [ ] `render_report`의 기존 호출부 2곳(daemon·CLI)이 무변경으로 동작한다
- [ ] mistune으로 렌더한 마크다운에 `<table>`이 2개 이상이다(§1 체크리스트 + §5 태스크)
- [ ] `HTMLParser`로 파싱한 HTML에 `script` 태그가 0개다
- [ ] 실패로 종결된 케이스의 보고서 §5에 라운드·태스크가 실제로 찍힌다
- [ ] `grep -rn "build_persistence(" src/ tests/`의 호출부가 전부 속성 접근이다

## 이 계획이 **하지 않는** 것

| 미포함 | 어디로 |
|---|---|
| 증거 표의 "무엇을 물었나"(요청 스펙) | P4. POST 프로버가 생겨야 실을 값이 있다. `ReportModel`의 `EvidenceRecord`에 필드가 붙는 시점이다 |
| Timeline(라운드별 시각) | P6. 이벤트 로그(`case_events`)를 읽어야 하는데, 그 읽기 경로가 웹 API와 함께 온다 |
| 다중 RCA 후보(`Verdict.alternates`) | P6. 도메인 모델·verify 규칙·벤치 채점을 동시에 건드린다. `VerdictSnapshot.alternates` 필드만 미리 열어 뒀다 |
| `case label` CLI와 `RootCauseLabel` | P8. 스냅샷이 대조 대상을 확보했으니 라벨은 그 위에 얹는다 |
| `history_shown` 채우기 | P8. 필드만 열어 뒀다 |
| 사용자 지정 HTML 템플릿 | 스펙 §6 YAGNI. `ReportConfig.format`만 있고 `template`은 없다 |
