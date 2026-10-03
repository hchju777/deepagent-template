"""네트워크 없는 LLM 대역.

- `EchoAdapter`: 개발용. 프롬프트를 되돌려준다 — 배선이 연결됐는지만 본다.
- `ScriptedAdapter`: 테스트의 축. 예약된 응답을 **순서대로** 재생한다.

## 왜 테스트가 대본을 쓰는가

"가설이 둘일 때 select가 몇 개를 고르는가"를 검증하려면 LLM이 무엇을 답할지
테스트가 정해야 한다. 진짜 LLM은 매번 다르게 답해서 이걸 못 한다.
결정론이 없으면 테스트는 무작위로 깨지고, 무작위로 깨지는 테스트는 아무도
믿지 않게 되고, 안 믿는 테스트는 없는 것과 같다.
"""
import asyncio
from pathlib import Path

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


class ExplodingAdapter(LlmPort):
    """**실제로 던진다.** 무raise 방어를 검사하는 유일한 방법이다.

    `ScriptedAdapter`에 예외를 예약하면 그건 `status="error"` 응답으로 바뀌어 나온다 —
    "어댑터가 계약대로 실패를 값으로 돌려준 것"이다. 그것만으로 테스트하면
    `ask_json`의 최외곽 try/except를 **지워도 전부 통과한다**. 9e에서 실제로 그랬다
    (`fakes.py`의 `ExplodingRunner`가 같은 이유로 있다).
    """

    def __init__(self, message: str = "게이트웨이 폭발"):
        self._message = message
        self.prompts: list[str] = []

    def describe(self) -> str:
        return "exploding(항상 던진다)"

    async def ask(self, prompt: str) -> LlmReply:
        self.prompts.append(prompt)
        raise RuntimeError(self._message)


class FileTurnAdapter(LlmPort):
    """**바깥의 무언가가 답을 써 넣는** 턴 방식. 프롬프트를 `NNN-ask.md`로 내고
    `NNN-reply.md`가 생길 때까지 기다린다.

    왜 있나: 사내 모델은 이 리포 밖에서만 돈다. 측정을 매번 사내에 부탁하면 사람이
    결과를 손으로 옮겨야 하고, 그게 11a 후반의 왕복 전부였다. 이 어댑터면 **같은
    배선**(CLI → 리드 → 그래프 → 트레이스)을 여기서 끝까지 돌리고, 리드 자리에만
    약한 모델 대역을 세울 수 있다.

    답 파일이 안 오면 `status="error"`로 흡수한다 — 던지지 않는다(규율 1).
    """

    def __init__(self, turn_dir: str | Path, *, clock: Clock, model: str = "file",
                 timeout_s: float = 1800.0, poll_s: float = 1.0):
        self._dir = Path(turn_dir)
        self._clock = clock
        self._model = model
        self._timeout = timeout_s
        self._poll = poll_s
        self._turn = 0

    def describe(self) -> str:
        return f"file {self._model} → {self._dir}"

    async def ask(self, prompt: str) -> LlmReply:
        self._turn += 1
        asked_at = self._clock()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            ask = self._dir / f"{self._turn:03d}-ask.md"
            reply = self._dir / f"{self._turn:03d}-reply.md"
            reply.unlink(missing_ok=True)          # 지난 실행의 답을 새 답으로 오인하지 않게
            ask.write_text(prompt, encoding="utf-8")
            waited = 0.0
            while not reply.exists():
                if waited >= self._timeout:
                    return LlmReply(status="error", asked_at=asked_at, model=self._model,
                                    error=f"{reply.name}이 {self._timeout:.0f}초 안에 안 왔다")
                await asyncio.sleep(self._poll)
                waited += self._poll
            # 쓰는 쪽이 아직 쓰는 중일 수 있다 — 한 박자 뒤에 읽는다.
            await asyncio.sleep(self._poll)
            return LlmReply(status="ok", asked_at=asked_at, model=self._model,
                            text=reply.read_text(encoding="utf-8"))
        except Exception as exc:                                        # noqa: BLE001
            return LlmReply(status="error", asked_at=asked_at, model=self._model,
                            error=f"{type(exc).__name__}: {exc}")

