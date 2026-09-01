"""전역(app) config 스키마 — 스펙 §4.5-①.

전역 키가 사이트 계층에 섞이면 예산·상한이 사이트 수만큼 곱해진다.
전역은 이 모델로만, 사이트는 schema_site로만 검증한다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubagentBudgets(StrictModel):
    data_prober: int = 8
    code_tracer: int = 6
    recompute_verifier: int = 4


class EngineConfig(StrictModel):
    max_rounds: int = 6
    parallel_width: int = 3
    subagent_budgets: SubagentBudgets = SubagentBudgets()
    autonomous_question_policy: Literal["default_and_log", "park"] = "default_and_log"


class InvestigationsConfig(StrictModel):
    max_concurrent: int = 2
    awaiting_human_timeout_h: int = 72


class LlmProfiles(StrictModel):
    judge: str
    subagent: str
    lead: str


class LlmConfig(StrictModel):
    profiles: LlmProfiles


class PatrolBudget(StrictModel):
    max_calls_per_hour: int = 30


class AppPatrol(StrictModel):
    llm_budget: PatrolBudget = PatrolBudget()


class RetentionConfig(StrictModel):
    closed_case_evidence_d: int = 90
    ledger_d: int = 30
    checkpoint_ttl_d: int = 14


class StoreConfig(StrictModel):
    retention: RetentionConfig = RetentionConfig()


class AppConfig(StrictModel):
    engine: EngineConfig = EngineConfig()
    investigations: InvestigationsConfig = InvestigationsConfig()
    llm: LlmConfig
    patrol: AppPatrol = AppPatrol()
    store: StoreConfig = StoreConfig()
    timezone: str = "Asia/Seoul"
