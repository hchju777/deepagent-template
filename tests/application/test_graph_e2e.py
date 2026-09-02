"""그래프 E2E 3종 — 계획 3b 완료 기준: frame→조사 라운드→판정→검증 완주,
interrupt 재개, verify 재작성 경로를 스크립트 LLM·스텁만으로 결정론 실증한다.

전부 parallel_width=1로 select→execute를 직렬화해 ScriptedLLM(lead) 각본과
ToolFake(subagent) 각본이 라운드 순서와 정확히 대응하게 만든다.
"""
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.application.deps import EngineDeps
from src.application.usecase import investigate_case, resume_case
from src.config.schema_app import EngineConfig
from src.config.schema_site import SiteConfig
from src.domain.case import Case
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters
from src.infrastructure.llm import ScriptedLLM
from src.knowledge.topology import Topology
from tests.application.test_nodes_frame import SITE, T
from tests.application.test_subagents import ToolFake

TOPO = Topology.model_validate({})
SITE_MONGO_REDIS = SiteConfig.model_validate({
    "target": {"mongo": {"url": "mongodb://x:27017"}, "redis": {"url": "redis://x:6379"}}})


def _deps(*, lead_responses, subagent_messages, site=SITE, seeds=None):
    return EngineDeps(
        lead_llm=ScriptedLLM(lead_responses),
        subagent_llm=ToolFake(messages=iter(subagent_messages)),
        adapters=build_adapters(site, TOPO, clock=lambda: T, stub_seeds=seeds or StubSeeds()),
        store=InMemoryCaseStore(), topology=TOPO,
        engine_cfg=EngineConfig(parallel_width=1))


def _mongo_call(collection, call_id="c1"):
    return AIMessage(content="", tool_calls=[{
        "name": "mongo_find", "id": call_id,
        "args": {"collection": collection, "filter_json": "{}"}}])


def _redis_call(key, call_id="c2"):
    return AIMessage(content="", tool_calls=[{
        "name": "redis_get", "id": call_id, "args": {"key": key}}])


def _report(evidence_ids, summary="확인"):
    ids = ", ".join(f'"{e}"' for e in evidence_ids)
    return AIMessage(content=f'{{"status": "ok", "summary": "{summary}", "evidence_ids": [{ids}]}}')


FRAME_ONE_TASK_JSON = (
    '{"hypotheses": [{"id": "h-1", "statement": "OEE 계산 이상"}], '
    '"tasks": [{"id": "t-1", "goal": "twin_state 조회", "role": "data_prober"}]}')
INTEGRATE_CONCLUDE_JSON = '{"decision": "conclude"}'


# ── 시나리오 1: 해피패스 미니 OEE ──────────────────────────────────────────
FRAME_TWO_TASKS_JSON = (
    '{"hypotheses": [{"id": "h-1", "statement": "OEE 계산 이상"}, '
    '{"id": "h-2", "statement": "plan 동기화 지연"}], '
    '"tasks": [{"id": "t-1", "goal": "twin_state 조회", "role": "data_prober"}, '
    '{"id": "t-2", "goal": "plan:6 조회", "role": "data_prober"}]}')
INTEGRATE_CONTINUE_JSON = '{"decision": "continue"}'
STALE_DATA_VERDICT_JSON = (
    '{"verdict_type": "stale_data", "confidence": "high", "narrative": "plan 동기화 지연으로 '
    '오늘자 계획 시간이 반영되지 않아 OEE가 왜곡됐다.", '
    '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1", "ev-2"]}}')


