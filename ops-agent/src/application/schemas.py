"""LLM이 돌려준 텍스트에서 **구조를 꺼낸다.** 실패는 값으로 돌린다.

## 왜 파싱이 따로 한 곳인가

리드(frame·integrate)와 나중의 서브에이전트(11b)가 같은 관용 범위를 써야 한다.
각자 벗기면 한쪽은 코드펜스를 받아 주고 다른 쪽은 안 받아서, **같은 모델이 같은
응답을 줬는데 노드마다 다르게 동작한다.**

## 무raise

여기서 던지면 LangGraph superstep이 죽는다. 파싱 실패는 `Parsed.error`로 돌리고,
노드가 그걸 보고 라운드를 정직하게 끝낸다.
"""
import json
from dataclasses import dataclass
from typing import Any

from src.domain.base import StrictModel


@dataclass(frozen=True)
class Parsed:
    """파싱 결과. 성공도 실패도 이 한 타입이다."""

    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


def strip_fence(text: str) -> str:
    """```json … ``` 울타리를 벗긴다.

    모델에게 "JSON만"이라고 해도 울타리를 두르는 일이 흔하다. 거부하는 것보다
    벗기는 쪽이 맞다 — 내용은 우리가 요청한 그대로이고, 거부하면 멀쩡한 답이
    "형식 오류"로 버려진다.
    """
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    parts = cleaned.split("```")
    if len(parts) < 2:
        return cleaned
    body = parts[1]
    return body[4:].strip() if body.lstrip().startswith("json") else body.strip()


def parse_object(text: str) -> Parsed:
    """텍스트에서 JSON 객체 하나를 꺼낸다.

    울타리를 벗겨도 앞뒤에 말이 붙어 있으면 **첫 `{`부터 마지막 `}`까지**를 다시
    시도한다. 모델이 "네, 아래와 같습니다:"를 붙이는 것은 형식 위반이지 답이
    틀린 것이 아니다.
    """
    cleaned = strip_fence(text or "")
    if not cleaned:
        return Parsed(False, error="응답이 비어 있다")

    for candidate in (cleaned, _between_braces(cleaned)):
        if candidate is None:
            continue
        try:
            loaded = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            return Parsed(True, data=loaded)
        return Parsed(False, error=f"JSON 객체가 아니다 — {type(loaded).__name__}")
    return Parsed(False, error=f"JSON으로 읽을 수 없다 — {cleaned[:120]!r}")


def _between_braces(text: str) -> str | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start:end + 1]


def validate(data: dict, model: type[StrictModel]) -> Parsed:
    """꺼낸 객체를 모델로 검증한다. **`extra="forbid"`가 여기서 값을 한다** —
    모델이 지어낸 필드가 조용히 State에 들어가지 않는다."""
    try:
        return Parsed(True, data=model.model_validate(data).model_dump())
    except Exception as exc:                                        # noqa: BLE001
        detail = getattr(exc, "errors", None)
        if callable(detail):
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(최상위)'}: {e['msg']}"
                for e in detail())
            return Parsed(False, error=problems or str(exc))
        return Parsed(False, error=f"{type(exc).__name__}: {exc}")
