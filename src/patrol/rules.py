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

전역 계약: KnownRuleError 외에는 어떤 입력에도 raise하지 않는다. params가
가리키는 bound(min/max/max_age_s)나 field가 애초에 rule을 평가할 수 없는
형태(비수치 bound, 부재한 필수 bound, 문자열이 아닌 field)라면 — 이는
데이터 이상이 아니라 rule 설정 자체가 잘못된 것이므로 finding이 아니라
KnownRuleError다(§C1/§I3). 데이터 쪽 이상(필드 부재, 비수치 값, NaN)은
계속 finding으로 처리한다(§M4) — 값이 이상한 것과 설정이 이상한 것은
다른 문제이고, 후자를 조용히 finding으로 삼키면 설정 오류가 매 패트롤마다
"이상 탐지"로 둔갑해 원인 파악을 방해한다.
"""
import math
import math
from datetime import datetime
from numbers import Number
from typing import Any, Callable, Literal

from src.config.schema_app import StrictModel
from src.domain.envelope import ProbeResult


class KnownRuleError(Exception):
    """rule 이름/이름 부재뿐 아니라, bound·field 등 rule을 평가할 수 없게
    만드는 모든 설정 결함에서 던진다 — 판정기 중 유일하게 허용되는 예외."""


class RuleVerdict(StrictModel):
    status: Literal["ok", "finding"]
    reason: str


def get_path(data: Any, dotted: str) -> Any | None:
    """dict/list에 대해 점 경로("a.b.0.c")로 값을 조회한다. 없으면 None.

    리스트는 정수 인덱스 세그먼트로 접근한다("items.0.name"). dotted가
    문자열이 아니면 None을 돌려준다 — 이 유틸리티 자체는 방어적으로 굴고,
    "field는 문자열이어야 한다"는 rule 설정 의미론(KnownRuleError)은 호출부인
    _field_name에서 판단한다.
    """
    if not isinstance(dotted, str):
        return None
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


def _field_name(params: dict, rule_name: str, *, required: bool = True) -> str | None:
    """params["field"]가 있으면 문자열이어야 한다 — 아니면 config 오류다.

    field 자체가 없는 것은 exists(required=False로 호출)에서만 허용된다 —
    데이터 전체를 보는 것이 그 규칙의 정상 형태이므로 None을 돌려준다.
    range/max/freshness는 field 없이는 애초에 무엇을 검사할지 정할 수 없는
    설정이므로(호출부가 기본 required=True로 둔다) field 부재 자체가
    KnownRuleError다 — 필드 부재를 "값이 없다"는 데이터 이상(finding)으로
    삼키면 설정 실수가 매 패트롤마다 이상 탐지로 둔갑한다(모듈 docstring).
    있는데 문자열이 아니면(예: 123) get_path에 넘겨도 애초에 의미가 없는
    설정이므로 마찬가지로 KnownRuleError.
    """
    field = params.get("field")
    if field is None:
        if required:
            raise KnownRuleError(f"rule {rule_name}에는 field가 필요하다")
        return None
    if not isinstance(field, str):
        raise KnownRuleError(f"rule {rule_name}의 field는 문자열이어야 한다 — {field!r}")
    return field


def _as_number(value: Any) -> float | None:
    """rule bound(min/max/max_age_s)를 숫자로 정규화한다.

    bool은 int의 서브클래스라 명시적으로 제외한다(True/False가 bound로
    들어오는 것은 설정 실수일 뿐 유효한 1/0 의도가 아니다). 문자열은 변환을
    시도하지 않는다 — "1000"과 1000은 설정 의도가 다른 값이고, 암묵적
    형변환은 타입이 어긋난 설정 오류를 조용히 감춘다. NaN도 거부한다 — NaN과의
    비교는 항상 False라 bound 검사를 조용히 무력화시킨다(값이 통과해도 아무
    비교도 걸리지 않는 채로 통과하는 것이지 의도한 "제한 없음"이 아니다).
    변환 불가면 None — 호출부가 이를 "bound 없음"과 "bound가 비수치"를
    구분해 KnownRuleError로 올릴지 판단한다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        if math.isnan(result):
            return None
        return result
    return None


def _numeric_value(value: Any) -> tuple[float | None, str | None]:
    """관측 데이터 값(필드 부재/비수치/NaN)을 판정한다. rule 설정이 아니라
    데이터 이상이므로 KnownRuleError가 아니라 finding 사유 문자열을 돌려준다.

    반환: (숫자면 그 값, 문제 있으면 사유 문자열) — 정상이면 (value, None).
    """
    if not isinstance(value, Number) or isinstance(value, bool):
        return None, "필드 부재/비수치"
    if isinstance(value, float) and math.isnan(value):
        return None, "비수치(NaN)"
    return float(value), None


