# 계획 3a: 조사 엔진 부품 (도메인·State·스토어·LLM·브리핑·서브에이전트) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스펙 §2의 조사 엔진을 이루는 부품 — 케이스 도메인 모델(인과 사슬 Verdict 포함), CaseState+리듀서, 케이스 Store, LLM 층(실구현+스크립트 가짜), 브리핑 조립(토폴로지 역추적), `create_agent` 기반 서브에이전트 3종 — 을 구현한다. 그래프 노드·배선·E2E는 계획 3b.

**Architecture:** domain에 케이스 모델과 Store 포트, application에 스키마·브리핑·서브에이전트, infrastructure에 LLM 어댑터와 InMemory Store. 서브에이전트는 스펙 §2.4 확정대로 `create_agent` 유계 ReAct 루프 — 도구는 계획 2의 AdapterSet 래핑이고, **도구가 결과 본문을 Store에 넣고 증거 id를 돌려주는** 구조라 서브에이전트 보고가 자연히 id 인용이 된다. 모든 LLM 호출 지점은 주입 가능(스크립트 가짜로 결정론 테스트 — 스펙 §5.5).

**Tech Stack:** 계획 2 위에 langgraph, langchain, langchain-core, langchain-openai 추가.

## Global Constraints (스펙에서 발췌 — 모든 태스크에 적용)

- **서브에이전트는 절대 raise하지 않는다**: 최외곽 catch-all이 예외·예산 초과·파싱 실패를 `status:"error"` SubagentReport로 변환 (§2.4 — Send 브랜치 하나의 예외가 superstep을 죽인다).
- **예산 = `recursion_limit`** (루프 스텝 상한, §2.4 확정). config `engine.subagent_budgets.<role>`이 값의 출처.
- **증거는 id로**: 도구 결과 본문은 Store에, 반환은 id+요약+봉투 메타. State에는 digest만 (§2.3).
- **시계 주입**: 노드·도구에서 `datetime.now()` 금지. 기준 시각은 케이스 T0와 주입 clock (§2.5).
- **Verdict는 인과 사슬**: root_cause 1 + contributing 0..N, 각각 컴포넌트 id+증거 인용. `inconclusive`/`degraded`면 root_cause 없음 허용 (§2.3).
- **LLM 구조화 출력**: 본문 JSON → pydantic 파싱(`parse_structured`). 파싱 실패 처리는 호출자 계약(재시도 1회는 노드 몫 — 3b).
- **unknown key 거부**(StrictModel), **코드 주석·문서·오류 메시지는 한국어**(라이브러리 원문 인용 허용), 기동 거부 철학 유지.
- pymongo 핀: langgraph-checkpoint-mongodb는 **계획 4에서 도입** — 이번엔 InMemory 체크포인터 범위라 핀 변경 없음.

## File Structure

```
src/domain/
├── case.py              # Case, EvidenceRef, Hypothesis, PlanTask, CauseLink, Verdict
└── store.py             # CaseStorePort + InMemoryCaseStore
src/application/
├── __init__.py
├── state.py             # CaseState + merge_by_id 리듀서
├── schemas.py           # FrameOutput, IntegrateOutput, SubagentReport, parse_structured
├── briefing.py          # upstream_slice(토폴로지 역추적), build_briefing
└── subagents.py         # make_tools(역할별 도구), run_subagent(create_agent 유계 루프)
src/infrastructure/llm.py # build_chat_model(지연 import), ScriptedLLM
tests/domain/test_case.py, test_store.py
tests/application/__init__.py, test_state.py, test_schemas.py, test_briefing.py, test_subagents.py
tests/infrastructure/test_llm.py
```

---

### Task 1: LangGraph 의존성 + 케이스 도메인 모델

**Files:**
- Modify: `requirements.txt`
- Create: `src/domain/case.py`, `tests/application/__init__.py`
- Test: `tests/domain/test_case.py`

**Interfaces:**
- Produces (전부 StrictModel):
  - `Role = Literal["data_prober", "code_tracer", "recompute_verifier"]`
  - `VerdictType = Literal["logic_bug","data_loss","config_error","stale_data","external","inconclusive","degraded"]`
  - `EvidenceRef(id, source, summary, as_of: datetime|None=None, complete: bool=True, effective_as_of: datetime|None=None)`
  - `Hypothesis(id, statement, status: Literal["open","supported","refuted"]="open", supporting_ids: list[str]=[], refuting_ids: list[str]=[])`
  - `PlanTask(id, goal, role: Role, input_evidence_ids: list[str]=[], priority: int=100, status: Literal["pending","running","ok","error","cancelled"]="pending", result_summary: str|None=None, result_evidence_ids: list[str]=[], error: str|None=None)`
  - `CauseLink(component, evidence_ids: list[str], relation: str|None=None)`
  - `Verdict(verdict_type: VerdictType, root_cause: CauseLink|None, contributing: list[CauseLink]=[], confidence: Literal["high","medium","low"], recommendations: list[str]=[], caveats: list[str]=[], narrative: str)` — 검증자: `verdict_type`이 `inconclusive`/`degraded`가 **아니면** `root_cause` 필수.
  - `Case(id, gbm, fct, origin: Literal["human","patrol"], symptom, t0: datetime, knowledge_digests: dict[str,str]={})`

- [ ] **Step 1: 의존성 추가**

