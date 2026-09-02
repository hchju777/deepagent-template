"""그래프 E2E 3종 — 계획 3b 완료 기준: frame→조사 라운드→판정→검증 완주,
interrupt 재개, verify 재작성 경로를 스크립트 LLM·스텁만으로 결정론 실증한다.

전부 parallel_width=1로 select→execute를 직렬화해 ScriptedLLM(lead) 각본과
ToolFake(subagent) 각본이 라운드 순서와 정확히 대응하게 만든다.
"""
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
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

# 아래 셋 + make_e2e_deps는 test_worker.py(계획 4b)가 그대로 재사용하는 모듈 수준
# 이름이다 — 워커 테스트도 "frame이 태스크 하나를 내고 → integrate가 바로
# conclude하고 → 실재 증거를 인용하는 verdict로 완주"라는 동일한 최소 각본이
# 필요해서, 여기 이미 있는 조각을 알기 쉬운 이름으로만 다시 노출한다.
FRAME_ONE_TASK = FRAME_ONE_TASK_JSON
INTEGRATE_CONCLUDE = INTEGRATE_CONCLUDE_JSON
# 시나리오 2의 ONE_EVIDENCE_VERDICT_JSON과 내용이 같다(ev-1 하나만 인용하는
# 최소 verdict) — 이름만 별도로 노출해 워커 테스트가 시나리오 순서에
# 얽매이지 않게 한다.
VERDICT_JSON = (
    '{"verdict_type": "stale_data", "confidence": "high", "narrative": "plan 동기화 지연.", '
    '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1"]}}')


def make_e2e_deps(store, *, lead, subagent=None, site=SITE, seeds=None):
    """_deps와 달리 store를 호출부가 넘긴 것을 그대로 쓴다 — 워커가 lease 밖에서
    미리 읽은 초기 증거(evidence_refs_for_case)와, 엔진 실행 중 새로 쌓이는
    증거가 같은 Store 인스턴스에 있어야 다음 라운드·다음 케이스 조회에서
    일관되게 보이기 때문이다(§계획 4b). subagent를 안 넘기면 FRAME_ONE_TASK가
    내는 단일 data_prober 태스크("twin_state 조회")를 그대로 처리할 mongo_find
    호출 + 보고 한 쌍을 기본값으로 쓴다.
    """
    if subagent is None:
        subagent = [_mongo_call("twin_state"), _report(["ev-2"])]
    return EngineDeps(
        lead_llm=ScriptedLLM(lead),
        subagent_llm=ToolFake(messages=iter(subagent)),
        adapters=build_adapters(site, TOPO, clock=lambda: T, stub_seeds=seeds or StubSeeds()),
        store=store, topology=TOPO,
        engine_cfg=EngineConfig(parallel_width=1))


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


# ── 시나리오 4: parallel_width=2 — 한 브랜치 에러가 다른 브랜치를 죽이지 않는다(I6) ──
class KeyedToolFake(ToolFake):
    """호출 순서가 아니라 태스크 goal의 키워드로 응답 시퀀스를 고르는 fake.

    ToolFake(GenericFakeChatModel)는 공유 iterator를 소비한다 — parallel_width>=2로
    execute 브랜치가 Send fan-out으로 동시에 돌면 create_agent가 스레드 풀에서
    _generate를 병렬로 부르고, next() 호출 순서가 스케줄링에 좌우돼 각본과 브랜치가
    어긋날 수 있다. 이 fake는 매 호출마다 메시지 본문에서 키워드를 찾아 그 브랜치
    전용 큐에서만 소비한다 — 각 create_agent 실행(=각 태스크)이 자신의 시퀀스만 쓴다.
    """
    branches: dict[str, list[AIMessage]] = {}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = " ".join(str(getattr(m, "content", "")) for m in messages)
        for keyword, queue in self.branches.items():
            if keyword in text and queue:
                return ChatResult(generations=[ChatGeneration(message=queue.pop(0))])
        raise RuntimeError(f"KeyedToolFake: 매칭되는 브랜치 없음 — {text[:200]!r}")


