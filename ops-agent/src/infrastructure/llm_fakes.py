"""네트워크 없는 LLM 대역.

- `EchoAdapter`: 개발용. 프롬프트를 되돌려준다 — 배선이 연결됐는지만 본다.
- `ScriptedAdapter`: 테스트의 축. 예약된 응답을 **순서대로** 재생한다.

## 왜 테스트가 대본을 쓰는가

"가설이 둘일 때 select가 몇 개를 고르는가"를 검증하려면 LLM이 무엇을 답할지
테스트가 정해야 한다. 진짜 LLM은 매번 다르게 답해서 이걸 못 한다.
결정론이 없으면 테스트는 무작위로 깨지고, 무작위로 깨지는 테스트는 아무도
믿지 않게 되고, 안 믿는 테스트는 없는 것과 같다.
"""
from src.domain.base import Clock
from src.domain.llm import LlmPort, LlmReply


class EchoAdapter(LlmPort):
    def __init__(self, *, clock: Clock, model: str = "echo"):
        self._clock = clock
        self._model = model
        self.prompts: list[str] = []

    def describe(self) -> str:
        return f"echo {self._model} → (네트워크 없음)"

    async def ask(self, prompt: str) -> LlmReply:
        self.prompts.append(prompt)
        return LlmReply(status="ok", asked_at=self._clock(), model=self._model,
                        text=f"[echo] {prompt}")


class ScriptedAdapter(LlmPort):
    def __init__(self, replies, *, clock: Clock, model: str = "scripted"):
        self._replies = list(replies)
        self._clock = clock
        self._model = model
        self.prompts: list[str] = []

    def describe(self) -> str:
        return f"scripted {self._model} ({len(self._replies)}개 남음)"

    async def ask(self, prompt: str) -> LlmReply:
        self.prompts.append(prompt)
        if not self._replies:
            # 여기서 **던진다**. 대본 소진은 대상 시스템의 실패가 아니라
            # 테스트가 잘못 쓰인 것이고, 그건 조용히 넘어가면 안 된다.
            raise RuntimeError("대본 소진 — 예약된 응답보다 호출이 많다")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            return LlmReply(status="error", asked_at=self._clock(), model=self._model,
                            error=f"{type(reply).__name__}: {reply}")
        return LlmReply(status="ok", asked_at=self._clock(), model=self._model, text=reply)
