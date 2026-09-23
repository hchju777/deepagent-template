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
_DETAIL_CHARS = 2400


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

        body, ours = detail(result.data, limit=self._detail_chars)
        ref = EvidenceRef(
            id=EvidenceRef.make_id(task.id, 1),
            source=source,
            summary=_summarize(result.data),
            body="\n".join(body),
            as_of=result.envelope.observed_at,
            # **우리가 자른 것도 잘린 것이다.** 예전엔 봉투만 봤고, 예산에서
            # 잘라 놓고 `complete=True`라고 적었다. 그러면 리드는 자기가 본 것이
            # 전부인 줄 알고 "없다"를 단정한다 — 10b에서 겪은 그 실패다.
            complete=result.envelope.complete and not ours)
        note = ("" if result.envelope.complete
                else f" (표본이 잘렸다: {result.envelope.truncated_reason})")
        if result.envelope.complete and ours:
            note = " (예산에서 잘렸다 — 없는 것이 아니다)"
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


def detail(data: Any, *, limit: int = _DETAIL_CHARS) -> tuple[list[str], bool]:
    """리드가 읽을 여러 줄. **개행은 우리가 만든 것만 있다.**

    `repr`을 그냥 자르지 않고 모양을 본다:

    - 문서 목록 → **필드 목록** 한 줄 + 문서를 통째로 몇 건
    - 스칼라 목록(컬렉션·토픽·키 이름) → 개수 + 앞에서부터 들어가는 만큼
    - 그 밖 → repr

    필드 목록이 먼저인 이유: "이 컬렉션에 뭐가 들어 있나"가 조사의 실제 질문이고,
    그건 문서 한 건을 다 보기 전에 답할 수 있다. 예산이 모자라도 그건 남는다.

    **둘째 값은 "우리가 잘랐나"다.** 호출부가 그걸 봉투에 실어야 한다 — 자른 사실을
    안 실으면 리드가 조각을 전부로 착각한다.
    """
    if isinstance(data, dict):
        return _dict_detail(data, limit=limit)
    if isinstance(data, list):
        return _list_detail(data, limit=limit)
    line, cut = _line(repr(data), limit)
    return [line], cut


def _dict_detail(mapping: dict, *, limit: int) -> tuple[list[str], bool]:
    """**키 먼저.** 목록에서 필드를 먼저 보여 주는 것과 같은 이유다.

    중첩 config를 `repr`로 눕혀 그냥 자르면 **뒤쪽 키가 통째로 사라진다.** 사내
    측정에서 실제로 그랬다: `rules`가 예산을 다 먹고 `mongo.collection`이 잘려
    나갔는데, 그게 바로 조사가 찾던 이름이었다.
    """
    if not mapping:
        return ["비어 있다"], False
    head, cut_head = _line(", ".join(str(key) for key in mapping), limit)
    rows, cut_rows = _entries(mapping, limit=limit)
    return [f"키 {len(mapping)}개: {head}"] + rows, cut_head or cut_rows


def _entries(mapping: dict, *, limit: int) -> tuple[list[str], bool]:
    """값을 싣되, **안 들어가는 키는 건너뛰고 계속한다.**

    목록(`_fill`)은 첫 예산 초과에서 멈춘다 — 문서 `[1]` 다음에 `[5]`가 나오면
    읽는 사람이 헷갈리기 때문이다. dict는 반대다: config는 거대한 하위 트리
    하나(`rules` 같은)와 작고 중요한 키 여럿으로 돼 있는 것이 보통이라, 거기서
    멈추면 **뒤의 키가 통째로 안 보인다.** 사내 측정에서 `mongo.collection`이
    정확히 그렇게 사라졌다.
    """
    taken, used, skipped, cut = [], 0, 0, False
    for key, value in mapping.items():
        flat, cut_row = _line(f"{key}: {value!r}", limit)
        if taken and used + len(flat) > limit:
            skipped += 1
            continue
        taken.append(flat)
        used += len(flat)
        cut = cut or cut_row
    if skipped:
        taken.append(f"… {skipped}개 키는 예산에서 빠졌다 (위 키 목록에는 있다)")
        cut = True
    return taken, cut


def _list_detail(rows: list, *, limit: int) -> tuple[list[str], bool]:
    if not rows:
        return ["0건 — 비어 있다"], False
    if all(isinstance(row, dict) for row in rows):
        fields, seen = [], set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fields.append(str(key))
        head, cut_head = _line(", ".join(fields), limit)
        body, cut_body = _fill([f"[{i}] {repr(row)}" for i, row in enumerate(rows, 1)],
                               limit=limit, total=len(rows))
        return [f"{len(rows)}건 · 필드: {head}"] + body, cut_head or cut_body
    # 이름 목록은 **한 줄에 여러 개**로 채운다. 한 줄에 하나씩 쓰면 같은 예산에
    # 훨씬 적게 보이는데, 여기서 리드가 하려는 일이 바로 "179개 중에 고르기"다 —
    # 이름이 더 보일수록 고를 수 있는 폭이 넓어진다.
    packed, cut = _pack([str(row) for row in rows], limit=limit)
    return [f"{len(rows)}건"] + packed, cut


_PACK_WIDTH = 100


def _pack(names: list[str], *, limit: int) -> tuple[list[str], bool]:
    lines, row, used = [], [], 0
    for name in names:
        flat, _ = _line(name, _PACK_WIDTH)
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
        return lines, True
    return lines, False


def _fill(candidates: list[str], *, limit: int, total: int) -> tuple[list[str], bool]:
    """예산이 닿는 데까지 싣고, **못 실은 것이 있으면 말한다.**

    조용히 자르면 리드는 그게 전부인 줄 알고 "없다"를 단정한다 — `complete=False`와
    같은 이유로, 안 보인 것이 예산 밖이었을 뿐인지 실제로 없는지를 구별해야 한다.
    """
    taken, used, cut = [], 0, False
    for row in candidates:
        flat, cut_row = _line(row, limit)
        cut = cut or cut_row
        if taken and used + len(flat) > limit:
            break
        taken.append(flat)
        used += len(flat)
    if len(taken) < total:
        taken.append(f"… {total - len(taken)}건 더 있다 (예산에서 잘림 — 없는 것이 아니다)")
        cut = True
    return taken, cut


def _line(text: str, limit: int) -> tuple[str, bool]:
    """한 줄로 눕히고 예산에 맞춘다. **데이터의 개행이 줄을 만들지 못하게** 한다.

    둘째 값이 "잘랐나"다. 예전엔 `…(잘림)` 표시만 붙이고 호출부에 안 알려 줘서,
    봉투는 `complete=True`인 채로 나갔다.
    """
    flat = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    if len(flat) <= limit:
        return flat, False
    return flat[:limit] + " …(잘림)", True
