"""운영 리포트 시나리오 선언 — 무엇을 어디서 어느 기간으로 읽는가.

## 왜 사이트 config가 아니라 별도 파일인가

리포트는 **사이트를 가로지른다.** 사이트 계층(`fct/gumi/mx.json`)에 두면 사이트마다
잡이 등록돼 **같은 리포트가 N번 돌고 메일도 N통** 간다. 사이트가 리포트에 대해
말할 수 있는 것은 켜고 끄는 것뿐이다.

## 왜 컬렉션 이름과 필드 이름까지 config인가

`alarm`을 코드에 박으면 다른 컬렉션으로 같은 리포트를 낼 수 없고, 필드 이름이
법인마다 다를 때 손댈 곳이 코드가 된다. **코드는 "어떻게 집계하는가"만 알고,
"무엇을 읽는가"는 config가 안다.**
"""
from typing import Literal

from pydantic import Field, model_validator

from src.domain.base import StrictModel

# Python의 weekday(): 월=0 … 토=5, 일=6
_WEEKDAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class FieldMap(StrictModel):
    """문서의 필드 이름. 기본값은 사내 `alarm` 컬렉션 기준이지만 전부 바꿀 수 있다."""
    gbm: str = "gbm"
    plant: str = "plant"
    part_code: str = "part_code"
    line_code: str = "line_code"
    line_name: str = "line_name"
    scenario_id: str = "scen_id"
    scenario_name: str = "scen_name"
    status: str = "status"


class SourceSpec(StrictModel):
    collection: str = Field(min_length=1)
    date_field: str = Field(min_length=1)
    # `occ_date`는 datetime이 아니라 **문자열**이다. 그래서 범위 비교가
    # 사전순으로 일어나고, 형식이 고정폭·큰자리우선이 아니면 조용히 틀린 구간을
    # 읽는다(`%d/%m/%Y`면 "01/12/2025" < "02/01/2026"이 사전순으로 참이 아니다).
    # `window.py`의 `date_format_problem`이 기동에서 그것을 막는다.
    date_format: str = "%Y-%m-%d %H:%M:%S"
    fields: FieldMap = FieldMap()
    # 미해제로 보는 status 값. 사내 규약: 0 발생 · 10 접수 · 1 조치시작.
    # 2(조치완료 수동)와 40(조치완료 자동)이 해제다.
    unresolved_status: list[int] = [0, 10, 1]
    # 법인 하나에서 읽어 올 문서 수 상한. 하한이 있는 이유: 0·음수는 pymongo에서
    # "무제한"이고, 리포트는 법인 N개로 팬아웃하므로 그 한 줄이 N개 법인에
    # 동시에 무제한 커서를 연다.
    sample: int = Field(default=50_000, ge=1)

    @model_validator(mode="after")
    def _status_values_are_distinct(self):
        if len(set(self.unresolved_status)) != len(self.unresolved_status):
            raise ValueError("unresolved_status에 중복이 있다")
        return self


class WindowSpec(StrictModel):
    # 어제(직전 평일)를 포함해 과거로 세는 평일 수.
    business_days: int = Field(default=7, ge=1, le=60)
    # 전주 동요일과 비교할 것인가. 데이터가 보존 기간(TTL) 밖이면 그 칸만 비고,
    # 별도 경고는 내지 않는다 — 없는 것이 정상인 상황이기 때문이다.
    compare_previous_week: bool = True
    # 제외할 요일. 사내 휴일 달력이 없으므로 주말만 뺀다.
    exclude_weekdays: list[str] = ["sat", "sun"]

    @model_validator(mode="after")
    def _weekday_names_are_known(self):
        unknown = [d for d in self.exclude_weekdays if d.lower() not in _WEEKDAY_NAMES]
        if unknown:
            raise ValueError(f"모르는 요일 이름 — {', '.join(unknown)}. "
                             f"쓸 수 있는 것: {', '.join(_WEEKDAY_NAMES)}")
        if len(set(d.lower() for d in self.exclude_weekdays)) == 7:
            raise ValueError("모든 요일을 제외하면 조회할 날이 없다")
        return self

    def excluded(self) -> set[int]:
        return {_WEEKDAY_NAMES[d.lower()] for d in self.exclude_weekdays}


class ReportScope(StrictModel):
    # GBM 목록과 각 GBM의 대상 법인은 **사람이 적는다.** registry에서 유추하면
    # 새 사이트를 등록하는 순간 리포트 분모가 조용히 바뀐다.
    gbms: list[str] = Field(min_length=1)
    sites: list[str] = Field(min_length=1)      # "gbm/fct"
    max_parallel_sites: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def _sites_are_well_formed(self):
        for site in self.sites:
            if site.count("/") != 1 or not all(site.split("/")):
                raise ValueError(f"사이트는 'gbm/fct' 형식이다 — {site!r}")
            gbm = site.split("/")[0]
            if gbm not in self.gbms:
                raise ValueError(f"{site}의 GBM({gbm})이 gbms 목록에 없다")
        if len(set(self.sites)) != len(self.sites):
            raise ValueError("sites에 중복이 있다")
        # 법인이 하나도 없는 GBM은 리포트에 **빈 열**로 남는다. 그 빈 칸은
        # "알람이 0건"과 "대상이 아님"을 구분해 주지 않아서, 관리자가 GBM 간
        # 비교를 할 때 조용히 잘못 읽는다.
        empty = [g for g in self.gbms
                 if not any(s.split("/")[0] == g for s in self.sites)]
        if empty:
            raise ValueError(f"대상 법인이 하나도 없는 GBM — {', '.join(empty)}")
        return self

    def sites_of(self, gbm: str) -> list[str]:
        return [s.split("/")[1] for s in self.sites if s.split("/")[0] == gbm]


class ReportScenario(StrictModel):
    kind: Literal["alarm_daily"]     # 향후 종류 확장의 discriminator
    title: str = Field(min_length=1)
    enabled: bool = True
    source: SourceSpec
    window: WindowSpec = WindowSpec()
    scope: ReportScope
