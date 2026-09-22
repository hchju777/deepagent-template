"""트레이스 요약 — **사람이 붙여넣을 수 있는 크기로, 가려야 할 것은 가리고.**

이 도구가 있기 전까지 사내에서 고치는 쪽으로 건너온 것은 진단 요약뿐이었다.
"리드가 무엇을 보고 무엇을 뱉었는지"는 한 번도 안 건너왔고, 그래서 매 라운드
원인을 코드에서 역추적했다 — 그게 왕복의 진짜 원인이었다.
"""
import json

from src.application.trace_digest import digest
from src.domain.actions import describe


def _file(prompt: str, reply: str, *, name: str = "01-r2-integrate.md",
          verdict: str = "읽었다") -> list[tuple[str, str]]:
    """`_make_tracer`가 쓰는 형식 그대로. **형식이 갈리면 이 도구가 조용히 빈다.**"""
    return [(name,
             f"# c-1 · integrate · 라운드 2\n\n결과: {verdict}\n\n"
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


def test_프롬프트가_어디로_가는지_블록별로_센다():
    """첫 전체 트레이스에서 r4 응답이 13.6K 프롬프트 뒤에 깨졌다. **총량만으로는
    어느 예산을 줄일지 알 수 없다** — 증거인지, 태스크 목록인지, 예시인지."""
    prompt = _prompt(evidence="- t-1.e1 | mongo.find c | 1건\n" * 20,
                     tasks="- t-1 done\n" * 5)
    text = "\n".join(digest(_file(prompt, json.dumps({"tasks": []}))))
    head = next(line for line in text.splitlines() if line.startswith("r2 "))
    assert "프롬프트" in head and "= 증거" in head and "태스크" in head
    assert "나머지" in head


def test_가설과_결정을_찍는다():
    """가설이 전부 refuted인데 conclude인지, 인용이 몇 건인지 — 12a로 넘긴 질문의
    데이터가 여기서 나온다."""
    reply = json.dumps({"decision": "conclude", "hypotheses": [
        {"id": "h-1", "status": "refuted", "supporting_ids": [], "refuting_ids": ["t-1.e1"]},
        {"id": "h-2", "status": "refuted"}], "tasks": []})
    text = "\n".join(digest(_file(_prompt(), reply)))
    assert "h-1 refuted(인용 1)" in text and "h-2 refuted(인용 0)" in text
    assert "decision=conclude" in text


def test_같은_라운드가_두_번이면_재시도라고_적는다():
    """첫 답을 못 읽어 다시 물은 것을 새 라운드처럼 찍으면 "라운드가 하나 더
    돌았다"로 읽힌다."""
    one = _file(_prompt(), json.dumps({"tasks": []}))[0]
    two = ("02-r2-integrate.md", one[1])
    text = "\n".join(digest([one, two]))
    assert "재시도 2회째" in text and text.count("r2 integrate") == 2


def test_못_읽은_응답의_앞머리를_보여준다():
    """빈 답인지, 산문인지, 잘린 JSON인지가 앞머리에서 갈린다. 내용 전체는 안
    찍는다 — 증거를 되뇌었을 수 있다."""
    text = "\n".join(digest(_file(_prompt(), "{\"decision\": \"continue\", \"hyp" + "x" * 500)))
    assert "시작:" in text and "x" * 200 not in text
    assert "빈 응답" in "\n".join(digest(_file(_prompt(), "   ")))


def test_우리_판정을_시도마다_찍는다():
    """스키마가 거부한 답도 JSON으로는 읽힌다. 요약이 제 눈으로만 보면 **거부된 답이
    결론처럼 찍힌다** — 두 번째 전체 트레이스의 r3가 `decision=conclude`로 보였지만
    실제로는 거부됐고, 되물은 답이 `continue`였다. 왜 거부됐는지는 이 줄에만 있다."""
    reply = json.dumps({"decision": "conclude", "hypotheses": [], "tasks": []})
    refused = _file(_prompt(), reply,
                    verdict="**못 읽었다** — status는 supported|refuted|open 중 하나다")
    text = "\n".join(digest(refused))
    assert "못 읽었다" in text and "supported|refuted|open" in text
    assert "결과: 읽었다" in "\n".join(digest(_file(_prompt(), reply)))


def test_frame_가설은_status를_안_찍는다():
    reply = json.dumps({"hypotheses": [{"id": "h-1", "statement": "s"},
                                       {"id": "h-2", "statement": "s"}], "tasks": []})
    text = "\n".join(digest(_file(_prompt(), reply, name="01-r0-frame.md")))
    line = next(l for l in text.splitlines() if l.strip().startswith("가설"))
    assert "h-1" in line and "h-2" in line and "?" not in line


def test_거부_뒤_되물은_것을_재시도와_가른다():
    """같은 라운드의 두 번째 파일은 둘 중 하나다. JSON을 못 읽어 다시 물은 것과 거부
    뒤 되물은 것을 같은 이름으로 찍으면, 되물음이 "모델이 JSON을 못 냈다"로 읽힌다."""
    from src.application.nodes import REDO_MARK

    first = _file(_prompt(), json.dumps({"tasks": []}))[0]
    redo = _prompt(rejected=f"- {REDO_MARK}t-6: 이미 한 읽기를 또 냈다 — 받지 않는다")
    second = _file(redo, json.dumps({"tasks": []}), name="02-r2-integrate.md")[0]
    text = "\n".join(digest([first, second]))
    assert "거부 뒤 다시 물음" in text and "재시도" not in text


def test_되물음_앞의_버려진_답은_이미_한_질의가_아니다():
    """되물었으면 앞 답은 통째로 버려진 것이다. 그 답의 질의를 들고 있으면 되물은 답이
    같은 읽기를 내는 것을 반복으로 찍는다 — 로컬 대역 측정에서 그렇게 찍혔다."""
    from src.application.nodes import REDO_MARK

    read = _task("t-6", "kafka.tail", {"topic": "T", "limit": 5})
    first = _file(_prompt(), json.dumps({"tasks": [read]}))[0]
    redo = _prompt(rejected=f"- {REDO_MARK}t-4: 이미 있는 태스크 id를 다시 냈다 — 받지 않는다")
    second = _file(redo, json.dumps({"tasks": [read]}), name="02-r2-integrate.md")[0]
    text = "\n".join(digest([first, second]))
    assert "이미 r2에서 한 질의" not in text


def test_대기_중이던_태스크를_같은_id로_다시_내면_갱신이라고_적는다():
    """세 번째 로컬 실행에서 리드가 굶던 t-4를 같은 id로 다시 냈고 엔진은 갱신으로
    받았는데, 요약은 "이미 r0에서 한 질의"로 찍었다. 정상 동작이 반복으로 읽힌다."""
    read = _task("t-4", "kafka.list_topics", {})
    first = _file(_prompt(), json.dumps({"tasks": [read]}), name="01-r0-frame.md")[0]
    later = _file(_prompt(tasks="- t-4 [pending] 토픽 목록"), json.dumps({"tasks": [read]}),
                  name="02-r1-integrate.md")[0]
    text = "\n".join(digest([first, later]))
    assert "대기 중이던 태스크의 갱신" in text and "이미 r0에서 한 질의" not in text

