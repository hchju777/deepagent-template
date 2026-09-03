"""파라미터 값 해석기 — 스펙 §2-N2·§2-N3.

값을 config에 적으면 즉시 썩는다(사업부/법인마다 다르고 매일 바뀐다). 그래서
config는 **값이 어디서 오는지**만 선언하고, 실제 값은 점검 실행 시점에 살아 있는
소스에서 읽는다.

**전부-또는-전무**(§2-N3): 해석기가 하나라도 값을 못 내면 호출 자체를 하지 않는다.
"불러놓고 판정만 안 한다"로는 부족한 이유 — 빈 필터로 나간 요청은 endpoint에 따라
0/0/0(거짓 경보)이 되기도 하고 전체 조회(거짓 안심, 조용해서 더 위험)가 되기도
하는데 어느 쪽인지 알 방법이 없다. 게다가 전체 조회로 돌아온 응답이 증거로 박제되면
나중에 서브에이전트가 "정상 확인됨"의 근거로 인용한다 — 잘못된 범위의 응답은
증거가 아니라 오염원이므로 애초에 만들지 않는다.

이 모듈은 절대 raise하지 않는다. 모든 실패는 ResolveResult.problems로 흡수한다.
"""
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from src.config.schema_app import StrictModel
from src.infrastructure.factory import AdapterSet


class ResolveResult(StrictModel):
    params: dict[str, Any] = {}
    omitted: list[str] = []       # unfiltered로 의도적으로 뺀 키
    problems: list[str] = []      # 하나라도 있으면 호출하지 않는다
    truncated: list[str] = []     # 카디널리티로 잘라낸 사실(보고서가 적어야 한다)


def _pluck(rows, field: str) -> list:
    """행 목록에서 field를 뽑아 중복을 없앤다(등장 순서 보존).

    순서를 보존하는 이유: 같은 점검이 매번 다른 순서로 나가면 증거 출처 digest가
    달라져(entry_evidence_source가 canonical_digest를 쓰지만 리스트 순서는 살아 있다)
    같은 질문이 여러 증거로 흩어진다.
    """
    seen, out = set(), []
    for row in rows if isinstance(rows, list) else []:
        value = row.get(field) if isinstance(row, dict) else None
        if value is not None and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _apply_cardinality(name: str, values: list, cardinality: str,
                       truncated: list[str]) -> list:
    if cardinality == "all":
        return values
    limit = int(cardinality.split(":")[1])
    if len(values) <= limit:
        return values
    if cardinality.startswith("first:"):
        kept = values[:limit]
    else:                                    # sample:N — 고르게 훑는다(결정론 유지)
        step = max(1, len(values) // limit)
        kept = values[::step][:limit]
    truncated.append(f"{name}: {len(values)}개 중 {len(kept)}개만 사용({cardinality})")
    return kept


async def _read_values(name: str, spec, *, adapters: AdapterSet, problems: list) -> list | None:
    """어댑터에서 값을 읽는다. 어댑터 미설정·조회 실패는 problems에 남기고 None."""
    kind = spec.from_
    if kind == "rest":
        if adapters.rest is None:
            problems.append(f"해석기 {name!r}: rest 어댑터 미설정")
            return None
        result = await adapters.rest.query(spec.entry, {})
    elif kind == "mongo":
        if adapters.mongo is None:
            problems.append(f"해석기 {name!r}: mongo 어댑터 미설정")
            return None
        result = await adapters.mongo.find(spec.collection, spec.filter)
    else:                                    # redis
        if adapters.redis is None:
            problems.append(f"해석기 {name!r}: redis 어댑터 미설정")
            return None
        result = await adapters.redis.scan(spec.pattern)
    if result.status == "error":
        problems.append(f"해석기 {name!r} 조회 실패 — {result.error}")
        return None
    data = result.data
    if kind == "redis":
        return list(data) if isinstance(data, list) else []
    rows = data.get("body") if kind == "rest" and isinstance(data, dict) else data
    return _pluck(rows, spec.field)


async def resolve_params(specs: dict, *, adapters: AdapterSet,
                         clock: Callable[[], datetime]) -> ResolveResult:
    """해석기 스펙들을 실제 값으로 바꾼다. 절대 raise하지 않는다.

    첫 실패에서 멈추지 않고 전부를 훑어 problems에 모은다 — 기동 거부 철학과 같은
    이유로, 사람이 한 번에 하나씩만 고치게 하지 않는다.
    """
    params, omitted, problems, truncated = {}, [], [], []
    for name, spec in specs.items():
        kind = spec.from_
        try:
            if kind == "unfiltered":
                # 빈 리스트를 보내는 것과 키를 안 보내는 것은 대상에서 다르게 동작한다.
                omitted.append(name)
                continue
            if kind == "clock":
                now = clock()
                params[name] = {"today": now.date().isoformat(),
                                "yesterday": (now - timedelta(days=1)).date().isoformat(),
                                "now_iso": now.isoformat()}[spec.expr]
                continue
            values = await _read_values(name, spec, adapters=adapters, problems=problems)
            if values is None:
                continue
            if not values:
                problems.append(f"해석기 {name!r}가 빈 결과를 냈다 — 빈 값을 보내면 "
                                f"대상에 따라 거짓 경보 또는 거짓 안심이 된다")
                continue
            params[name] = _apply_cardinality(name, values, spec.cardinality, truncated)
        except Exception as exc:                                   # noqa: BLE001 — 무raise 계약
            problems.append(f"해석기 {name!r} 실패 — {type(exc).__name__}: {exc}")
    return ResolveResult(params=params, omitted=omitted, problems=problems,
                         truncated=truncated)
