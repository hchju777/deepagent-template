"""원천 재집계(11b 3b) — **로직을 실행하지 않는다.** 리드가 코드를 읽고 세운 기대("배지의 alarm은 이 창의
alarm 문서 수여야 한다") 중 세거나 더하면 확인되는 것을, 우리 읽기 경로로 원천에서 다시 만들어 앞선 증거의
값과 대조한다.

왜 있는가: ① 화면의 숫자는 대상의 코드를 거친 것이고 이건 원천에서 독립적으로 만든 숫자다 — 둘이 다르면
"원천과 화면 사이에서 바뀌었다"는, 코드를 아무리 읽어도 안 나오는 사실이 생긴다. ② `mongo.find`는 예산에서
잘린 표본이라 7,000건을 표본으로 셀 수 없다 — count/sum은 전체에 대한 정확한 수다. ③ 리드의 "원천에 7건
있다"는 산문 속 주장이지만 이건 filter와 대조 대상이 적힌 기계의 증거다(규율 3의 숫자 판).

한계: 질의 모양(filter·집계)의 기대에만 듣는다. merge·후처리는 경계값 대조와 읽기의 몫이고, 그 함수의 어느
줄이 틀렸는지는 실행이 필요해 v1 밖이다. 기대값이 없거나 숫자가 아니면 불일치가 아니라 **error**다 — 잘못
가리킨 것과 틀린 것은 다른 사실이다.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from src.domain.actions import describe
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import RecomputePort

# `items[0].alarm` · `[1].n` · `total` — dict 키와 목록 색인만. 그 이상은 리드가 값을 다시 읽는 편이 낫다.
_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")
_JUNK = re.compile(r"\[\d+\]|[^.\[\]]+|\.")


def pluck(data: Any, path: str) -> Any:
    """증거 원본 안의 값 하나. 없으면 None(호출부가 error로 만든다)."""
    if not isinstance(path, str) or not path.strip() or _JUNK.sub("", path):
        return None
    cur = data
    for m in _TOKEN.finditer(path):
        idx, key = m.group(1), m.group(2)
        if key is not None:
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        else:
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return None
            cur = cur[int(idx)]
    return cur


class Recomputer(RecomputePort):
    """mongo 읽기 포트 + 앞선 증거의 원본 조회. 던지지 않는다."""

    def __init__(self, mongo, lookup: Callable[[str], Any], *, clock: Clock):
        self._mongo = mongo
        self._lookup = lookup
        self._clock = clock

    async def count(self, collection: str, filter: dict, expect: dict) -> ProbeResult:
        source = describe("recompute.count", {"collection": collection, "filter": filter, "expect": expect})
        expected, problem = self._expected(expect)
        if problem:
            return ProbeResult.failed(problem, source=source, clock=self._clock)
        got = await self._mongo.count(collection, filter)
        if got.status == "error":
            return ProbeResult.failed(f"원천 읽기 실패 — {got.error}", source=source, clock=self._clock)
        return self._compare(got.data, expected, expect, source)

    async def sum(self, collection: str, filter: dict, field: str, expect: dict) -> ProbeResult:
        source = describe("recompute.sum", {"collection": collection, "filter": filter, "field": field, "expect": expect})
        expected, problem = self._expected(expect)
        if problem:
            return ProbeResult.failed(problem, source=source, clock=self._clock)
        got = await self._mongo.find(collection, filter, projection=[field])
        if got.status == "error":
            return ProbeResult.failed(f"원천 읽기 실패 — {got.error}", source=source, clock=self._clock)
        if not got.envelope.complete:
            # 잘린 표본의 합은 합이 아니다 — 작게 낸 합을 "불일치"로 읽으면 없는 이상을 보고한다.
            return ProbeResult.failed(f"표본이 잘려 합계를 못 낸다 — {got.envelope.truncated_reason}. "
                                      f"filter를 좁히거나 count로 바꾼다", source=source, clock=self._clock)
        docs = got.data if isinstance(got.data, list) else []
        values = [d.get(field) for d in docs if isinstance(d, dict)]
        numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return self._compare(sum(numbers), expected, expect, source,
                             docs=len(docs), without_field=len(docs) - len(numbers))

    def _expected(self, expect: Any) -> tuple[Any, str | None]:
        if self._mongo is None:
            return None, "mongo 어댑터가 없다 — config가 선언하지 않았다"
        if not (isinstance(expect, dict) and isinstance(expect.get("evidence"), str)
                and isinstance(expect.get("path"), str)):
            return None, "expect는 {evidence, path}여야 한다 — 앞선 증거 id와 그 안의 값 위치"
        raw = self._lookup(expect["evidence"])
        if raw is None:
            return None, (f"기대값을 못 찾았다 — 증거 {expect['evidence']}의 원본이 이 실행기에 없다. "
                          f"다른 프로세스에서 만든 증거면 그 읽기를 다시 낸다")
        value = pluck(raw, expect["path"])
        if value is None:
            return None, f"기대값을 못 찾았다 — 증거 {expect['evidence']} 안에 {expect['path']}가 없다"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"기대값이 숫자가 아니다 — {expect['path']} = {value!r}"
        return value, None

    def _compare(self, recomputed: Any, expected: Any, expect: dict, source: str, **extra) -> ProbeResult:
        data = {"recomputed": recomputed, "expected": expected, "match": recomputed == expected,
                "evidence": expect["evidence"], "path": expect["path"], **extra}
        return ProbeResult.succeeded(data, source=source, clock=self._clock)
