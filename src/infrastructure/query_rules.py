"""읽기 전용을 선언이 아니라 메커니즘으로 만드는 순수 판정들 — 스펙 §4.1 원칙 ②.

I/O가 없어 실구현·스텁·기동 검증이 같은 규칙을 공유하고, 단위 테스트가 전부를 덮는다.
"""
import re
from urllib.parse import parse_qsl, urlsplit
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


def endpoint_allowed(endpoint, patterns, *, query_keys=frozenset()):
    """토폴로지/등재 패턴과의 전체 일치 판정.

    문자 단위 거부에서 URL 파싱으로 바꿨다. 이전에는 `?`/`#`/`;`를 통째로 막아
    정상 쿼리 파라미터(MES 점검이 요구한다)까지 못 쓰게 됐다 — 파싱하면 path와
    query를 갈라 각각에 맞는 규칙을 적용할 수 있다.

    거부 규칙:
    - scheme/netloc이 있으면(절대 URL) 거부 — base_url을 우회한다.
    - fragment는 무조건 거부 — 절단돼 등록되지 않은 끝점이 된다(실증됨).
    - path에 `%`가 있으면 거부 — `%2e%2e` 경로 순회 우회로.
    - path 세그먼트에 `.`/`..`/`;`가 있으면 거부 — 순회와 매트릭스 파라미터.
    - query 키가 query_keys에 없으면 거부. 기본값이 빈 집합이라 **등재하지 않은
      호출은 지금까지와 똑같이 쿼리를 쓸 수 없다.**

    `{자리표시자}`는 `[^/?#;]+`로 컴파일한다 — `[^/]+`는 구분자를 삼켜서
    `/lines/L1?_method=DELETE&/oee`가 패턴에 매치되는 우회로를 만든다.
    """
    # urlsplit은 개행·탭을 조용히 제거한다(WHATWG URL 규약). 판정은 정규화된
    # 문자열을 보는데 httpx는 원본을 받으므로, 그대로 두면 "판정한 것과 보내는 것이
    # 다르다"는 원래 버그와 같은 구조가 된다. 파싱 전에 거부한다.
    if any(ch in endpoint for ch in "\r\n\t") or any(ord(ch) < 0x20 for ch in endpoint):
        return False
    parts = urlsplit(endpoint)
    if parts.scheme or parts.netloc or parts.fragment:
        return False
    path = parts.path
    if "%" in path or any(seg in (".", "..") or ";" in seg for seg in path.split("/")):
        return False
    if parts.query:
        keys = {k for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
        if not keys or not keys <= set(query_keys):
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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, want)


def _type_problem(name: str, value, want: str) -> str | None:
    if want.startswith("list["):
        if not isinstance(value, list):
            return f"body 필드 {name!r}는 {want}여야 한다 (받은 타입: {type(value).__name__})"
        elem = _SCALARS[want[5:-1]]
        bad = [v for v in value if not _is_exact(v, elem)]
        return (f"body 필드 {name!r}의 원소 타입이 {want}와 다르다 (예: {bad[0]!r})"
                if bad else None)
    if not _is_exact(value, _SCALARS[want]):
        return f"body 필드 {name!r}는 {want}여야 한다 (받은 타입: {type(value).__name__})"
    return None


def entry_body_problems(body: dict, schema: dict) -> list[str]:
    """등재 항목의 닫힌 body 스키마를 검증한다. 문제 목록을 돌려준다(빈 리스트면 통과).

    메서드 수준에서 잃은 "쓰기가 불가능하다"는 성질의 정직한 대체물이다 —
    "POST는 쓰기 가능한 동사다"를 "이 항목은 이 키들을 이 타입으로만 실을 수
    있다"로 되돌린다.

    필드 **누락**은 문제로 보지 않는다: 어떤 필드가 필수인지는 대상 API가 정하고
    우리는 모른다(계획 9의 OpenAPI가 답할 문제). 여기서 강제하면 스키마를 우리
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


def entry_call_problems(entry, params: dict) -> list[str]:
    """등재 항목 호출의 params를 검증한다 — GET이면 쿼리 키, POST면 body 스키마.

    두 어댑터가 이 함수를 공유해야 판정이 갈라지지 않는다. 계획 7에서 claim의
    두 구현이 갈라져 프로덕션 버그를 테스트가 못 잡은 일이 실제로 있었다.
    """
    if entry.method == "GET":
        unknown = sorted(set(params) - set(entry.query_keys))
        return [f"허용되지 않은 쿼리 키: {unknown}"] if unknown else []
    return entry_body_problems(params, entry.body_schema)


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
