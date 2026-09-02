# 계획 3b: 조사 엔진 그래프 (노드·배선·E2E) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스펙 §2의 조사 엔진 그래프를 완성한다 — frame/select/execute/integrate/ask_human/conclude/verify 노드, Send 배선과 라운드 상한, autonomous 정책, InMemorySaver 체크포인터, `investigate_case` 유스케이스, 스크립트 LLM+스텁 E2E 3종(해피패스·ask→resume·verify 재작성).

**Architecture:** 노드는 `EngineDeps`를 클로저로 받는 팩토리(`make_nodes`)로 만들고, graph.py가 배선한다. 통제 경계(§2.2)는 코드가: 라운드 상한 강제 conclude, select 게이트, 병렬 폭, interrupt 위치, verify 규칙. LLM은 lead(ScriptedLLM 호환 ainvoke)와 subagent(BaseChatModel)로 분리 주입.

**3a 인계 노트 반영 (설계 구속):**
1. `SubagentReport.evidence_ids`는 **실측 수집 전체**다(인용 아님) — execute는 그걸 그대로 EvidenceRef로 승격하고, "인용"의 의미는 Verdict의 evidence_ids에만 있다(verify가 검증).
2. 서브에이전트 error 경로는 부분 증거를 첨부하지 않는다 — execute는 error 태스크에서 EvidenceRef를 만들지 않는다(고아 본문은 Store에 남지만 State에 안 올라옴 — 의도).
3. `merge_by_id`는 id 충돌을 조용히 덮어쓴다 — **integrate가 new_tasks의 기존 id 충돌을 드롭하고 qa_log에 기록**한다(조용한 무시 금지).
4. StrictModel 계층 방향은 이번 범위 밖(기존 패턴 유지).

## Global Constraints (스펙에서 발췌 — 모든 태스크에 적용)

- **interrupt는 노드 최상단에만** (§2.4 설계 원칙 — resume 시 노드 선두 재실행이라 interrupt 앞 LLM 호출 금지).
- **라운드 상한 도달 시 강제 conclude, "미확정" 허용·억지 결론 금지** (§2.4). **증거 0건 conclude는 결정론적으로 `degraded`** (§5.4-F4 — LLM 호출 없이 코드가 생성).
- **verify는 결정론** (§2.4): ① 인용 id 전부 Store에 실재 ② root_cause·contributing **다리마다** 인용 비어있지 않음 ③ 인용 중 `complete=False` 증거가 있으면 그 id가 caveats에 명시되어야 함(incomplete 부정 증거 방어의 기계 검증형). 실패 시 재작성 1회 → 재실패 시 confidence="low" 강등 + "검증 미통과" caveat 추가 후 통과.
- **LLM 파싱 재시도 1회** (frame/integrate/conclude): 실패 원인을 붙여 재프롬프트, 두 번째도 실패하면 노드별 안전 경로(frame→degraded verdict, integrate→강제 conclude, conclude→degraded verdict).
- **노드는 절대 raise하지 않는다.** `datetime.now()` 금지(§2.5). 한국어 프롬프트·주석·메시지. StrictModel·기동 거부 철학 유지.
- 체크포인터는 InMemorySaver(개발·테스트) — Mongo 체크포인터·타임아웃·수명주기는 계획 4.

## File Structure

```
src/domain/case.py           # (수정) Case.target_locator 필드 추가
src/application/
├── deps.py              # EngineDeps
├── nodes.py             # 노드 팩토리 make_nodes + 라우터들
├── graph.py             # build_engine → compiled StateGraph
└── usecase.py           # investigate_case, resume_case
tests/application/test_nodes_frame.py, test_nodes_select_execute.py,
                  test_nodes_integrate.py, test_nodes_conclude_verify.py, test_graph_e2e.py
```

그래프 형태 (§2.1):
```
START → frame → (verdict 생겼으면 END, 아니면 select)
select → (실행 가능 태스크를 Send로 execute×N | 0건이면 integrate)
execute → integrate                    (Send 전부가 barrier로 수렴)
integrate → (continue→select | ask→ask_human | conclude→conclude)
ask_human → integrate                  (interrupt 최상단, resume 답변은 qa_log로)
conclude → verify
verify → (문제+첫 시도→conclude 재작성 | 통과·강등→END)
```

---

### Task 1: EngineDeps + Case.target_locator + frame 노드

