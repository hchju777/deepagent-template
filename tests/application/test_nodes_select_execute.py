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
