"""코드 본문에서 자원 참조를 찾는 판정(`trace._collect_reads`) — 인덱스가 함수마다 자원을 붙일 때 쓴다.

11b 추적기의 끝점 테스트로 덮여 있던 규칙들을, 추적기를 지우며(6d-4) 판정 함수에 직접 건다. 규칙은 전부 사내
오탐에서 나왔다 — 11c의 grep 판정과 같은 기준이다."""
from src.knowledge import trace as tr
from src.knowledge.flow import Name


def _reads(segment: str, names, aliases=None, cap="확실"):
    out = []
    tr._collect_reads(segment, 10, "f.py", cap, list(names), dict(aliases or {}),
                      lambda kind, name, grade, path, line, via: out.append((name, grade, line, via)))
    return out


HIST = Name("collection", "mx_hist_v2", "mongodb_collection.history")
ALARM = Name("collection", "alarm", "mongodb_collection.alarm")
LINE_KEY = Name("rediskey", "line:{id}", "redis_key.line_status")
FACES = [Name("rediskey", "SITE:g:x", "redis_key.gauge_face_x", code_value="g:x"),
         Name("rediskey", "SITE:g:y", "redis_key.gauge_face_y", code_value="g:y")]


def test_한_단어_config_키는_조상_키가_같은_줄에_있어야_읽기다():
    """DAO 부모의 `{"history": 0}` 같은 필드명이 config 키 토큰 `history`와 같아도 읽기가 아니다 — 사내 읽기 611개 중
    510개가 이 가짜였다. `cfg["mongodb_collection"]["history"]`처럼 조상 키가 같은 줄에 있으면 받는다."""
    assert _reads('opts = {"history": 0}\n', [HIST]) == []
    assert _reads('coll = cfg["mongodb_collection"]["history"]\n', [HIST]) == [("mx_hist_v2", "추정", 10, "key")]


def test_한_단어_리터럴은_같은_줄에_읽기쓰기_동사가_있어야_읽기다():
    """`counts = {"alarm": 0, …}`은 배지 상태값이고 `mongo["alarm"].count_documents(...)`는 읽기다 — 사내 /summary
    끝점이 "alarm 컬렉션을 읽는다"고 나왔던 오탐."""
    assert _reads('counts = {"alarm": 0, "caution": 0}\n', [ALARM]) == []
    assert _reads('n = mongo["alarm"].count_documents({})\n', [ALARM]) == [("alarm", "확실", 10, "literal")]


def test_템플릿_리터럴은_따옴표_안에_있어야_읽기다():
    """`line:{id}`의 리터럴 `line:`은 타입 주석 `line: str`과도 겹친다 — 같은 줄에서 따옴표가 먼저 열려 있어야 한다."""
    assert _reads("def f(line: str):\n    pass\n", [LINE_KEY]) == []
    assert _reads('def f(id):\n    return r.get("line:" + id)\n', [LINE_KEY]) == [("line:{id}", "확실", 11, "literal")]


def test_코드가_키_토큰을_템플릿으로_조립하면_그_머리로_시작하는_키_전부가_읽기다():
    """사내 모양: `cfg.get("redis_key", f"<머리>_{name}")` — 토큰이 통째로 없어 하나도 안 잡혔다. 조상 키가 같은 줄에
    있고 `f"<머리>{…}"`가 있으면 그 머리의 키 전부를 추정으로 낸다. 조상 키가 없는 같은 템플릿은 읽기가 아니다."""
    assert _reads('v = cfg.get("redis_key", f"gauge_face_{f}")\n', FACES) == [
        ("SITE:g:x", "추정", 10, "key"), ("SITE:g:y", "추정", 10, "key")]
    assert _reads('tag = f"gauge_face_{f}"\n', FACES) == []


def test_별칭_경유와_추정_길은_출처와_등급으로_남는다():
    """`Keys.LINE_STATUS`처럼 Enum 뒤의 키는 `alias`로, 추정 호출 아래의 리터럴은 추정으로 — 리드가 등급을 본다."""
    assert _reads("key = Keys.LINE_STATUS\n", [], aliases={"LINE_STATUS": LINE_KEY}) == [("line:{id}", "추정", 10, "alias")]
    assert _reads('n = mongo["alarm"].count_documents({})\n', [ALARM], cap="추정") == [("alarm", "추정", 10, "literal")]
