"""점검을 실제로 돌린다 — 읽고, 판정하고, **한 사이트의 장애가 나머지를 안 끈다.**

## 격리가 이 파일의 존재 이유다

사이트가 28개다. 구미 Mongo가 안 붙는다고 SEVT 순찰이 멈추면 **장애 하나가 감시
전체를 끈다.** 그리고 그 상태는 조용하다 — 케이스가 안 열리는 것이 "이상이 없어서"와
구별되지 않는다.

그래서 세 겹으로 막는다:

1. 읽기(`domain/actions.py`)가 무raise
2. 프로브 묶음(`patrol/probes.py`)이 무raise
3. **여기가 사이트 단위로 무raise** — 어댑터를 만드는 것부터가 던질 수 있다
   (config는 통과했는데 호스트 이름이 안 풀리는 경우 등)

3번이 없으면 1·2번이 아무리 견고해도 `build_adapters` 한 줄에서 전부 죽는다.
"""
from src.config.schema_patrol import CheckConfig
from src.domain.base import Clock
from src.domain.patrol import CheckOutcome
from src.patrol.probes import run_probes
from src.patrol.rules import judge


async def run_check(name: str, check: CheckConfig, *, adapters, site: str,
                    clock: Clock) -> CheckOutcome:
    """점검 하나 — 읽고 판정한다."""
    probes = await run_probes(name, check, adapters=adapters, site=site, clock=clock)
    return judge(probes, check, clock=clock)


async def run_site(site_config, *, adapters, clock: Clock,
                   only: str | None = None) -> list[CheckOutcome]:
    """사이트 하나의 활성 점검을 전부 돈다."""
    checks = site_config.patrol.active()
    if only is not None:
        checks = {only: site_config.patrol.checks[only]} if only in site_config.patrol.checks else {}
    return [await run_check(name, check, adapters=adapters,
                            site=str(site_config.site), clock=clock)
            for name, check in checks.items()]


async def run_sites(entries, *, load, build, clock: Clock,
                    only: str | None = None) -> list[CheckOutcome]:
    """사이트마다 격리해서 돈다.

    `load`와 `build`를 인자로 받는 이유: 이 모듈이 config 로더와 어댑터 팩토리를
    직접 알면 테스트가 실제 config 트리를 요구하게 된다. **격리가 되는지**를 보려면
    "터지는 사이트"를 만들 수 있어야 하고, 그건 주입으로만 된다.
    """
    outcomes: list[CheckOutcome] = []
    for entry in entries:
        site = str(entry)
        try:
            site_config = load(entry)
            adapters = build(site_config)
        except Exception as exc:                                    # noqa: BLE001
            # 이 사이트는 못 본다. **그 사실을 남기고 다음 사이트로 간다** —
            # 조용히 건너뛰면 28개 중 하나가 빠진 것을 아무도 모른다.
            outcomes.append(CheckOutcome(
                check="(사이트)", site=site, concern="system", status="unreachable",
                reason=f"사이트를 열 수 없다 — {type(exc).__name__}: {exc}"))
            continue
        try:
            outcomes += await run_site(site_config, adapters=adapters, clock=clock,
                                       only=only)
        except Exception as exc:                                    # noqa: BLE001
            outcomes.append(CheckOutcome(
                check="(사이트)", site=site, concern="system", status="unreachable",
                reason=f"점검 중 예상 밖 오류 — {type(exc).__name__}: {exc}"))
        finally:
            try:
                await adapters.close()
            except Exception:                                       # noqa: BLE001
                pass
    return outcomes
