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

from src.application.nodes import REDO_MARK
from src.application.schemas import parse_object
from src.domain.actions import DISCOVERED_ARGS, describe

_FILE = re.compile(r"^(\d+)-r(\d+)-(\w+)\.md$")
_VERDICT = re.compile(r"^결과: (.+)$", re.M)
_REASON_CHARS = 160
_SECRETISH = re.compile(r"pass|secret|token|credential|pwd", re.I)
_VALUE_CHARS = 40


def digest(entries: list[tuple[str, str]], *, brief: bool = False) -> list[str]:
    """`(파일명, 내용)` 목록 → 붙여넣을 줄들. **파일명 순서가 곧 라운드 순서다.**

    `brief`는 손으로 옮기는 사람을 위한 것이다 — "이미 물은 것"과 "예시가 보여준 것"을
    뺀다. 둘은 라운드마다 제일 길고, 둘 다 코드에서 다시 만들 수 있다(예시는 `used`로,
    물은 것은 앞 라운드의 태스크로). 리드가 낸 것과 판정만 사람이 옮기면 된다.
    """
    lines, asked, attempts = ["트레이스 요약"], {}, {}
    last_added: set[str] = set()
    for name, text in sorted(entries):
        match = _FILE.match(name)
        if match is None:
            continue
        _, round_no, node = match.groups()
        # 같은 라운드가 두 번이면 **재시도**다 — 첫 답을 못 읽어서 다시 물은 것.
        # 그걸 새 라운드처럼 찍으면 "라운드가 하나 더 돌았다"로 읽힌다.
        attempts[(round_no, node)] = attempts.get((round_no, node), 0) + 1
        prompt = _section(text, "물어본 것")
        reply = _section(text, "날것 응답")
        # 거부 뒤 되물었으면 **앞 답은 통째로 버려진 것**이다. 그 답의 질의를 "이미 한
        # 질의"로 들고 있으면 되물은 답이 같은 읽기를 내는 것을 반복으로 찍는다 — 로컬
        # 대역 측정에서 실제로 그렇게 찍혔다(t-6 `kafka.tail`).
        if REDO_MARK in _block(prompt, "버려진 태스크"):
            for query in last_added:
                asked.pop(query, None)
        before = set(asked)
        lines += _round(round_no, node, prompt, reply, asked,
                        attempt=attempts[(round_no, node)], verdict=_verdict(text),
                        brief=brief)
        last_added = set(asked) - before
    if len(lines) == 1:
        lines.append("  (읽을 수 있는 트레이스 파일이 없다)")
    return lines


def _verdict(text: str) -> str:
    """트레이스 파일 머리의 **우리 판정** — `읽었다` / `못 읽었다 — 사유`.

    요약이 제 눈으로 JSON을 다시 읽는 것과는 다르다. 스키마가 거부한 답도 JSON으로는
    읽히므로, 이 줄이 없으면 **거부된 답이 결론처럼 찍힌다** — 두 번째 전체 트레이스의
    r3가 `decision=conclude`로 보였지만 실제로는 거부됐고, 되물은 답이 `continue`였다.
    """
    match = _VERDICT.search(text)
    if match is None:
        return ""
    verdict = match.group(1).strip()
    return verdict if len(verdict) <= _REASON_CHARS else verdict[:_REASON_CHARS] + "…"


