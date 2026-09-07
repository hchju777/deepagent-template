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
    # 점검과 **같은 모양**이다: 프로브는 target/probe/params/sample/resolve만 읽으므로
    # MetricSpec이 그 계약을 그대로 만족한다(어댑터 객체를 만들지 않는다). body도
    # `params.body`에 둔다 — 등재 항목의 닫힌 스키마 검증이 그 자리를 본다(규율 9).
    params: dict[str, Any] = {}
    resolve: dict[str, ResolverSpec] = {}
    sample: int | None = None
    extract: str = Field(min_length=1)  # 점 경로 — 빈 문자열이면 무엇을 뽑는지가 없다
    reduce: Reduce
    window: str | None = None           # "24h" 등 — 표본이 무엇을 물었는지 보고서에 적는다
    required: bool = True               # False면 이 지표의 누락은 커버리지에서 빠진다
    unit: str | None = None

    @model_validator(mode="after")
    def _body_and_resolve_do_not_overlap(self):
        """점검과 같은 함정을 같은 방식으로 막는다 — 어느 쪽이 이기는지 config만 봐서
        알 수 없으면, 사람이 값을 고쳤는데 안 바뀌는 형태로 조용히 고장 난다."""
        static = self.params.get("body") if isinstance(self.params, dict) else None
        if isinstance(static, dict):
            overlap = sorted(set(static) & set(self.resolve))
            if overlap:
                raise ValueError(f"params.body와 resolve에 같은 키가 있다: {overlap}")
        return self


class ScenarioScope(StrictModel):
    sites: Literal["all"] | list[str] = "all"      # "gbm/fct"
    exclude: list[str] = []
    # 상한은 코드가 쥔다(규율 6). 기존 세마포어는 사이트당 하나뿐이라 30 사이트 팬아웃이면
    # 최대 120 in-flight가 조사 워커 트래픽과 함께 대상 시스템으로 나간다.
    max_parallel_sites: int = Field(default=4, ge=1)


class OutputSpec(StrictModel):
    format: Literal["html", "md"] = "html"
    # None이면 `report.output_dir/fleet`을 쓴다. 기본값을 CWD 상대 문자열로 두면 케이스
    # 보고서와 집계 리포트가 서로 다른 곳에 흩어지고 리포 루트에 output/이 생긴다.
    output_dir: str | None = None
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


