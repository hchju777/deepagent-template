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
import json
import re
from collections import OrderedDict
from typing import Any

from src.application.recompute import Recomputer
from src.domain.actions import ACTIONS, DISCOVERY_ACTIONS, action_problem, describe, narrowed, run_action
from src.domain.base import Clock
from src.domain.case import Case, EvidenceRef, PlanTask
from src.domain.investigation import TaskOutcome, TaskRunnerPort
from src.domain.jsonpath import steps

_SUMMARY_CHARS = 160
# 한 건이라도 **통째로** 보이게 하는 것이 요점이다. 문서를 반쯤 자르면 리드는
# 필드 이름은 보고 값은 못 봐서, 같은 질의를 말만 바꿔 다시 낸다.
_DETAIL_CHARS = 2400
# 리드가 **좁혀서** 낸 읽기(`actions.narrowed`)의 상한. 증거 블록 전체 예산(`evidence_total_chars`)과 같은 수다 — 고른
# 부분은 통째로 보여야 하고, 그래도 안 들어가면 더 좁히라는 열린 질문이 남는다. 상한이 없으면 `path=record`
# 한 번이 프롬프트를 혼자 채운다.
_NARROWED_CHARS = 12000
# 원천 재집계가 기대값을 꺼낼 원본을 몇 건까지 들고 있나. 케이스 하나의 증거는 수십 건이다.
_RAW_KEEP = 256


class ProbeRunner(TaskRunnerPort):
    """사이트 하나의 어댑터 묶음에 붙는다."""

    def __init__(self, adapters, *, clock: Clock, detail_chars: int = _DETAIL_CHARS,
                 narrowed_chars: int = _NARROWED_CHARS):
        self._adapters = adapters
        self._detail_chars = detail_chars
        self._narrowed_chars = max(narrowed_chars, detail_chars)
        # 시계를 필수로 받는다(규율 2). 어댑터가 자기 시계를 갖고 있지만, 포트에
        # **닿기 전에** 거부하는 경우(미등재 action 등)에는 우리가 봉투를 만들어야
        # 하고, 그때 `datetime.now()`로 떨어지면 테스트가 시간에 묶인다.
        self._clock = clock
        # 원천 재집계(11b 3b)는 앞선 증거의 **원본**에서 기대값을 꺼낸다. State의 증거 body는 렌더한
        # 텍스트라 값을 못 꺼내고, 저장소에 원본을 남기는 것은 비밀·용량 문제라 안 한다 — 이 실행기가
        # 만든 결과를 증거 id별로 프로세스 안에 들고 있는다(개수 상한). 다른 프로세스에서 재개된
        # 케이스면 첫 recompute가 "모른다"고 답하고 리드가 그 읽기를 다시 낸다.
        self._raw: OrderedDict[str, Any] = OrderedDict()
        self._recompute = Recomputer(getattr(adapters, "mongo", None), self._raw.get, clock=clock)

    def describe(self) -> str:
        return f"probe({', '.join(self._adapters.available()) or '없음'})"

    async def run(self, task: PlanTask, *, case: Case) -> TaskOutcome:
        if not task.action:
            return TaskOutcome(task_id=task.id, status="error",
                               error="action이 없다 — ProbeRunner는 태스크가 선언한 읽기만 한다")
        source = describe(task.action, task.params)
        if task.action.startswith("recompute."):
            # 표의 검사는 같이 받되, 어댑터가 아니라 우리 실행기가 돈다.
            problem = action_problem(task.action, task.params)
            if problem is not None:
                return TaskOutcome(task_id=task.id, status="error", error=f"{source} — {problem}")
            expect = task.params.get("expect")
            if isinstance(expect, dict):
                wrong = target_row_problem(self._raw.get(str(expect.get("evidence"))), expect.get("path"), focus_of(case))
                if wrong is not None:
                    return TaskOutcome(task_id=task.id, status="error", error=f"{source} — {wrong}")
            _, method, required, _ = ACTIONS[task.action]
            try:
                result = await getattr(self._recompute, method)(*[task.params[n] for n in required])
            except Exception as exc:                                    # noqa: BLE001
                return TaskOutcome(task_id=task.id, status="error",
                                   error=f"{source} — 재집계가 던졌다 — {type(exc).__name__}: {exc}")
        else:
            result = await run_action(self._adapters, task.action, task.params,
                                      clock=self._clock)
        if result.status == "error":
            return TaskOutcome(task_id=task.id, status="error",
                               error=f"{source} — {result.error}")

        body, ours = detail(result.data, limit=(self._narrowed_chars if narrowed(task.action, task.params)
                                                else self._detail_chars), focus=focus_of(case))
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
        self._raw[ref.id] = result.data
        while len(self._raw) > _RAW_KEEP:
            self._raw.popitem(last=False)
        return TaskOutcome(task_id=task.id, status="ok",
                           summary=f"{source} → {ref.summary}{note}", evidence=[ref],
                           found=_found(task.action, result.data))