`requirements.txt`에 추가:
```
# ── 조사 엔진 (계획 3) ────────────────────────────────────
langgraph>=1.2,<2
langchain>=1.3,<2
langchain-core>=1.5,<2
langchain-openai>=1.4,<2       # config llm.profiles가 가리키는 OpenAI 호환 게이트웨이용
```
설치: `.venv/bin/pip install -q "langgraph>=1.2,<2" "langchain>=1.3,<2" "langchain-core>=1.5,<2" "langchain-openai>=1.4,<2"`

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/domain/test_case.py`

```python
from datetime import datetime

import pytest
from pydantic import ValidationError
from src.domain.case import Case, CauseLink, Hypothesis, PlanTask, Verdict

T = datetime(2026, 9, 3, 8, 0)


def test_결론있는_판정은_root_cause가_필수():
    Verdict(verdict_type="stale_data", confidence="high", narrative="…",
            root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]))
    with pytest.raises(ValidationError):
        Verdict(verdict_type="stale_data", confidence="high", narrative="…", root_cause=None)
    # 미확정·조사실패는 root_cause 없음 허용
    Verdict(verdict_type="inconclusive", confidence="low", narrative="…", root_cause=None)
    Verdict(verdict_type="degraded", confidence="low", narrative="…", root_cause=None)


def test_케이스와_태스크_기본값():
    case = Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="OEE 512%", t0=T)
    assert case.knowledge_digests == {}
    task = PlanTask(id="t-1", goal="mongo 조회", role="data_prober")
    assert task.status == "pending" and task.priority == 100
    with pytest.raises(ValidationError):
        PlanTask(id="t-2", goal="x", role="ghost_role")
    hyp = Hypothesis(id="h-1", statement="계산 이상")
    assert hyp.status == "open"
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/pytest tests/domain/test_case.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 4: 구현** — `src/domain/case.py`

```python
"""케이스 도메인 모델 — 스펙 §1.1, §2.3.

케이스는 조사 사건의 단위다. Verdict는 인과 사슬(근본 원인 + 기여 요인)이며,
모든 주장은 증거 id를 인용한다 — 인용의 실재 검증은 verify 노드(3b) 몫이고
여기서는 "결론이 있으면 root_cause가 있어야 한다"는 형태 제약만 강제한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from src.config.schema_app import StrictModel

Role = Literal["data_prober", "code_tracer", "recompute_verifier"]
VerdictType = Literal["logic_bug", "data_loss", "config_error", "stale_data",
                      "external", "inconclusive", "degraded"]


class EvidenceRef(StrictModel):
    """State에 남는 증거 참조 — 본문은 케이스 Store에 있다(§2.3)."""
    id: str
    source: str                       # 예: "mongo:twin_state", "code:twin-services@a3f9c2"
    summary: str
    as_of: datetime | None = None
    complete: bool = True             # 결과 봉투에서 상속 — 불완전 부정 증거 금지(verify)
    effective_as_of: datetime | None = None


class Hypothesis(StrictModel):
    id: str
    statement: str
    status: Literal["open", "supported", "refuted"] = "open"
    supporting_ids: list[str] = []
    refuting_ids: list[str] = []


class PlanTask(StrictModel):
    id: str
    goal: str
    role: Role
    input_evidence_ids: list[str] = []    # select 게이트: 전부 실재해야 실행 가능(§2.4)
    priority: int = 100                   # 낮을수록 먼저, 동률이면 FIFO
    status: Literal["pending", "running", "ok", "error", "cancelled"] = "pending"
    result_summary: str | None = None
    result_evidence_ids: list[str] = []
    error: str | None = None


class CauseLink(StrictModel):
    component: str                        # 토폴로지의 서비스/locator 참조
    evidence_ids: list[str]
    relation: str | None = None           # 기여 요인의 경우: 근본 원인과의 관계 서술


class Verdict(StrictModel):
    verdict_type: VerdictType
    root_cause: CauseLink | None = None
    contributing: list[CauseLink] = []
    confidence: Literal["high", "medium", "low"]
    recommendations: list[str] = []
    caveats: list[str] = []
    narrative: str

    @model_validator(mode="after")
    def _conclusive_needs_root_cause(self):
        if self.verdict_type not in ("inconclusive", "degraded") and self.root_cause is None:
            raise ValueError("결론이 있는 판정에는 root_cause가 필요하다")
        return self


class Case(StrictModel):
    id: str
    gbm: str
    fct: str
    origin: Literal["human", "patrol"]
    symptom: str
    t0: datetime
    knowledge_digests: dict[str, str] = {}   # 토폴로지·룰·deployment digest 박제(§2.5-3)
```

`tests/application/__init__.py`: 빈 파일.

- [ ] **Step 5: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/domain/test_case.py -v` → PASS, 전체 `.venv/bin/pytest` → PASS

```bash
git add requirements.txt src/domain/case.py tests/domain/test_case.py tests/application/__init__.py
git commit -m "Add case domain models with a causal-chain verdict"
```

---

### Task 2: CaseState + 리듀서

**Files:**
- Create: `src/application/__init__.py`, `src/application/state.py`
- Test: `tests/application/test_state.py`

**Interfaces:**
- Produces:
  - `merge_by_id(existing: list, update: list) -> list` — pydantic 모델 리스트를 `.id` 기준 병합: 같은 id는 **교체**(뒤가 이김), 새 id는 뒤에 추가, 기존 순서 유지. LangGraph 리듀서로 쓰인다 — **리듀서는 기계적 병합만 하고 검증하지 않는다**(검증은 노드가 출력 만들 때, §2.3).
  - `CaseState(BaseModel)` — 필드: `case: Case`, `plan_tasks: Annotated[list[PlanTask], merge_by_id]=[]`, `evidence: Annotated[list[EvidenceRef], merge_by_id]=[]`, `hypotheses: Annotated[list[Hypothesis], merge_by_id]=[]`, `round: int=0`, `decision: Literal["continue","ask","conclude"]|None=None`, `question: str|None=None`, `qa_log: Annotated[list[dict], operator.add]=[]`, `verdict: Verdict|None=None`, `verify_attempts: int=0`, `verify_problems: list[str]=[]`, `interaction_policy: Literal["interactive","autonomous"]="autonomous"`, `autonomous_question_policy: Literal["default_and_log","park"]="default_and_log"`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_state.py`