**Files:**
- Modify: `src/domain/case.py` (필드 1개 추가)
- Create: `src/application/deps.py`, `src/application/nodes.py` (frame 부분 + route_after_frame)
- Test: `tests/application/test_nodes_frame.py`

**Interfaces:**
- `Case.target_locator: str | None = None` — 증상의 토폴로지 끝점(순찰 Finding의 target/접수 시 해석). frame이 슬라이스 시작점으로 사용, None이면 빈 슬라이스.
- `EngineDeps` (dataclass): `lead_llm`(ainvoke 표면), `subagent_llm`(BaseChatModel), `adapters: AdapterSet`, `store: CaseStorePort`, `topology: Topology`, `engine_cfg: EngineConfig`, `rules_text: str = ""`, `history_text: str = ""`, `docs_text: str = ""`.
- `make_nodes(deps: EngineDeps) -> dict[str, Callable]` — 이 태스크에서는 `"frame"`만 구현(이후 태스크가 같은 dict에 추가). 반환 노드는 전부 `async def node(state: CaseState) -> dict`.
- `frame(state)`: ① briefing 조립(`upstream_slice(deps.topology, case.target_locator)` — None이면 빈 Topology) ② lead_llm에 브리핑+지시(FrameOutput JSON, id 규약 h-*/t-*) ③ `parse_structured(FrameOutput)` 실패 시 오류를 붙여 재시도 1회 ④ 성공: `{"hypotheses": [...], "plan_tasks": [...]}` / 이중 실패: `{"verdict": Verdict(verdict_type="degraded", confidence="low", narrative="frame 출력 파싱 실패 — 조사 개시 불가", caveats=[원인])}`.
- `route_after_frame(state) -> str`: `state.verdict`가 있으면 `"__end__"` 아니면 `"select"`. (graph.py에서 END 심볼로 매핑.)
- frame 프롬프트(모듈 상수, 한국어): 브리핑을 주고 — "가설(h-1..)과 조사 태스크(t-1.., role은 data_prober/code_tracer/recompute_verifier 중 하나, 재계산 태스크는 input_evidence_ids에 의존 증거 id)를 세워라. 반드시 JSON 하나만: {\"hypotheses\": [...], \"tasks\": [...]}" + 필드 스키마 예시.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_nodes_frame.py`

```python
from datetime import datetime, timezone

from src.application.deps import EngineDeps
from src.application.nodes import make_nodes, route_after_frame
from src.application.state import CaseState
from src.config.schema_app import EngineConfig
from src.config.schema_site import SiteConfig
from src.domain.case import Case
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters
from src.infrastructure.llm import ScriptedLLM
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {"rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                                  "via": "twin-api"}}})
SITE = SiteConfig.model_validate({"target": {"mongo": {"url": "mongodb://x:27017"}}})

FRAME_JSON = ('{"hypotheses": [{"id": "h-1", "statement": "계산 이상"}], '
              '"tasks": [{"id": "t-1", "goal": "twin_state 조회", "role": "data_prober"}]}')


def _deps(lead_responses):
    return EngineDeps(
        lead_llm=ScriptedLLM(lead_responses), subagent_llm=None,
        adapters=build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=StubSeeds()),
        store=InMemoryCaseStore(), topology=TOPO, engine_cfg=EngineConfig())


def _state():
    return CaseState(case=Case(id="c-1", gbm="mx", fct="gumi", origin="patrol",
                               symptom="OEE 512%", t0=T, target_locator="rest:/oee"))


async def test_frame은_가설과_계획을_세우고_브리핑을_프롬프트에_담는다():
    deps = _deps([FRAME_JSON])
    update = await make_nodes(deps)["frame"](_state())
    assert [h.id for h in update["hypotheses"]] == ["h-1"]
    assert [t.id for t in update["plan_tasks"]] == ["t-1"]
    prompt_text = str(deps.lead_llm.calls[0])
    assert "OEE 512%" in prompt_text and "rest:/oee" in prompt_text   # 브리핑 포함


async def test_파싱_실패는_한_번_재시도하고_이중_실패면_degraded():
    deps = _deps(["JSON 아님", "여전히 아님"])
    update = await make_nodes(deps)["frame"](_state())
    assert update["verdict"].verdict_type == "degraded"
    assert len(deps.lead_llm.calls) == 2                              # 재시도 1회


