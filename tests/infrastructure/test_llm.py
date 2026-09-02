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