```python
from src.application.state import CaseState, merge_by_id
from src.domain.case import PlanTask


def _t(id, **kw):
    return PlanTask(id=id, goal="g", role="data_prober", **kw)


def test_merge_by_id는_교체와_추가_순서_유지():
    existing = [_t("t-1"), _t("t-2")]
    update = [_t("t-2", status="ok"), _t("t-3")]
    merged = merge_by_id(existing, update)
    assert [t.id for t in merged] == ["t-1", "t-2", "t-3"]
    assert merged[1].status == "ok"                    # 같은 id는 교체


def test_병렬_브랜치의_서로_다른_태스크_갱신이_합쳐진다():
    # Send 병렬 실행을 모사: 두 브랜치가 각자 자기 태스크만 갱신
    base = [_t("t-1", status="running"), _t("t-2", status="running")]
    after_branch_a = merge_by_id(base, [_t("t-1", status="ok")])
    after_both = merge_by_id(after_branch_a, [_t("t-2", status="error", error="타임아웃")])
    assert after_both[0].status == "ok" and after_both[1].status == "error"


def test_케이스스테이트_기본값():
    from datetime import datetime
    from src.domain.case import Case
    state = CaseState(case=Case(id="c", gbm="mx", fct="gumi", origin="patrol",
                                symptom="s", t0=datetime(2026, 9, 3)))
    assert state.round == 0 and state.plan_tasks == [] and state.verdict is None
    assert state.interaction_policy == "autonomous"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/application/test_state.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/application/state.py`

```python
"""조사 엔진의 State — 스펙 §2.3의 세 축(계획·케이스 파일 참조·판정).

리듀서는 기계적 병합만 한다: 같은 id 교체, 새 id 추가. 개수 상한·전이 검증은
노드가 출력을 만들 때 수행한다 — 리듀서가 superstep 중간에 raise하면 안 되므로.
"""
import operator
from typing import Annotated, Literal

from pydantic import BaseModel

from src.domain.case import Case, EvidenceRef, Hypothesis, PlanTask, Verdict


def merge_by_id(existing: list, update: list) -> list:
    merged = list(existing)
    index = {item.id: i for i, item in enumerate(merged)}
    for item in update:
        if item.id in index:
            merged[index[item.id]] = item
        else:
            index[item.id] = len(merged)
            merged.append(item)
    return merged


class CaseState(BaseModel):
    case: Case
    plan_tasks: Annotated[list[PlanTask], merge_by_id] = []
    evidence: Annotated[list[EvidenceRef], merge_by_id] = []
    hypotheses: Annotated[list[Hypothesis], merge_by_id] = []
    round: int = 0
    decision: Literal["continue", "ask", "conclude"] | None = None
    question: str | None = None
    qa_log: Annotated[list[dict], operator.add] = []
    verdict: Verdict | None = None
    verify_attempts: int = 0
    verify_problems: list[str] = []
    interaction_policy: Literal["interactive", "autonomous"] = "autonomous"
    autonomous_question_policy: Literal["default_and_log", "park"] = "default_and_log"
```

`src/application/__init__.py`: 빈 파일.

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/application/test_state.py -v` → PASS

```bash
git add src/application tests/application/test_state.py
git commit -m "Add CaseState with mechanical merge-by-id reducers"
```

---

### Task 3: 케이스 Store — 포트 + InMemory

**Files:**
- Create: `src/domain/store.py`
- Test: `tests/domain/test_store.py`

**Interfaces:**
- Produces:
  - `CaseStorePort(ABC)` — sync 메서드: `put_evidence(case_id: str, source: str, body: object) -> str`(증거 id 부여·본문 저장·id 반환, id 형식 `ev-<n>` 케이스별 증가), `get_evidence(case_id, evidence_id) -> object`(없으면 `KeyError` — 유일하게 raise 허용: 인용 실재 검증이 이 예외에 기댐), `has_evidence(case_id, evidence_id) -> bool`, `put_code_knowledge(service: str, commit: str, spec: str) -> None`, `get_code_knowledge(service, commit) -> str | None`(캐시 미스는 None — §3.4).
  - `InMemoryCaseStore(CaseStorePort)` — dict 기반. 개발·테스트·3b E2E용. (Mongo 구현은 계획 4.)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/domain/test_store.py`

```python
import pytest
from src.domain.store import InMemoryCaseStore


def test_증거는_케이스별_증가_id로_저장되고_조회된다():
    store = InMemoryCaseStore()
    e1 = store.put_evidence("c-1", "mongo:twin_state", {"oee": 5.12})
    e2 = store.put_evidence("c-1", "redis:plan:7", None)
    other = store.put_evidence("c-2", "rest:/oee", {"v": 1})
    assert (e1, e2, other) == ("ev-1", "ev-2", "ev-1")     # 케이스별 독립 증가
    assert store.get_evidence("c-1", "ev-1") == {"oee": 5.12}
    assert store.has_evidence("c-1", "ev-2") and not store.has_evidence("c-1", "ev-9")
    with pytest.raises(KeyError):
        store.get_evidence("c-1", "ev-9")


def test_코드_지식_캐시는_커밋_키로():
    store = InMemoryCaseStore()
    assert store.get_code_knowledge("twin-aggregator", "a3f9c2") is None
    store.put_code_knowledge("twin-aggregator", "a3f9c2", "OEE = output/planned_time")
    assert "planned_time" in store.get_code_knowledge("twin-aggregator", "a3f9c2")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/domain/test_store.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/domain/store.py`