def _round(round_no: str, node: str, prompt: str, reply: str,
           asked: dict[str, str], *, attempt: int = 1, verdict: str = "",
           brief: bool = False) -> list[str]:
    evidence = _block(prompt, "모은 증거")
    rejected = _block(prompt, "버려진 태스크")
    seen_names = evidence                       # 이름이 증거에 있나 — 문자열로 본다
    # 증거 줄의 `source`가 곧 그 질의다. `describe`가 만든 문자열이므로
    # 우리도 같은 함수로 만들어서 **정확히** 대조한다.
    visible = {line.split(" | ")[1].strip()
               for line in evidence.splitlines()
               if line.startswith("- ") and line.count(" | ") >= 2}
    shown = _tasks(_example(prompt))
    # 아직 안 돈 태스크를 같은 id로 다시 낸 것은 **갱신**이다(우선순위를 올려 달라는 뜻).
    # 그걸 "이미 한 질의"로 찍으면 정상 동작이 반복으로 읽힌다.
    waiting = set(re.findall(r"^- (t-\d+) \[pending\]",
                             _block(prompt, "지금까지의 태스크"), re.M))
    made = _tasks(parse_object(reply).data if parse_object(reply).ok else None)

    # 같은 라운드의 두 번째 파일은 둘 중 하나다 — JSON을 못 읽어 다시 물은 것, 또는
    # 거부 뒤 **되물은** 것. 후자는 `<버려진 태스크>`의 머리말로 안다.
    labels = []
    if REDO_MARK in rejected:
        labels.append("거부 뒤 다시 물음")
        if attempt > 2:
            labels.append(f"재시도 {attempt}회째")
    elif attempt > 1:
        labels.append(f"재시도 {attempt}회째")
    label = f" ({' · '.join(labels)})" if labels else ""
    out = [f"\nr{round_no} {node}{label} · 프롬프트 {len(prompt):,}자"
           f" = {_sizes(prompt, evidence)}"
           + (f" · 결과: {verdict}" if verdict else "")]
    out.append(f"  리드가 본 것 : 태스크 {_count(_block(prompt, '지금까지의 태스크'))}"
               f" · 증거 {_count(evidence)}(잘림 {evidence.count('⚠ 표본이 잘렸다')})"
               f" · 버려진 것 {_count(rejected)}")
    if not brief:
        out.append("  이미 물은 것(증거에 보임) : "
                   + ("  ".join(sorted(_clip(q) for q in visible)) or "(없음)"))
        out.append("  예시가 보여준 것 : "
                   + ("  ".join(_shape(a, p) for _, a, p in shown) or "(없음)"))
    parsed = parse_object(reply)
    if not parsed.ok:
        out.append("  리드가 낸 것 : (응답을 JSON으로 못 읽었다 — "
                   f"{len(reply):,}자, {_peek(reply)})")
        return out
    body = parsed.data if isinstance(parsed.data, dict) else {}
    hyps = [h for h in body.get("hypotheses", []) if isinstance(h, dict)]
    if node == "frame":
        # frame의 가설엔 아직 판정도 인용도 없다 — `?(인용 0)`은 없는 결함처럼 읽힌다.
        out.append("  가설 : " + ("  ".join(str(h.get("id", "?")) for h in hyps) or "(없음)"))
    else:
        out.append("  가설 : " + ("  ".join(
        f"{h.get('id', '?')} {h.get('status', '?')}"
        f"(인용 {len(h.get('supporting_ids') or []) + len(h.get('refuting_ids') or [])})"
            for h in hyps) or "(없음)")
            + (f" · decision={body['decision']}" if body.get("decision") else ""))
    if not made:
        out.append("  리드가 낸 것 : (없음)")
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
        if task_id in waiting:
            marks.append("대기 중이던 태스크의 갱신")
        elif spoken in visible:
            marks.append("**이미 한 질의 — 증거에 보이는데도 또 냈다**")
        elif query in asked:
            marks.append(f"이미 {asked[query]}에서 한 질의(증거엔 안 보였다)")
        else:
            asked[query] = f"r{round_no}"
        out.append(f"    {task_id} {query}"
                   + (f"   ← {' · '.join(marks)}" if marks else ""))
    return out


def _sizes(prompt: str, evidence: str) -> str:
    """**13K가 어디로 가는지.** 총량만 보면 예산을 어디서 줄일지 알 수 없다."""
    parts = {"증거": len(evidence),
             "태스크": len(_block(prompt, "지금까지의 태스크")),
             "가설": len(_block(prompt, "가설")),
             "버려진": len(_block(prompt, "버려진 태스크"))}
    example = _example_text(prompt)
    parts["예시"] = len(example)
    parts["나머지"] = max(0, len(prompt) - sum(parts.values()))
    return " + ".join(f"{k} {v:,}" for k, v in parts.items() if v)


def _peek(reply: str) -> str:
    """못 읽은 응답의 **앞머리** — 빈 답인지, 산문인지, 잘린 JSON인지가 여기서 갈린다."""
    head = reply.strip().replace("\n", " ")[:60]
    return f"시작: {head!r}" if head else "빈 응답"


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


def _example_text(prompt: str) -> str:
    """프롬프트 안에 박힌 예시 JSON의 **원문**. **중괄호를 세어서** 찾는다 — 예시는
    코드펜스 없이 들어가므로 정규식으로는 끝을 못 찾는다."""
    for start in (i for i, ch in enumerate(prompt) if ch == "{"):
        depth = 0
        for end in range(start, len(prompt)):
            depth += (prompt[end] == "{") - (prompt[end] == "}")
            if depth == 0:
                chunk = prompt[start:end + 1]
                try:
                    body = json.loads(chunk)
                except ValueError:
                    break
                if isinstance(body, dict) and "tasks" in body:
                    return chunk
                break
    return ""


def _example(prompt: str):
    text = _example_text(prompt)
    return json.loads(text) if text else None


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
