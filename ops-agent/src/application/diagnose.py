"""조사 한 번이 **스스로 건강 상태를 재서** 짧게 보고한다.

## 왜 도구가 재는가

지금까지는 사람이 CLI 출력 60줄을 옮겨 적고, 그걸 읽으며 "같은 질의가 몇 번
반복됐나"를 손으로 셌다. 실제로 그렇게 해서 두 번의 버그를 찾았다 —
**같은 id 재사용**과 **id만 다른 같은 질의**. 둘 다 표를 그려 봐야 보였다.

손으로 셀 수 있는 것은 도구가 세야 한다. 사람이 옮겨 적는 양이 줄고, 무엇보다
**매번 같은 것을 센다** — 눈으로 세면 바쁜 날엔 놓친다.

## 12b가 이걸 쓴다

조사 보고서가 "몇 라운드 돌았고 무엇을 봤는가"를 적을 때 필요한 숫자가 그대로
여기 있다. 그래서 CLI가 아니라 application에 둔다.
"""
from src.application.state import CaseState
from src.domain.actions import describe


def diagnose(state: CaseState) -> list[str]:
    """사람이 그대로 복사해 붙일 수 있는 짧은 블록."""
    queries = [(t.id, describe(t.action, t.params), t.status)
               for t in state.plan_tasks if t.action]
    distinct = {q for _, q, _ in queries}
    ran = [q for q in queries if q[2] in ("ok", "error")]
    repeats = len(ran) - len({q for _, q, _ in ran})

    kinds: dict[str, int] = {}
    for problem in state.llm_errors:
        # "t-5: 이미 한 읽기를 또 냈다 — …" 에서 종류만 센다.
        label = problem.split(":", 1)[-1].split("—")[0].strip() or problem
        kinds[label] = kinds.get(label, 0) + 1

    status: dict[str, int] = {}
    for h in state.hypotheses:
        status[h.status] = status.get(h.status, 0) + 1

    cut = sum(1 for ref in state.evidence if not ref.complete)
    lines = [
        "진단",
        f"  라운드 {state.round} · 끝난 이유 {state.stopped_by}",
        f"  읽기 {len(ran)}회 · 서로 다른 질의 {len(distinct)}개"
        + (f" · **중복 {repeats}회**" if repeats else " · 중복 없음"),
        f"  증거 {len(state.evidence)}건"
        + (f" · 잘린 것 {cut}건" if cut else " · 잘린 것 없음"),
        # **무엇이** 잘렸는지 안 말하면 다음 라운드가 짐작이 된다. 실제로 그랬다:
        # "잘린 것 2건"만 보고는 리드가 이름을 못 본 건지 아닌지 알 수 없었다.
        *[f"    ✂ {ref.source}" for ref in state.evidence if not ref.complete],
        f"  가설 {len(state.hypotheses)}개"
        + (f" ({' · '.join(f'{k} {v}' for k, v in sorted(status.items()))})"
           if status else ""),
    ]
    if kinds:
        lines.append(f"  리드 계약 위반 {len(state.llm_errors)}건")
        lines += [f"    {count}× {label}" for label, count in sorted(kinds.items())]
    else:
        lines.append("  리드 계약 위반 없음")

    lines.append("  질의")
    mark = {"ok": "✅", "error": "❌", "pending": "⬜", "running": "…"}
    for task_id, query, task_status in queries:
        lines.append(f"    {mark.get(task_status, '?')} {task_id}  {query}")
    return lines