```python
"""케이스 Store 포트 — 증거 본문과 코드 지식 캐시가 사는 곳 (스펙 §2.3, §3.4).

State에는 증거 id+요약만 남고 본문은 여기 있다. get_evidence의 KeyError는
의도된 계약이다: 인용 실재 검증(verify)이 "없는 id 인용"을 이 예외로 잡는다.
"""
from abc import ABC, abstractmethod
from collections import defaultdict


class CaseStorePort(ABC):
    @abstractmethod
    def put_evidence(self, case_id: str, source: str, body: object) -> str: ...

    @abstractmethod
    def get_evidence(self, case_id: str, evidence_id: str) -> object: ...

    @abstractmethod
    def has_evidence(self, case_id: str, evidence_id: str) -> bool: ...

    @abstractmethod
    def put_code_knowledge(self, service: str, commit: str, spec: str) -> None: ...

    @abstractmethod
    def get_code_knowledge(self, service: str, commit: str) -> str | None: ...


class InMemoryCaseStore(CaseStorePort):
    def __init__(self):
        self._evidence: dict[str, dict[str, tuple[str, object]]] = defaultdict(dict)
        self._counters: dict[str, int] = defaultdict(int)
        self._code: dict[tuple[str, str], str] = {}

    def put_evidence(self, case_id, source, body):
        self._counters[case_id] += 1
        evidence_id = f"ev-{self._counters[case_id]}"
        self._evidence[case_id][evidence_id] = (source, body)
        return evidence_id

    def get_evidence(self, case_id, evidence_id):
        return self._evidence[case_id][evidence_id][1]     # 없으면 KeyError(계약)

    def has_evidence(self, case_id, evidence_id):
        return evidence_id in self._evidence[case_id]

    def put_code_knowledge(self, service, commit, spec):
        self._code[(service, commit)] = spec

    def get_code_knowledge(self, service, commit):
        return self._code.get((service, commit))
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/domain/test_store.py -v` → PASS

```bash
git add src/domain/store.py tests/domain/test_store.py
git commit -m "Add the case store holding evidence bodies and code knowledge"
```

---

### Task 4: LLM 층 — 구조화 출력 스키마 + 실구현/스크립트 가짜

**Files:**
- Create: `src/application/schemas.py`, `src/infrastructure/llm.py`
- Test: `tests/application/test_schemas.py`, `tests/infrastructure/test_llm.py`

**Interfaces:**
- Produces (schemas.py):
  - `FrameOutput(hypotheses: list[Hypothesis], tasks: list[PlanTask])`
  - `IntegrateOutput(hypotheses: list[Hypothesis]=[], new_tasks: list[PlanTask]=[], cancel_task_ids: list[str]=[], decision: Literal["continue","ask","conclude"], question: str|None=None)` — 검증자: decision=="ask"면 question 필수.
  - `SubagentReport(status: Literal["ok","error"], summary: str, evidence_ids: list[str]=[], error: str|None=None)`
  - `parse_structured(text: str, model_cls) -> tuple[obj|None, str|None]` — 본문에서 JSON을 찾아 파싱: ```json 펜스 블록 우선, 없으면 첫 `{`부터 마지막 `}`까지. 성공 시 `(obj, None)`, 실패 시 `(None, "한국어 원인")`. **절대 raise하지 않는다.**
- Produces (llm.py):
  - `build_chat_model(model_name: str, *, base_url: str|None=None, api_key: str|None=None)` — `langchain_openai.ChatOpenAI` **지연 import**로 생성 (temperature=0). 게이트웨이 전제(스펙 §4.3).
  - `ScriptedLLM(responses: list[str])` — 결정론 테스트용. `async def ainvoke(self, messages) -> AIMessage` — 호출 순서대로 예약 응답 반환, 소진되면 `RuntimeError("스크립트 소진")`. `.calls: list` 에 받은 메시지 기록(프롬프트 단언용). 노드가 요구하는 표면은 `ainvoke`뿐 — ChatOpenAI와 duck-type 호환.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/application/test_schemas.py`:
```python
from src.application.schemas import FrameOutput, IntegrateOutput, parse_structured


def test_json_펜스와_생짜_JSON_모두_파싱():
    fenced = '설명...\n```json\n{"status": "ok", "summary": "s"}\n```'
    from src.application.schemas import SubagentReport
    obj, err = parse_structured(fenced, SubagentReport)
    assert err is None and obj.status == "ok"
    raw = '{"status": "error", "summary": "s", "error": "boom"}'
    obj2, _ = parse_structured(raw, SubagentReport)
    assert obj2.status == "error"


def test_파싱_실패는_raise가_아니라_원인_반환():
    from src.application.schemas import SubagentReport
    obj, err = parse_structured("JSON 없음", SubagentReport)
    assert obj is None and err is not None
    obj2, err2 = parse_structured('{"status": "ghost"}', SubagentReport)
    assert obj2 is None and "ghost" in err2


def test_ask_결정에는_question_필수():
    import pytest
    from pydantic import ValidationError
    IntegrateOutput(decision="ask", question="계획 변경이 있었나요?")
    with pytest.raises(ValidationError):
        IntegrateOutput(decision="ask")
```

