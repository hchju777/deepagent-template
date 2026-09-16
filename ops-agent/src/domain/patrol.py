"""순찰이 한 번 훑은 결과.

## 왜 "못 읽었다"를 따로 말하는가

순찰의 산출물은 평온할 때 **"아무 일도 없음"**이다. 그런데 대상에 못 붙었을 때의
산출물도 같은 모양이다 — 둘 다 조용하다. 밤새 케이스가 하나도 안 열렸을 때 그게
"평온했다"인지 "구미에 아예 못 붙었다"인지 구별할 방법이 없다.

사이트가 28개라 이건 예외가 아니라 **일상 경로**다. 하루에 하나쯤은 안 붙는다.
그래서 `ok`와 대비되는 값이 `finding` 하나가 아니라 `unreachable`이 하나 더 있다.
"""
from datetime import datetime
from typing import Any, Literal

from src.domain.base import StrictModel
from src.domain.concern import Concern
from src.domain.envelope import ProbeResult


class ProbeSet(StrictModel):
    """점검 하나가 선언한 프로브들을 **동시에** 읽은 결과.

    동시에 읽는 이유는 as_of 정렬이다. `summary_badge`와 `prod_status`를 읽는 시점이
    벌어지면 "생산 중이었는데 그 사이에 멈춤"이 "생산 중인데 0/0/0"으로 보인다.
    """

    check: str
    site: str
    results: dict[str, ProbeResult] = {}

    @property
    def failed(self) -> list[str]:
        """실패한 프로브 이름들. **하나라도 있으면 판정할 수 없다**(전부-또는-전무)."""
        return sorted(name for name, r in self.results.items() if r.status == "error")

    @property
    def status(self) -> Literal["ok", "unreachable"]:
        return "unreachable" if self.failed else "ok"

    def reason(self) -> str:
        if not self.failed:
            return f"{len(self.results)}개 프로브 전부 읽었다"
        detail = "; ".join(f"{name}: {self.results[name].error}" for name in self.failed)
        return f"못 읽은 프로브 — {detail}"


class Finding(StrictModel):
    """이상 하나. **항목 하나에 하나다.**

    badge 세 개가 0/0/0이면 finding도 셋이다. 하나로 묶지 않는 이유가 셋 있다:

    - **조사가 찍을 데를 갖는다.** "어딘가 0/0/0"이면 조사의 대상이 빈다.
    - **6단계가 대상별로 연속을 센다.** 묶으면 "A는 3회째, B는 1회째"를 못 가른다.
    - **중복 케이스 방지도 대상 단위**여야 맞다.
    """

    check: str
    site: str
    concern: Concern
    # 무엇이 이상한가 — identity 필드를 이어 붙인 것("Line/Target Rate").
    target: str
    reason: str
    # 판정의 근거가 된 값. 보고서와 케이스 설명이 이걸 그대로 쓴다.
    observed: dict[str, Any] = {}
    observed_at: datetime


class CheckOutcome(StrictModel):
    """점검 하나를 판정한 결과.

    `ok`와 대비되는 값이 `finding` 하나가 아니라 **`unreachable`이 하나 더 있다.**
    못 읽은 것을 `ok`로 접으면 감시가 자기 실패를 숨긴다 — 사이트가 28개라 하루에
    하나쯤은 안 붙고, 그건 예외가 아니라 일상 경로다.

    `skipped`는 판정을 **안 한** 것이다(생산 중이 아니라서). 못 한 것과 다르다.
    """

    check: str
    site: str
    concern: Concern
    status: Literal["ok", "finding", "skipped", "unreachable"]
    reason: str
    findings: list[Finding] = []
    # 판정 대상이 몇 개였는가. "이상 없음"이 0개를 본 결과인지 12개를 본 결과인지
    # 구별되어야 한다 — 전자는 사실 아무것도 확인 못 한 것이다.
    examined: int = 0
