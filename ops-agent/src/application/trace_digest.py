"""트레이스를 **사람이 붙여넣을 수 있는 크기**로 줄인다.

## 왜 필요한가

`--trace`가 남기는 것은 프롬프트 전문과 날것 응답이다 — 라운드당 수천 자다. 그런데
사내에서 돌리는 사람과 고치는 사람이 다르고, 그 사이는 **손으로 옮기는 대화**다.
그래서 지금까지 진단 요약만 오갔고, **리드가 무엇을 보고 무엇을 뱉었는지는 한 번도
건너오지 못했다.** 매 라운드 원인을 코드에서 역추적했고, 그게 왕복의 진짜 원인이었다.

여기서 뽑는 것은 셋뿐이다:

1. **리드가 본 것의 크기** — 태스크 몇 개, 증거 몇 건(잘린 것 몇 건), 버려진 것 몇 건
2. **예시가 보여 준 것** — 우리가 사실상 지시한 다음 수
3. **리드가 낸 것** — 그리고 그것이 예시와 같은 모양인지, 증거에 없는 이름인지,
   이미 한 질의인지

그리고 **리드가 이미 물은 것이 화면에 보였는지**를 같이 적는다. 증거 줄의 `source`가
곧 그 질의라, 리드는 그걸 볼 수 있다 — 다만 **증거를 못 만든 태스크의 질의는
어디에도 안 보인다.** 반복이 나왔을 때 "볼 수 있었는데 또 낸 것"인지 "볼 수가
없었던 것"인지가 하네스 결함과 모델 한계를 가르는 지점이고, 그걸 여기서 가른다.

2와 3을 나란히 놓는 것이 요점이다. 이 모델은 예시를 베끼므로, **베낀 것인지 스스로
고른 것인지**가 하네스 결함과 모델 한계를 가르는 유일한 신호다.

## 비밀값

증거 **내용은 아예 안 찍는다**(건수만 센다). 대상 config에는 비밀이 있을 수 있고,
그건 프롬프트에 실려 트레이스 파일에 남는다. params는 찍되 비밀처럼 생긴 **이름**의
값은 가린다 — `key`는 안 가린다, 그건 Redis 키 이름이지 비밀이 아니다.
"""
import json
import re

from src.application.schemas import parse_object
from src.domain.actions import DISCOVERED_ARGS, describe

_FILE = re.compile(r"^(\d+)-r(\d+)-(\w+)\.md$")
_SECRETISH = re.compile(r"pass|secret|token|credential|pwd", re.I)
_VALUE_CHARS = 40


def digest(entries: list[tuple[str, str]]) -> list[str]:
    """`(파일명, 내용)` 목록 → 붙여넣을 줄들. **파일명 순서가 곧 라운드 순서다.**"""
    lines, asked = ["트레이스 요약"], {}
    for name, text in sorted(entries):
        match = _FILE.match(name)
        if match is None:
            continue
        _, round_no, node = match.groups()
        prompt = _section(text, "물어본 것")
        reply = _section(text, "날것 응답")
        lines += _round(round_no, node, prompt, reply, asked)
    if len(lines) == 1:
        lines.append("  (읽을 수 있는 트레이스 파일이 없다)")
    return lines