`tests/infrastructure/test_llm.py`:
```python
import pytest
from src.infrastructure.llm import ScriptedLLM


async def test_스크립트_LLM은_순서대로_응답하고_소진되면_시끄럽게():
    llm = ScriptedLLM(['{"a": 1}', "두번째"])
    r1 = await llm.ainvoke([("user", "질문1")])
    assert r1.content == '{"a": 1}'
    r2 = await llm.ainvoke([("user", "질문2")])
    assert r2.content == "두번째"
    assert len(llm.calls) == 2
    with pytest.raises(RuntimeError):
        await llm.ainvoke([("user", "초과")])
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/application/test_schemas.py tests/infrastructure/test_llm.py -v` → FAIL

- [ ] **Step 3: 구현**

`src/application/schemas.py`:
```python
"""LLM 구조화 출력 스키마와 파서 — 스펙 §2.3·§2.4.

LLM 출력은 본문 JSON → pydantic 검증으로 받는다. with_structured_output 대신
이 경로를 쓰는 이유: 스크립트 가짜 LLM으로 전체 결정론 테스트가 되고(§5.5),
게이트웨이 호환성(도구 호출 미지원 모델)도 넓어진다.
"""
import json
import re
from typing import Literal

from pydantic import ValidationError, model_validator

from src.config.schema_app import StrictModel
from src.domain.case import Hypothesis, PlanTask

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class FrameOutput(StrictModel):
    hypotheses: list[Hypothesis]
    tasks: list[PlanTask]


class IntegrateOutput(StrictModel):
    hypotheses: list[Hypothesis] = []
    new_tasks: list[PlanTask] = []
    cancel_task_ids: list[str] = []
    decision: Literal["continue", "ask", "conclude"]
    question: str | None = None

    @model_validator(mode="after")
    def _ask_needs_question(self):
        if self.decision == "ask" and not self.question:
            raise ValueError("decision=ask면 question이 필요하다")
        return self


class SubagentReport(StrictModel):
    status: Literal["ok", "error"]
    summary: str
    evidence_ids: list[str] = []
    error: str | None = None


def parse_structured(text, model_cls):
    """본문에서 JSON을 찾아 model_cls로 검증한다. 실패는 (None, 한국어 원인)."""
    match = _FENCE.search(text)
    candidate = match.group(1) if match else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if candidate is None:
        return None, "응답에서 JSON을 찾지 못했다"
    try:
        return model_cls.model_validate(json.loads(candidate)), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 파싱 실패 — {exc}"
    except ValidationError as exc:
        return None, f"스키마 검증 실패 — {exc}"
```

`src/infrastructure/llm.py`:
```python
"""LLM 어댑터 — 실구현은 OpenAI 호환 게이트웨이(ChatOpenAI), 테스트는 ScriptedLLM.

노드·서브에이전트가 요구하는 표면은 async ainvoke(messages) -> .content 뿐이다.
"""
from langchain_core.messages import AIMessage


def build_chat_model(model_name, *, base_url=None, api_key=None):
    from langchain_openai import ChatOpenAI   # 지연 import — 스텁 전용 환경 배려
    return ChatOpenAI(model=model_name, base_url=base_url, api_key=api_key, temperature=0)


class ScriptedLLM:
    """예약된 응답을 순서대로 재생한다 — 결정론 테스트의 축(스펙 §5.5)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("스크립트 소진 — 예약된 응답보다 호출이 많다")
        return AIMessage(content=self._responses.pop(0))
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/application/test_schemas.py tests/infrastructure/test_llm.py -v` → PASS, 전체 → PASS

```bash
git add src/application/schemas.py src/infrastructure/llm.py \
        tests/application/test_schemas.py tests/infrastructure/test_llm.py
git commit -m "Add structured-output schemas and a scripted test LLM"
```

---

### Task 5: 브리핑 조립 — 토폴로지 역추적 슬라이스

**Files:**
- Create: `src/application/briefing.py`
- Test: `tests/application/test_briefing.py`

**Interfaces:**
- Consumes: `Topology`(계획 1), `Case`.
- Produces:
  - `upstream_slice(topology: Topology, start_locator: str, *, max_depth: int = 3) -> Topology` — 시작 locator에서 **상류로** 유계 BFS: locator를 키로 갖는 derivation의 `via` 서비스와 `inputs`, 그리고 각 input locator를 `writes`하는 서비스들을 따라간다. 반환은 부분 Topology(포함된 services·derivations만). start가 어느 derivation·write에도 없으면 빈 Topology.
  - `build_briefing(case: Case, topo_slice: Topology, *, rules_text: str = "", history_text: str = "", docs_text: str = "") -> str` — 한국어 브리핑 문자열: 증상·T0, 슬라이스의 파생 사슬(각 derivation을 "출력 ← via ← inputs" 한 줄로), 적용 룰, 유사 이력, 문서 발췌 순. 비어 있는 섹션은 "없음" 명시(조용한 생략 금지).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_briefing.py`

```python
from datetime import datetime

from src.application.briefing import build_briefing, upstream_slice
from src.domain.case import Case
from src.knowledge.topology import Topology

