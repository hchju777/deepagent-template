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
from zoneinfo import ZoneInfo
from typing import Any

from src.config.schema_app import StrictModel
from src.infrastructure.factory import AdapterSet


class ResolveResult(StrictModel):
    params: dict[str, Any] = {}
    omitted: list[str] = []       # unfiltered로 의도적으로 뺀 키
    problems: list[str] = []      # 하나라도 있으면 호출하지 않는다
    truncated: list[str] = []     # 카디널리티로 잘라낸 사실(보고서가 적어야 한다)


def _pluck(rows, field: str) -> tuple[list, int, int]:
    """행 목록에서 field를 뽑아 중복 없이 정렬해 돌려준다 — (값, 총 행 수, 쓴 행 수).

    **정렬해서 돌려준다.** Mongo find는 자연 순서(불안정)라 등장 순서를 그대로
    물려받으면 매 실행 다른 표본을 점검하고, 리스트 순서가 살아 있는
    canonical_digest 탓에 같은 질문이 여러 증거로 흩어진다. 결정론은 이 리포의
    최우선 규율이다.

    쓴 행 수를 같이 돌려주는 이유: 500행 중 3행에만 그 필드가 있어도 값 3개는
    완벽하게 정상으로 보인다. 버린 행을 세지 않으면 "일부만 봤다"가 사라진다 —
    카디널리티 절단을 truncated로 남기는 것과 같은 규율이다.
    """
    seen, out, used = set(), [], 0
    rows = rows if isinstance(rows, list) else []
    for row in rows:
        value = row.get(field) if isinstance(row, dict) else None
        if value is None:
            continue
        used += 1
        # 집합 멤버십만 쓰면 0과 False, 1과 True, 1과 1.0이 병합된다 —
        # 파이썬에서 그것들이 같은 해시·같은 값이기 때문이다.
        key = (type(value).__name__, value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    # 타입명으로 먼저 그룹을 가르면 그룹 안은 동종이라 자연 비교가 안전하다.
    # str(v)로 정렬하면 [2,10,1]이 [1,10,2]가 된다 — 결정론을 얻으려다 "매번 같지만
    # 틀린 표본"을 만들고, 스키마 검증도 어댑터도 그걸 못 막는다.
    return sorted(out, key=lambda v: (type(v).__name__, v)), len(rows), used


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


async def _read_values(name: str, spec, *, adapters: AdapterSet, problems: list,
                       truncated: list) -> list | None:
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
        # 카디널리티 한도를 소스에 밀지 **않는다**. limit을 밀면 대상 부하는 줄지만
        # 전체 개수를 모르게 되어 "500개 중 50개만 봤다"를 못 적는다 — 이 시스템에서
        # 그 문장이 부하보다 비싸다. 조회량은 guards.max_rows가 이미 막는다.
        result = await adapters.mongo.find(spec.collection, spec.filter)
    else:                                    # redis
        if adapters.redis is None:
            problems.append(f"해석기 {name!r}: redis 어댑터 미설정")
            return None
        result = await adapters.redis.scan(spec.pattern)
    if result.status == "error":
        problems.append(f"해석기 {name!r} 조회 실패 — {result.error}")
        return None
    if not result.envelope.complete:
        # 어댑터가 guards.max_rows로 잘랐다. 그 사실을 버리면 잘린 표본으로 물어본
        # 결과가 "완전한 증거"로 박제된다 — 이 모듈이 막으려던 바로 그 형태다.
        # 막지 않고 드러내는 것이 이 시스템의 방식이다(verify가 불완전 증거의
        # 부정 결론을 이미 금지한다).
        truncated.append(f"{name}: 소스가 잘림({result.envelope.truncated_reason})")
    data = result.data
    if kind == "redis":
        return list(data) if isinstance(data, list) else []
    # rest 응답은 body가 곧 행 목록인 형태만 다룬다. {"items": [...]}처럼 감싸는
    # 응답은 body 경로 지정이 필요하고, 그건 실제로 그런 API를 만난 뒤에 연다.
    rows = data.get("body") if kind == "rest" and isinstance(data, dict) else data
    values, total, used = _pluck(rows, spec.field)
    if total and used < total:
        truncated.append(f"{name}: {total}행 중 {used}행만 {spec.field!r}를 갖고 있다")
    return values


async def resolve_params(specs: dict, *, adapters: AdapterSet,
                         clock: Callable[[], datetime],
                         timezone_name: str = "UTC") -> ResolveResult:
    """해석기 스펙들을 실제 값으로 바꾼다. 절대 raise하지 않는다.

    첫 실패에서 멈추지 않고 전부를 훑어 problems에 모은다 — 기동 거부 철학과 같은
    이유로, 사람이 한 번에 하나씩만 고치게 하지 않는다. 다만 하나라도 실패하면
    **params를 비워 돌려준다**: 전부-또는-전무를 호출자 규율이 아니라 반환 타입이
    지켜야 새 호출부가 부분 결과를 쓰는 일이 생기지 않는다.
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
                # 스케줄러는 app.timezone으로 도는데 clock은 UTC다. 그대로 두면
                # 00:00~09:00 KST 사이에 today가 항상 어제가 되어, 아침 cron이
                # 매일 100% 전날 날짜로 대상을 호출한다.
                now = clock().astimezone(ZoneInfo(timezone_name))
                params[name] = {"today": now.date().isoformat(),
                                "yesterday": (now - timedelta(days=1)).date().isoformat(),
                                "now_iso": now.isoformat()}[spec.expr]
                continue
            values = await _read_values(name, spec, adapters=adapters, problems=problems,
                                        truncated=truncated)
            if values is None:
                continue
            if not values:
                problems.append(f"해석기 {name!r}가 빈 결과를 냈다 — 빈 값을 보내면 "
                                f"대상에 따라 거짓 경보 또는 거짓 안심이 된다")
                continue
            params[name] = _apply_cardinality(name, values, spec.cardinality, truncated)
        except Exception as exc:                                   # noqa: BLE001 — 무raise 계약
            problems.append(f"해석기 {name!r} 실패 — {type(exc).__name__}: {exc}")
    if problems:
        # 전부-또는-전무를 호출자 규율이 아니라 반환 타입이 지키게 한다 —
        # 부분 결과를 돌려주면 새 호출부가 그것을 그대로 쓴다.
        return ResolveResult(problems=problems)
    return ResolveResult(params=params, omitted=omitted, problems=problems,
                         truncated=truncated)
