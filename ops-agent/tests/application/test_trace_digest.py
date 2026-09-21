"""트레이스 요약 — **사람이 붙여넣을 수 있는 크기로, 가려야 할 것은 가리고.**

이 도구가 있기 전까지 사내에서 고치는 쪽으로 건너온 것은 진단 요약뿐이었다.
"리드가 무엇을 보고 무엇을 뱉었는지"는 한 번도 안 건너왔고, 그래서 매 라운드
원인을 코드에서 역추적했다 — 그게 왕복의 진짜 원인이었다.
"""
import json

from src.application.trace_digest import digest
from src.domain.actions import describe


def _file(prompt: str, reply: str) -> list[tuple[str, str]]:
    """`_make_tracer`가 쓰는 형식 그대로. **형식이 갈리면 이 도구가 조용히 빈다.**"""
    return [("01-r2-integrate.md",
             f"# c-1 · integrate · 라운드 2\n\n결과: 읽었다\n\n"
             f"## 물어본 것 ({len(prompt):,}자)\n\n````\n{prompt}\n````\n\n"
             f"## 날것 응답\n\n````\n{reply}\n````\n")]


def _prompt(*, evidence: str = "", example=None, tasks: str = "", rejected: str = "") -> str:
    body = json.dumps(example or {"tasks": []}, ensure_ascii=False)
    return (f"<지금까지의 태스크>\n{tasks}\n</지금까지의 태스크>\n\n"
            f"<모은 증거>\n{evidence}\n</모은 증거>\n\n"
            f"<버려진 태스크>\n{rejected}\n</버려진 태스크>\n\n{body}\n")


def _task(task_id, action, params):
    return {"id": task_id, "goal": "g", "role": "data_prober", "action": action,
            "params": params, "priority": 1, "input_evidence_ids": []}


def test_예시와_리드가_낸_것을_나란히_놓는다():
    """이 모델은 예시를 베낀다. **베낀 것인지 스스로 고른 것인지**가 하네스 결함과
    모델 한계를 가르는 유일한 신호라, 둘을 붙여 놔야 읽힌다."""
    prompt = _prompt(example={"tasks": [_task("t-9", "redis.get", {"key": "지시문"})]})
    reply = json.dumps({"tasks": [_task("t-7", "redis.get", {"key": "x"})]})
    text = "\n".join(digest(_file(prompt, reply)))
    assert "예시가 보여준 것" in text and "redis.get" in text
    assert "예시와 같은 action" in text


def test_증거에_없는_이름을_표시한다():
    """사내 측정에서 `key='pipeline:alarm:stats'`를 지어냈다. 예시가 `redis.get`을
    보여 줬는데 **증거에 키 이름이 하나도 없었다** — 없는 걸 채우라고 하면 지어낸다."""
    prompt = _prompt(evidence="- t-1.e1 | mongo.find collection='alarm' | 3건")
    reply = json.dumps({"tasks": [_task("t-7", "redis.get", {"key": "pipeline:alarm:stats"})]})
    text = "\n".join(digest(_file(prompt, reply)))
    assert "증거에 없는 이름" in text and "pipeline:alarm:stats" in text


def test_증거에_보이는_질의를_또_내면_구별한다():
    """**여기가 하네스와 모델을 가르는 지점이다.**

    증거 줄의 `source`가 곧 그 질의라 리드는 그걸 볼 수 있다. 보이는데도 또 냈으면
    모델 쪽이고, 안 보였으면 우리가 안 보여 준 것이다.
    """
    spoken = describe("code.grep", {"patterns": ["GUMI_ALARM_EVENT_MAIN"]})
    prompt = _prompt(evidence=f"- t-4.e1 | {spoken} | 3건")
    reply = json.dumps({"tasks": [
        _task("t-8", "code.grep", {"patterns": ["GUMI_ALARM_EVENT_MAIN"]})]})
    text = "\n".join(digest(_file(prompt, reply)))
    assert "증거에 보이는데도 또 냈다" in text


def test_증거에_안_보였으면_그렇게_적는다():
    prompt = _prompt(evidence="- t-1.e1 | mongo.count collection='alarm' | 1건")
    reply = json.dumps({"tasks": [_task("t-8", "code.grep", {"patterns": ["X"]})]})
    text = "\n".join(digest(_file(prompt, reply)))
    assert "증거에 보이는데도" not in text


def test_증거_내용은_안_찍는다():
    """**대상 config에는 비밀이 있을 수 있다.** 그건 프롬프트에 실려 트레이스에
    남고, 이 요약은 사람이 대화에 붙여넣는다. 건수만 센다."""
    prompt = _prompt(evidence="- t-1.e1 | mongo.find c | 1건\n    password: hunter2")
    text = "\n".join(digest(_file(prompt, json.dumps({"tasks": []}))))
    assert "hunter2" not in text
    assert "증거 1" in text


def test_비밀처럼_생긴_인자는_가린다():
    reply = json.dumps({"tasks": [_task("t-7", "rest.query",
                                        {"entry": "e", "api_token": "abcd1234"})]})
    text = "\n".join(digest(_file(_prompt(), reply)))
    assert "abcd1234" not in text and "***" in text


def test_키_이름은_안_가린다():
    """`key`는 Redis 키 이름이지 비밀이 아니다. 가리면 **제일 중요한 신호가 사라진다.**"""
    reply = json.dumps({"tasks": [_task("t-7", "redis.get", {"key": "oee:L3"})]})
    assert "oee:L3" in "\n".join(digest(_file(_prompt(), reply)))


def test_못_읽는_응답에도_안_죽는다():
    """모델이 JSON을 안 낸 라운드가 **제일 알고 싶은 라운드**다. 거기서 도구가
    죽으면 나머지 라운드까지 못 본다."""
    text = "\n".join(digest(_file(_prompt(), "무슨 말인지 모를 응답")))
    assert "JSON으로 못 읽었다" in text


def test_트레이스가_없으면_그렇게_말한다():
    assert "트레이스 파일이 없다" in "\n".join(digest([]))
