"""프로브 레지스트리 — 점검(CheckConfig)을 어댑터 호출로 잇는다.

프로브 계약(스펙 §4.6-9): 프로브는 절대 raise하지 않는다. 어댑터 미설정,
target 형식 오류, params 부재 등은 전부 error ProbeResult로 돌려준다 —
최외곽 try/except가 예상 밖 예외까지 마지막 방어선으로 잡는다.
"""
from typing import Awaitable, Callable

from src.config.schema_site import CheckConfig
from src.domain.envelope import Envelope, ProbeResult
from src.infrastructure.factory import AdapterSet
from src.infrastructure.query_rules import filter_problems
from src.patrol.resolvers import resolve_params

ProbeFn = Callable[..., Awaitable[ProbeResult]]

_TARGET_PREFIX_TO_PROBE = {
    "rest": "rest_get",
    "redis": "redis_get",
    "mongo": "mongo_recent",
    "kafka": "kafka_lag",
}


def _error(msg: str, clock) -> ProbeResult:
    return ProbeResult(status="error", envelope=Envelope(observed_at=clock()), error=msg)


def _split_target(target: str | None) -> tuple[str, str] | None:
    """"kind:나머지" 형식을 분리한다. 콜론이 없거나 target이 없으면 None."""
    if not target or ":" not in target:
        return None
    kind, _, rest = target.partition(":")
    return kind, rest


