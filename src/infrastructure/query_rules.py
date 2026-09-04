"""읽기 전용을 선언이 아니라 메커니즘으로 만드는 순수 판정들 — 스펙 §4.1 원칙 ②.

I/O가 없어 실구현·스텁·기동 검증이 같은 규칙을 공유하고, 단위 테스트가 전부를 덮는다.
"""
import math
import re
from urllib.parse import urlsplit

from src.knowledge.digest import canonical_digest
from datetime import datetime, timezone

_AGG_ALLOW = {"$match", "$project", "$group", "$sort", "$limit", "$skip", "$count", "$unwind"}
_AGG_BANNED_NESTED = {"$function", "$accumulator", "$where"}
_FILTER_ALLOW = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin",
                 "$exists", "$regex", "$options", "$and", "$or"}
_READONLY_ROLES = {"read", "readAnyDatabase"}


def aggregate_problems(pipeline):
    """스테이지 이름은 허용 목록으로, 스테이지 내부는 금지 연산자 재귀 탐색으로 검사한다.

    $function/$accumulator/$where는 스테이지 최상위가 아니라 허용된 스테이지 안에
    중첩되어 나타나므로(실제 MongoDB 문법), 내부까지 걸어야 JS 실행을 막는다.
    """
    problems = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _AGG_BANNED_NESTED:
                    problems.append(f"aggregate 내부 연산자 {key!r}는 금지된다 (JS 실행 차단)")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for stage in pipeline:
        for op, body in stage.items():
            if op not in _AGG_ALLOW:
                problems.append(f"aggregate 스테이지 {op!r}는 허용 목록에 없다 (쓰기/JS 실행 차단)")
            walk(body)
    return problems


def filter_problems(filter):
    """filter 스펙의 연산자들을 재귀적으로 검사하고 허용 목록 밖의 문제들을 돌려준다."""
    problems = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("$") and key not in _FILTER_ALLOW:
                    problems.append(f"filter 연산자 {key!r}는 허용 목록에 없다")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(filter)
    return problems


def endpoint_allowed(endpoint, patterns):
    """토폴로지 등록 끝점(GET)과의 전체 일치 판정.

    **불변식: 판정한 문자열 전체가 곧 경로여야 한다.** 문자를 하나씩 열거해 막는
    방식은 이 자리에서 세 번 뚫렸다 — `..` 순회, `?`/`#`/`;` 삼킴, 그리고 선두
    스페이스(urlsplit이 lstrip하는 집합은 `\x00`–`\x20`인데 가드는 `< 0x20`만
    막았다). 매번 원인이 같았다: **정규화된 것을 판정하고 원본을 전송한다.**

    그래서 `urlsplit(endpoint).path == endpoint`를 요구한다. urlsplit이 무엇을
    벗겨내든(공백·제어문자), 어디로 옮기든(쿼리·프래그먼트·netloc) 두 값이
    달라지므로 한 번에 걸린다. 새 우회 문자를 알아낼 필요가 없다.

    쿼리 파라미터가 필요한 점검은 등재 항목(`target.rest.entries`)으로 표현하고,
    그쪽은 entry_call_problems가 `query_schema`로 판정한다.

    나머지 거부 규칙(경로 순회·퍼센트 인코딩·매트릭스 파라미터·역슬래시)은
    urlsplit이 건드리지 않아 위 불변식만으로는 안 걸리는 것들이다.

    `{자리표시자}`는 `[^/?#;]+`로 컴파일한다 — `[^/]+`는 구분자를 삼킨다.
    """
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        # urlsplit은 잘못된 IPv6 리터럴 등에 ValueError를 던진다. endpoint는
        # 서브에이전트 LLM이 정하고 두 어댑터의 호출은 try/except 밖이라, 여기서
        # 새면 무raise 규율이 깨진다.
        return False
    path = parts.path
    if path != endpoint:            # 벗겨졌거나 옮겨졌다 — 판정과 전송이 갈린다
        return False
    if "%" in path or "\\" in path:
        return False
    if any(seg in (".", "..") or ";" in seg for seg in path.split("/")):
        return False
    for pattern in patterns:
        segs = re.split(r"\{[^/}]+\}", pattern)
        regex = "[^/?#;]+".join(re.escape(seg) for seg in segs)
        if re.fullmatch(regex, path):
            return True
    return False


_SCALARS = {"str": str, "int": int, "float": float, "bool": bool}


def _is_exact(value, want: type) -> bool:
    """bool을 int로 통과시키지 않는다 — 파이썬에서 bool은 int의 하위 타입이라
    isinstance(True, int)가 참이고, 그대로 두면 {"limit": True}가 1로 나간다."""
    if want is int and isinstance(value, bool):
        return False
    if want is float:                       # int는 float 자리에 허용한다(JSON 관례)
        # NaN·inf는 JSON 직렬화가 거부한다 — 여기서 안 막으면 실구현만 error가 되고
        # 스텁은 통과해, 테스트가 전부 스텁인 이 리포에서 갈라짐이 안 잡힌다.
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))
    return isinstance(value, want)


