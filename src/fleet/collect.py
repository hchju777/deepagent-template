"""사이트 하나에서 지표 표본 하나 — 기존 프로브를 그대로 쓴다(계획 16/P7).

새 포트도, 새 프로브도 만들지 않는다(규율 9): `MetricSpec`이 프로브가 읽는 계약
(target·probe·params·sample·resolve)을 그대로 만족하므로 어댑터 객체조차 없다.

**절대 raise하지 않는다.** 사이트 하나의 실패는 그 사이트의 커버리지 항목이지 집계
전체의 죽음이 아니다 — 30개 중 하나가 죽었다고 나머지 29개를 못 보면, 그 순간
"집계가 안 돌았다"와 "3개 법인이 누락됐다"를 구별할 수 없게 된다.
"""
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from src.config.schema_app import StrictModel
from src.fleet.reduce import extract
from src.patrol.probes import PROBES, resolve_probe

_WINDOW_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class SiteSample(StrictModel):
    gbm: str
    fct: str
    values: list[float] = []
    skipped: int = 0                  # 숫자가 아니라 못 센 항목 — 조용히 버리지 않는다
    status: Literal["covered", "missing", "fallback"]
    reason: str | None = None
    effective_as_of: datetime | None = None


def _window_seconds(window: str | None) -> float | None:
    if not isinstance(window, str) or len(window) < 2 or window[-1] not in _WINDUNITS_KEYS:
        return None
    try:
        return float(window[:-1]) * _WINDOW_UNITS[window[-1]]
    except ValueError:
        return None


_WINDUNITS_KEYS = set(_WINDOW_UNITS)


async def collect_site(spec, *, gbm: str, fct: str, adapters: Any,
                       clock: Callable[[], datetime], timezone_name: str) -> SiteSample:
    def missing(reason: str) -> SiteSample:
        return SiteSample(gbm=gbm, fct=fct, status="missing", reason=reason)

    try:
        probe_name = resolve_probe(spec)
        if probe_name is None or probe_name not in PROBES:
            return missing("프로브 해석 불가")
        result = await PROBES[probe_name](adapters, spec, clock=clock,
                                          timezone_name=timezone_name)
        if result.status == "error":
            return missing(result.error or "프로브 실행 실패")
        values, skipped = extract(result.data, spec.extract)
        effective = result.envelope.effective_as_of
        # 값은 왔는데 대상이 창보다 오래된 것을 돌려줬다 — 숫자에는 넣되 보고서가 그
        # 사실을 적는다. 조용히 섞으면 "어제 값으로 오늘을 말한다"가 된다.
        seconds = _window_seconds(spec.window)
        if effective is not None and seconds is not None \
                and effective < clock() - timedelta(seconds=seconds):
            return SiteSample(gbm=gbm, fct=fct, values=values, skipped=skipped,
                              status="fallback", effective_as_of=effective,
                              reason=f"effective_as_of가 요청 창보다 이르다 ({effective.isoformat()})")
        if not result.envelope.complete:
            return SiteSample(gbm=gbm, fct=fct, values=values, skipped=skipped,
                              status="fallback", effective_as_of=effective,
                              reason=result.envelope.truncated_reason or "불완전한 응답")
        return SiteSample(gbm=gbm, fct=fct, values=values, skipped=skipped,
                          status="covered", effective_as_of=effective)
    except Exception as exc:                                       # noqa: BLE001 — 무raise
        return missing(f"{type(exc).__name__}: {exc}")