async def test_route_after_frame():
    state = _state()
    assert route_after_frame(state) == "select"
    deps = _deps(["x", "y"])
    failed = state.model_copy(update=await make_nodes(deps)["frame"](state))
    assert route_after_frame(failed) == "__end__"
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/application/test_nodes_frame.py -v` → FAIL

- [ ] **Step 3: 구현** — deps.py 전체 + nodes.py의 frame·route_after_frame·`_ask_llm` 헬퍼:

```python
# src/application/deps.py
"""엔진 의존성 묶음 — 노드 팩토리(make_nodes)가 클로저로 받는다."""
from dataclasses import dataclass
from typing import Any

from src.config.schema_app import EngineConfig
from src.domain.store import CaseStorePort
from src.infrastructure.factory import AdapterSet
from src.knowledge.topology import Topology


@dataclass
class EngineDeps:
    lead_llm: Any                 # async ainvoke(messages) -> .content
    subagent_llm: Any             # BaseChatModel (create_agent용)
    adapters: AdapterSet
    store: CaseStorePort
    topology: Topology
    engine_cfg: EngineConfig
    rules_text: str = ""
    history_text: str = ""
    docs_text: str = ""
```

```python
# src/application/nodes.py (이 태스크 분량)
"""조사 엔진 노드들 — 스펙 §2.4. 통제는 코드가, 판단은 LLM이.

모든 노드는 절대 raise하지 않고 부분 상태 update(dict)를 반환한다.
LLM 파싱은 재시도 1회 후 노드별 안전 경로로 강등된다.
"""
from src.application.briefing import build_briefing, upstream_slice
from src.application.schemas import FrameOutput, parse_structured
from src.domain.case import Verdict
from src.knowledge.topology import Topology

_FRAME_PROMPT = """너는 디지털 트윈 운영 조사의 리드다. 아래 브리핑을 읽고 초기 가설과 조사 계획을 세워라.

{briefing}

규칙:
- 가설 id는 h-1.., 태스크 id는 t-1.. 로 붙인다.
- role은 data_prober(데이터 조회)/code_tracer(코드 로직 규명)/recompute_verifier(재계산 대조) 중 하나.
- 재계산 태스크는 input_evidence_ids에 의존하는 증거 id를 적는다(없으면 빈 배열 — 아직 없으면 이후 라운드에서 추가된다).
- 반드시 JSON 하나만 출력한다:
{{"hypotheses": [{{"id": "h-1", "statement": "..."}}], "tasks": [{{"id": "t-1", "goal": "...", "role": "...", "input_evidence_ids": [], "priority": 10}}]}}"""


async def _ask_llm(llm, prompt, schema):
    """파싱 재시도 1회 계약 — (obj, None) 또는 (None, 마지막 오류)."""
    response = await llm.ainvoke([("user", prompt)])
    obj, err = parse_structured(response.content, schema)
    if obj is not None:
        return obj, None
    retry = await llm.ainvoke([
        ("user", f"{prompt}\n\n이전 응답은 다음 이유로 거부됐다: {err}\nJSON만 다시 출력하라.")])
    return parse_structured(retry.content, schema)


def make_nodes(deps):
    async def frame(state):
        case = state.case
        topo_slice = (upstream_slice(deps.topology, case.target_locator)
                      if case.target_locator else Topology())
        briefing = build_briefing(case, topo_slice, rules_text=deps.rules_text,
                                  history_text=deps.history_text, docs_text=deps.docs_text)
        output, err = await _ask_llm(deps.lead_llm, _FRAME_PROMPT.format(briefing=briefing),
                                     FrameOutput)
        if output is None:
            return {"verdict": Verdict(
                verdict_type="degraded", confidence="low",
                narrative="frame 출력 파싱 실패 — 조사 개시 불가", caveats=[err])}
        return {"hypotheses": output.hypotheses, "plan_tasks": output.tasks}

    return {"frame": frame}


def route_after_frame(state):
    return "__end__" if state.verdict is not None else "select"
