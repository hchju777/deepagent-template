"""전역(app) config 스키마 — 스펙 §4.5-①.

전역 키가 사이트 계층에 섞이면 예산·상한이 사이트 수만큼 곱해진다.
전역은 이 모델로만, 사이트는 schema_site로만 검증한다.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

from src.domain.concern import CONCERNS


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
    # 접수 되묻기 상한 — 턴으로 쪼개면 호출자가 무한히 부를 수 있다(규율 6).
    # 넘으면 대상 없이 조사에 들어간다(기존 "이중 실패"와 같은 착지점).
    max_intake_turns: int = 3


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
    # concern별 수신자. 선언되지 않은 concern은 recipients로 폴백한다 — 전부
    # 적으라고 강제하면 같은 목록을 두 번 쓰게 되고, 한쪽만 고치는 순간 조용히
    # 갈라진다.
    recipients_by_concern: dict[str, list[str]] = {}
    username: str | None = None
    password: SecretStr | None = None
    use_tls: bool = False

    @model_validator(mode="after")
    def _concern_keys_are_known(self):
        # 오타("operations")면 그 목록이 영원히 안 쓰이고 아무도 모른다. boot이
        # 아니라 검증자로 잡는 이유: config 로드 시점에 걸리면 `config show`에도
        # 드러난다.
        empty = sorted(k for k, v in self.recipients_by_concern.items() if not v)
        if empty:
            # 빈 목록을 "보내지 마라"로 읽을지 "기본으로 폴백"으로 읽을지 사람이
            # 헷갈린다. 채널을 끄려면 그 키를 지우면 된다.
            raise ValueError(f"recipients_by_concern의 빈 목록: {empty} — 기본 수신자로 "
                             f"폴백시키려면 그 키를 지워라")
        unknown = sorted(set(self.recipients_by_concern) - set(CONCERNS))
        if unknown:
            raise ValueError(f"recipients_by_concern의 알 수 없는 concern: {unknown} "
                             f"— {list(CONCERNS)} 중 하나여야 한다")
        return self

    @model_validator(mode="after")
    def _enabled_needs_host_and_recipients(self):
        # 조용히 실패하는 pending 무한 적재를 기동 시점에 막는다 — host/recipients가
        # 비면 send_report는 매번 SMTP 연결에서 예외를 내고 pending만 계속 쌓는다.
        if self.enabled and (not self.host or not self.recipients):
            raise ValueError("메일을 켜려면 host와 recipients가 필요하다")
        return self


class ReportConfig(StrictModel):
    output_dir: str = "output"
    format: Literal["md", "html"] = "html"   # 기본 산출물은 HTML(스펙 §4.1)
    mail: MailConfig = MailConfig()


# ── 접근 술어(스펙 §3.5) ─────────────────────────────────────────────
# domain이 아니라 여기 사는 이유: 모든 도메인 모델이 이 모듈의 StrictModel에
# 의존하므로 schema_app이 domain을 import하면 순환이 닫힌다. Concern은 의존
# 없는 leaf로 뺄 수 있었지만 이쪽은 pydantic 모델이라 그 길이 없다.
# 접근 술어 — 어떤 주체가 어느 사이트를 조사·조회할 수 있는가 (스펙 §3.5).
# 
# **읽기 전용이라고 폭발 반경이 작지 않다.** 인증 없는
# `POST /cases {gbm:"mx", fct:"suwon"}`은 실질적으로 "수원 법인의 Redis/Mongo/Kafka와
# 소스 저장소에 읽기 권한을 가진 LLM 에이전트를 돌리고 결과를 메일로 보내라"는
# 요청이다. 그리고 `awaiting_human`이 프롬프트 주입구가 된다 — 답을 넣을 수 있는
# 누구든 그 텍스트가 리드 프롬프트에 직행하고 evidence로 박제된다.
# 
# **지금 넣는 이유**: `(gbm, fct)` 축이 이미 레코드·지문·레저 키·사이트 런타임 맵
# 전체에 꿰여 있어 그 위에 주체를 얹는 건 싸다. 이벤트 스토어·read API·UI를 주체
# 없이 다 만든 뒤 소급하면 그 셋을 전부 다시 만져야 한다.
# 
# **최소형을 지킨다 — 필드 1개(`CaseRecord.requested_by`), 술어 1개, 검사 1곳
# (접수 경계).** 역할·권한 등급·리소스별 ACL은 만들지 않는다. 실제 인증(토큰 검증·
# 세션)은 전송 계층의 몫이고, 여기는 **주체가 주어졌을 때의 판정**만 한다.

Site = tuple[str, str]

_ENTRY = re.compile(r"^[^/*\s]+/([^/*\s]+|\*)$")


class AccessPolicy(StrictModel):
    """주체 → 볼 수 있는 사이트 목록(`"mx/gumi"` 또는 `"mx/*"`)."""

    allow: dict[str, list[str]] = {}

    @model_validator(mode="after")
    def _entries_are_sound(self):
        # 오타(`mx`, `mx/gumi/extra`)를 조용히 두면 그 주체는 영원히 아무것도 못
        # 보는데 아무도 모른다. 사업부 자리의 `*`도 막는다 — 전 법인 허용은
        # 선언을 아예 비우는 것으로 표현한다(그쪽이 읽는 사람에게 분명하다).
        for subject, entries in self.allow.items():
            if not subject:
                raise ValueError("access.allow의 주체 이름이 비어 있다")
            for entry in entries:
                if not _ENTRY.match(entry):
                    raise ValueError(f"access.allow[{subject!r}]의 {entry!r}는 "
                                     f'"gbm/fct" 또는 "gbm/*" 형태여야 한다')
        return self

    def can_access(self, subject: str | None, gbm: str, fct: str) -> bool:
        """선언이 비어 있으면 전부 허용, 아니면 목록 안일 때만 허용한다.

        선언이 있는데 주체가 없으면(익명) 거부한다 — 통과시키면 인증이 없는 것과
        같아서 테이블 전체가 장식이 된다.
        """
        if not self.allow:
            return True
        if not subject:
            return False
        return any(entry in (f"{gbm}/{fct}", f"{gbm}/*")
                   for entry in self.allow.get(subject, []))

    def sites_for(self, subject: str | None,
                  known: list[Site] | None = None) -> list[Site] | None:
        """주체가 볼 수 있는 사이트 목록 — 목록 API의 필터 근거.

        `None`은 "제한 없음"이고 `[]`는 "아무것도 못 봄"이다. 둘을 섞으면 선언
        없는 주체가 전부를 보게 되거나 그 반대가 된다.

        `mx/*`가 있으면 `known`이 필요하다 — 어떤 fct들이 실재하는지는 registry가
        알고 이 계층은 모른다. 지어내지 않고 요구한다.
        """
        if not self.allow:
            return None
        entries = self.allow.get(subject, []) if subject else []
        if any(e.endswith("/*") for e in entries) and known is None:
            raise ValueError("와일드카드 선언을 펼치려면 known 사이트 목록이 필요하다")
        out: list[Site] = []
        for gbm, fct in (known or []):
            if self.can_access(subject, gbm, fct):
                out.append((gbm, fct))
        if known is None:
            out = [tuple(e.split("/")) for e in entries]
        return out


class AppConfig(StrictModel):
    engine: EngineConfig = EngineConfig()
    access: AccessPolicy = AccessPolicy()
    investigations: InvestigationsConfig = InvestigationsConfig()
    llm: LlmConfig
    patrol: AppPatrol = AppPatrol()
    store: StoreConfig = StoreConfig()
    report: ReportConfig = ReportConfig()
    timezone: str = "Asia/Seoul"