async def rest_get(adapters: AdapterSet, check: CheckConfig, *, clock,
                    timezone_name: str) -> ProbeResult:
    """target "rest:/path" → adapters.rest.get(path)."""
    try:
        if adapters.rest is None:
            return _error("어댑터 미설정: rest", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "rest":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, path = parts
        return await adapters.rest.get(path)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


async def redis_get(adapters: AdapterSet, check: CheckConfig, *, clock,
                    timezone_name: str) -> ProbeResult:
    """target "redis:key" → adapters.redis.get(key)."""
    try:
        if adapters.redis is None:
            return _error("어댑터 미설정: redis", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "redis":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, key = parts
        return await adapters.redis.get(key)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


async def mongo_recent(adapters: AdapterSet, check: CheckConfig, *, clock,
                    timezone_name: str) -> ProbeResult:
    """target "mongo:coll" → 최근 문서 find(sort=ts_field desc, limit=sample or 20)."""
    try:
        if adapters.mongo is None:
            return _error("어댑터 미설정: mongo", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "mongo":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, coll = parts
        ts_field = check.params.get("ts_field", "ts")
        limit = check.sample or 20
        return await adapters.mongo.find(coll, {}, sort=[(ts_field, -1)], limit=limit)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


async def mongo_find(adapters: AdapterSet, check: CheckConfig, *, clock,
                     timezone_name: str) -> ProbeResult:
    """target "mongo:coll" → `params.filter`로 좁힌 find. 정렬은 `params.sort`, 상한은 sample.

    `mongo_recent`가 `filter={}`를 못 박고 있어 config로는 "특정 컬렉션에 특정 질의"를
    표현할 수 없었다. 포트(`MongoReaderPort.find`)는 처음부터 필터를 받았고, 어댑터와
    스텁이 **같은 판정 함수**(`filter_problems`)로 연산자 허용 목록을 강제한다 —
    `$where`·`$function`처럼 서버측 JS를 도는 연산자는 표현 불가능하다(규율 9: 읽기
    전용은 문서가 아니라 메커니즘이다).

    해석기(`resolve`)가 낸 값은 필터에 **합쳐진다** — 리스트는 `$in`, 단일 값은 동등
    비교다. 값을 config에 적으면 즉시 썩기 때문이다(사업부마다 다르고 매일 바뀐다).
    정적 필터와 키가 겹치면 거부한다: 어느 쪽이 이기는지 config만 봐서 알 수 없으면
    사람이 값을 고쳤는데 안 바뀌는 형태로 조용히 고장 난다(`params.body`와 같은 규약).
    """
    try:
        if adapters.mongo is None:
            return _error("어댑터 미설정: mongo", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "mongo":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, coll = parts
        static = check.params.get("filter", {})
        if not isinstance(static, dict):
            return _error(f"params.filter는 dict여야 한다 (받은 타입: {type(static).__name__})",
                          clock)
        sort, problem = _mongo_sort(check.params.get("sort"))
        if problem is not None:
            return _error(problem, clock)

        resolved = await resolve_params(check.resolve, adapters=adapters, clock=clock,
                                        timezone_name=timezone_name)
        if resolved.problems:
            # 전부-또는-전무(§2-N3): 하나라도 못 내면 질의하지 않는다. 빈 필터로 전체를
            # 긁으면 "거짓 안심"이 되고, 그건 조용해서 더 위험하다.
            return _error("; ".join(resolved.problems), clock)
        overlap = sorted(set(static) & set(resolved.params))
        if overlap:
            return _error(f"params.filter와 resolve에 같은 키가 있다: {overlap}", clock)
        merged = dict(static)
        for key, value in resolved.params.items():
            merged[key] = {"$in": value} if isinstance(value, list) else value
        return await adapters.mongo.find(coll, merged, sort=sort,
                                         limit=check.sample or None)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


def mongo_find_problems(params: dict, resolve: dict) -> list[str]:
    """mongo_find 설정의 정적 문제를 전부 모아 돌려준다(기동 검증용).

    런타임에도 같은 판정이 돌지만(프로브·어댑터), 오타 하나가 매 순찰 error로만 드러나면
    사람은 그것을 "대상 시스템이 이상하다"로 읽는다 — 기동 거부 철학 그대로다.
    """
    problems: list[str] = []
    static = params.get("filter", {})
    if not isinstance(static, dict):
        problems.append(f"params.filter는 dict여야 한다 (받은 타입: {type(static).__name__})")
    else:
        problems += filter_problems(static)
        overlap = sorted(set(static) & set(resolve or {}))
        if overlap:
            problems.append(f"params.filter와 resolve에 같은 키가 있다: {overlap}")
    _, problem = _mongo_sort(params.get("sort"))
    if problem is not None:
        problems.append(problem)
    return problems


def _mongo_sort(spec) -> tuple[list[tuple[str, int]] | None, str | None]:
    """JSON에는 튜플이 없다 — `[["ts", -1]]`을 포트가 받는 모양으로 바꾼다."""
    if spec is None:
        return None, None
    if not isinstance(spec, list):
        return None, f"params.sort는 리스트여야 한다 (받은 타입: {type(spec).__name__})"
    out = []
    for item in spec:
        if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str) \
                or item[1] not in (1, -1):
            return None, f"params.sort의 항목은 [필드, 1|-1]이어야 한다: {item!r}"
        out.append((item[0], item[1]))
    return out, None


async def kafka_lag(adapters: AdapterSet, check: CheckConfig, *, clock,
                    timezone_name: str) -> ProbeResult:
    """params["group"] → adapters.kafka.group_offsets(group)."""
    try:
        if adapters.kafka is None:
            return _error("어댑터 미설정: kafka", clock)
        group = check.params.get("group")
        if not group:
            return _error("params에 group이 없다", clock)
        return await adapters.kafka.group_offsets(group)
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


async def rest_query(adapters: AdapterSet, check: CheckConfig, *, clock,
                     timezone_name: str) -> ProbeResult:
    """target "rest:<항목명>" → 해석기로 params를 만들어 adapters.rest.query 호출.

    해석 결과는 등재 스키마 검증을 **다시** 통과해야 소켓에 나간다(어댑터가 한다) —
    계획 8의 불변식("판정한 것 = 보내는 것")이 해석 경로에도 그대로 적용된다.
    """
    try:
        if adapters.rest is None:
            return _error("어댑터 미설정: rest", clock)
        parts = _split_target(check.target)
        if parts is None or parts[0] != "rest":
            return _error(f"target 형식 오류: {check.target!r}", clock)
        _, entry = parts
        static = check.params.get("body", {})
        if not isinstance(static, dict):
            return _error(f"params.body는 dict여야 한다 (받은 타입: {type(static).__name__})",
                          clock)
        resolved = await resolve_params(check.resolve, adapters=adapters, clock=clock,
                                        timezone_name=timezone_name)
        if resolved.problems:
            # 전부-또는-전무(§2-N3): 하나라도 못 내면 호출하지 않는다. finding이
            # 아니라 error다 — 우리 쪽 실패가 "현장 이상"으로 둔갑하면 안 된다
            # (KnownRuleError가 존재하는 이유와 같은 논리).
            return _error("파라미터 해석 실패 — " + "; ".join(resolved.problems), clock)
        result = await adapters.rest.query(entry, {**static, **resolved.params})
        if result.status != "ok":
            return result
        if resolved.omitted and isinstance(result.data, dict):
            # unfiltered의 존재 이유는 "해석이 실패해 우연히 전체를 봤다"와 "일부러
            # 전체를 봤다"를 코드가 구별하는 것이다. 증거에 남기지 않으면 그 구별이
            # 판정 시점에는 사라진다 — 나중에 서브에이전트가 전체 조회 결과를
            # "범위를 좁혀 확인함"으로 읽는다.
            request = result.data.get("request")
            if isinstance(request, dict):
                result = result.model_copy(update={"data": {
                    **result.data, "request": {**request, "unfiltered": resolved.omitted}}})
        if resolved.truncated:
            # 잘린 표본으로 "이상 없음"을 단정하는 것을 verify가 자동으로 막는다
            # (불완전 증거의 부정 결론 금지) — 기존 메커니즘을 그대로 쓴다.
            # 대상이 이미 알려준 절단 이유가 있으면 **잇는다**. 덮어쓰면 두 절단 중
            # 하나가 증거에서 사라진다.
            reasons = ([result.envelope.truncated_reason] if result.envelope.truncated_reason
                       else []) + resolved.truncated
            envelope = result.envelope.model_copy(update={
                "complete": False, "truncated_reason": "; ".join(reasons)})
            return result.model_copy(update={"envelope": envelope})
        return result
    except Exception as exc:
        return _error(f"프로브 실행 실패 — {type(exc).__name__}: {exc}", clock)


PROBES: dict[str, ProbeFn] = {
    "rest_get": rest_get,
    "rest_query": rest_query,
    "redis_get": redis_get,
    "mongo_recent": mongo_recent,
    # mongo의 **기본** 프로브는 여전히 mongo_recent다 — mongo_find는 probe로 명시할
    # 때만 쓰인다. 기존 점검은 한 글자도 안 바뀐다.
    "mongo_find": mongo_find,
    "kafka_lag": kafka_lag,
}


def resolve_probe(check: CheckConfig) -> str | None:
    """check.probe가 있으면 그것, 없으면 target 접두사로 기본 프로브를 고른다.

    rest는 접두사만으로 갈리지 않는다: `rest:/path`는 토폴로지 등록 끝점의 GET,
    `rest:<이름>`은 등재 항목이다. 새 구분자를 만들지 않아 _split_target이 그대로
    동작하고 기존 점검은 한 글자도 안 바뀐다.
    """
    if check.probe is not None:
        return check.probe
    parts = _split_target(check.target)
    if parts is None:
        return None
    kind, rest = parts
    if kind == "rest":
        return "rest_get" if rest.startswith("/") else "rest_query"
    return _TARGET_PREFIX_TO_PROBE.get(kind)
