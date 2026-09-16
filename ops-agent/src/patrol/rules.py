"""판정 — **"지금 이 응답이 이상한가"만 답한다.**

## 상태를 갖지 않는다

"몇 회 연속 이상인가"는 여기서 세지 않는다. 그건 **케이스를 열 것인가**의 문제이고
6단계 게이트의 일이다. 이유 셋:

1. 상태를 넣으면 순수 함수가 아니게 되고, 같은 입력에 다른 답이 나온다 — 테스트가
   시간에 묶이고 이 리포가 지킨 결정론이 깨진다.
2. "이상한가"와 "얼마나 지속됐나"는 다른 질문이다. 섞으면 둘 다 흐려진다.
3. 게이트는 **어차피 상태를 갖는다**(이미 연 케이스를 기억해야 중복을 막는다).
   연속 카운트를 거기 얹는 것은 새 부담이 아니다.

## 판정할 수 없는 것을 "이상 없음"으로 접지 않는다

가드(`only_when`)를 확인 못 하면 `unreachable`이다. "생산 중인지 모르는데 0/0/0"은
이상인지 **알 수 없다** — `ok`로 접으면 진짜 이상을 놓치고, `finding`으로 내면 거짓
알람이다. 셋 다 아닌 값이 필요하다.

`skipped`는 또 다르다: 확인했더니 생산 중이 **아니어서** 판정을 안 한 것이다.
못 한 것과 안 한 것을 같은 칸에 넣으면, 대상이 죽은 날이 "쉬는 날"로 보인다.
"""
import math
from typing import Any

from src.config.schema_patrol import CheckConfig, Guard, ItemsAllZeroParams, ProbeRef
from src.domain.base import Clock
from src.domain.patrol import CheckOutcome, Finding, ProbeSet


class _Missing:
    """"그 경로가 없다"와 "값이 None이다"를 가르는 표시.

    `None`을 부재의 표시로 쓰면 `{"status": null}`이 "필드가 없다"와 같아진다.
    전자는 대상이 말한 것이고 후자는 우리가 잘못 물은 것이다.
    """

    def __repr__(self) -> str:
        return "(없음)"


MISSING = _Missing()


def get_path(data: Any, dotted: str) -> Any:
    """`"response.status"`처럼 점 경로로 값을 꺼낸다. 없으면 `MISSING`."""
    if not dotted:
        return data
    current = data
    for part in dotted.split("."):
        if isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current[part]
        elif isinstance(current, list):
            if not part.lstrip("-").isdigit():
                return MISSING
            index = int(part)
            if not -len(current) <= index < len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def _numeric(value: Any) -> str | None:
    """개수로 쓸 수 있는 값인가. 문제가 있으면 사유를 돌려준다.

    bool을 막는 이유: 파이썬에서 `False == 0`이라 `{"alarm": false}`가 **"현장이
    멈췄다"로 둔갑한다.** NaN도 막는다 — 0과 비교하는 것 자체가 무의미하다.
    """
    if isinstance(value, bool):
        return f"bool이다 ({value!r}) — 0으로 세면 안 된다"
    if not isinstance(value, (int, float)):
        return f"수치가 아니다 ({value!r})"
    if not math.isfinite(value):
        return f"유한한 수가 아니다 ({value!r})"
    return None


