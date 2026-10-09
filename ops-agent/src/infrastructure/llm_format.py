"""OpenAI 규약의 `response_format` — 어댑터 둘이 같은 함수로 만든다(갈라지면 한쪽만 문법 강제를 받는다)."""
import re

from src.domain.llm_schema import is_closed

_NAME = re.compile(r"[^A-Za-z0-9_-]")


def response_format(mode: str, schema: dict | None) -> dict | None:
    """`none`이거나 스키마가 없으면(자유 질문) 안 보낸다. `json_object`는 문법만, `json_schema`는 모양까지 — strict는 닫힐 때만."""
    if mode == "none" or schema is None:
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    name = (_NAME.sub("_", str(schema.get("title") or "reply")) or "reply")[:64]
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": is_closed(schema)}}
