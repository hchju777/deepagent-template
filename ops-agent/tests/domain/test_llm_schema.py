"""답 스키마의 닫힘 판정과 닫기 — strict로 보낼 수 있는지는 코드가 정한다."""
from src.domain.llm_schema import is_closed, strictify

CLOSED = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}


def test_닫힌_스키마만_strict로_보낼_수_있다():
    assert is_closed(CLOSED)
    assert not is_closed({"type": "object"})                                           # 자유형 객체
    assert not is_closed({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})   # 덧붙임 허용
    assert not is_closed({"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                          "required": ["a"], "additionalProperties": False})           # b가 선택
    nested = {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "object"}}},
              "required": ["tasks"], "additionalProperties": False}
    assert not is_closed(nested)                                                       # 안쪽의 자유형 `params`·`filter`가 이 모양이다


def test_strictify는_전부_required로_닫고_default를_지우고_null은_남긴다():
    loose = {"title": "reply", "type": "object",
             "properties": {"root": {"anyOf": [{"$ref": "#/$defs/Link"}, {"type": "null"}], "default": None},
                            "notes": {"type": "array", "items": {"type": "string"}, "default": []},
                            "kind": {"type": "string", "enum": ["a", "b"]}},
             "required": ["kind"],
             "$defs": {"Link": {"type": "object", "properties": {"component": {"type": "string"},
                                                                 "relation": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}},
                                "required": ["component"]}}}
    strict = strictify(loose)
    assert strict["required"] == ["root", "notes", "kind"] and strict["additionalProperties"] is False
    assert "default" not in strict["properties"]["root"] and "default" not in strict["properties"]["notes"]
    link = strict["$defs"]["Link"]
    assert link["required"] == ["component", "relation"] and link["additionalProperties"] is False
    assert {"type": "null"} in link["properties"]["relation"]["anyOf"]                # 선택은 null로 남는다
    assert is_closed(strict) and not is_closed(loose)
    assert loose["required"] == ["kind"]                                               # 원본은 안 건드린다
