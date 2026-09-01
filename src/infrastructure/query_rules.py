"""읽기 전용을 선언이 아니라 메커니즘으로 만드는 순수 판정들 — 스펙 §4.1 원칙 ②.

I/O가 없어 실구현·스텁·기동 검증이 같은 규칙을 공유하고, 단위 테스트가 전부를 덮는다.
"""
import re
from datetime import datetime, timezone

_AGG_ALLOW = {"$match", "$project", "$group", "$sort", "$limit", "$skip", "$count", "$unwind"}
_FILTER_ALLOW = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin",
                 "$exists", "$regex", "$and", "$or"}
_READONLY_ROLES = {"read", "readAnyDatabase"}


def aggregate_problems(pipeline):
    """aggregation 파이프라인의 스테이지를 검사하고 허용 목록 밖의 문제들을 돌려준다."""
    problems = []
    for stage in pipeline:
        for op in stage:
            if op not in _AGG_ALLOW:
                problems.append(f"aggregate 스테이지 {op!r}는 허용 목록에 없다 (쓰기/JS 실행 차단)")
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
    """토폴로지 정의의 패턴과 대조하고 끝점이 허용되는지 판정한다.

    패턴의 {자리표시자}를 [^/]+ ("/" 제외한 1개 이상)로 바꾼 정규식으로 전체 일치를 검사한다.
    경로 순회(../) 공격을 차단하고, 정확한 경로 길이만 일치시킨다.
    """
    for pattern in patterns:
        regex = "^" + re.sub(r"\{[^/}]+\}", r"[^/]+", pattern) + "$"
        if re.match(regex, endpoint):
            return True
    return False


def kafka_effective_start(requested, resolved_ts, earliest_ts):
    """offsets_for_times 결과로 달성 시작 시각을 정한다.

    resolved_ts가 None이면 요청 시각이 보존 밖 — earliest로 폴백하고 True를 돌려
    호출자가 봉투 effective_as_of에 명시하게 한다 (조용한 폴백 금지, 스펙 §4.2).
    """
    if resolved_ts is not None:
        return datetime.fromtimestamp(resolved_ts / 1000, tz=timezone.utc), False
    return datetime.fromtimestamp(earliest_ts / 1000, tz=timezone.utc), True


def mongo_role_problems(conn_status):
    """MongoDB connectionStatus 응답에서 허용 목록 밖의 롤을 문제로 보고한다.

    authenticatedUserRoles에서 {read, readAnyDatabase} 밖의 롤을 찾으면 문제 목록을 돌려준다.
    인증 사용자가 없으면(무인증 법인) 빈 목록을 돌려준다.
    """
    roles = conn_status.get("authInfo", {}).get("authenticatedUserRoles", [])
    return [f"Mongo 계정 롤 {r['role']!r}(db={r.get('db')})는 읽기 전용이 아니다"
            for r in roles if r.get("role") not in _READONLY_ROLES]