def judge_items_all_zero(probes: ProbeSet, check: CheckConfig, *,
                         clock: Clock) -> CheckOutcome:
    params: ItemsAllZeroParams = check.params
    now = clock()

    def outcome(status: str, reason: str, findings=(), examined: int = 0) -> CheckOutcome:
        return CheckOutcome(check=probes.check, site=probes.site, concern=check.concern,
                            status=status, reason=reason, findings=list(findings),
                            examined=examined)

    def finding(target: str, reason: str, observed: dict | None = None) -> Finding:
        return Finding(check=probes.check, site=probes.site, concern=check.concern,
                       target=target, reason=reason, observed=observed or {},
                       observed_at=now)

    if probes.status == "unreachable":
        return outcome("unreachable", probes.reason())

    guard: Guard | None = params.only_when
    if guard is not None:
        where = f"{guard.probe}.{guard.path}" if guard.path else guard.probe
        value = get_path(probes.results[guard.probe].data, guard.path)
        if value is MISSING:
            # 가드를 **확인할 수 없다**. ok로 접으면 놓치고 finding이면 거짓 알람이다.
            return outcome("unreachable", f"가드를 확인할 수 없다 — {where}가 없다")
        if value != guard.equals:
            return outcome("skipped",
                           f"판정 안 함 — {where}가 {value!r}다 (기대: {guard.equals!r})")

    ref: ProbeRef = params.items
    where = f"{ref.probe}.{ref.path}" if ref.path else ref.probe
    items = get_path(probes.results[ref.probe].data, ref.path)
    if items is MISSING:
        return outcome("finding", f"{where} 경로가 응답에 없다",
                       [finding(where, "판정 대상 경로가 응답에 없다")])
    if not isinstance(items, list):
        # 응답 모양이 바뀐 것이다. 조용히 통과시키면 그날부터 감시가 죽는다.
        return outcome("finding", f"{where}가 리스트가 아니다 — {type(items).__name__}",
                       [finding(where, f"판정 대상이 리스트가 아니다 — {type(items).__name__}")])
    if not items:
        # "빠진 항목은 검사 대상이 아니다"가 이 점검의 전제다. 빈 것은 이상이 아니다.
        return outcome("ok", f"{where}에 판정할 항목이 없다")

    findings: list[Finding] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        label = f"{where}#{index}"
        if not isinstance(item, dict):
            findings.append(finding(label, f"항목이 dict가 아니다 — {type(item).__name__}"))
            continue

        missing_id = [f for f in params.identity if f not in item]
        if missing_id:
            findings.append(finding(label, f"식별 필드 부재 — {', '.join(missing_id)}"))
            continue
        target = "/".join(str(item[f]) for f in params.identity)
        if target in seen:
            # 덮어쓰면 항목 하나가 조용히 사라진다.
            findings.append(finding(
                target, f"식별자가 중복이다 — #{seen[target]}과 #{index}가 같다"))
            continue
        seen[target] = index

        observed = {f: item.get(f, MISSING) for f in params.counts}
        missing_counts = [f for f in params.counts if f not in item]
        if missing_counts:
            # `caution`을 `cuation`으로 적은 응답을 실제로 만났다. 조용히 건너뛰면
            # 남은 [0, 0]만 보고 "전부 0"이라 판정한다 — 없는 이상을 만들어 낸다.
            findings.append(finding(target, f"필드 부재 — {', '.join(missing_counts)}",
                                    {f: repr(v) for f, v in observed.items()}))
            continue

        problems = [f"{f}: {p}" for f in params.counts
                    if (p := _numeric(item[f])) is not None]
        if problems:
            findings.append(finding(target, f"수치가 아닌 값 — {'; '.join(problems)}",
                                    {f: repr(item[f]) for f in params.counts}))
            continue

        if all(item[f] == 0 for f in params.counts):
            findings.append(finding(
                target, f"전부 0 — {'·'.join(params.counts)}이 모두 0이다",
                {f: item[f] for f in params.counts}))

    if findings:
        return outcome("finding", f"{len(items)}개 중 {len(findings)}건",
                       findings, examined=len(items))
    return outcome("ok", f"이상 없음 ({len(items)}개 항목)", examined=len(items))


RULES = {"items_all_zero": judge_items_all_zero}


def judge(probes: ProbeSet, check: CheckConfig, *, clock: Clock) -> CheckOutcome:
    """rule 이름으로 판정기를 고른다. **이름은 스키마가 이미 검증했다**(Literal)."""
    return RULES[check.rule](probes, check, clock=clock)
