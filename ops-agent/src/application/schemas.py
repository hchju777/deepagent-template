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
    # 모델이 덧붙여서 **걷어낸** 키들. 성공이지만 조용하면 안 되는 것이다.
    dropped: tuple[str, ...] = ()


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
    """꺼낸 객체를 모델로 검증한다.

    ## 모르는 키는 **걷어내되 기록한다** — 규율 5의 좁은 예외

    `StrictModel`(`extra="forbid"`)은 config와 도메인 객체에 그대로 둔다. 거기서
    모르는 키는 **사람이 잘못 적은 것**이고, 시끄럽게 죽는 쪽이 맞다.

    LLM 응답은 다르다. 약한 모델은 `"confidence": 0.9` 같은 곁다리 필드를 자주
    붙이는데, 그걸로 응답 전체를 버리면 **멀쩡한 조사 계획이 라운드째 날아간다.**
    사내 모델로 재 보니 곁다리를 붙이는 것은 형식 실수이지 계획이 틀린 것이 아니었다.

    그래서 `extra_forbidden`**만** 골라 걷어내고 다시 검증한다. 값이 틀린 것
    (`"decision": "maybe"`)은 여전히 실패다 — 그건 형식 실수가 아니라 **모델이 우리
    어휘를 안 따른 것**이고, 걷어낼 수도 없다. 그 실패는 수리 재시도로 간다.

    걷어낸 키는 `Parsed.dropped`로 돌려서 `llm_errors`에 남는다. 조용히 고치면
    프롬프트가 안 먹히고 있다는 것을 아무도 모른다.
    """
    try:
        return Parsed(True, data=model.model_validate(data).model_dump())
    except Exception as first:                                      # noqa: BLE001
        extras = _extra_paths(first)
        if not extras:
            return Parsed(False, error=_explain(first))

    pruned = _without(data, extras)
    try:
        return Parsed(True, data=model.model_validate(pruned).model_dump(),
                      dropped=tuple(".".join(str(p) for p in path) for path in extras))
    except Exception as second:                                     # noqa: BLE001
        # 걷어내고도 안 되면 곁다리가 문제가 아니었다. 원래 사유를 알린다.
        return Parsed(False, error=_explain(second))


def _extra_paths(exc: Exception) -> list[tuple]:
    """`extra_forbidden` 오류의 경로들.

    다른 오류가 섞여 있어도 **여기서 걸러 내지 않는다.** 걷어낸 뒤 다시 검증하면
    그 오류가 그대로 다시 나서 어차피 실패하고, 그때의 오류 메시지가 **더 정확하다** —
    이미 걷어낸 키를 다시 탓하지 않으므로 수리 재시도가 진짜 문제만 모델에게 말한다.

    (처음엔 "다른 오류가 하나라도 있으면 빈 목록"으로 조기 반환했는데, RED 확인에서
    그걸 지워도 테스트가 전부 통과했다. 방어가 아니라 지름길이었다.)
    """
    detail = getattr(exc, "errors", None)
    if not callable(detail):
        return []
    return [tuple(p["loc"]) for p in detail() if p.get("type") == "extra_forbidden"]


def _without(data: dict, paths: list[tuple]):
    """경로들을 지운 사본. 원본은 안 건드린다 — 실패하면 원본으로 오류를 설명해야 한다."""
    import copy

    clone = copy.deepcopy(data)
    for path in paths:
        node = clone
        try:
            for step in path[:-1]:
                node = node[step]
            del node[path[-1]]
        except (KeyError, IndexError, TypeError):
            continue          # 경로가 안 맞으면 그냥 둔다. 아래 재검증이 잡는다
    return clone


def _explain(exc: Exception) -> str:
    detail = getattr(exc, "errors", None)
    if callable(detail):
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(최상위)'}: {e['msg']}"
            for e in detail())
        return problems or str(exc)
    return f"{type(exc).__name__}: {exc}"
