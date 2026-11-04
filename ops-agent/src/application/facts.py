"""코드가 증거에서 **대조해 낸 사실**(R2-2b-2) — 리드의 판단이 아니라 기계의 대조다.

사내 실측 c-1: r2의 리드가 "요약 키가 없다"를 알아냈는데 r3에서 잊었다. 강한 모델은 기억하고 약한 모델은 못 한다 —
그래서 하네스가 쥔다. 발견 읽기(`redis.scan`)가 돌려준 이름(`TaskOutcome.found`)과 흐름 그래프가 선언한 이름을
대조해 "선언됐는데 없는 키"를 State(`facts`)에 남기고, `<열린 질문>`이 매 턴 보여 준다.

**말할 수 있을 때만 말한다.** 잘린 표본(`complete=False`)으로는 "없다"를 주장할 수 없고, `hb:*`로 훑은 결과는
`alarm:stats:{line}`에 대해 아무 말도 못 한다 — 패턴의 글자 접두와 템플릿의 `{` 앞부분이 겹칠 때만 그 키를 본 것이다.
"""
import re

from src.domain.actions import DISCOVERY_ACTIONS

_WILD = re.compile(r"[*?\[]")
_KIND_WORD = {"rediskey": "키", "collection": "컬렉션", "topic": "토픽"}


def key_head(template: str) -> str:
    """`alarm:stats:{line}` → `alarm:stats:` — config의 키 템플릿에서 코드에 실제로 있는 앞부분."""
    return template.split("{", 1)[0]


def covers(pattern: str, template: str) -> bool:
    """이 scan 패턴이 그 템플릿의 키들을 **봤다**고 할 수 있나."""
    head = key_head(template)
    m = _WILD.search(pattern)
    if m is None:
        return pattern.startswith(head)            # 글자 그대로의 패턴은 그 키 하나만 본 것이다
    prefix = pattern[:m.start()]
    return head.startswith(prefix) or prefix.startswith(head)


def present(template: str, found) -> bool:
    if "{" in template:
        head = key_head(template)
        return any(name.startswith(head) for name in found)
    return template in found


def missing_names(declared, found, pattern: str) -> list[str]:
    return [t for t in declared if covers(pattern, t) and not present(t, found)]


def facts_for(task, outcome, declared: dict) -> list[str]:
    """태스크 하나의 결과에서 코드가 남길 사실. 발견 읽기가 아니거나, 실패했거나, 잘렸으면 빈 목록이다."""
    kind = DISCOVERY_ACTIONS.get(task.action or "")
    if (kind is None or outcome.status != "ok" or not outcome.evidence
            or not outcome.evidence[0].complete or not declared.get(kind)):
        return []
    pattern = str(task.params.get("pattern") or "*")
    missing = missing_names(declared[kind], outcome.found, pattern)
    if not missing:
        return []
    word = _KIND_WORD.get(kind, kind)
    scope = f"scan {pattern}, " if task.action == "redis.scan" else ""
    return [f"선언됐는데 없는 {word} {len(missing)}개 ({scope}{task.id}): {', '.join(missing)}"]