TOPO = Topology.model_validate({
    "services": {
        "edge-gateway":    {"writes": [{"kind": "kafka", "topic": "edge.raw"}]},
        "twin-aggregator": {"reads": [{"kind": "kafka", "topic": "edge.raw"}],
                            "writes": [{"kind": "mongo", "collection": "twin_state"}]},
        "twin-api":        {"reads": [{"kind": "mongo", "collection": "twin_state"}],
                            "writes": [{"kind": "rest", "endpoint": "/oee"}]},
        "unrelated":       {"writes": [{"kind": "redis", "key": "other:*"}]},
    },
    "derivations": {
        "rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                      "via": "twin-api", "key": "line"},
        "mongo:twin_state": {"inputs": [{"kind": "kafka", "topic": "edge.raw"}],
                             "via": "twin-aggregator"},
    }})


def test_상류_슬라이스는_사슬만_담고_무관_서비스는_뺀다():
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=3)
    assert set(sliced.services) == {"twin-api", "twin-aggregator", "edge-gateway"}
    assert set(sliced.derivations) == {"rest:/oee", "mongo:twin_state"}


def test_깊이_제한이_사슬을_자른다():
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=1)
    assert "twin-api" in sliced.services
    assert "edge-gateway" not in sliced.services


def test_브리핑은_빈_섹션을_명시한다():
    case = Case(id="c", gbm="mx", fct="gumi", origin="patrol",
                symptom="OEE 512%", t0=datetime(2026, 9, 3, 8, 0))
    text = build_briefing(case, upstream_slice(TOPO, "rest:/oee"))
    assert "OEE 512%" in text and "rest:/oee" in text and "twin-aggregator" in text
    assert "없음" in text            # rules/history/docs 미제공 → 명시
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/application/test_briefing.py -v` → FAIL

- [ ] **Step 3: 구현** — `src/application/briefing.py`

```python
"""frame의 케이스 브리핑 — 스펙 §3.6. 전체 코퍼스 덤프 금지, 유계 슬라이스만.

상류 역추적: 증상의 끝점 locator에서 derivation(무엇이 이걸 만드나)과
writes(누가 이 데이터를 쓰나)를 따라 유계 깊이로 거슬러 올라간다(§3.1).
"""
from collections import deque

from src.knowledge.topology import Topology


def upstream_slice(topology, start_locator, *, max_depth=3):
    services, derivations = {}, {}
    queue = deque([(start_locator, 0)])
    seen = {start_locator}
    while queue:
        locator, depth = queue.popleft()
        if depth >= max_depth:
            continue
        deriv = topology.derivations.get(locator)
        if deriv is not None:
            derivations[locator] = deriv
            if deriv.via in topology.services:
                services[deriv.via] = topology.services[deriv.via]
            for ref in deriv.inputs:
                if ref.locator not in seen:
                    seen.add(ref.locator)
                    queue.append((ref.locator, depth + 1))
        for name, svc in topology.services.items():
            if any(ref.locator == locator for ref in svc.writes):
                services[name] = svc
                for read in svc.reads:
                    if read.locator not in seen:
                        seen.add(read.locator)
                        queue.append((read.locator, depth + 1))
    return Topology(services=services, derivations=derivations)


def _or_none(text):
    return text.strip() if text and text.strip() else "없음"


def build_briefing(case, topo_slice, *, rules_text="", history_text="", docs_text=""):
    chain_lines = [
        f"- {output} ← via {deriv.via} ← inputs: "
        + ", ".join(ref.locator for ref in deriv.inputs)
        + (f" (key: {deriv.key})" if deriv.key != "fan-in" else " (fan-in)")
        for output, deriv in topo_slice.derivations.items()]
    services_line = ", ".join(sorted(topo_slice.services)) or "없음"
    return "\n".join([
        f"[케이스] {case.id} — {case.gbm}/{case.fct}, 접수 경로: {case.origin}",
        f"[증상] {case.symptom}",
        f"[T0] {case.t0.isoformat()}",
        "[토폴로지 슬라이스 — 파생 사슬(상류 방향)]",
        *(chain_lines or ["없음"]),
        f"[관련 서비스] {services_line}",
        f"[적용 룰] {_or_none(rules_text)}",
        f"[유사 이력] {_or_none(history_text)}",
        f"[관련 문서] {_or_none(docs_text)}",
    ])
```

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/application/test_briefing.py -v` → PASS

```bash
git add src/application/briefing.py tests/application/test_briefing.py
git commit -m "Slice the topology upstream and assemble the case briefing"
```

---

### Task 6: 서브에이전트 3종 — create_agent 유계 루프

**Files:**
- Create: `src/application/subagents.py`
- Test: `tests/application/test_subagents.py`

