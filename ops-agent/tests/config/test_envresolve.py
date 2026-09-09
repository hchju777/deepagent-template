"""`${VAR}` 치환 — 못 찾은 것을 던지지 않고 모아서 돌려준다."""
from src.config.envresolve import resolve_env


def test_값을_찾으면_치환한다():
    resolved, missing = resolve_env({"password": "${PW}"}, {"PW": "hunter2"})
    assert resolved == {"password": "hunter2"}
    assert missing == []


def test_중첩된_구조를_전부_훑는다():
    raw = {"infra": {"kafka": {"bootstrap_server": ["${B1}", "${B2}"]}}}
    resolved, missing = resolve_env(raw, {"B1": "h1:9092", "B2": "h2:9092"})
    assert resolved["infra"]["kafka"]["bootstrap_server"] == ["h1:9092", "h2:9092"]
    assert missing == []


def test_문자열_중간에_박힌_참조도_치환한다():
    resolved, _ = resolve_env("mongodb://u:${PW}@host:27017", {"PW": "s3cret"})
    assert resolved == "mongodb://u:s3cret@host:27017"


def test_못_찾은_것들을_전부_모아서_돌려준다():
    # 하나씩 던지면 사람은 고치고 다시 돌리기를 3번 반복한다.
    _, missing = resolve_env({"a": "${X}", "b": ["${Y}", "${Z}"]}, {})
    assert missing == ["X", "Y", "Z"]


def test_빈_값은_없는_것으로_친다():
    # `.env`에 `KEY=` 라고만 적힌 상태 — "설정했다고 믿는데 안 된" 제일 흔한 모양.
    _, missing = resolve_env({"a": "${PW}"}, {"PW": ""})
    assert missing == ["PW"]


def test_못_찾으면_원문을_남긴다():
    # 빈 문자열로 바꿔치기하면 무엇이 안 채워졌는지 안 보인다.
    resolved, _ = resolve_env({"a": "${PW}"}, {})
    assert resolved == {"a": "${PW}"}


def test_참조가_아닌_문자열은_건드리지_않는다():
    resolved, missing = resolve_env({"url": "redis://host:6379", "db": 0, "on": True}, {})
    assert resolved == {"url": "redis://host:6379", "db": 0, "on": True}
    assert missing == []


def test_이름이_이상한_참조도_누락으로_잡는다():
    # ${MY-KEY} 같은 것을 "참조가 아니다"로 흘려보내면 그 문자열이 그대로
    # 비밀번호가 되어 소켓에 나간다. 증상은 "인증 실패"뿐이라 원인이 안 보인다.
    _, missing = resolve_env({"password": "${MY-KEY}"}, {"MY_KEY": "hunter2"})
    assert missing == ["MY-KEY"]