```

`src/domain/case.py`의 `Case`에 `target_locator: str | None = None` 필드 추가(t0 아래).

- [ ] **Step 4: 통과 확인 후 커밋** — 전체 스위트 PASS 확인.

```bash
git add src/domain/case.py src/application/deps.py src/application/nodes.py tests/application/test_nodes_frame.py
git commit -m "Frame a case into hypotheses and a plan, degrading on parse failure"
```

---

### Task 2: select + Send 배선 + execute

**Files:**
- Modify: `src/application/nodes.py`
- Test: `tests/application/test_nodes_select_execute.py`

**Interfaces:**
- `select(state)`: 실행 가능 = `status=="pending"` 이고 `input_evidence_ids ⊆ {e.id for e in state.evidence}`. 정렬 `(priority, 계획 등재 순)`, 최대 `engine_cfg.parallel_width`개. 선택된 태스크를 `status="running"`으로 갱신해 반환: `{"plan_tasks": [running된 것들]}`. (선택 0건이어도 raise 없음 — 라우터가 integrate로 보냄.)
- `route_after_select(state) -> list[Send] | str`: `status=="running"` 태스크마다 `Send("execute", {"task": task.model_dump(mode="json"), "case_id": state.case.id})`, 없으면 `"integrate"`. (running 판정 기준이므로 select가 방금 굴린 것만 잡히도록 — execute가 끝나면 ok/error로 바뀐다.)
- `execute(payload: dict)`: payload에서 PlanTask 복원 → `run_subagent(task, adapters=deps.adapters, store=deps.store, llm=deps.subagent_llm, budget=deps.engine_cfg.subagent_budgets.<role>, case_id=...)` →
  - ok: `plan_tasks=[task(status="ok", result_summary, result_evidence_ids=report.evidence_ids)]` + `evidence=[EvidenceRef(...) for id in report.evidence_ids]` — EvidenceRef는 `store.get_evidence_record`의 메타(source/as_of/complete/effective_as_of)로 조립, summary는 `repr(store.get_evidence(...))[:160]`.
  - error: `plan_tasks=[task(status="error", error=report.error)]`, evidence 없음(인계 노트 2).
  - 최외곽 try/except — 어떤 예외도 error 태스크 update로(노드 raise 금지).
- 예산 조회: `getattr(deps.engine_cfg.subagent_budgets, task.role)`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_nodes_select_execute.py`

```python
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.types import Send

from src.application.nodes import make_nodes, route_after_select
from src.application.state import CaseState
from src.domain.case import Case, EvidenceRef, PlanTask
from tests.application.test_nodes_frame import SITE, TOPO, T, _deps
from tests.application.test_subagents import ToolFake

from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters


def _state(tasks, evidence=()):
    return CaseState(
        case=Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T),
        plan_tasks=list(tasks), evidence=list(evidence))


async def test_select는_게이트와_병렬폭을_지킨다():
    deps = _deps([])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"parallel_width": 2})
    tasks = [
        PlanTask(id="t-1", goal="g", role="data_prober", priority=10),
        PlanTask(id="t-2", goal="g", role="data_prober", priority=5),
        PlanTask(id="t-3", goal="g", role="recompute_verifier",
                 input_evidence_ids=["ev-9"], priority=1),   # 증거 없음 → 게이트에 걸림
        PlanTask(id="t-4", goal="g", role="data_prober", priority=20),
    ]
    update = await make_nodes(deps)["select"](_state(tasks))
    running = [t.id for t in update["plan_tasks"]]
    assert running == ["t-2", "t-1"]          # 우선순위순, 폭 2, t-3은 게이트

    routed = route_after_select(_state(tasks).model_copy(update=update))
    assert all(isinstance(s, Send) for s in routed) and len(routed) == 2


async def test_실행가능_0건이면_integrate로_폴백():
    deps = _deps([])
    update = await make_nodes(deps)["select"](_state(
        [PlanTask(id="t-1", goal="g", role="data_prober", input_evidence_ids=["ev-9"])]))
    assert update["plan_tasks"] == []
    assert route_after_select(_state([])) == "integrate"


async def test_execute는_증거를_봉투_메타와_함께_승격한다():
    deps = _deps([])
    seeds = StubSeeds(mongo_collections={"twin_state": [{"line": 7, "oee": 5.12}]})
    deps.adapters = build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=seeds)
    deps.store = InMemoryCaseStore()
    deps.subagent_llm = ToolFake(messages=iter([
        AIMessage(content="", tool_calls=[{
            "name": "mongo_find", "id": "c1",
            "args": {"collection": "twin_state", "filter_json": '{"line": 7}'}}]),
        AIMessage(content='{"status": "ok", "summary": "확인", "evidence_ids": ["ev-1"]}'),
    ]))
    task = PlanTask(id="t-1", goal="조회", role="data_prober", status="running")
    update = await make_nodes(deps)["execute"](
        {"task": task.model_dump(mode="json"), "case_id": "c-1"})
    assert update["plan_tasks"][0].status == "ok"
    ref = update["evidence"][0]
    assert isinstance(ref, EvidenceRef) and ref.id == "ev-1"
    assert ref.source == "mongo:twin_state" and ref.as_of == T and ref.complete is True


async def test_execute_error_태스크는_증거를_안_만든다():
    deps = _deps([])
    deps.subagent_llm = ToolFake(messages=iter([AIMessage(content="말로만")]))
    task = PlanTask(id="t-1", goal="g", role="data_prober", status="running")
    update = await make_nodes(deps)["execute"](
        {"task": task.model_dump(mode="json"), "case_id": "c-1"})
    assert update["plan_tasks"][0].status == "error"
    assert update.get("evidence", []) == []
```