**Interfaces:**
- Consumes: `AdapterSet`(계획 2 factory), `CaseStorePort`, `SubagentReport`/`parse_structured`(Task 4), `PlanTask`.
- Produces:
  - `make_tools(role: Role, *, adapters: AdapterSet, store: CaseStorePort, case_id: str) -> list` — 역할별 langchain `@tool` 목록:
    - `data_prober`: `mongo_find(collection, filter_json, limit)`, `mongo_count(collection, filter_json)`, `redis_get(key)`, `redis_scan(pattern)`, `redis_ttl(key)`, `kafka_read(topic, start_iso, end_iso)`, `kafka_group_offsets(group)`, `rest_get(endpoint)` — config에 없는 어댑터(None)는 도구 자체를 만들지 않는다.
    - `code_tracer`: `code_show(repo, commit, path)`, `code_grep(repo, commit, pattern)`, `code_head(repo)` — CodeRepoError는 도구 안에서 잡아 오류 문자열 반환(raise 금지).
    - `recompute_verifier`: `get_evidence(evidence_id)`(Store 본문 조회 — 입력 증거 재독) + code_tracer 도구들.
    - **모든 프로브 도구의 계약**: ProbeResult가 ok면 본문을 `store.put_evidence(case_id, source, body)`로 저장하고 `"[증거 {id}] {요약} (complete={bool}, effective_as_of={...})"` 문자열 반환. error면 `"[오류] {원인}"` 반환. **도구는 절대 raise하지 않는다.**
  - `async run_subagent(task: PlanTask, *, adapters, store, llm, budget: int, case_id: str) -> SubagentReport` — `langchain.agents.create_agent(model=llm, tools=make_tools(...), system_prompt=역할별 한국어 프롬프트)`를 만들어 `ainvoke({"messages": [("user", 태스크 목표+입력 증거 id)]}, config={"recursion_limit": budget})`. 마지막 AI 메시지를 `parse_structured(SubagentReport)`. **최외곽 catch-all**: 어떤 예외(GraphRecursionError 포함)도 `SubagentReport(status="error", summary="", error=...)`로 변환. 파싱 실패도 error 보고.
  - 역할별 시스템 프롬프트(한국어, 모듈 상수): 공통 골자 — "너는 {역할}이다. 도구만으로 조사하고, 마지막에 반드시 JSON 하나로 보고하라: {\"status\": ..., \"summary\": ..., \"evidence_ids\": [도구가 준 증거 id들]}. 추측 금지, 도구 결과만 근거로."

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_subagents.py`

```python
"""GenericFakeChatModel로 서브에이전트 루프를 결정론 검증한다.

fake는 입력과 무관하게 예약된 AIMessage를 재생한다: 도구 호출 1회 → 최종 JSON 보고.
"""
from datetime import datetime, timezone

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from src.application.subagents import make_tools, run_subagent
from src.config.schema_site import SiteConfig
from src.domain.case import PlanTask
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({"services": {}, "derivations": {}})
SITE = SiteConfig.model_validate(
    {"target": {"mongo": {"url": "mongodb://x:27017"}}})


def _adapters():
    seeds = StubSeeds(mongo_collections={"twin_state": [{"line": 7, "oee": 5.12}]})
    return build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=seeds)


class ToolFake(GenericFakeChatModel):
    """create_agent가 부르는 bind_tools를 no-op으로 — fake는 도구 스키마가 필요 없다."""

    def bind_tools(self, tools, **kwargs):
        return self


def _fake(messages):
    return ToolFake(messages=iter(messages))


async def test_도구가_증거를_저장하고_보고가_id를_인용한다():
    store = InMemoryCaseStore()
    llm = _fake([
        AIMessage(content="", tool_calls=[{
            "name": "mongo_find", "id": "call-1",
            "args": {"collection": "twin_state", "filter_json": '{"line": 7}', "limit": 5}}]),
        AIMessage(content='{"status": "ok", "summary": "oee 5.12 확인", "evidence_ids": ["ev-1"]}'),
    ])
    task = PlanTask(id="t-1", goal="twin_state에서 line 7 조회", role="data_prober")
    report = await run_subagent(task, adapters=_adapters(), store=store,
                                llm=llm, budget=10, case_id="c-1")
    assert report.status == "ok" and report.evidence_ids == ["ev-1"]
    assert store.get_evidence("c-1", "ev-1") == [{"line": 7, "oee": 5.12}]


async def test_예산_초과와_파싱_실패는_error_보고다():
    store = InMemoryCaseStore()
    # 예산 2로는 도구 호출 루프가 못 끝난다 → GraphRecursionError → error 보고
    endless = _fake([
        AIMessage(content="", tool_calls=[{
            "name": "mongo_count", "id": f"call-{n}",
            "args": {"collection": "twin_state", "filter_json": "{}"}}])
        for n in range(9)])
    task = PlanTask(id="t-2", goal="g", role="data_prober")
    report = await run_subagent(task, adapters=_adapters(), store=store,
                                llm=endless, budget=2, case_id="c-1")
    assert report.status == "error" and report.error

    # 최종 응답이 JSON이 아니면 파싱 실패 → error 보고
    chatty = _fake([AIMessage(content="말로만 하는 보고")])
    report2 = await run_subagent(task, adapters=_adapters(), store=store,
                                 llm=chatty, budget=10, case_id="c-1")
    assert report2.status == "error" and "JSON" in report2.error


async def test_config에_없는_어댑터의_도구는_만들지_않는다():
    tools = make_tools("data_prober", adapters=_adapters(),
                       store=InMemoryCaseStore(), case_id="c-1")
    names = {t.name for t in tools}
    assert "mongo_find" in names and "redis_get" not in names   # SITE엔 mongo만
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/application/test_subagents.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `src/application/subagents.py`

핵심 골격 (구현자는 이 계약대로 완성한다 — 도구 함수들은 아래 `mongo_find` 패턴의 반복):

