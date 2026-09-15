"""순찰이 한 번 훑은 결과.

## 왜 "못 읽었다"를 따로 말하는가

순찰의 산출물은 평온할 때 **"아무 일도 없음"**이다. 그런데 대상에 못 붙었을 때의
산출물도 같은 모양이다 — 둘 다 조용하다. 밤새 케이스가 하나도 안 열렸을 때 그게
"평온했다"인지 "구미에 아예 못 붙었다"인지 구별할 방법이 없다.

사이트가 28개라 이건 예외가 아니라 **일상 경로**다. 하루에 하나쯤은 안 붙는다.
그래서 `ok`와 대비되는 값이 `finding` 하나가 아니라 `unreachable`이 하나 더 있다.
"""
from typing import Literal

from src.domain.base import StrictModel
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