def focus_of(case) -> tuple[str, ...]:
    """케이스 `target`의 식별 값들 — 순찰 판정이 `/`로 이은 것(`rules.py`)을 되푼다. 목록 증거에서 이 값이 전부 든 행을
    앞에 통째로 둔다(R2-2b-2). 한 글자는 어디에나 들어서 안 센다."""
    target = getattr(case, "target", None) or ""
    return tuple(part for part in target.split("/") if len(part) >= 2)


_TARGET_ROWS = re.compile(r" · 대상 행 (.+?) 먼저")


def target_rows(body: str) -> list[str]:
    """증거 머리줄이 짚은 대상 행 경로들(`response[2]`) — `detail`이 쓴 그 줄을 읽는다. 브리핑이 케이스 블록과 recompute 예시에
    싣는다(같은 모듈이 쓰고 읽어 형식이 갈리지 않는다)."""
    match = _TARGET_ROWS.search(body or "")
    return match.group(1).split(", ") if match else []


def target_row_problem(raw: Any, path: Any, focus: tuple[str, ...]) -> str | None:
    """recompute `expect.path`가 문서 목록의 행을 지나면 그 행이 케이스 대상 행인가 — 아니면 사유와 대상 행 경로.

    사내 측정 #4: 리드가 `response[0].alarm`(첫 행 — 다른 배지)을 기대값으로 써 엉뚱한 "불일치"가 사실로 남았다. 행을 고르는 기준은
    증거를 보여 줄 때 대상 행을 앞세운 것과 같다(값이 전부 든 행). 그 목록에 대상 값이 든 행이 없으면 가릴 수 없어 막지 않는다.
    """
    parts = steps(path) if focus else None
    cur, walked = raw, ""
    for step in parts or ():
        if isinstance(step, int):
            if not _doc_list(cur) or step >= len(cur):
                return None
            hits = [i for i, row in enumerate(cur) if all(value in _compact(row) for value in focus)]
            if hits and step not in hits:
                rows = ", ".join(f"{walked}[{i}]" for i in hits)
                return (f"expect.path의 {walked}[{step}]는 케이스 대상({'/'.join(focus)})의 행이 아니다 — 대상 행은 {rows}. "
                        f"그 경로로 다시 내라")
            cur, walked = cur[step], f"{walked}[{step}]"
        else:
            if not isinstance(cur, dict) or step not in cur:
                return None
            cur, walked = cur[step], f"{walked}.{step}" if walked else step
    return None


def _found(action: str, data: Any) -> list[str]:
    """발견 읽기의 이름 목록 그대로 — 코드가 선언된 이름과 대조한다(`application/facts.py`)."""
    if action in DISCOVERY_ACTIONS and isinstance(data, list) and all(isinstance(x, str) for x in data):
        return list(data)
    return []


def _summarize(data: Any) -> str:
    """**사람이 볼 한 줄.** 리드가 읽을 것은 `_detail`이다 — 둘은 다른 일이다.

    `repr`인 이유는 개행을 이스케이프하기 위해서다. 대상 데이터는 여러 줄이 정상인데,
    날것으로 실리면 목록 블록에 가짜 항목이 붙는다.
    """
    if isinstance(data, list):
        return f"{len(data)}건 {repr(data)[:_SUMMARY_CHARS]}"
    return repr(data)[:_SUMMARY_CHARS]


def detail(data: Any, *, limit: int = _DETAIL_CHARS, focus: tuple[str, ...] = ()) -> tuple[list[str], bool]:
    """리드가 읽을 여러 줄. **개행은 우리가 만든 것만 있다.**

    `repr`을 그냥 자르지 않고 모양을 본다:

    - 문서 목록 → **필드 목록** 한 줄 + 문서를 통째로 몇 건
    - 스칼라 목록(컬렉션·토픽·키 이름) → 개수 + 앞에서부터 들어가는 만큼
    - 그 밖 → repr

    필드 목록이 먼저인 이유: "이 컬렉션에 뭐가 들어 있나"가 조사의 실제 질문이고,
    그건 문서 한 건을 다 보기 전에 답할 수 있다. 예산이 모자라도 그건 남는다.

    **둘째 값은 "우리가 잘랐나"다.** 호출부가 그걸 봉투에 실어야 한다 — 자른 사실을
    안 실으면 리드가 조각을 전부로 착각한다.

    `focus`는 케이스 `target`의 식별 값들 — 문서 목록에서 이 값이 전부 든 행을 앞에 통째로 둔다(R2-2b-2).
    """
    if isinstance(data, dict):
        return _dict_detail(data, limit=limit, focus=focus)
    if isinstance(data, list):
        return _list_detail(data, limit=limit, focus=focus)
    line, cut = _line(repr(data), limit)
    return [line], cut