```python
"""서브에이전트 3종 — create_agent 유계 ReAct 루프 (스펙 §2.4 확정).

- 자유가 값하는 안쪽 루프는 라이브러리에, 규율은 이 모듈의 경계에:
  도구는 결과를 Store에 넣고 증거 id를 돌려주며(인용의 원천), 어떤 실패도
  raise 대신 error 보고로 변환된다(superstep 보호).
"""
import json

from langchain_core.tools import tool

from src.application.schemas import SubagentReport, parse_structured
from src.domain.case import PlanTask

_PROMPTS = {
    "data_prober": (
        "너는 데이터 프로버다. 주어진 도구만으로 대상 시스템의 데이터를 조회해 조사 목표에 답하라.\n"
        "도구 결과에 [증거 ev-N]이 표시된다 — 마지막에 반드시 JSON 하나로만 보고하라:\n"
        '{"status": "ok"|"error", "summary": "발견 요약(한국어)", "evidence_ids": ["ev-N", ...]}\n'
        "추측 금지. 도구가 준 결과만 근거로 삼고, 실패했으면 status를 error로 하라."),
    "code_tracer": (
        "너는 코드 추적자다. 주어진 도구로 대상 서비스의 코드를 읽고 변환 로직을 규명하라.\n"
        "마지막에 반드시 JSON 하나로만 보고하라: "
        '{"status": ..., "summary": "로직 명세(한국어)", "evidence_ids": [...]}'),
    "recompute_verifier": (
        "너는 재계산 검증자다. get_evidence로 입력 증거를 읽고, 로직 명세대로 기대값을 도출해\n"
        "실제값과 대조하라. 마지막에 반드시 JSON 하나로만 보고하라: "
        '{"status": ..., "summary": "샘플별 일치/불일치(한국어)", "evidence_ids": [...]}'),
}


def _evidence_line(evidence_id, summary, envelope):
    eff = envelope.effective_as_of.isoformat() if envelope.effective_as_of else "-"
    return f"[증거 {evidence_id}] {summary} (complete={envelope.complete}, effective_as_of={eff})"


def make_tools(role, *, adapters, store, case_id):
    tools = []

    if role in ("data_prober",) and adapters.mongo is not None:
        @tool
        async def mongo_find(collection: str, filter_json: str, limit: int = 20) -> str:
            """Mongo 컬렉션을 필터로 조회한다. filter_json은 JSON 문자열."""
            try:
                filter = json.loads(filter_json)
            except json.JSONDecodeError as exc:
                return f"[오류] filter_json 파싱 실패 — {exc}"
            result = await adapters.mongo.find(collection, filter, limit=limit)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = store.put_evidence(case_id, f"mongo:{collection}", result.data)
            return _evidence_line(evidence_id, f"{collection} {len(result.data)}건", result.envelope)
        tools.append(mongo_find)
        # mongo_count도 같은 패턴으로 추가

    # data_prober: redis_get/redis_scan/redis_ttl(adapters.redis), kafka_read/
    # kafka_group_offsets(adapters.kafka), rest_get(adapters.rest) — 전부 같은 패턴:
    #   어댑터 None이면 도구 생성 안 함 / ok→store.put_evidence→[증거 ...] / error→[오류 ...]
    # code_tracer: code_show/code_grep/code_head — adapters.code 사용, CodeRepoError는
    #   try/except로 잡아 "[오류] ..." 반환. 결과 본문도 store.put_evidence로 저장.
    # recompute_verifier: get_evidence(evidence_id) — store 본문을 JSON 문자열로 반환
    #   (없는 id는 "[오류] 없는 증거 id") + code_tracer 도구 일체.
    return tools


async def run_subagent(task: PlanTask, *, adapters, store, llm, budget, case_id) -> SubagentReport:
    from langchain.agents import create_agent   # 지연 import

    tools = make_tools(task.role, adapters=adapters, store=store, case_id=case_id)
    goal = task.goal
    if task.input_evidence_ids:
        goal += f"\n입력 증거 id: {', '.join(task.input_evidence_ids)}"
    try:
        agent = create_agent(model=llm, tools=tools, system_prompt=_PROMPTS[task.role])
        result = await agent.ainvoke({"messages": [("user", goal)]},
                                     config={"recursion_limit": budget})
        final = result["messages"][-1].content
        report, err = parse_structured(final, SubagentReport)
        if report is None:
            return SubagentReport(status="error", summary="",
                                  error=f"보고 JSON 파싱 실패 — {err}")
        return report
    except Exception as exc:   # 예산 초과(GraphRecursionError) 포함 — raise 금지 계약
        return SubagentReport(status="error", summary="",
                              error=f"서브에이전트 실행 실패 — {type(exc).__name__}: {exc}")
```

구현자 주의: 위 골격의 주석 처리된 도구들을 전부 실제로 구현하라(각 4~8줄, mongo_find 패턴 반복). `create_agent`의 정확한 import 경로·인자명은 설치된 langchain 1.x에서 introspection으로 확인하고, 다르면 코드를 맞추되 계약(모델·도구·시스템 프롬프트·recursion_limit)은 유지하라.

- [ ] **Step 4: 통과 확인 후 커밋**

Run: `.venv/bin/pytest tests/application/test_subagents.py -v` → PASS, 전체 → PASS

```bash
git add src/application/subagents.py tests/application/test_subagents.py
git commit -m "Run role subagents as budgeted create_agent loops over stored evidence"
```

---

## 완료 기준 (계획 3a)

- `.venv/bin/pytest` 전체 통과 (계획 2의 70개 + 신규 전부).
- ScriptedLLM/GenericFakeChatModel만으로 서브에이전트 루프가 결정론으로 검증됨 — 도구→Store→증거 id 인용 사슬이 실제로 돎.
- 어떤 새 코드도 노드·도구 경계 밖으로 예외를 던지지 않음 (Store.get_evidence의 KeyError만 예외 — 계약).

## 계획 3b 예고

frame/select/execute/integrate/ask_human/conclude/verify 노드, Send 배선과 라운드 상한, autonomous 정책 3종, InMemorySaver 체크포인터, investigate_case 유스케이스, 스크립트 LLM+스텁 어댑터 E2E(미니 OEE 시나리오, park/resume 포함).
