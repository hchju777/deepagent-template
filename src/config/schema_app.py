"""전역(app) config 스키마 — 스펙 §4.5-①.

전역 키가 사이트 계층에 섞이면 예산·상한이 사이트 수만큼 곱해진다.
전역은 이 모델로만, 사이트는 schema_site로만 검증한다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator


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
    # 케이스 임차(lease) 유효 시간(초) — 계획 4b. 조사 한 라운드가 걸릴 수 있는
    # 최대 시간보다 길어야 한다 — 워커(InvestigationWorker)가 엔진 호출 동안
    # lease_ttl_s/3 간격의 keepalive로 계속 갱신하므로(계획 4b I5), 실제로
    # 필요한 건 "keepalive 한 틱이 늦어져도 다른 워커가 뺏어가지 않을 여유"뿐이다.
    # float를 허용하는 이유는 테스트가 아주 짧은 TTL로 keepalive 갱신 자체를
    # 실시간에 관찰해야 하기 때문이다(정수로는 표현 불가한 해상도).
    lease_ttl_s: float = 900
    # 다른 프로세스(api·다른 워커)가 연 케이스를 이 데몬이 보게 하는 재스캔 간격.
    # 기동 시 1회 스캔만으로는 나중에 생긴 케이스를 영원히 못 본다.
    requeue_interval_s: float = 30
    # 조사 한 건의 벽시계 상한(초). keepalive가 lease를 무한 갱신하므로 이 상한이
    # 없으면 멈춘 LLM 호출 하나가 lease와 동시 상한 슬롯을 영구 점유한다.
    max_wall_clock_s: float = 1800


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
    self_check_errors: int = 3          # 자기 감시 연속 error 임계값 — 계획 4b


class RetentionConfig(StrictModel):
    closed_case_evidence_d: int = 90
    ledger_d: int = 30
    checkpoint_ttl_d: int = 14
    sends_d: int = 30           # 발송 레저(sends) 보존기한 — F6, 계획 5
    events_d: int = 30          # case_events 보존기한 — 저장을 시작했으니 상한도 같이 정한다
    # 판정 스냅샷 보존기한. 다른 것들보다 훨씬 길다 — 사람 라벨은 몇 달 뒤에 오는데
    # 그때 대조할 대상이 없으면 스냅샷을 남긴 의미가 사라진다.
    snapshots_d: int = 730


class StoreConfig(StrictModel):
    retention: RetentionConfig = RetentionConfig()
    backend: Literal["memory", "mongo"] = "memory"     # 계획 4b: 영속화 백엔드 선택
    mongo_url: str | None = None                       # 예: "${AGENT_MONGO_URL}" 참조
    mongo_db: str = "deepagent"

    @model_validator(mode="after")
    def _mongo_backend_needs_url(self):
        if self.backend == "mongo" and not self.mongo_url:
            raise ValueError("store.backend=mongo면 store.mongo_url이 필요하다")
        return self


class MailConfig(StrictModel):
    enabled: bool = False
    host: str = ""
    port: int = 25
    sender: str = ""
    recipients: list[str] = []
    username: str | None = None
    password: SecretStr | None = None
    use_tls: bool = False

    @model_validator(mode="after")
    def _enabled_needs_host_and_recipients(self):
        # 조용히 실패하는 pending 무한 적재를 기동 시점에 막는다 — host/recipients가
        # 비면 send_report는 매번 SMTP 연결에서 예외를 내고 pending만 계속 쌓는다.
        if self.enabled and (not self.host or not self.recipients):
            raise ValueError("메일을 켜려면 host와 recipients가 필요하다")
        return self


class ReportConfig(StrictModel):
    output_dir: str = "output"
    mail: MailConfig = MailConfig()


class AppConfig(StrictModel):
    engine: EngineConfig = EngineConfig()
    investigations: InvestigationsConfig = InvestigationsConfig()
    llm: LlmConfig
    patrol: AppPatrol = AppPatrol()
    store: StoreConfig = StoreConfig()
    report: ReportConfig = ReportConfig()
    timezone: str = "Asia/Seoul"
