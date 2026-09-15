"""태스크가 **선언한** 읽기 하나를 수행한다. LLM이 없다.

등재 목록과 인자 검사는 `domain/actions.py`가 갖는다 — 순찰의 프로브(4단계)와 이것이
**같은 표**를 쓴다. 각자 자기 표를 들면 언젠가 한쪽만 넓어지고, 넓은 쪽이 곧 우리
허용 범위가 된다.

여기 남는 것은 **태스크 ↔ 읽기의 번역**뿐이다: `PlanTask`를 action/params로 풀고,
`ProbeResult`를 `TaskOutcome`과 증거로 되돌린다.

## 증거는 실제 결과에서만 만든다

`ProbeResult`의 봉투를 그대로 물려받는다 — 특히 `complete`. 표본이 잘렸는데 완전한
척하면 12a의 verify가 "없음"을 근거로 한 결론을 못 걸러낸다.
"""
from typing import Any

from src.domain.actions import describe, run_action
from src.domain.base import Clock
from src.domain.case import Case, EvidenceRef, PlanTask
from src.domain.investigation import TaskOutcome, TaskRunnerPort


class ProbeRunner(TaskRunnerPort):
    """사이트 하나의 어댑터 묶음에 붙는다."""

    def __init__(self, adapters, *, clock: Clock):
        self._adapters = adapters
        # 시계를 필수로 받는다(규율 2). 어댑터가 자기 시계를 갖고 있지만, 포트에
        # **닿기 전에** 거부하는 경우(미등재 action 등)에는 우리가 봉투를 만들어야
        # 하고, 그때 `datetime.now()`로 떨어지면 테스트가 시간에 묶인다.
        self._clock = clock

    def describe(self) -> str:
        return f"probe({', '.join(self._adapters.available()) or '없음'})"

    async def run(self, task: PlanTask, *, case: Case) -> TaskOutcome:
        if not task.action:
            return TaskOutcome(task_id=task.id, status="error",
                               error="action이 없다 — ProbeRunner는 태스크가 선언한 읽기만 한다")
        source = describe(task.action, task.params)
        result = await run_action(self._adapters, task.action, task.params,
                                  clock=self._clock)
        if result.status == "error":
            return TaskOutcome(task_id=task.id, status="error",
                               error=f"{source} — {result.error}")

        ref = EvidenceRef(
            id=EvidenceRef.make_id(task.id, 1),
            source=source,
            summary=_summarize(result.data),
            as_of=result.envelope.observed_at,
            complete=result.envelope.complete)
        note = ("" if result.envelope.complete
                else f" (표본이 잘렸다: {result.envelope.truncated_reason})")
        return TaskOutcome(task_id=task.id, status="ok",
                           summary=f"{source} → {ref.summary}{note}", evidence=[ref])


_SUMMARY_CHARS = 160


def _summarize(data: Any) -> str:
    """증거 한 줄에 실을 요약.

    `repr`인 이유는 **개행을 이스케이프하기 위해서**다. 대상 데이터는 여러 줄이
    정상인데, 날것으로 프롬프트에 실리면 증거 목록 블록에 가짜 항목이 붙는다
    (9e의 `fenced()`와 같은 위험이고, 여기는 11b에서 프롬프트에 들어간다).
    """
    if isinstance(data, list):
        return f"{len(data)}건 {repr(data)[:_SUMMARY_CHARS]}"
    return repr(data)[:_SUMMARY_CHARS]
