"""JSON 경로 고르기 — `redis.get(key, path)`가 쓴다. 값 전체를 400줄에서 자르는 대신 **고른 부분을 통째로** 주기 위해서다."""
import json

from src.domain.jsonpath import select


def test_점과_대괄호로_내려간다():
    doc = {"record": [{"data": {"x": 1}}, {"data": {"x": 2}}], "metadata": {"status": "ok"}}
    assert select(doc, "record[1].data") == (True, {"x": 2})
    assert select(doc, "record[0].data.x") == (True, 1)
    assert select(doc, "metadata") == (True, {"status": "ok"})
    assert select([10, 20], "[1]") == (True, 20)
    assert select(doc, "") == (True, doc)                             # 빈 경로는 전부


def test_JSON_문자열이면_먼저_푼다():
    assert select(json.dumps({"a": {"b": 3}}), "a.b") == (True, 3)
    ok, why = select("그냥 글자", "a")
    assert not ok and "JSON" in why


def test_없는_자리는_있는_것을_알려_주며_실패한다():
    doc = {"record": [{"data": 1}], "metadata": {}}
    ok, why = select(doc, "record[0].nope")
    assert not ok and "nope" in why and "data" in why
    ok, why = select(doc, "record[5]")
    assert not ok and "1개" in why
    ok, why = select(doc, "metadata.x.y")
    assert not ok and "x" in why
    ok, why = select(doc, 5)                                           # 리드가 숫자를 적어도 던지지 않는다
    assert not ok and "문자열" in why
