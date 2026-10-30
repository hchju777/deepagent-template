"""OpenAI 규약의 `response_format` — 어댑터 둘이 같은 함수로 만든다(갈라지면 한쪽만 문법 강제를 받는다)."""
import re

from src.domain.llm_schema import is_closed

_NAME = re.compile(r"[^A-Za-z0-9_-]")


# 함수 호출 모드(R3-5v ⑦)에서 강제할 함수의 설명 — 모델에게는 "답을 이 함수의 인자로"가 전부다.
TOOL_NOTE = "답을 이 함수의 인자로 낸다"


def _name(schema: dict) -> str:
    return (_NAME.sub("_", str(schema.get("title") or "reply")) or "reply")[:64]


def response_format(mode: str, schema: dict | None) -> dict | None:
    """`none`·`tools`이거나 스키마가 없으면(자유 질문) 안 보낸다. `json_object`는 문법만, `json_schema`는 모양까지 — strict는 닫힐 때만."""
    if mode in ("none", "tools") or schema is None:
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {"name": _name(schema), "schema": schema, "strict": is_closed(schema)}}


def request_fields(mode: str, schema: dict | None) -> dict:
    """요청 본문에 얹을 것 — 어댑터 둘이 같은 함수로. `tools`면 답 스키마를 함수 하나의 인자로 보내고 `tool_choice`로 그 함수를 강제한다(네이티브 함수 호출,
    R3-5v ⑦); 아니면 `response_format`(위). 스키마가 없으면(자유 질문) 아무것도 안 얹는다."""
    if mode == "tools":
        if schema is None:
            return {}
        name = _name(schema)
        return {"tools": [{"type": "function", "function": {"name": name, "description": TOOL_NOTE, "parameters": schema}}],
                "tool_choice": {"type": "function", "function": {"name": name}}}
    fmt = response_format(mode, schema)
    return {"response_format": fmt} if fmt is not None else {}
