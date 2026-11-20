"""법인별로 한 번 긁어 온다. **실패한 법인도 결과로 돌아온다.**

## 왜 실패를 예외로 올리지 않는가

법인 하나가 방화벽에 막히는 것은 예상 밖의 일이 아니다 — 사내에서 **정상적으로
일어나는 일**이다. 여기서 raise하면 그 한 법인 때문에 리포트가 아예 안 나가고,
나머지 법인의 숫자도 아무도 못 본다.

그래서 실패는 `SiteOutcome(status="error")`로 흡수하고, 리포트 하단이 그 법인을
**이름으로** 남긴다. 조용히 빠지면 "전사 합계"가 말 없이 줄고, 관리자는 그걸
"알람이 줄었다"로 읽는다 — 그게 이 시스템이 낼 수 있는 최악의 거짓말이다.

## 왜 한 법인에 쿼리 한 번인가

섹션마다 쿼리를 날리면 대상 Mongo 왕복이 섹션 수만큼 늘고, 섹션들이 **서로 다른
순간**의 데이터를 본다. 한 번 긁어서 `Facts` 한 덩어리로 들고 있으면 모든 섹션이
같은 스냅샷을 본다.

## 동시 실행 상한

`scope.max_parallel_sites`가 세마포어가 된다. 법인 20개에 동시에 붙으면 우리가
읽기 전용이어도 대상 쪽 커넥션과 디스크를 그만큼 동시에 쓴다 — "성능에 개입하지
않는다"가 깨지는 지점이다.
"""
import asyncio
from pathlib import Path
from typing import Any

from src.config.loader import load_registry, load_site_config
from src.config.schema_report import ReportScenario
from src.domain.base import Clock
from src.report.facts import Facts, SiteOutcome
from src.report.rows import normalize, projection
from src.report.window import ReportWindow, date_filter


async def collect(scenario: ReportScenario, *, config_root: Path, env: dict[str, str],
                  window: ReportWindow, clock: Clock,
                  seeds: dict[str, Any] | None = None) -> Facts:
    """시나리오가 지정한 법인 전부를 읽어 `Facts`를 만든다.

    `seeds`를 주면 스텁 어댑터를 쓴다(factory가 유일한 전환점이다). 대상에 붙지
    않고 집계·렌더링을 끝까지 돌려 볼 수 있어야, 사내에서 처음 돌릴 때 "숫자가
    이상한 것"과 "못 붙은 것"을 구별할 수 있다.
    """
    disabled = _disabled_sites(config_root)
    seeds_for = _seed_picker(seeds)
    semaphore = asyncio.Semaphore(scenario.scope.max_parallel_sites)

    async def one(site: str) -> tuple[SiteOutcome, list]:
        async with semaphore:
            return await _read_site(site, scenario=scenario, config_root=config_root,
                                    env=env, window=window, clock=clock,
                                    seeds=seeds_for(site), disabled=disabled)

    results = await asyncio.gather(*(one(site) for site in scenario.scope.sites))

    rows = [row for _, site_rows in results for row in site_rows]
    # 정렬해 두는 이유: 어디선가 "첫 행"을 보는 코드가 생겼을 때 그 값이 Mongo의
    # 자연 순서(삽입 순서)에 따라 달라지지 않게 한다. 집계 자체는 순서에 무관하다.
    rows.sort(key=lambda r: (r.gbm, r.fct, r.occurred_at, r.line_code, r.scenario_id))
    return Facts(window=window, source=scenario.source, thresholds=scenario.thresholds,
                 rows=tuple(rows), sites=tuple(outcome for outcome, _ in results),
                 gbms=tuple(scenario.scope.gbms))


def _seed_picker(seeds: dict[str, Any] | None):
    """가짜 데이터를 사이트별로 나눠 줄지, 전부에게 같은 것을 줄지.

    최상위 키가 전부 `gbm/fct` 모양이면 사이트별로 본다. 그렇지 않으면 어댑터
    묶음 하나로 보고 모든 사이트에 같은 것을 준다.

    사이트별을 지원하는 이유: 법인마다 다른 데이터로 돌려 봐야 "법인별 비교"
    섹션이 실제로 다른 숫자를 내는지 확인할 수 있다. 전부 같은 데이터면 그
    섹션이 고장 나 있어도 눈에 안 띈다.
    """
    if not seeds:
        return lambda _site: None
    if all("/" in key and key.count("/") == 1 for key in seeds):
        return lambda site: seeds.get(site, {})
    return lambda _site: seeds


def _disabled_sites(config_root: Path) -> dict[str, str]:
    """registry에서 꺼져 있는 사이트 → 이유.

    기동 검증은 이것을 오류로 보지 않는다("지금 못 붙는다"는 정상 상태). 대신
    리포트가 제외 목록에 이름으로 남긴다.
    """
    try:
        registry = load_registry(config_root)
    except Exception:                                              # noqa: BLE001
        return {}          # registry 문제는 기동 검증이 이미 보고했다
    return {str(e): "registry.json에서 enabled=false"
            for e in registry.sites if not e.enabled}


async def _read_site(site: str, *, scenario: ReportScenario, config_root: Path,
                     env: dict[str, str], window: ReportWindow, clock: Clock,
                     seeds: dict[str, Any] | None,
                     disabled: dict[str, str]) -> tuple[SiteOutcome, list]:
    gbm, fct = site.split("/", 1)

    if site in disabled:
        return SiteOutcome(gbm=gbm, fct=fct, status="skipped",
                           reason=disabled[site]), []

    # 최외곽 방어선. 설정 오류·어댑터 조립 실패·예상 밖 예외까지 여기서 멈춘다 —
    # 한 법인의 문제가 나머지 법인의 숫자를 가려선 안 된다.
    try:
        config, _ = load_site_config(config_root, gbm, fct, env=env)
    except Exception as exc:                                       # noqa: BLE001
        return SiteOutcome(gbm=gbm, fct=fct, status="error",
                           error=f"설정을 읽을 수 없다 — {type(exc).__name__}: {exc}"), []

    if config.infra.mongodb is None:
        return SiteOutcome(gbm=gbm, fct=fct, status="skipped",
                           reason="이 사이트에 mongodb 설정이 없다"), []

    from src.infrastructure.factory import build_adapters

    adapters = None
    try:
        adapters = build_adapters(config, clock=clock, seeds=seeds)
        if adapters.mongo is None:
            return SiteOutcome(gbm=gbm, fct=fct, status="skipped",
                               reason="mongo 어댑터가 조립되지 않았다"), []
        result = await adapters.mongo.find(
            scenario.source.collection,
            date_filter(scenario.source, window),
            limit=scenario.source.sample,
            projection=projection(scenario.source))
    except Exception as exc:                                       # noqa: BLE001
        return SiteOutcome(gbm=gbm, fct=fct, status="error",
                           error=f"{type(exc).__name__}: {exc}"), []
    finally:
        if adapters is not None:
            await adapters.close()

    if result.status == "error":
        return SiteOutcome(gbm=gbm, fct=fct, status="error", error=result.error,
                           source=result.source), []

    documents = result.data if isinstance(result.data, list) else []
    rows, problems = normalize(documents, source=scenario.source, window=window,
                              gbm=gbm, fct=fct)
    return SiteOutcome(
        gbm=gbm, fct=fct, status="ok",
        fetched=len(documents), kept=len(rows),
        complete=result.envelope.complete,
        truncated_reason=result.envelope.truncated_reason,
        source=result.source, problems=problems), rows
