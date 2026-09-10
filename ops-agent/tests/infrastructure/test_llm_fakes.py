"""네트워크 없는 대역 — 테스트의 결정론을 만드는 축."""
import pytest

from src.infrastructure.llm_fakes import EchoAdapter, ScriptedAdapter
from tests.conftest import T0


async def test_echo는_프롬프트를_되돌려준다(clock):
    llm = EchoAdapter(clock=clock)
    reply = await llm.ask("무엇이 이상한가?")
    assert reply.status == "ok" and "무엇이 이상한가?" in reply.text
    assert llm.prompts == ["무엇이 이상한가?"]


async def test_대본은_순서대로_재생된다(clock):
    llm = ScriptedAdapter(['{"step": 1}', '{"step": 2}'], clock=clock)
    assert (await llm.ask("a")).text == '{"step": 1}'
    assert (await llm.ask("b")).text == '{"step": 2}'
    assert llm.prompts == ["a", "b"]


async def test_대본에_예외를_섞어_실패를_재현한다(clock):
    llm = ScriptedAdapter([TimeoutError("게이트웨이 응답 없음")], clock=clock)
    reply = await llm.ask("a")
    assert reply.status == "error" and "게이트웨이 응답 없음" in reply.error
    assert reply.asked_at == T0


async def test_대본이_소진되면_던진다(clock):
    """대상 시스템의 실패가 아니라 **테스트가 잘못 쓰인 것**이므로 조용히 넘기지 않는다."""
    llm = ScriptedAdapter(["one"], clock=clock)
    await llm.ask("a")
    with pytest.raises(RuntimeError, match="대본 소진"):
        await llm.ask("b")
