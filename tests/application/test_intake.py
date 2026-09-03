"""접수 대화(intake) 단위 테스트 — 스크립트 LLM으로 결정론 검증."""
from datetime import datetime, timezone
from types import SimpleNamespace

from src.application.intake import intake
from src.infrastructure.llm import ScriptedLLM
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"reads": [{"kind": "mongo", "collection": "twin_state"}],
                              "writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {"rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                                  "via": "twin-api"}}})


def _deps(responses):
    return SimpleNamespace(lead_llm=ScriptedLLM(responses))


async def test_한_번에_target_locator를_얻으면_추가_질문_없이_끝난다():
    deps = _deps(['{"gbm": "mx", "fct": "gumi", "target_locator": "mongo:twin_state", '
                 '"missing": []}'])

    result = await intake("OEE가 512%다", deps=deps, topology=TOPO, clock=lambda: T,
                          gbm="mx", fct="gumi")

    assert result.symptom == "OEE가 512%다"
    assert result.gbm == "mx" and result.fct == "gumi"
    assert result.target_locator == "mongo:twin_state"
    assert result.qa == []
    assert len(deps.lead_llm.calls) == 1


async def test_locator_목록이_프롬프트에_실린다():
    deps = _deps(['{"gbm": "mx", "fct": "gumi", "target_locator": null, "missing": []}'])

    await intake("증상", deps=deps, topology=TOPO, clock=lambda: T, gbm="mx", fct="gumi")

    prompt_text = deps.lead_llm.calls[0][0][1]
    assert "mongo:twin_state" in prompt_text and "rest:/oee" in prompt_text
    assert "mx/gumi" in prompt_text


async def test_missing이_있고_ask가_있으면_한_번_더_물어_target_locator를_받는다():
    deps = _deps([
        '{"gbm": "mx", "fct": "gumi", "target_locator": null, "missing": ["몇 번 라인인가요?"]}',
        '{"gbm": "mx", "fct": "gumi", "target_locator": "mongo:twin_state", "missing": []}',
    ])
    asked = []

    async def ask(question: str) -> str:
        asked.append(question)
        return "7번 라인"

    result = await intake("증상", deps=deps, topology=TOPO, clock=lambda: T,
                          gbm="mx", fct="gumi", ask=ask)

    assert asked == ["몇 번 라인인가요?"]
    assert result.target_locator == "mongo:twin_state"
    assert result.qa == [{"question": "몇 번 라인인가요?", "answer": "7번 라인",
                          "at": T.isoformat()}]
    assert len(deps.lead_llm.calls) == 2
    # 두 번째 호출 프롬프트에 답변이 실렸는지
    second_prompt = deps.lead_llm.calls[1][0][1]
    assert "7번 라인" in second_prompt


async def test_missing이_있어도_ask가_없으면_재시도_없이_qa에_남기고_끝난다():
    deps = _deps(['{"gbm": "mx", "fct": "gumi", "target_locator": null, '
                 '"missing": ["몇 번 라인인가요?"]}'])

    result = await intake("증상", deps=deps, topology=TOPO, clock=lambda: T,
                          gbm="mx", fct="gumi")

    assert result.target_locator is None
    assert len(deps.lead_llm.calls) == 1
    assert result.qa == [{"kind": "missing_unresolved", "questions": ["몇 번 라인인가요?"],
                          "at": T.isoformat()}]


async def test_1차_파싱_실패는_재시도로_회복된다():
    deps = _deps([
        "이건 JSON이 아니다",
        '{"gbm": "mx", "fct": "gumi", "target_locator": "mongo:twin_state", "missing": []}',
    ])

    result = await intake("증상", deps=deps, topology=TOPO, clock=lambda: T,
                          gbm="mx", fct="gumi")

    assert result.target_locator == "mongo:twin_state"
    assert len(deps.lead_llm.calls) == 2


async def test_파싱_이중_실패는_raise_없이_주어진_사이트로_target_locator_None을_돌려준다():
    deps = _deps(["이건 JSON이 아니다", "이것도 JSON이 아니다"])

    result = await intake("증상", deps=deps, topology=TOPO, clock=lambda: T,
                          gbm="mx", fct="gumi")

    assert result.gbm == "mx" and result.fct == "gumi"
    assert result.target_locator is None
    assert result.qa and result.qa[0]["kind"] == "intake_failed"
    assert len(deps.lead_llm.calls) == 2


async def test_LLM_호출_자체가_예외를_던져도_raise하지_않는다():
    class Boom:
        calls = []

        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("네트워크 끊김")

    deps = SimpleNamespace(lead_llm=Boom())

    result = await intake("증상", deps=deps, topology=TOPO, clock=lambda: T,
                          gbm="mx", fct="gumi")

    assert result.target_locator is None
    assert result.qa[0]["kind"] == "intake_failed"
    assert "네트워크 끊김" in result.qa[0]["reason"]


async def test_topology가_None이면_locator_목록_없음으로_진행한다():
    deps = _deps(['{"gbm": "mx", "fct": "gumi", "target_locator": null, "missing": []}'])

    result = await intake("증상", deps=deps, topology=None, clock=lambda: T,
                          gbm="mx", fct="gumi")

    assert result.target_locator is None
    prompt_text = deps.lead_llm.calls[0][0][1]
    assert "없음" in prompt_text
