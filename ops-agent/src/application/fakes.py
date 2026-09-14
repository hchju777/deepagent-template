"""네트워크 없는 실행기 대역.

## 왜 **던지는** 짝이 따로 있는가

`ScriptedAdapter`(LLM 대역)가 예외를 응답으로 바꿔 준 탓에, `comment_on`의 최외곽
try/except를 지워도 대본 테스트 27개가 전부 통과한 적이 있다. 방어를 검사하려면
대본이 아니라 **실제로 던지는 물건**이 필요하다.

`ScriptedRunner`도 같은 성질을 갖는다 — 예약된 `TaskOutcome`을 돌려줄 뿐이라
`status="error"`를 재생해도 그건 "실행기가 얌전히 실패를 보고한 것"이다.
`ExplodingRunner`가 있어야 "실행기가 계약을 어겼을 때 노드가 버티는가"를 볼 수 있다.
"""
from src.domain.case import Case, PlanTask
from src.domain.investigation import TaskOutcome, TaskRunnerPort


class ScriptedRunner(TaskRunnerPort):
    """태스크 id별로 예약된 결과를 돌려준다. 예약이 없으면 빈 성공."""

    def __init__(self, outcomes: dict[str, TaskOutcome] | None = None):
        self._outcomes = dict(outcomes or {})
        self.ran: list[str] = []

    def describe(self) -> str:
        return f"scripted({len(self._outcomes)}개 예약)"

    async def run(self, task: PlanTask, *, case: Case) -> TaskOutcome:
        self.ran.append(task.id)
        return self._outcomes.get(
            task.id, TaskOutcome(task_id=task.id, status="ok", summary="(대본 없음)"))


class ExplodingRunner(TaskRunnerPort):
    """**실제로 던진다.** 무raise 방어를 검사하는 유일한 방법이다."""

    def __init__(self, message: str = "폭발"):
        self._message = message
        self.ran: list[str] = []

    def describe(self) -> str:
        return "exploding(항상 던진다)"

    async def run(self, task: PlanTask, *, case: Case) -> TaskOutcome:
        self.ran.append(task.id)
        raise RuntimeError(self._message)
