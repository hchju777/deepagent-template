from datetime import datetime, timezone

from src.application.deps import EngineDeps
from src.application.nodes import make_nodes, route_after_frame
from src.application.state import CaseState
from src.config.schema_app import EngineConfig
from src.config.schema_site import CheckConfig, SiteConfig
from src.domain.case import Case
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters
from src.infrastructure.llm import ScriptedLLM
from src.knowledge.deployment import Deployment
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
# 제외 대상(`mongo:quality`·`qc-service`)은 **토폴로지 안에 있고 슬라이스 밖**이다 —
# 토폴로지 밖의 것을 고르면 `slice_=deps.topology`로 바꾸는 변조가 통과한다(검증 리뷰 F1).
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]},
                 "qc-service": {"reads": [{"kind": "mongo", "collection": "qc_raw"}],
                                "writes": [{"kind": "mongo", "collection": "quality"}]}},
    "derivations": {"rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                                  "via": "twin-api"},
                    "mongo:quality": {"inputs": [{"kind": "mongo", "collection": "qc_raw"}],
                                      "via": "qc-service"}}})
SITE = SiteConfig.model_validate({"target": {"mongo": {"url": "mongodb://x:27017"}}})

FRAME_JSON = ('{"hypotheses": [{"id": "h-1", "statement": "계산 이상"}], '
              '"tasks": [{"id": "t-1", "goal": "twin_state 조회", "role": "data_prober"}]}')


def _deps(lead_responses, **extra):
    return EngineDeps(
        lead_llm=ScriptedLLM(lead_responses), subagent_llm=None,
        adapters=build_adapters(SITE, TOPO, clock=lambda: T, stub_seeds=StubSeeds()),
        store=InMemoryCaseStore(), topology=TOPO, engine_cfg=EngineConfig(), **extra)


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


FRAME_JSON_INJECTED_STATUS = (
    '{"hypotheses": [{"id": "h-1", "statement": "계산 이상"}], '
    '"tasks": [{"id": "t-1", "goal": "twin_state 조회", "role": "data_prober", '
    '"status": "running", "result_summary": "가짜 요약", '
    '"result_evidence_ids": ["ev-x"], "error": "가짜 오류"}]}')


async def test_frame은_LLM이_주입한_태스크_상태를_pending으로_강제한다():
    # frame 각본이 status=running·result_evidence_ids를 실어 보내도(주입) select
    # 게이트·폭을 우회해 곧장 fan-out되면 안 된다(C1) — State에는 pending·빈 결과로 들어가야 한다.
    deps = _deps([FRAME_JSON_INJECTED_STATUS])
    update = await make_nodes(deps)["frame"](_state())
    task = update["plan_tasks"][0]
    assert task.status == "pending"
    assert task.result_summary is None
    assert task.result_evidence_ids == []
    assert task.error is None


class _RaisingLLM:
    """ainvoke 자체가 전송 예외를 던지는 가짜 — I4가 이를 잡는지 검증한다."""

    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append(messages)
        raise RuntimeError("네트워크 오류")


async def test_LLM_호출_자체가_실패해도_raise없이_degraded로_강등한다():
    deps = _deps([])
    deps.lead_llm = _RaisingLLM()
    update = await make_nodes(deps)["frame"](_state())      # raise되면 이 줄에서 테스트가 실패한다
    assert update["verdict"].verdict_type == "degraded"


async def test_frame은_케이스에_실린_이력을_브리핑에_싣는다():
    # 이력은 deps가 아니라 Case로 흐른다 — 엔진은 사이트당 한 번 조립돼 캐시되므로
    # deps의 정적 필드는 케이스마다 못 바꾼다(worker._engine_for).
    from src.domain.case import HistoryHit
    deps = _deps(['{"hypotheses": [], "tasks": []}'])
    case = Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T,
                history=[HistoryHit(case_id="c-old", tier=2, reason="다른 점검이 같은 대상을",
                                    verdict_type="stale_data", component="plan-sync",
                                    summary="ev-1을 보면 멈췄다")])
    await make_nodes(deps)["frame"](CaseState(case=case))
    prompt = str(deps.lead_llm.calls[0])
    assert "c-old" in prompt and "tier 2" in prompt and "plan-sync" in prompt
    assert "ev-1" not in prompt                 # 과거 id는 브리핑에 나가지 않는다(규율 3)


CHECKS = {
    "api.oee_range": CheckConfig.model_validate(
        {"judge": "rule", "schedule": {"interval": "10m"}, "target": "rest:/oee",
         "params": {"rule": "range", "min": 0, "max": 100}}),
    "quality.range": CheckConfig.model_validate(
        {"judge": "rule", "schedule": {"interval": "10m"}, "target": "mongo:quality",
         "params": {"rule": "exists"}}),
}
DEPLOY = Deployment.model_validate({"services": {
    "twin-api": {"repo": "twin", "commit": "abc123"},
    # 토폴로지에는 있고 슬라이스에는 없는 서비스 — 같은 이유로 여기가 제외 대상이다
    "qc-service": {"repo": "x", "commit": "def456"}}})


async def test_frame이_슬라이스에_걸리는_룰만_브리핑에_싣는다():
    deps = _deps([FRAME_JSON], checks=CHECKS)
    await make_nodes(deps)["frame"](_state())
    prompt_text = str(deps.lead_llm.calls[0])
    assert "api.oee_range" in prompt_text
    assert "quality.range" not in prompt_text


async def test_frame이_슬라이스_서비스의_배포_커밋을_브리핑에_싣는다():
    deps = _deps([FRAME_JSON], deployment=DEPLOY)
    await make_nodes(deps)["frame"](_state())
    prompt_text = str(deps.lead_llm.calls[0])
    assert "abc123" in prompt_text
    assert "def456" not in prompt_text


def test_docs_text라는_이름이_두_모듈에서_사라졌다():
    # 자유 문서는 코퍼스도 선별기도 없다 — 매번 "없음"을 찍는 자리는 리드 프롬프트의
    # 잡음이자, 다음 사람에게 "배선돼 있다"는 착각을 준다.
    #
    # 이 테스트가 지키는 것은 **그 이름의 부재**이지 "생산자 없는 섹션이 없다"는
    # 성질이 아니다 — 다른 이름으로 같은 것을 되살리면 통과한다(검증 리뷰 F6).
    # 되돌림 방지에는 충분하고, 성질 자체는 사람이 리뷰에서 본다.
    import inspect

    from src.application import briefing, deps as deps_module
    assert "docs_text" not in inspect.getsource(briefing)
    assert "docs_text" not in inspect.getsource(deps_module)
