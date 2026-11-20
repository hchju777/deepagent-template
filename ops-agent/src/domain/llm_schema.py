"""리드의 답 스키마를 **서버에 보내기 위한** 변환 — strict(닫힌) 모양인지 판정하고, 닫을 수 있는 것은 닫는다.

사내 10-08: 빠른 모델이 태스크 `goal`의 여는 따옴표를 자주 빠뜨려 JSON 파싱이 깨졌다. 게이트웨이는 OpenAI 규약의
`response_format`(`json_object`·`json_schema`)을 받으므로 서버가 문법을 강제하게 한다. `strict: true`는 모든 객체가 닫혀
있어야(`additionalProperties: false`, 전부 `required`, 자유형 객체 없음) 받아들여지는데, 액션 턴의 `params`와 `filter`는
자유형이라 **닫을 수 없다** — 그 스키마는 strict 없이 보낸다(문법 강제는 그래도 된다). 판정 턴의 답은 닫힌다.
"""
import copy
from typing import Any


def strictify(schema: dict) -> dict:
    """닫을 수 있는 스키마를 닫는다 — 객체마다 `additionalProperties: false`, 속성 전부 `required`, `default` 제거(strict가 거부한다).
    pydantic이 `Optional`을 `anyOf [..., null]`로 적어 두므로 선택 필드는 null로 남는다."""
    out = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            node.pop("default", None)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(out)
    return out


def is_closed(schema: Any) -> bool:
    """모든 객체가 닫혀 있나 — strict로 보내도 되는가. 속성 없는 `{"type": "object"}`(자유형)가 하나라도 있으면 아니다."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" or "properties" in schema:
            props = schema.get("properties")
            if not props or schema.get("additionalProperties") is not False:
                return False
            if set(schema.get("required", ())) != set(props):
                return False
        return all(is_closed(value) for value in schema.values())
    if isinstance(schema, list):
        return all(is_closed(value) for value in schema)
    return True
