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
