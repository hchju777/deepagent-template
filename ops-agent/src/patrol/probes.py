"""점검이 선언한 프로브들을 실제로 읽는다. **판정은 하지 않는다**(5단계).

## 무raise

순찰 잡 하나가 raise하면 스케줄러가 그 잡을 조용히 스케줄에서 빼버릴 수 있다.
그러면 순찰이 스스로 죽어도 아무도 모르는 상태가 된다 — 밤새 케이스가 안 열리는데
그게 "이상이 없어서"인지 "순찰이 죽어서"인지 구별할 수 없다.

읽기 자체는 `domain/actions.py`가 이미 무raise다. 여기서 또 감싸는 이유는 계약을
어기는 구현 하나가 사이트 전체의 순찰을 지우면 안 되기 때문이다.
"""
import asyncio

from src.config.schema_patrol import CheckConfig
from src.domain.actions import run_action
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.patrol import ProbeSet


async def run_probes(name: str, check: CheckConfig, *, adapters, site: str,
                     clock: Clock) -> ProbeSet:
    """점검 하나의 프로브를 **동시에** 읽는다.

    동시에 읽는 이유는 as_of 정렬이다 — `ProbeSet`의 설명 참고. 프로브가 둘뿐이라
    성능 얘기가 아니다.
    """
    names = list(check.probes)

    async def one(probe_name: str) -> ProbeResult:
        spec = check.probes[probe_name]
        try:
            return await run_action(adapters, spec.action, spec.params, clock=clock)
        except Exception as exc:                                    # noqa: BLE001
            return ProbeResult.failed(f"예상 밖 오류 — {type(exc).__name__}: {exc}",
                                      source=f"{probe_name}:{spec.action}", clock=clock)

    results = await asyncio.gather(*(one(n) for n in names))
    return ProbeSet(check=name, site=site, results=dict(zip(names, results)))
