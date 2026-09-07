"""표본에서 값을 뽑고(extract) 하나로 접는다(reduce) — 순수 함수(계획 16/P7).

감축은 **Literal 6종뿐이다.** 범용 집계 DSL은 사용자 입력 표현식이라 감사할 수 없고,
규율 6이 금지한다(방향 문서 기각 목록). 새 종류가 필요하면 스펙을 먼저 고친다.
"""
from typing import Any


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

    경로 **어디에서든** 리스트를 만나면 각 항목으로 갈라져 나머지 경로를 뽑는다 —
    응답이 `{"body": {"rows": [...]}}`처럼 중첩되는 것이 보통이라, 첫 세그먼트만
    갈라지면 실전 경로를 못 읽는다(테스트가 실제로 잡았다).
    """
    if not isinstance(dotted, str) or not dotted:
        return [], 1
    return _walk(payload, dotted.split("."))


def _walk(node: Any, segments: list[str]) -> tuple[list[float], int]:
    # 인덱스 세그먼트를 **팬아웃보다 먼저** 본다 — 순서를 반대로 두면 리스트가 먼저
    # 소진돼 인덱스 분기가 죽은 코드가 되고, 오타난 인덱스 경로가 skipped를 부풀려
    # 불완전 사유를 조작한다(검증 리뷰 M-10).
    # **유효한** 인덱스일 때만 인덱스로 읽는다. 범위를 벗어나면 팬아웃으로 폴백한다 —
    # 리스트 원소인 dict의 키가 숫자 문자열인 경우(연도·에러코드·라인번호)가 실전
    # 페이로드에 흔하고, 인덱스로만 해석하면 그 경로가 조용히 사라진다(검증 리뷰 M-12).
    if isinstance(node, list) and segments and segments[0].isdigit() \
            and int(segments[0]) < len(node):
        return _walk(node[int(segments[0])], segments[1:])
    if isinstance(node, list):
        values, skipped = [], 0
        for item in node:
            got, miss = _walk(item, segments)
            values.extend(got)
            skipped += miss
        return values, skipped
    if not segments:
        number = _as_number(node)
        return ([number], 0) if number is not None else ([], 1)
    head, rest = segments[0], segments[1:]
    if isinstance(node, dict) and head in node:
        return _walk(node[head], rest)
    return [], 1


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