def _round(round_no: str, node: str, prompt: str, reply: str,
           asked: dict[str, str]) -> list[str]:
    evidence = _block(prompt, "모은 증거")
    seen_names = evidence                       # 이름이 증거에 있나 — 문자열로 본다
    # 증거 줄의 `source`가 곧 그 질의다. `describe`가 만든 문자열이므로
    # 우리도 같은 함수로 만들어서 **정확히** 대조한다.
    visible = {line.split(" | ")[1].strip()
               for line in evidence.splitlines()
               if line.startswith("- ") and line.count(" | ") >= 2}
    shown = _tasks(_example(prompt))
    made = _tasks(parse_object(reply).data if parse_object(reply).ok else None)

    out = [f"\nr{round_no} {node} · 프롬프트 {len(prompt):,}자"]
    out.append(f"  리드가 본 것 : 태스크 {_count(_block(prompt, '지금까지의 태스크'))}"
               f" · 증거 {_count(evidence)}(잘림 {evidence.count('⚠ 표본이 잘렸다')})"
               f" · 버려진 것 {_count(_block(prompt, '버려진 태스크'))}")
    out.append("  이미 물은 것(증거에 보임) : "
               + ("  ".join(sorted(_clip(q) for q in visible)) or "(없음)"))
    out.append("  예시가 보여준 것 : "
               + ("  ".join(_shape(a, p) for _, a, p in shown) or "(없음)"))
    if not made:
        out.append("  리드가 낸 것 : (응답을 JSON으로 못 읽었다)")
        return out
    out.append("  리드가 낸 것 :")
    for task_id, action, params in made:
        marks = []
        if action in {a for _, a, _ in shown}:
            marks.append("예시와 같은 action")
        ghosts = [f"{k}={v!r}" for k, v in sorted(params.items())
                  if k in DISCOVERED_ARGS and isinstance(v, str) and v not in seen_names]
        if ghosts:
            marks.append(f"증거에 없는 이름 {', '.join(ghosts)}")
        query = _shape(action, params)
        spoken = describe(action, params)
        if spoken in visible:
            marks.append("**이미 한 질의 — 증거에 보이는데도 또 냈다**")
        elif query in asked:
            marks.append(f"이미 {asked[query]}에서 한 질의(증거엔 안 보였다)")
        else:
            asked[query] = f"r{round_no}"
        out.append(f"    {task_id} {query}"
                   + (f"   ← {' · '.join(marks)}" if marks else ""))
    return out


def _tasks(body) -> list[tuple[str, str, dict]]:
    if not isinstance(body, dict):
        return []
    found = []
    for task in body.get("tasks", []):
        if isinstance(task, dict) and task.get("action"):
            found.append((str(task.get("id", "?")), str(task["action"]),
                          task.get("params") or {}))
    return found


def _shape(action: str, params: dict) -> str:
    """`action{k:v}`. **비밀처럼 생긴 이름의 값은 가린다.**"""
    inside = ", ".join(
        f"{k}:{'***' if _SECRETISH.search(str(k)) else _clip(v)}"
        for k, v in sorted(params.items()))
    return f"{action}{{{inside}}}"


def _clip(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= _VALUE_CHARS else text[:_VALUE_CHARS] + "…"


def _example(prompt: str):
    """프롬프트 안에 박힌 예시 JSON. **중괄호를 세어서** 찾는다 — 예시는 코드펜스
    없이 들어가므로 정규식으로는 끝을 못 찾는다."""
    for start in (i for i, ch in enumerate(prompt) if ch == "{"):
        depth = 0
        for end in range(start, len(prompt)):
            depth += (prompt[end] == "{") - (prompt[end] == "}")
            if depth == 0:
                try:
                    body = json.loads(prompt[start:end + 1])
                except ValueError:
                    break
                if isinstance(body, dict) and "tasks" in body:
                    return body
                break
    return None


def _section(text: str, head: str) -> str:
    """`## <head>` 다음의 코드펜스 안."""
    start = text.find(f"## {head}")
    if start < 0:
        return ""
    fence = text.find("````", start)
    end = text.find("````", fence + 4)
    return "" if fence < 0 or end < 0 else text[fence + 4:end].strip("\n")


def _block(prompt: str, tag: str) -> str:
    start = prompt.find(f"<{tag}>")
    end = prompt.find(f"</{tag}>")
    return "" if start < 0 or end < 0 else prompt[start + len(tag) + 2:end].strip()


def _count(block: str) -> int:
    """`- `로 시작하는 줄의 수. 내용은 세지 않는다 — 비밀이 섞일 수 있다."""
    return sum(1 for line in block.splitlines() if line.startswith("- "))