- [ ] **Step 2: 실패 확인** → FAIL

- [ ] **Step 3: 구현** — 계약대로 nodes.py에 select/execute 추가, route_after_select는 `from langgraph.types import Send`. (테스트의 `deps.engine_cfg.model_copy`가 동작하도록 EngineConfig는 pydantic — 이미 그렇다.)

- [ ] **Step 4: 전체 스위트 PASS 후 커밋**

```bash
git add src/application/nodes.py tests/application/test_nodes_select_execute.py
git commit -m "Gate, fan out, and execute plan tasks into enveloped evidence"
```

---

### Task 3: integrate — 보드 갱신·정책·라운드 상한

**Files:**
- Modify: `src/application/nodes.py`
- Test: `tests/application/test_nodes_integrate.py`

**Interfaces:**
- `integrate(state)`: ① 프롬프트: 가설 보드, 태스크 현황(ok/error/pending 요약 + error 원인), 증거 목록(id·summary·complete·effective_as_of), 라운드 `{round+1}/{max_rounds}` ② `_ask_llm(IntegrateOutput)` ③ update 조립:
  - `hypotheses`: 그대로 병합. `new_tasks`: **기존 id와 충돌하면 드롭하고 `qa_log`에 `{"kind": "task_id_collision", "id": ...}` 기록**(인계 노트 3). `cancel_task_ids`: pending인 것만 `status="cancelled"`로.
  - `decision` 해석: LLM이 `ask`인데 `interaction_policy=="autonomous"`이고 `autonomous_question_policy=="default_and_log"`면 → `qa_log`에 `{"kind": "auto_answered", "question": ..., "answer": "보수적 기본값으로 진행"}` 추가하고 `decision="continue"`로 대체. (park이면 ask 유지.)
  - **라운드 상한**: `round+1 >= engine_cfg.max_rounds`면 decision을 무조건 `"conclude"`로 덮어씀(qa_log에 `{"kind": "round_cap"}`).
  - 파싱 이중 실패: `decision="conclude"` + qa_log `{"kind": "integrate_parse_failure", "error": ...}`.
  - 반환에 `round: round+1`, `decision`, `question`(ask 유지 시) 포함.
- `route_after_integrate(state) -> str`: `"select"`(continue) / `"ask_human"`(ask) / `"conclude"`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_nodes_integrate.py`

```python
from src.application.nodes import make_nodes, route_after_integrate
from src.application.state import CaseState
from src.domain.case import Case, Hypothesis, PlanTask
from tests.application.test_nodes_frame import T, _deps


def _state(**kw):
    base = dict(case=Case(id="c-1", gbm="mx", fct="gumi", origin="patrol",
                          symptom="s", t0=T))
    base.update(kw)
    return CaseState(**base)


async def test_보드_갱신과_id_충돌_드롭():
    deps = _deps(['{"hypotheses": [{"id": "h-1", "statement": "갱신", "status": "supported"}], '
                  '"new_tasks": [{"id": "t-1", "goal": "충돌", "role": "data_prober"}, '
                  '{"id": "t-9", "goal": "신규", "role": "data_prober"}], '
                  '"decision": "continue"}'])
    state = _state(plan_tasks=[PlanTask(id="t-1", goal="기존", role="data_prober", status="ok")],
                   hypotheses=[Hypothesis(id="h-1", statement="원래")])
    update = await make_nodes(deps)["integrate"](state)
    new_ids = [t.id for t in update["plan_tasks"]]
    assert "t-9" in new_ids and "t-1" not in new_ids            # 충돌 드롭
    assert any(e["kind"] == "task_id_collision" for e in update["qa_log"])
    assert update["round"] == 1 and update["decision"] == "continue"