FRAME_TWO_TASKS_PARALLEL_JSON = (
    '{"hypotheses": [{"id": "h-1", "statement": "OEE 계산 이상"}], '
    '"tasks": [{"id": "t-1", "goal": "twin_state 정상 조회", "role": "data_prober"}, '
    '{"id": "t-2", "goal": "twin_state 실패 유도 조회", "role": "data_prober"}]}')


async def test_parallel_width_2에서_한_브랜치_에러가_다른_브랜치를_죽이지_않는다():
    seeds = StubSeeds(mongo_collections={"twin_state": [{"line": 7, "oee": 5.12}]})
    deps = _deps(
        lead_responses=[FRAME_TWO_TASKS_PARALLEL_JSON, INTEGRATE_CONCLUDE_JSON,
                        ONE_EVIDENCE_VERDICT_JSON],
        subagent_messages=[], seeds=seeds)
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"parallel_width": 2})
    deps.subagent_llm = KeyedToolFake(messages=iter([]), branches={
        "정상 조회": [_mongo_call("twin_state"), _report(["ev-1"])],
        "실패 유도": [AIMessage(content="말로만 있고 JSON이 아님")],
    })
    case = Case(id="c-parallel-1", gbm="mx", fct="gumi", origin="patrol",
               symptom="OEE 512%", t0=T)

    result = await investigate_case(case, deps=deps)

    assert "__interrupt__" not in result
    tasks_by_id = {t.id: t for t in result["plan_tasks"]}
    assert tasks_by_id["t-1"].status == "ok"
    assert tasks_by_id["t-2"].status == "error" and tasks_by_id["t-2"].error
    assert [e.id for e in result["evidence"]] == ["ev-1"]      # 성공 브랜치 증거만 실렸다
    # error 브랜치가 라운드를 죽이지 않고 conclude·verify까지 정상 완주했다.
    assert result["verdict"].root_cause.component == "plan-sync"
    assert result["verify_problems"] == []


# ── usecase 가드레일: question_policy 해석(M7), interrupt 경로 기동 거부(M8) ──────
async def test_question_policy_None은_engine_cfg의_autonomous_question_policy를_따른다():
    # M7: question_policy를 안 넘기면(None) deps.engine_cfg가 park면 ask가 그대로
    # 살아남아 interrupt까지 가야 한다 — 예전 하드코딩 기본값("default_and_log")이면
    # 자동응답으로 삼켜져 이 조사는 절대 멈추지 않는다.
    seeds = StubSeeds(mongo_collections={"twin_state": [{"line": 7, "oee": 5.12}]})
    deps = _deps(
        lead_responses=[FRAME_ONE_TASK_JSON, ASK_JSON],
        subagent_messages=[_mongo_call("twin_state"), _report(["ev-1"])],
        seeds=seeds)
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    checkpointer = InMemorySaver()
    case = Case(id="c-park-1", gbm="mx", fct="gumi", origin="patrol", symptom="OEE 512%", t0=T)

    paused = await investigate_case(case, deps=deps, checkpointer=checkpointer, thread_id=case.id)

    assert "__interrupt__" in paused


async def test_interrupt_경로인데_checkpointer가_없으면_기동을_거부한다():
    # M8: interaction_policy가 autonomous가 아니거나 해석된 question_policy가 park면
    # ask_human에서 멈출 수 있다 — checkpointer 없이 멈추면 그 조사는 영영 재개 불가능
    # 하므로, 그래프를 돌리기 전에 이 함수 서두에서 거부한다.
    deps = _deps(lead_responses=[], subagent_messages=[])
    case = Case(id="c-guard-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T)

    with pytest.raises(ValueError):
        await investigate_case(case, deps=deps, interaction_policy="interactive")

    deps2 = _deps(lead_responses=[], subagent_messages=[])
    deps2.engine_cfg = deps2.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    with pytest.raises(ValueError):
        await investigate_case(case, deps=deps2)
