"""읽기 전용을 선언이 아니라 메커니즘으로 만드는 순수 판정들 — 스펙 §4.1 원칙 ②.

I/O가 없어 실구현·스텁·기동 검증이 같은 규칙을 공유하고, 단위 테스트가 전부를 덮는다.
"""
import re
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
    """토폴로지 패턴과의 전체 일치 판정. 리터럴 구간은 이스케이프, {자리표시자}는 [^/]+.

    경로 순회(.., .)와 퍼센트 인코딩은 패턴 매칭 전에 조기 거부한다. `{자리표시자}`가
    `[^/]+`로 컴파일되므로 `/api/v1/lines/../oee`가 자리표시자 구간과 매치되어
    통과해버리는데, httpx가 이를 다시 `/api/v1/oee`로 정규화해 실제로는 비허용
    끝점에 도달한다(실증됨) — `%2e%2e` 같은 퍼센트 인코딩도 동일하게 우회로 쓰인다.

    `?`/`#`/`;`도 같은 이유로 거부한다. `[^/]+`가 이 셋을 삼키므로
    `/api/v1/lines/L1?_method=DELETE&/oee`가 패턴에 매치되는데, httpx는
    `GET /api/v1/lines/L1` + query `_method=DELETE&/oee`로 나간다(실증됨) — 등록되지
    않은 끝점에 도달하고, `_method` 오버라이드를 존중하는 대상이면 GET이 DELETE로
    동작해 "완전 읽기 전용"이 깨진다. endpoint 문자열은 서브에이전트 LLM이 정하므로
    (application/subagents.py의 rest_get 도구) 이 판정이 유일한 방어선이다.
    `#`은 프래그먼트가 절단돼 다른 끝점이 되고, `;`은 매트릭스 파라미터로 새 나간다.

    쿼리 파라미터가 정말 필요한 점검(MES API 등)은 이 함수를 우회하는 게 아니라
    `(method, path, 허용 쿼리 키)` 항목을 config에 등재하는 형태로 열어야 한다 —
    지금은 등재 스키마가 없으므로 전면 거부가 정직한 상태다.
    """
    if "%" in endpoint:
        return False
    if any(ch in endpoint for ch in "?#;"):
        return False
    if any(segment in (".", "..") for segment in endpoint.split("/")):
        return False
    for pattern in patterns:
        parts = re.split(r"\{[^/}]+\}", pattern)
        regex = "[^/]+".join(re.escape(part) for part in parts)
        if re.fullmatch(regex, endpoint):
            return True
    return False


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