def _dict_detail(mapping: dict, *, limit: int, focus: tuple[str, ...] = ()) -> tuple[list[str], bool]:
    """**키 먼저.** 목록에서 필드를 먼저 보여 주는 것과 같은 이유다.

    중첩 config를 `repr`로 눕혀 그냥 자르면 **뒤쪽 키가 통째로 사라진다.** 사내
    측정에서 실제로 그랬다: `rules`가 예산을 다 먹고 `mongo.collection`이 잘려
    나갔는데, 그게 바로 조사가 찾던 이름이었다.
    """
    if not mapping:
        return ["비어 있다"], False
    head, cut_head = _line(", ".join(str(key) for key in mapping), limit)
    rows, cut_rows = _entries(mapping, limit=limit, focus=focus)
    return [f"키 {len(mapping)}개: {head}"] + rows, cut_head or cut_rows


def _entries(mapping: dict, *, limit: int, focus: tuple[str, ...] = ()) -> tuple[list[str], bool]:
    """값을 싣되, **안 들어가는 키는 건너뛰고 계속한다.**

    목록(`_fill`)은 첫 예산 초과에서 멈춘다 — 문서 `[1]` 다음에 `[5]`가 나오면
    읽는 사람이 헷갈리기 때문이다. dict는 반대다: config는 거대한 하위 트리
    하나(`rules` 같은)와 작고 중요한 키 여럿으로 돼 있는 것이 보통이라, 거기서
    멈추면 **뒤의 키가 통째로 안 보인다.** 사내 측정에서 `mongo.collection`이
    정확히 그렇게 사라졌다.
    """
    taken, used, skipped, cut = [], 0, 0, False
    for key, value in mapping.items():
        if _doc_list(value):
            # 문서 목록 값(`rest.query`의 `response`)은 한 줄로 눕히지 않는다 — 사내 실측에서 19개 항목이 첫 항목에서
            # 잘렸다. 목록처럼 필드 줄 + 항목당 한 줄로, 남은 예산 안에서(대상 행 먼저).
            block, cut_block = _list_detail(value, limit=max(limit - used, 0), focus=focus, at=str(key))
            lines = [f"{key}: {block[0]}"] + [f"  {row}" for row in block[1:]]
            taken += lines
            used += sum(len(line) for line in lines)
            cut = cut or cut_block
            continue
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


def _doc_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(row, dict) for row in value)


def _compact(row: dict) -> str:
    # 압축 JSON — repr보다 짧고(따옴표·공백) 리드가 `filter`·`path`에 그대로 옮겨 쓸 수 있는 모양이다.
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)


def _list_detail(rows: list, *, limit: int, focus: tuple[str, ...] = (), at: str = "") -> tuple[list[str], bool]:
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
        texts = [_compact(row) for row in rows]
        # 케이스 `target`의 값이 전부 든 행 — 사내 실측에서 대상 행은 19개 중 뒤쪽이라 예산 밖이었다. 앞에 두되
        # `[n]`은 원래 자리이고 **경로 문법과 같은 0부터**다 — 1부터 적었더니 리드가 그대로 옮기면 옆 행을 가리켰다. 머리줄은
        # 대상 행의 경로(`at`은 dict 안의 키)를 그대로 — 리드가 path·expect.path에 옮겨 쓰고, 브리핑이 읽는다(`target_rows`).
        hits = [i for i, text in enumerate(texts) if focus and all(value in text for value in focus)]
        order = hits + [i for i in range(len(rows)) if i not in set(hits)]
        body, cut_body = _fill([f"[{i}] {texts[i]}" for i in order], limit=limit, total=len(rows))
        note = f" · 대상 행 {', '.join(f'{at}[{i}]' for i in hits)} 먼저" if hits else ""
        return [f"{len(rows)}건 · 필드: {head}{note}"] + body, cut_head or cut_body
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
