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
