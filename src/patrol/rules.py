"""rule 판정기 — ProbeResult의 데이터를 규칙에 따라 ok/finding으로 판정한다.

판정 계약(스펙): 판정기는 데이터만 본다. status="error"인 ProbeResult를
error 3상으로 거르는 것은 runner의 책임이다 — 판정기는 error를 별도로
처리하지 않고, 다른 ok 결과와 마찬가지로 result.data(대개 None)를 기준으로
그대로 규칙을 적용한다. 즉 exists 룰이면 "대상 부재"로 finding, range/max/
freshness면 필드 부재로 finding이 나온다 — 이는 우연이 아니라 의도한
방어선이다(runner가 필터링을 빠뜨려도 조용히 ok로 넘어가지 않는다).

미지의 rule 이름 또는 params에 "rule" 키가 없으면 config 오류이므로
KnownRuleError를 던진다 — 판정기 중 유일하게 허용되는 예외이며, runner가
잡아서 error 3상("rule 설정 오류 — ...")으로 레저에 남긴다.
"""
from datetime import datetime
from numbers import Number
from typing import Any, Callable, Literal

from src.config.schema_app import StrictModel
from src.domain.envelope import ProbeResult


class KnownRuleError(Exception):
    """params["rule"]이 없거나 알려지지 않은 이름일 때 — config 오류."""


class RuleVerdict(StrictModel):
    status: Literal["ok", "finding"]
    reason: str


def get_path(data: Any, dotted: str) -> Any | None:
    """dict/list에 대해 점 경로("a.b.0.c")로 값을 조회한다. 없으면 None.

    리스트는 정수 인덱스 세그먼트로 접근한다("items.0.name").
    """
    current = data
    for segment in dotted.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return None
            current = current[segment]
        elif isinstance(current, list):
            if not segment.lstrip("-").isdigit():
                return None
            idx = int(segment)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
        else:
            return None
    return current


def _parse_timestamp(value: Any, *, reference: datetime) -> datetime | None:
    """ISO 문자열 또는 datetime을 datetime으로 정규화한다.

    naive/aware 혼합 비교 TypeError를 막기 위해, 파싱 결과가 naive면
    reference(clock() 결과)의 tzinfo를 그대로 붙인다. reference 자체가
    naive면 결과도 naive로 둔다.
    """
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if ts.tzinfo is None and reference.tzinfo is not None:
        ts = ts.replace(tzinfo=reference.tzinfo)
    return ts


def _is_empty(data: Any) -> bool:
    if data is None:
        return True
    if isinstance(data, (dict, list, tuple, set, str)):
        return len(data) == 0
    return False


def _judge_range(result: ProbeResult, params: dict) -> RuleVerdict:
    field = params.get("field")
    value = get_path(result.data, field) if field else None
    if not isinstance(value, Number) or isinstance(value, bool):
        return RuleVerdict(status="finding", reason=f"필드 부재/비수치 — {field}: {value!r}")
    lo = params.get("min")
    hi = params.get("max")
    if lo is not None and value < lo:
        return RuleVerdict(status="finding", reason=f"범위 미달 — {field}={value} < min({lo})")
    if hi is not None and value > hi:
        return RuleVerdict(status="finding", reason=f"범위 초과 — {field}={value} > max({hi})")
    return RuleVerdict(status="ok", reason=f"범위 내 — {field}={value}")


def _judge_exists(result: ProbeResult, params: dict) -> RuleVerdict:
    field = params.get("field")
    value = get_path(result.data, field) if field else result.data
    if _is_empty(value):
        return RuleVerdict(status="finding", reason="대상 부재")
    return RuleVerdict(status="ok", reason=f"대상 존재 — {value!r}")


def _judge_freshness(result: ProbeResult, params: dict, *, clock: Callable[[], datetime]) -> RuleVerdict:
    field = params.get("field")
    raw = get_path(result.data, field) if field else None
    if raw is None:
        return RuleVerdict(status="finding", reason=f"필드 부재 — {field}")
    now = clock()
    ts = _parse_timestamp(raw, reference=now)
    if ts is None:
        return RuleVerdict(status="finding", reason=f"시각 파싱 실패 — {field}: {raw!r}")
    age_s = (now - ts).total_seconds()
    max_age_s = params.get("max_age_s")
    if age_s > max_age_s:
        return RuleVerdict(status="finding", reason=f"신선도 초과 — {field} age={age_s:.0f}s > {max_age_s}s")
    return RuleVerdict(status="ok", reason=f"신선함 — {field} age={age_s:.0f}s")


def _judge_max(result: ProbeResult, params: dict) -> RuleVerdict:
    field = params.get("field")
    value = get_path(result.data, field) if field else None
    if not isinstance(value, Number) or isinstance(value, bool):
        return RuleVerdict(status="finding", reason=f"필드 부재/비수치 — {field}: {value!r}")
    max_value = params.get("max")
    if value > max_value:
        return RuleVerdict(status="finding", reason=f"상한 초과 — {field}={value} > max({max_value})")
    return RuleVerdict(status="ok", reason=f"상한 이내 — {field}={value}")


_RULES: dict[str, Callable] = {
    "range": lambda result, params, clock: _judge_range(result, params),
    "exists": lambda result, params, clock: _judge_exists(result, params),
    "freshness": lambda result, params, clock: _judge_freshness(result, params, clock=clock),
    "max": lambda result, params, clock: _judge_max(result, params),
}


def judge_by_rule(result: ProbeResult, params: dict, *, clock: Callable[[], datetime]) -> RuleVerdict:
    """params["rule"] 종류에 따라 result.data를 판정한다.

    미지의 rule 이름 또는 "rule" 키 부재는 KnownRuleError — config 오류다.
    result.status가 "error"여도 별도 처리 없이 data(대개 None) 기준으로
    그대로 판정한다(모듈 docstring 참고) — error 필터링은 runner의 몫이다.
    """
    rule_name = params.get("rule")
    handler = _RULES.get(rule_name) if rule_name is not None else None
    if handler is None:
        raise KnownRuleError(f"알 수 없는 rule — {rule_name!r}")
    return handler(result, params, clock)
