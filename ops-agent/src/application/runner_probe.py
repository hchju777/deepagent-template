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

_SUMMARY_CHARS = 160
# 한 건이라도 **통째로** 보이게 하는 것이 요점이다. 문서를 반쯤 자르면 리드는
# 필드 이름은 보고 값은 못 봐서, 같은 질의를 말만 바꿔 다시 낸다.
_DETAIL_CHARS = 1200


class ProbeRunner(TaskRunnerPort):
    """사이트 하나의 어댑터 묶음에 붙는다."""

    def __init__(self, adapters, *, clock: Clock, detail_chars: int = _DETAIL_CHARS):
        self._adapters = adapters
        self._detail_chars = detail_chars
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
            body="\n".join(detail(result.data, limit=self._detail_chars)),
            as_of=result.envelope.observed_at,
            complete=result.envelope.complete)
        note = ("" if result.envelope.complete
                else f" (표본이 잘렸다: {result.envelope.truncated_reason})")
        return TaskOutcome(task_id=task.id, status="ok",
                           summary=f"{source} → {ref.summary}{note}", evidence=[ref])


def _summarize(data: Any) -> str:
    """**사람이 볼 한 줄.** 리드가 읽을 것은 `_detail`이다 — 둘은 다른 일이다.

    `repr`인 이유는 개행을 이스케이프하기 위해서다. 대상 데이터는 여러 줄이 정상인데,
    날것으로 실리면 목록 블록에 가짜 항목이 붙는다.
    """
    if isinstance(data, list):
        return f"{len(data)}건 {repr(data)[:_SUMMARY_CHARS]}"
    return repr(data)[:_SUMMARY_CHARS]


def detail(data: Any, *, limit: int = _DETAIL_CHARS) -> list[str]:
    """리드가 읽을 여러 줄. **개행은 우리가 만든 것만 있다.**

    `repr`을 그냥 자르지 않고 모양을 본다:

    - 문서 목록 → **필드 목록** 한 줄 + 문서를 통째로 몇 건
    - 스칼라 목록(컬렉션·토픽·키 이름) → 개수 + 앞에서부터 들어가는 만큼
    - 그 밖 → repr

    필드 목록이 먼저인 이유: "이 컬렉션에 뭐가 들어 있나"가 조사의 실제 질문이고,
    그건 문서 한 건을 다 보기 전에 답할 수 있다. 예산이 모자라도 그건 남는다.
    """
    if isinstance(data, list):
        return _list_detail(data, limit=limit)
    return [_line(repr(data), limit)]


def _list_detail(rows: list, *, limit: int) -> list[str]:
    if not rows:
        return ["0건 — 비어 있다"]
    if all(isinstance(row, dict) for row in rows):
        fields, seen = [], set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fields.append(str(key))
        lines = [f"{len(rows)}건 · 필드: {_line(', '.join(fields), limit)}"]
        return lines + _fill([f"[{i}] {repr(row)}" for i, row in enumerate(rows, 1)],
                             limit=limit, total=len(rows))
    # 이름 목록은 **한 줄에 여러 개**로 채운다. 한 줄에 하나씩 쓰면 같은 예산에
    # 훨씬 적게 보이는데, 여기서 리드가 하려는 일이 바로 "179개 중에 고르기"다 —
    # 이름이 더 보일수록 고를 수 있는 폭이 넓어진다.
    return [f"{len(rows)}건"] + _pack([str(row) for row in rows], limit=limit)


_PACK_WIDTH = 100


def _pack(names: list[str], *, limit: int) -> list[str]:
    lines, row, used = [], [], 0
    for name in names:
        flat = _line(name, _PACK_WIDTH)
        if row and len(", ".join(row)) + len(flat) + 2 > _PACK_WIDTH:
            lines.append(", ".join(row))
            used += len(lines[-1])
            row = []
            if used > limit:
                break
        row.append(flat)
    if row and used <= limit:
        lines.append(", ".join(row))
    shown = sum(line.count(", ") + 1 for line in lines)
    if shown < len(names):
        lines.append(f"… {len(names) - shown}건 더 있다 (예산에서 잘림 — 없는 것이 아니다)")
    return lines


def _fill(candidates: list[str], *, limit: int, total: int) -> list[str]:
    """예산이 닿는 데까지 싣고, **못 실은 것이 있으면 말한다.**

    조용히 자르면 리드는 그게 전부인 줄 알고 "없다"를 단정한다 — `complete=False`와
    같은 이유로, 안 보인 것이 예산 밖이었을 뿐인지 실제로 없는지를 구별해야 한다.
    """
    taken, used = [], 0
    for row in candidates:
        flat = _line(row, limit)
        if taken and used + len(flat) > limit:
            break
        taken.append(flat)
        used += len(flat)
    if len(taken) < total:
        taken.append(f"… {total - len(taken)}건 더 있다 (예산에서 잘림 — 없는 것이 아니다)")
    return taken


def _line(text: str, limit: int) -> str:
    """한 줄로 눕히고 예산에 맞춘다. **데이터의 개행이 줄을 만들지 못하게** 한다."""
    flat = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return flat if len(flat) <= limit else flat[:limit] + " …(잘림)"
