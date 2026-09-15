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


class CheckConfig(StrictModel):
    enabled: bool = True
    # **기본값을 두지 않는다.** 0/0/0은 operation인데 기본값이 있으면 안 적었을 때
    # 조용히 system으로 인프라팀에 간다. 뜻은 domain/concern.py — 원인이 아니라
    # "먼저 물어볼 곳"이다.
    concern: Concern
    probes: dict[str, ProbeSpec] = Field(min_length=1)


class PatrolConfig(StrictModel):
    checks: dict[str, CheckConfig] = {}

    def active(self) -> dict[str, CheckConfig]:
        return {name: check for name, check in self.checks.items() if check.enabled}


__all__ = ["ACTIONS", "CheckConfig", "PatrolConfig", "ProbeSpec"]