def _parse_timestamp(value: Any) -> datetime | None:
    """ISO 문자열 또는 datetime을 datetime으로 파싱한다. 실패하면 None."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _align_tz(ts: datetime, now: datetime) -> tuple[datetime, datetime]:
    """naive/aware 혼합 비교 TypeError를 막기 위해 둘의 tzinfo를 맞춘다.

    어느 쪽이든 한쪽만 naive면, aware인 쪽의 tzinfo를 그대로 naive 쪽에
    붙여 둘 다 aware로 통일한다(방향 상관없이 대칭적으로 처리). 둘 다
    naive거나 둘 다 aware면 그대로 둔다.
    """
    if ts.tzinfo is None and now.tzinfo is not None:
        ts = ts.replace(tzinfo=now.tzinfo)
    elif ts.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=ts.tzinfo)
    return ts, now


def _is_empty(data: Any) -> bool:
    if data is None:
        return True
    if isinstance(data, (dict, list, tuple, set, str)):
        return len(data) == 0
    return False


def _judge_range(result: ProbeResult, params: dict) -> RuleVerdict:
    field = _field_name(params, "range")
    raw_min = params.get("min")
    raw_max = params.get("max")
    lo = hi = None
    if raw_min is not None:
        lo = _as_number(raw_min)
        if lo is None:
            raise KnownRuleError(f"rule range의 min이 수치가 아니다 — {raw_min!r}")
    if raw_max is not None:
        hi = _as_number(raw_max)
        if hi is None:
            raise KnownRuleError(f"rule range의 max가 수치가 아니다 — {raw_max!r}")
    if lo is None and hi is None:
        raise KnownRuleError("rule range는 min/max 중 하나 이상이 필요하다")
    raw_value = get_path(result.data, field) if field else None
    value, bad_reason = _numeric_value(raw_value)
    if bad_reason is not None:
        return RuleVerdict(status="finding", reason=f"{bad_reason} — {field}: {raw_value!r}")
    if lo is not None and value < lo:
        return RuleVerdict(status="finding", reason=f"범위 미달 — {field}={value} < min({lo})")
    if hi is not None and value > hi:
        return RuleVerdict(status="finding", reason=f"범위 초과 — {field}={value} > max({hi})")
    return RuleVerdict(status="ok", reason=f"범위 내 — {field}={value}")


def _judge_exists(result: ProbeResult, params: dict) -> RuleVerdict:
    field = _field_name(params, "exists", required=False)
    value = get_path(result.data, field) if field else result.data
    if _is_empty(value):
        return RuleVerdict(status="finding", reason="대상 부재")
    return RuleVerdict(status="ok", reason=f"대상 존재 — {value!r}")


def _judge_freshness(result: ProbeResult, params: dict, *, clock: Callable[[], datetime]) -> RuleVerdict:
    field = _field_name(params, "freshness")
    max_age_s = _as_number(params.get("max_age_s"))
    if max_age_s is None:
        raise KnownRuleError(f"rule freshness의 max_age_s이 수치가 아니다 — {params.get('max_age_s')!r}")
    raw = get_path(result.data, field) if field else None
    if raw is None:
        return RuleVerdict(status="finding", reason=f"필드 부재 — {field}")
    ts = _parse_timestamp(raw)
    if ts is None:
        return RuleVerdict(status="finding", reason=f"시각 파싱 실패 — {field}: {raw!r}")
    now = clock()
    ts, now = _align_tz(ts, now)
    age_s = (now - ts).total_seconds()
    if age_s > max_age_s:
        return RuleVerdict(status="finding", reason=f"신선도 초과 — {field} age={age_s:.0f}s > {max_age_s}s")
    return RuleVerdict(status="ok", reason=f"신선함 — {field} age={age_s:.0f}s")


def _judge_max(result: ProbeResult, params: dict) -> RuleVerdict:
    field = _field_name(params, "max")
    max_value = _as_number(params.get("max"))
    if max_value is None:
        raise KnownRuleError(f"rule max의 max가 수치가 아니다 — {params.get('max')!r}")
    raw_value = get_path(result.data, field) if field else None
    value, bad_reason = _numeric_value(raw_value)
    if bad_reason is not None:
        return RuleVerdict(status="finding", reason=f"{bad_reason} — {field}: {raw_value!r}")
    if value > max_value:
        return RuleVerdict(status="finding", reason=f"상한 초과 — {field}={value} > max({max_value})")
    return RuleVerdict(status="ok", reason=f"상한 이내 — {field}={value}")


def _zero_values(raw) -> tuple[list[float] | None, str | None]:
    """판정 대상을 수치 목록으로 편다 — (값들, 데이터 이상 사유).

    리스트·dict·스칼라를 모두 받는다. 대상 API가 `[0,0,0]`으로도 `{"a":0,"b":0}`
    으로도 같은 사실을 표현하기 때문이고, 그 모양 차이는 우리 관심사가 아니다.

    bool을 수치로 받지 않는 이유: 파이썬에서 `False == 0`이라 `{"ok": false}`가
    "현장이 멈췄다"로 둔갑한다(query_rules._is_exact가 같은 함정을 다룬다).
    NaN도 수치가 아니다 — 0과 비교 자체가 무의미하다.
    """
    if isinstance(raw, dict):
        items = list(raw.values())
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = [raw]
    values = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, float)) \
                or not math.isfinite(item):
            return None, f"수치가 아닌 값이 섞여 있다 — {item!r}"
        values.append(float(item))
    return values, None


def _judge_all_zero(result: ProbeResult, params: dict) -> RuleVerdict:
    """모든 값이 0인가 — 운영 이상(0/0/0)의 판정.

    **빈 것과 전부 0인 것을 구별한다.** `[]`는 "전부 0"이 아니라 "표본이 없다"이고,
    둘을 같은 finding으로 묶으면 "질문을 잘못했다"와 "현장이 멈췄다"가 한 통에
    섞인다 — 계획 9가 전부-또는-전무로 막으려던 바로 그 혼동이다. `min_count`
    미만도 같은 이유로 다른 사유를 낸다: 라인 30개 중 2개만 돌아온 표본으로
    "현장이 멈췄다"를 단정할 수 없다.
    """
    field = _field_name(params, "all_zero")
    raw_min = params.get("min_count", 1)
    min_count = _as_number(raw_min)
    if min_count is None or min_count < 1 or min_count != int(min_count):
        raise KnownRuleError(f"rule all_zero의 min_count는 1 이상의 정수여야 한다 — {raw_min!r}")
    raw = get_path(result.data, field) if field else None
    if raw is None:
        return RuleVerdict(status="finding", reason=f"필드 부재 — {field}")
    values, bad_reason = _zero_values(raw)
    if bad_reason is not None:
        return RuleVerdict(status="finding", reason=f"{bad_reason} — {field}")
    if len(values) < int(min_count):
        return RuleVerdict(status="finding",
                           reason=f"표본 부족 — {field}에 {len(values)}개뿐"
                                  f"(min_count={int(min_count)}) — 전부 0인지 판정할 수 없다")
    if all(v == 0 for v in values):
        return RuleVerdict(status="finding",
                           reason=f"전부 0 — {field}의 {len(values)}개 값이 모두 0이다")
    return RuleVerdict(status="ok", reason=f"0이 아닌 값이 있다 — {field}")


_RULES: dict[str, Callable] = {
    "all_zero": lambda result, params, clock: _judge_all_zero(result, params),
    "range": lambda result, params, clock: _judge_range(result, params),
    "exists": lambda result, params, clock: _judge_exists(result, params),
    "freshness": lambda result, params, clock: _judge_freshness(result, params, clock=clock),
    "max": lambda result, params, clock: _judge_max(result, params),
}


def judge_by_rule(result: ProbeResult, params: dict, *, clock: Callable[[], datetime]) -> RuleVerdict:
    """params["rule"] 종류에 따라 result.data를 판정한다.

    미지의 rule 이름, "rule" 키 부재, 그리고 rule을 평가할 수 없게 만드는
    설정 결함(비수치/부재 bound, 문자열이 아닌 field)은 전부 KnownRuleError
    — config 오류다. 그 외에는 어떤 입력에도 raise하지 않는다.
    result.status가 "error"여도 별도 처리 없이 data(대개 None) 기준으로
    그대로 판정한다(모듈 docstring 참고) — error 필터링은 runner의 몫이다.
    """
    rule_name = params.get("rule")
    handler = _RULES.get(rule_name) if rule_name is not None else None
    if handler is None:
        raise KnownRuleError(f"알 수 없는 rule — {rule_name!r}")
    return handler(result, params, clock)