def _type_problem(name: str, value, want: str) -> str | None:
    if want.startswith("list["):
        if not isinstance(value, list):
            return f"body 필드 {name!r}는 {want}여야 한다 (받은 타입: {type(value).__name__})"
        elem = _SCALARS.get(want[5:-1])
        if elem is None:                    # 계획 10이 OpenAPI 유래 스키마를 넘길 때 대비
            return f"body 필드 {name!r}의 선언 타입 {want!r}을 알 수 없다"
        bad = [v for v in value if not _is_exact(v, elem)]
        return (f"body 필드 {name!r}의 원소 타입이 {want}와 다르다 (예: {bad[0]!r})"
                if bad else None)
    scalar = _SCALARS.get(want)
    if scalar is None:
        return f"body 필드 {name!r}의 선언 타입 {want!r}을 알 수 없다"
    if not _is_exact(value, scalar):
        return f"body 필드 {name!r}는 {want}여야 한다 (받은 타입: {type(value).__name__})"
    return None


def entry_body_problems(body: dict, schema: dict) -> list[str]:
    """등재 항목의 닫힌 body 스키마를 검증한다. 문제 목록을 돌려준다(빈 리스트면 통과).

    메서드 수준에서 잃은 "쓰기가 불가능하다"는 성질의 정직한 대체물이다 —
    "POST는 쓰기 가능한 동사다"를 "이 항목은 이 키들을 이 타입으로만 실을 수
    있다"로 되돌린다.

    필드 **누락**은 문제로 보지 않는다: 어떤 필드가 필수인지는 대상 API가 정하고
    우리는 모른다(계획 10의 OpenAPI가 답할 문제). 여기서 강제하면 스키마를 우리
    추측으로 좁히게 된다.
    """
    problems = []
    for name, value in body.items():
        want = schema.get(name)
        if want is None:
            problems.append(f"body 필드 {name!r}는 등재 스키마에 없다")
            continue
        problem = _type_problem(name, value, want)
        if problem:
            problems.append(problem)
    return problems


def entry_schema(entry) -> dict:
    """등재 항목이 실제로 검증에 쓰는 닫힌 스키마.

    boot과 어댑터가 **같은 식**을 봐야 한다. 한때 boot은 `body_schema or
    query_schema`, 어댑터는 `query_schema if GET else body_schema`였는데,
    `RestEntry._shape_is_sound`가 한쪽을 비워 두게 강제한 덕에 우연히 일치했을
    뿐이다 — 스키마 원천이 하나만 더 생기면 조용히 갈린다(계획 7에서 claim의 두
    구현이 갈라져 프로덕션 버그를 테스트가 못 잡은 것과 같은 형태).
    """
    return entry.query_schema if entry.method == "GET" else entry.body_schema


def entry_call_problems(entry, params: dict) -> list[str]:
    """등재 항목 호출의 params를 검증한다 — GET이면 쿼리 키, POST면 body 스키마.

    두 어댑터가 이 함수를 공유해야 판정이 갈라지지 않는다. 계획 7에서 claim의
    두 구현이 갈라져 프로덕션 버그를 테스트가 못 잡은 일이 실제로 있었다.
    """
    if not isinstance(params, dict):
        # 포트 docstring이 "소켓에 나가기 전에 error로 거부한다"고 단정한다.
        # 해석기가 실패 시 None을 돌려주면 즉시 이 경로다.
        return [f"params는 dict여야 한다 (받은 타입: {type(params).__name__})"]
    # GET도 POST와 같은 닫힌 스키마를 쓴다. 키만 보고 값을 안 보면 dict·list가
    # 그대로 쿼리에 실려(파이썬 repr, 파라미터 증식) 우리가 의도하지 않은 요청이
    # 나간다 — "닫힌 스키마를 통과해야 소켓에 나간다"가 반만 성립하던 자리다.
    return entry_body_problems(params, entry_schema(entry))


def entry_evidence_source(method: str, path: str, params: dict) -> str:
    """등재 항목 호출의 증거 출처 문자열.

    body digest를 붙이는 이유(스펙 §2-N4): 응답만 보관하면 "0/0/0"이 "현장이
    멈췄다"인지 "질문을 잘못했다"인지 구별할 수 없다. GET은 URL이 곧 질문이라
    이 문제가 없었고, POST를 열면서 처음 생기는 요구다.

    canonical_digest를 쓰므로 키 순서가 달라도 같은 질문이면 같은 출처가 된다 —
    증거가 질문 단위로 모이고 흩어지지 않는다.
    """
    return f"rest:{method}:{path}#{canonical_digest(params)[:8]}"


def kafka_effective_start(requested, resolved_ts, earliest_ts):
    """offsets_for_times 결과로 달성 시작 시각을 정한다.

    resolved_ts가 None이면 요청 시각이 보존 밖 — earliest로 폴백하고 True를 돌려
    호출자가 봉투 effective_as_of에 명시하게 한다 (조용한 폴백 금지, 스펙 §4.2).
    둘 다 None이면 빈 파티션 — 달성 시각을 정할 수 없어 요청 시각을 그대로, 폴백 표시.
    """
    if resolved_ts is not None:
        return datetime.fromtimestamp(resolved_ts / 1000, tz=timezone.utc), False
    if earliest_ts is not None:
        return datetime.fromtimestamp(earliest_ts / 1000, tz=timezone.utc), True
    return requested, True


def mongo_role_problems(conn_status):
    """MongoDB connectionStatus 응답에서 허용 목록 밖의 롤을 문제로 보고한다.

    authenticatedUserRoles에서 {read, readAnyDatabase} 밖의 롤을 찾으면 문제 목록을 돌려준다.
    인증 사용자가 없으면(무인증 법인) 빈 목록을 돌려준다.
    """
    roles = conn_status.get("authInfo", {}).get("authenticatedUserRoles", [])
    return [f"Mongo 계정 롤 {r.get('role')!r}(db={r.get('db')})는 읽기 전용이 아니다"
            for r in roles if r.get("role") not in _READONLY_ROLES]