async def test_해피패스_미니_OEE_조사가_완주한다():
    seeds = StubSeeds(
        mongo_collections={"twin_state": [{"line": 7, "oee": 5.12, "planned_time": 75}]},
        redis_data={"plan:6:today": "480"})   # plan:7은 없음(브리프의 미니 시드)
    deps = _deps(
        lead_responses=[FRAME_TWO_TASKS_JSON, INTEGRATE_CONTINUE_JSON,
                        INTEGRATE_CONCLUDE_JSON, STALE_DATA_VERDICT_JSON],
        subagent_messages=[
            _mongo_call("twin_state"), _report(["ev-1"]),      # 라운드1: t-1
            _redis_call("plan:6:today"), _report(["ev-2"])],   # 라운드2: t-2
        site=SITE_MONGO_REDIS, seeds=seeds)
    case = Case(id="c-oee-1", gbm="mx", fct="gumi", origin="patrol", symptom="OEE 512%", t0=T)

    result = await investigate_case(case, deps=deps)

    assert "__interrupt__" not in result
    verdict = result["verdict"]
    assert verdict.root_cause.component == "plan-sync"
    assert result["round"] == 2
    assert all(t.status == "ok" for t in result["plan_tasks"])
    assert len(result["evidence"]) == 2
    sources = {e.id: e.source for e in result["evidence"]}
    assert sources == {"ev-1": "mongo:twin_state", "ev-2": "redis:plan:6:today"}
    assert all(e.as_of == T and e.complete is True for e in result["evidence"])   # 봉투 메타


# ── 시나리오 2: ask → interrupt → resume ───────────────────────────────────
ASK_JSON = '{"decision": "ask", "question": "계획 변경이 있었나요?"}'
ONE_EVIDENCE_VERDICT_JSON = (
    '{"verdict_type": "stale_data", "confidence": "high", "narrative": "plan 동기화 지연.", '
    '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1"]}}')


async def test_ask는_interrupt로_멈추고_resume으로_conclude까지_이어진다():
    seeds = StubSeeds(mongo_collections={"twin_state": [{"line": 7, "oee": 5.12}]})
    deps = _deps(
        lead_responses=[FRAME_ONE_TASK_JSON, ASK_JSON, INTEGRATE_CONCLUDE_JSON,
                        ONE_EVIDENCE_VERDICT_JSON],
        subagent_messages=[_mongo_call("twin_state"), _report(["ev-1"])],
        seeds=seeds)
    checkpointer = InMemorySaver()
    case = Case(id="c-ask-1", gbm="mx", fct="gumi", origin="patrol", symptom="OEE 512%", t0=T)
    thread_id = case.id

    paused = await investigate_case(
        case, deps=deps, checkpointer=checkpointer, thread_id=thread_id,
        interaction_policy="interactive")
    assert "__interrupt__" in paused
    assert paused["qa_log"] == []                      # ask는 아직 사람에게 도달 전

    resumed = await resume_case(
        "계획 변경 없음", deps=deps, checkpointer=checkpointer, thread_id=thread_id)

    assert "__interrupt__" not in resumed
    human_entries = [e for e in resumed["qa_log"] if e["kind"] == "human_answer"]
    assert human_entries == [{"kind": "human_answer",
                              "question": "계획 변경이 있었나요?", "answer": "계획 변경 없음"}]
    assert resumed["verdict"] is not None
    assert resumed["verdict"].root_cause.component == "plan-sync"


# ── 시나리오 3: verify 재작성 ───────────────────────────────────────────────
GHOST_VERDICT_JSON = (
    '{"verdict_type": "stale_data", "confidence": "high", "narrative": "1차", '
    '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-99"]}}')      # 유령 id
REAL_VERDICT_JSON = (
    '{"verdict_type": "stale_data", "confidence": "high", "narrative": "2차", '
    '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1"]}}')       # 실재 id


async def test_verify_재작성은_실재_증거로_통과한다():
    seeds = StubSeeds(mongo_collections={"twin_state": [{"line": 7, "oee": 5.12}]})
    deps = _deps(
        lead_responses=[FRAME_ONE_TASK_JSON, INTEGRATE_CONCLUDE_JSON,
                        GHOST_VERDICT_JSON, REAL_VERDICT_JSON],
        subagent_messages=[_mongo_call("twin_state"), _report(["ev-1"])],
        seeds=seeds)
    case = Case(id="c-verify-1", gbm="mx", fct="gumi", origin="patrol", symptom="OEE 512%", t0=T)

    result = await investigate_case(case, deps=deps)

    assert "__interrupt__" not in result
    assert result["verify_attempts"] == 1
    assert result["verify_problems"] == []
    assert result["verdict"].root_cause.evidence_ids == ["ev-1"]
    assert result["verdict"].confidence == "high"       # 강등 없이 정상 통과
