"""순찰 점검 선언 — **무엇을 읽는가**.

## 점검 하나가 프로브 여러 개를 묶는다

원본 템플릿은 점검 하나에 프로브 하나였다. 우리는 묶는다. 이유는 구체적이다 —
"In Production일 때만 0/0/0이 이상"을 판정하려면 **두 응답**이 필요하다
(`summary_badge`와 `prod_status`). 원본의 `expected_state` rule은 *한 응답 안의 두
필드*를 보는 것이라 이 모양에 안 맞는다.

## 프로브에 이름을 주는 이유

rule이 `"items": "badge"`처럼 **이름으로** 가리킨다. 순서나 인덱스로 가리키면 프로브를
하나 끼워 넣을 때 조용히 어긋나고, 그 어긋남은 "판정이 이상하다"로만 드러난다.

## 왜 사이트 config인가

점검 정의는 `gbm/common.json`에 한 번 쓰고 사이트마다 다른 것(`params`의 line_code 등)만
아래 층이 덮는다. 28개 사이트에 같은 임계값을 28번 적으면 하나 고칠 때 27개가 남는다.
`app.json`이 아닌 이유도 그것이다 — 점검은 사이트마다 달라질 수 있다.
"""
from typing import Literal

from pydantic import Field, model_validator

from src.domain.actions import ACTIONS, action_problem
from src.domain.base import StrictModel
from src.domain.concern import Concern


class ProbeSpec(StrictModel):
    """점검이 읽는 것 하나.

    `action`은 `domain/actions.py`의 등재 목록에서만 고른다 — 순찰과 조사가 같은 표를
    쓴다(규율 9). `rest.query`의 params가 `{"entry": ..., "params": {...}}`로 한 겹 더
    들어가는 것은 포트 시그니처 그대로다.
    """

    action: str
    params: dict = {}

    @model_validator(mode="after")
    def _action_is_registered(self):
        # 기동이 아니라 **로드 시점**에 막는다. 오타난 action은 런타임에 매 순찰마다
        # 실패하는데, 그 실패는 "대상이 안 붙는다"처럼 보여 원인을 가린다.
        problem = action_problem(self.action, self.params)
        if problem is not None:
            raise ValueError(problem)
        return self


class ProbeRef(StrictModel):
    """판정이 볼 프로브와, 그 결과 안의 점 경로.

    **경로를 config가 명시한다.** REST 어댑터는 응답을 `{"request": ..., "status": 200,
    "response": <실제>}`로 감싸는데, mongo나 redis 프로브는 모양이 또 다르다. rule이
    "REST면 알아서 벗긴다"를 하면 프로브 종류마다 특례가 생기고, 그 특례는 어댑터가
    응답 모양을 바꾸는 날 조용히 틀린다.
    """

    probe: str
    path: str = ""            # 비우면 `result.data` 전체


class Guard(StrictModel):
    """이 값일 때만 판정한다 — 아니면 판정 자체를 안 한다."""

    probe: str
    path: str = ""
    # 문자열 비교만 한다. 숫자·bool이 필요해지면 그때 연다 — 지금 열어 두면
    # `False == 0` 같은 비교가 조용히 통과하는 길이 생긴다.
    equals: str


class ItemsAllZeroParams(StrictModel):
    """리스트의 항목마다 "지정한 개수 필드가 전부 0인가"를 본다."""

    items: ProbeRef
    # 항목을 무엇으로 식별하는가. 여럿이면 이어 붙인다("Line/Target Rate").
    identity: list[str] = Field(min_length=1)
    # 0인지 볼 필드들. **여기 적었는데 응답에 없으면 finding이다** — 조용히 건너뛰면
    # `caution`을 `cuation`으로 적은 응답에서 [0, 0]만 보고 "전부 0"이라 판정한다.
    counts: list[str] = Field(min_length=1)
    only_when: Guard | None = None


class CheckConfig(StrictModel):
    enabled: bool = True
    # **기본값을 두지 않는다.** 0/0/0은 operation인데 기본값이 있으면 안 적었을 때
    # 조용히 system으로 인프라팀에 간다. 뜻은 domain/concern.py — 원인이 아니라
    # "먼저 물어볼 곳"이다.
    concern: Concern
    probes: dict[str, ProbeSpec] = Field(min_length=1)
    # rule이 하나뿐이라 지금은 Literal이다. 둘째가 생기면 판별 유니온으로 바꾼다 —
    # `rule: str` + `params: dict`로 두면 params가 검증을 안 타고, 오타가 런타임까지 간다.
    rule: Literal["items_all_zero"]
    params: ItemsAllZeroParams

    @model_validator(mode="after")
    def _rule_points_at_declared_probes(self):
        """`"items": {"probe": "badges"}`처럼 이름이 틀리면 **로드 시점에** 막는다.

        런타임에 두면 매 순찰마다 실패하는데, 그 실패는 `unreachable`로 흡수되어
        "대상이 안 붙는다"처럼 보인다. 28사이트에서는 그런 줄 하나가 묻힌다.
        기동(boot)이 아니라 여기인 이유: config를 읽는 **모든 경로**가 이 검사를 탄다.
        """
        declared = set(self.probes)
        refs = [("params.items", self.params.items.probe)]
        if self.params.only_when is not None:
            refs.append(("params.only_when", self.params.only_when.probe))
        for where, probe in refs:
            if probe not in declared:
                raise ValueError(
                    f"{where}가 선언되지 않은 프로브를 가리킨다 — {probe!r} "
                    f"(선언된 것: {', '.join(sorted(declared))})")
        return self


class PatrolConfig(StrictModel):
    checks: dict[str, CheckConfig] = {}

    def active(self) -> dict[str, CheckConfig]:
        return {name: check for name, check in self.checks.items() if check.enabled}


__all__ = ["ACTIONS", "CheckConfig", "Guard", "ItemsAllZeroParams",
           "PatrolConfig", "ProbeRef", "ProbeSpec"]
