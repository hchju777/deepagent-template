"""LLM 어댑터 — 실구현은 OpenAI 호환 게이트웨이(ChatOpenAI), 테스트는 ScriptedLLM.

노드·서브에이전트가 요구하는 표면은 async ainvoke(messages) -> .content 뿐이다.
"""
from langchain_core.messages import AIMessage


def build_chat_model(model_name, *, base_url=None, api_key=None):
    from langchain_openai import ChatOpenAI   # 지연 import — 스텁 전용 환경 배려
    return ChatOpenAI(model=model_name, base_url=base_url, api_key=api_key, temperature=0)


class ScriptedLLM:
    """예약된 응답을 순서대로 재생한다 — 결정론 테스트의 축(스펙 §5.5)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("스크립트 소진 — 예약된 응답보다 호출이 많다")
        return AIMessage(content=self._responses.pop(0))
