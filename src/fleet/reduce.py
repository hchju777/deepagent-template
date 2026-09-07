"""표본에서 값을 뽑고(extract) 하나로 접는다(reduce) — 순수 함수(계획 16/P7).

감축은 **Literal 6종뿐이다.** 범용 집계 DSL은 사용자 입력 표현식이라 감사할 수 없고,
규율 6이 금지한다(방향 문서 기각 목록). 새 종류가 필요하면 스펙을 먼저 고친다.
"""
from typing import Any

from src.patrol.rules import get_path

REDUCERS = ("sum", "avg", "max", "min", "count", "count_nonzero")


def _as_number(value: Any) -> float | None:
    # bool은 int의 하위 타입이지만 수가 아니다 — True를 1로 세면 집계가 조용히 거짓이 된다.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def extract(payload: Any, dotted: str) -> tuple[list[float], int]:
    """점 경로로 값을 뽑는다. `(값들, 건너뛴 수)`.

    건너뛴 수를 **돌려주는 것**이 요점이다: 숫자가 아닌 항목을 조용히 버리면 "30개 중
    30개를 봤다"고 말하면서 실제로는 12개만 센 집계가 된다. 호출부가 이 수를 보고
    불완전으로 판정한다.

    리스트가 경로 중간에 있으면 각 항목에서 나머지 경로를 뽑는다(사이트 응답이
    `{"rows": [...]}` 모양인 것이 보통이다).
    """
    if not isinstance(dotted, str) or not dotted:
        return [], 1
    head, _, tail = dotted.partition(".")
    container = payload.get(head) if isinstance(payload, dict) else None
    if isinstance(container, list) and tail:
        values, skipped = [], 0
        for item in container:
            got, miss = extract(item, tail)
            values.extend(got)
            skipped += miss
        return values, skipped
    number = _as_number(get_path(payload, dotted))
    return ([number], 0) if number is not None else ([], 1)


def reduce_values(values: list[float], how: str) -> float | None:
    """표본을 하나로 접는다. **빈 표본은 0이 아니라 None이다.**

    count도 그렇다 — "아무 데서도 못 읽었다"를 0건으로 적으면, 읽는 사람은 "알람이
    없었다"로 읽는다. 그 구별이 이 계획의 존재 이유다.
    """
    if not values or how not in REDUCERS:
        return None
    if how == "sum":
        return float(sum(values))
    if how == "avg":
        return float(sum(values)) / len(values)
    if how == "max":
        return float(max(values))
    if how == "min":
        return float(min(values))
    if how == "count":
        return float(len(values))
    return float(sum(1 for v in values if v != 0))