async def test_autonomous_default_and_log는_질문을_기록하고_계속한다():
    deps = _deps(['{"decision": "ask", "question": "계획 변경이 있었나요?"}'])
    update = await make_nodes(deps)["integrate"](_state())
    assert update["decision"] == "continue"
    assert any(e["kind"] == "auto_answered" for e in update["qa_log"])

    deps2 = _deps(['{"decision": "ask", "question": "확인 필요"}'])
    state2 = _state(autonomous_question_policy="park")
    update2 = await make_nodes(deps2)["integrate"](state2)
    assert update2["decision"] == "ask" and update2["question"] == "확인 필요"


async def test_라운드_상한은_강제_conclude():
    deps = _deps(['{"decision": "continue"}'])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"max_rounds": 1})
    update = await make_nodes(deps)["integrate"](_state())
    assert update["decision"] == "conclude"
    assert any(e["kind"] == "round_cap" for e in update["qa_log"])


async def test_파싱_이중_실패는_강제_conclude():
    deps = _deps(["엉망", "또 엉망"])
    update = await make_nodes(deps)["integrate"](_state())
    assert update["decision"] == "conclude"
    assert any(e["kind"] == "integrate_parse_failure" for e in update["qa_log"])


def test_route_after_integrate():
    assert route_after_integrate(_state(decision="continue")) == "select"
    assert route_after_integrate(_state(decision="ask")) == "ask_human"
    assert route_after_integrate(_state(decision="conclude")) == "conclude"
```

- [ ] **Step 2~4**: FAIL 확인 → 계약대로 구현(프롬프트는 한국어 모듈 상수 `_INTEGRATE_PROMPT` — 증거의 `complete=False`·`effective_as_of` 표시 포함) → 전체 PASS → 커밋

```bash
git add src/application/nodes.py tests/application/test_nodes_integrate.py
git commit -m "Integrate rounds under a hard cap with policy-aware questions"
```

---

### Task 4: ask_human + conclude

**Files:**
- Modify: `src/application/nodes.py`
- Test: `tests/application/test_nodes_conclude_verify.py` (conclude 부분 — ask_human은 Task 6 E2E에서 그래프로 검증)

**Interfaces:**
- `ask_human(state)`: **첫 줄이 interrupt** — `answer = interrupt({"question": state.question})` (`from langgraph.types import interrupt`). 반환: `{"qa_log": [{"kind": "human_answer", "question": state.question, "answer": answer}], "decision": None, "question": None}`. interactive/park 공용(파킹=interrupt로 스레드 대기, 재개는 integrate로 — 고정 엣지).
- `conclude(state)`:
  - **결정론 degraded**: `state.evidence`가 비어 있으면 LLM 없이 `Verdict(verdict_type="degraded", confidence="low", narrative="증거 수집 전멸 — 조사 실패", caveats=[에러 태스크 원인 나열])` 반환.
  - LLM 경로: 프롬프트에 가설 보드(supported 우선)·증거 목록(id/summary/complete/effective_as_of)·태스크 에러율·**재작성 시 `state.verify_problems`**("다음 문제를 고쳐 다시 작성하라") 포함. 지시: Verdict JSON — 모든 주장에 실재 증거 id 인용, complete=False 증거를 쓰면 caveats에 그 id 명시, 확신 없으면 inconclusive 허용(억지 결론 금지).
  - `_ask_llm(Verdict)` 이중 실패 → degraded verdict(파싱 실패 caveat).
  - 반환: `{"verdict": ...}`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_nodes_conclude_verify.py` (conclude 분량)

