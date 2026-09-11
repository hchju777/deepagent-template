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
    # 읽을 때 추가로 시도할 형식들. **쓰는 형식은 하나뿐인데 읽는 형식은 여러 개**인
    # 비대칭이 의도적이다:
    #   · 질의 경계는 하나여야 한다 — 두 개면 어느 쪽으로 범위를 자를지 모른다.
    #   · 저장된 값은 섞여 있을 수 있다. 같은 컬렉션에 "2026-09-04 09:00:00"과
    #     "2026-09-04T09:00:00"이 함께 있어도, 앞 10글자가 고정폭이면 사전순
    #     범위 비교는 둘 다에 대해 성립한다. 그런데 strptime은 하나만 통과시킨다.
    # 순서대로 시도하고 처음 성공한 것을 쓴다.
    parse_formats: list[str] = []
    fields: FieldMap = FieldMap()
    # 미해제로 보는 status 값. 사내 규약: 0 발생 · 10 접수 · 1 조치시작.
    # 2(조치완료 수동)와 40(조치완료 자동)이 해제다.
    unresolved_status: list[int] = [0, 10, 1]
    # status 값 → 사람이 읽는 이름. 리포트에 `status=40`이라고 적히면 읽는 사람이
    # 매번 규약 문서를 찾아야 한다. 값의 뜻은 대상 시스템이 정하므로 config가 안다.
    status_labels: dict[int, str] = {0: "발생", 10: "접수", 1: "조치시작",
                                     2: "조치완료(수동)", 40: "조치완료(자동)"}
    # 법인 하나에서 읽어 올 문서 수 상한. 하한이 있는 이유: 0·음수는 pymongo에서
    # "무제한"이고, 리포트는 법인 N개로 팬아웃하므로 그 한 줄이 N개 법인에
    # 동시에 무제한 커서를 연다.
    sample: int = Field(default=50_000, ge=1)

    @model_validator(mode="after")
    def _status_values_are_distinct(self):
        if len(set(self.unresolved_status)) != len(self.unresolved_status):
            raise ValueError("unresolved_status에 중복이 있다")
        # 미해제로 세는 값에 이름이 없으면 "미해제 3건(status=7)"처럼 찍힌다 —
        # 이름을 빼먹은 것과 값을 잘못 적은 것이 구별되지 않는다.
        unnamed = [v for v in self.unresolved_status if v not in self.status_labels]
        if unnamed:
            raise ValueError(f"status_labels에 이름이 없는 unresolved_status — "
                             f"{', '.join(str(v) for v in unnamed)}")
        return self

    def formats(self) -> tuple[str, ...]:
        """읽을 때 시도할 형식 전부. 경계 형식이 항상 첫 번째다."""
        seen, ordered = set(), []
        for fmt in (self.date_format, *self.parse_formats):
            if fmt not in seen:
                seen.add(fmt)
                ordered.append(fmt)
        return tuple(ordered)

    def status_label(self, value: int | None) -> str:
        if value is None:
            return "(읽을 수 없음)"
        return self.status_labels.get(value, f"(모르는 값 {value})")


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


class Thresholds(StrictModel):
    """"이건 이상하다"의 경계. **config에 있어야 하는 이유**: 법인마다 알람
    밀도가 다르고, 운영이 돌면서 "이 정도는 평소"의 감각이 바뀐다. 코드에 박으면
    그 감각이 바뀔 때마다 배포가 필요하다.
    """
    # **직전 평일 평균** 대비 이 배수 이상이면 급증으로 본다. 기준이 전주 동요일
    # 하루가 아닌 이유는 `Facts.baseline`에 적혀 있다.
    spike_ratio: float = Field(default=1.5, gt=1.0)
    # 단, 건수가 이보다 적으면 급증으로 보지 않는다. 1건 → 3건은 3배지만
    # 그걸 급증이라 부르면 리포트가 매일 급증으로 가득 찬다(경보 피로).
    spike_min_count: int = Field(default=10, ge=1)
    # 같은 (법인·라인·알람항목)이 기간 내 이 횟수 이상이면 반복 알람 후보다.
    repeat_min_count: int = Field(default=5, ge=2)
    # 그리고 **며칠에 걸쳐** 있어야 하는가. 건수만 보면 알람이 많은 큰 라인이
    # 항상 걸려서 목록이 "큰 라인 순위"가 된다. 하루에 30번 터진 것(순간 장애)과
    # 7일 내내 매일 터진 것(방치된 만성 문제)은 다른 문제이고, 리포트가 찾아야
    # 하는 것은 후자다.
    repeat_min_days: int = Field(default=3, ge=1)
    # 반복 알람 목록의 길이.
    top_n: int = Field(default=10, ge=1, le=100)
    # TOP 표에서 **GBM 하나당** 몇 개를 보일 것인가. 전사 TOP N이 아닌 이유는
    # `ranking_by_gbm`에 적혀 있다 — 알람이 많은 GBM이 목록을 통째로 차지한다.
    top_per_gbm: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def _repeat_days_fit_in_window(self):
        # 이 검증은 WindowSpec을 알아야 완전하지만(평일 7일짜리 창에 min_days=10을
        # 적으면 목록이 영원히 빈다) 여기서는 자체 모순만 본다 — 창과의 대조는
        # ReportScenario가 한다.
        if self.repeat_min_days > self.repeat_min_count:
            raise ValueError(f"repeat_min_days({self.repeat_min_days})가 "
                             f"repeat_min_count({self.repeat_min_count})보다 클 수 없다 "
                             f"— 3일에 걸쳐 2건일 수는 없다")
        return self


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
    thresholds: Thresholds = Thresholds()

    @model_validator(mode="after")
    def _thresholds_fit_the_window(self):
        # 평일 7일짜리 창에 repeat_min_days=10을 적으면 반복 알람 섹션이 **영원히
        # 빈다.** 그런데 화면에는 "반복 알람 없음"으로 보여서, 설정 실수가
        # "문제가 없다"는 좋은 소식으로 둔갑한다.
        if self.thresholds.repeat_min_days > self.window.business_days:
            raise ValueError(
                f"repeat_min_days({self.thresholds.repeat_min_days})가 "
                f"business_days({self.window.business_days})보다 크다 — "
                f"반복 알람이 영원히 빈 목록이 된다")
        return self
