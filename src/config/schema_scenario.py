"""Fleet 집계 시나리오 스키마 — `patrol.checks`가 아니라 별도 파일이다(계획 16/P7).

방향 문서 §4.3의 근거 셋: ①`patrol.checks`는 사이트 계층 전용이라 전역 시나리오를
넣으면 사이트마다 잡이 등록돼 **같은 집계가 N번 돌고 메일도 N통** 간다 ②`CheckConfig.judge`가
필수이고 하류 전체가 "프로브 한 방 결과의 이상 판정"에 묶여 있는데 집계엔 판정이 없다
③한 dict에 두 모양을 섞으면 boot·스케줄러·자기감시·digest·`patrol status` 다섯 소비자가
전부 kind 분기를 해야 한다.
"""
from typing import Any, Literal

from pydantic import Field, model_validator

from src.config.schema_app import StrictModel
from src.config.schema_site import ResolverSpec, Schedule
from src.domain.concern import Concern
from src.domain.rollup import Reduce


class MetricSpec(StrictModel):
    """지표 하나가 **무엇을 묻고 어떻게 접는가**. 값이 아니라 값이 어디서 오는지다."""
    target: str | None = None          # 토폴로지 locator 또는 등재 항목(rest:<이름>)
    probe: str | None = None
    params: dict[str, Any] = {}
    body: dict[str, Any] = {}
    resolve: dict[str, ResolverSpec] = {}
    sample: int | None = None
    extract: str = Field(min_length=1)  # 점 경로 — 빈 문자열이면 무엇을 뽑는지가 없다
    reduce: Reduce
    window: str | None = None           # "24h" 등 — 표본이 무엇을 물었는지 보고서에 적는다
    required: bool = True               # False면 이 지표의 누락은 커버리지에서 빠진다
    unit: str | None = None


class ScenarioScope(StrictModel):
    sites: Literal["all"] | list[str] = "all"      # "gbm/fct"
    exclude: list[str] = []
    # 상한은 코드가 쥔다(규율 6). 기존 세마포어는 사이트당 하나뿐이라 30 사이트 팬아웃이면
    # 최대 120 in-flight가 조사 워커 트래픽과 함께 대상 시스템으로 나간다.
    max_parallel_sites: int = Field(default=4, ge=1)


class OutputSpec(StrictModel):
    format: Literal["html", "md"] = "html"
    output_dir: str = "output/fleet"
    mail: bool = False


class ScenarioConfig(StrictModel):
    kind: Literal["aggregate"]         # 향후 종류 확장의 discriminator
    concern: Concern
    enabled: bool = True
    title: str
    schedule: Schedule                 # 기존 재사용 — interval xor cron 검증이 공짜다
    scope: ScenarioScope = ScenarioScope()
    metrics: dict[str, MetricSpec]     # 이름→스펙(list 아님 — 사이트별 편집이 가능해야 한다)
    group_by: list[str] = []
    output: OutputSpec = OutputSpec()

    @model_validator(mode="after")
    def _needs_metrics(self):
        if not self.metrics:
            raise ValueError("시나리오는 지표를 하나 이상 선언해야 한다")
        return self


class SiteScenarioOverride(StrictModel):
    """사이트가 시나리오에 대해 말할 수 있는 것은 **켜고 끄는 것뿐**이다.

    지표를 사이트마다 재정의하면 "같은 시나리오"가 사이트마다 다른 것을 재고 집계가
    무의미해진다. dict인 이유는 리스트 deep-merge가 통째 대체 아니면 append라 사이트별
    편집에 틀린 의미가 되기 때문이다(리포에 이미 문서화된 근거).
    """
    enabled: bool | None = None