```python
from src.application.nodes import make_nodes
from src.application.state import CaseState
from src.domain.case import Case, EvidenceRef, PlanTask
from tests.application.test_nodes_frame import T, _deps

VERDICT_JSON = ('{"verdict_type": "stale_data", "confidence": "high", "narrative": "n", '
                '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1"]}}')


def _state(**kw):
    base = dict(case=Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T))
    base.update(kw)
    return CaseState(**base)


async def test_증거_전멸은_LLM_없이_degraded():
    deps = _deps([])            # 스크립트 없음 — LLM 호출되면 RuntimeError로 테스트가 실패한다
    state = _state(plan_tasks=[PlanTask(id="t-1", goal="g", role="data_prober",
                                        status="error", error="타임아웃")])
    update = await make_nodes(deps)["conclude"](state)
    assert update["verdict"].verdict_type == "degraded"
    assert any("타임아웃" in c for c in update["verdict"].caveats)


async def test_재작성_요청은_verify_problems를_프롬프트에_싣는다():
    deps = _deps([VERDICT_JSON])
    state = _state(evidence=[EvidenceRef(id="ev-1", source="mongo:twin_state", summary="s")],
                   verify_problems=["없는 id ev-9 인용"])
    update = await make_nodes(deps)["conclude"](state)
    assert update["verdict"].root_cause.component == "plan-sync"
    assert "없는 id ev-9 인용" in str(deps.lead_llm.calls[0])
```

- [ ] **Step 2~4**: FAIL → 구현(ask_human 포함 — 한국어 프롬프트 상수 `_CONCLUDE_PROMPT`) → 전체 PASS → 커밋

```bash
git add src/application/nodes.py tests/application/test_nodes_conclude_verify.py
git commit -m "Conclude with cited causal chains, degrading when evidence died"
```

---

### Task 5: verify — 결정론 가드레일 + 강등

**Files:**
- Modify: `src/application/nodes.py`
- Test: `tests/application/test_nodes_conclude_verify.py` (verify 추가)

**Interfaces:**
- `verify(state)` (LLM 없음): `state.verdict`의 모든 CauseLink(root_cause + contributing)에 대해 —
  1. `evidence_ids`가 비어 있으면 문제("다리에 인용 없음: {component}"). (inconclusive/degraded는 root_cause 없음이 정상 — 있는 다리만 검사.)
  2. 각 id가 `store.has_evidence(case_id, id)`로 실재하지 않으면 문제.
  3. 인용된 id 중 state.evidence에서 `complete=False`인 것이 있는데 그 id가 verdict.caveats 문자열들 안에 등장하지 않으면 문제("불완전 증거 {id}가 caveat에 명시되지 않음").
  - 문제 없음 → `{"verify_problems": []}` (통과 — 라우터가 END).
  - 문제 있고 `verify_attempts == 0` → `{"verify_problems": [...], "verify_attempts": 1}` (재작성 경로).
  - 문제 있고 `verify_attempts >= 1` → **강등 통과**: `{"verdict": verdict.model_copy(update={"confidence": "low", "caveats": verdict.caveats + ["검증 미통과: " + "; ".join(problems)]}), "verify_problems": []}`.
- `route_after_verify(state) -> str`: `state.verify_problems`가 비어 있으면 `"__end__"` 아니면 `"conclude"`.

- [ ] **Step 1: 실패하는 테스트 추가** — 같은 테스트 파일에

```python
from src.application.nodes import route_after_verify
from src.domain.case import CauseLink, Verdict


def _verdict(ids, caveats=()):
    return Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                   root_cause=CauseLink(component="plan-sync", evidence_ids=list(ids)),
                   caveats=list(caveats))


async def test_없는_id_인용은_재작성_경로():
    deps = _deps([])
    state = _state(verdict=_verdict(["ev-9"]))
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_attempts"] == 1 and update["verify_problems"]
    assert route_after_verify(state.model_copy(update=update)) == "conclude"


async def test_재실패는_강등_통과():
    deps = _deps([])
    state = _state(verdict=_verdict(["ev-9"]), verify_attempts=1)
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_problems"] == []
    assert update["verdict"].confidence == "low"
    assert any("검증 미통과" in c for c in update["verdict"].caveats)


async def test_불완전_증거는_caveat_명시를_요구한다():
    deps = _deps([])
    eid = deps.store.put_evidence("c-1", "kafka:edge.raw", [1, 2], complete=False)
    ok_ref = EvidenceRef(id=eid, source="kafka:edge.raw", summary="s", complete=False)
    bad = _state(evidence=[ok_ref], verdict=_verdict([eid]))
    update = await make_nodes(deps)["verify"](bad)
    assert update["verify_problems"]                       # caveat에 없음 → 문제

    good = _state(evidence=[ok_ref], verdict=_verdict([eid], caveats=[f"불완전 증거 {eid} 기반"]))
    update2 = await make_nodes(deps)["verify"](good)
    assert update2["verify_problems"] == []
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/application/nodes.py tests/application/test_nodes_conclude_verify.py
git commit -m "Verify citations deterministically, demoting twice-failed verdicts"
```

