"""리포트 테스트의 공용 재료 — 고정 날짜, 문서 만들기, 가짜 Mongo, 픽스처.

## 왜 conftest가 아니라 보통 모듈인가

픽스처는 conftest에 있으면 같은 디렉터리 아래에서 자동으로 보이지만 **디렉터리를
넘지 않는다.** 그래서 `tests/presentation`이 `tests/report`의 픽스처를 쓰려면
import가 필요하고, 그 import를 상대 경로(`from ..report.conftest import`)로 쓰면
`tests/__init__.py` 하나가 없는 환경에서

    ImportError: attempted relative import beyond top-level package

로 **테스트 수집 자체가 실패한다**(실제로 사내에서 났다. `__init__.py`가 있는
디렉터리까지 거슬러 올라가 패키지 이름을 정하는 pytest의 규칙 때문이고, 중간에
하나라도 빠지면 `..`가 최상위를 넘는다).

보통 모듈 + **절대 import**는 그 조건에 걸리지 않는다 — `ops-agent/`가 sys.path에
있으면 `tests`는 암묵적 namespace package로도 import되기 때문이다. 그리고 그건
모든 테스트가 이미 `from src...`로 쓰고 있는 것과 같은 전제다.

`tests/test_portability.py`가 테스트 트리에 상대 import가 다시 생기지 않는지 지킨다.
"""
from datetime import date, datetime

import pytest

from src.config.schema_report import ReportScenario, SourceSpec, Thresholds, WindowSpec
from src.domain.envelope import ProbeResult
from src.domain.ports import MongoReaderPort
from src.report.window import build_window

TODAY = date(2026, 9, 7)          # 월요일 — 어제가 금요일(2026-09-04)이 되도록
YESTERDAY = date(2026, 9, 4)


@pytest.fixture
def clock():
    return lambda: datetime(2026, 9, 7, 8, 0, 0)


@pytest.fixture
def source() -> SourceSpec:
    return SourceSpec(collection="alarm", date_field="occ_date")


@pytest.fixture
def window(source):
    return build_window(WindowSpec(), today=TODAY)


def doc(day: date, *, hour: int = 9, plant: str = "gumi", gbm: str = "mx",
        line: str = "P222", line_name: str = "조립2라인", scen: str = "S01",
        scen_name: str = "재고 불일치", status: int = 0, **extra) -> dict:
    body = {"occ_date": datetime(day.year, day.month, day.day, hour).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "gbm": gbm, "plant": plant, "part_code": "PN100",
            "line_code": line, "line_name": line_name,
            "scen_id": scen, "scen_name": scen_name, "status": status}
    body.update(extra)
    return body


class FakeMongo(MongoReaderPort):
    """호출부가 **무엇을 물었는지** 기록하는 가짜.

    `StubMongoReader`를 쓰지 않는 이유: 스텁은 대상 시스템을 흉내내는 물건이고,
    여기서 보고 싶은 것은 "우리가 무엇을 물었는가"다. 필터가 정확히 나가는지를
    스텁의 질의 흉내를 통과한 **결과로** 확인하면, 흉내가 틀릴 때 테스트가
    거짓 초록을 낸다.
    """

    def __init__(self, documents=None, *, fail: str | None = None,
                 truncated: str | None = None, clock=None):
        self.documents = documents or []
        self.fail = fail
        self.truncated = truncated
        self.calls: list[dict] = []
        self._clock = clock or (lambda: datetime(2026, 9, 7, 8, 0, 0))

    async def find(self, collection, filter, *, sort=None, limit=None, projection=None):
        self.calls.append({"collection": collection, "filter": filter,
                           "limit": limit, "projection": projection})
        source = f"fake-mongo:{collection}"
        if self.fail:
            return ProbeResult.failed(self.fail, source=source, clock=self._clock)
        rows = self.documents
        if projection:
            keep = set(projection)
            rows = [{k: v for k, v in row.items() if k in keep} for row in rows]
        return ProbeResult.succeeded(rows, source=source, clock=self._clock,
                                     truncated_reason=self.truncated)

    async def count(self, collection, filter):
        return ProbeResult.succeeded(len(self.documents), source="fake-mongo",
                                     clock=self._clock)


def scenario(**overrides) -> ReportScenario:
    body = {"kind": "alarm_daily", "title": "일일 알람 리포트",
            "source": {"collection": "alarm", "date_field": "occ_date"},
            "scope": {"gbms": ["mx"], "sites": ["mx/gumi", "mx/sevt"]}}
    body.update(overrides)
    return ReportScenario.model_validate(body)


def facts_from(rows, *, window, source, thresholds=None, sites=()):
    from src.report.facts import Facts, SiteOutcome
    if not sites:
        sites = (SiteOutcome(gbm="mx", fct="gumi", status="ok", kept=len(rows)),)
    return Facts(window=window, source=source,
                 thresholds=thresholds or Thresholds(),
                 rows=tuple(rows), sites=tuple(sites))