---

### Task 6: graph.py + usecase + E2E 3종

**Files:**
- Create: `src/application/graph.py`, `src/application/usecase.py`
- Test: `tests/application/test_graph_e2e.py`

**Interfaces:**
- `build_engine(deps: EngineDeps, *, checkpointer=None)` — StateGraph(CaseState) 배선(파일 상단 그래프 형태 그대로), `compile(checkpointer=checkpointer)`.
- `investigate_case(case: Case, *, deps, checkpointer=None, thread_id=None, interaction_policy="autonomous", question_policy="default_and_log") -> dict` — 초기 CaseState 조립 후 `ainvoke`, config `{"configurable": {"thread_id": thread_id or case.id}}`. 반환은 최종 state dict.
- `resume_case(answer: str, *, deps, checkpointer, thread_id) -> dict` — `Command(resume=answer)`로 재개(§2.4 park 재개 → ask_human 반환 → integrate).
- E2E 시나리오 (전부 `parallel_width=1`로 결정론 직렬화, ScriptedLLM(lead) + ToolFake(subagent)):
  1. **해피패스 미니 OEE**: 스텁 시드 mongo `twin_state=[{line:7, oee:5.12, planned_time:75}]`, redis `{"plan:6:today": "480"}`(plan:7 없음). 각본: frame(가설 2, 태스크 t-1 mongo·t-2 redis) → 라운드1 t-1 → integrate(continue) → 라운드2 t-2 → integrate(conclude) → conclude(Verdict: stale_data, root_cause plan-sync, evidence_ids ["ev-1","ev-2"]) → verify 통과. 단언: verdict.root_cause.component=="plan-sync", round==2, 태스크 전부 ok, evidence 2건에 봉투 메타.
  2. **ask→interrupt→resume**: `interaction_policy="interactive"` + 각본 integrate가 ask. `ainvoke` 결과에 `"__interrupt__"` 포함 확인 → `resume_case("계획 변경 없음")` → 이어서 conclude까지. 단언: qa_log에 human_answer, 최종 verdict 존재.
  3. **verify 재작성**: conclude 각본 1차가 ev-99(유령) 인용 → verify가 conclude 재진입 → 2차 각본이 실재 id 인용 → 통과. 단언: verify_attempts==1, 최종 verdict 정상 confidence.
- InMemorySaver: `from langgraph.checkpoint.memory import InMemorySaver` — 시나리오 2에서 필수(스레드 유지).

- [ ] **Step 1: 실패하는 테스트 작성** — 위 시나리오 3개를 실제 시드·각본 JSON으로 구현 (frame/integrate/conclude 각본 문자열은 Task 1~5 테스트의 JSON 형식을 재사용해 조립).
- [ ] **Step 2: FAIL 확인** (graph 모듈 부재)
- [ ] **Step 3: 구현** — build_engine·usecase. 라우터 매핑: `route_after_frame`의 `"__end__"` → `END`. conditional edge에 Send 리스트 반환(route_after_select).
- [ ] **Step 4: 전체 스위트 PASS 후 커밋**

```bash
git add src/application/graph.py src/application/usecase.py tests/application/test_graph_e2e.py
git commit -m "Wire the engine graph end to end with interrupt and rewrite paths"
```

---

## 완료 기준 (계획 3b)

- `.venv/bin/pytest` 전체 통과.
- E2E 3종이 스크립트 LLM·스텁만으로 결정론 통과: 케이스 하나가 frame→조사 라운드→판정→검증까지 완주하고, interrupt 재개와 verify 재작성 경로가 실증됨.
- 어떤 노드도 raise하지 않고, verify 통과 판정 없이 verdict가 END에 도달하는 경로가 없음(frame/conclude의 degraded 강등 포함 — 이들은 verify를 거치거나(conclude 경로) frame 단락으로 명시 종료).

## 계획 4 예고

순찰(판정기 3종·스케줄러·llm_budget), Finding→케이스 게이트(지문·스냅샷 가드레일), 케이스 큐·동시 상한·수명주기(owner/lease·타임아웃), Mongo 케이스 Store·체크포인터(pymongo 핀 확인), 실행 레저·하트비트.
