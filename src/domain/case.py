"""케이스 도메인 모델 — 스펙 §1.1, §2.3.

케이스는 조사 사건의 단위다. Verdict는 인과 사슬(근본 원인 + 기여 요인)이며,
모든 주장은 증거 id를 인용한다 — 인용의 실재 검증은 verify 노드(3b) 몫이고
여기서는 "결론이 있으면 root_cause가 있어야 한다"는 형태 제약만 강제한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from src.config.schema_app import StrictModel
from src.domain.concern import Concern

Role = Literal["data_prober", "code_tracer", "recompute_verifier"]
VerdictType = Literal["logic_bug", "data_loss", "config_error", "stale_data",
                      "external", "inconclusive", "degraded"]


class EvidenceRef(StrictModel):
    """State에 남는 증거 참조 — 본문은 케이스 Store에 있다(§2.3)."""
    id: str
    source: str                       # 예: "mongo:twin_state", "code:twin-services@a3f9c2"
    summary: str
    as_of: datetime | None = None
    complete: bool = True             # 결과 봉투에서 상속 — 불완전 부정 증거 금지(verify)
    effective_as_of: datetime | None = None


class Hypothesis(StrictModel):
    id: str
    statement: str
    status: Literal["open", "supported", "refuted"] = "open"
    supporting_ids: list[str] = []
    refuting_ids: list[str] = []


class PlanTask(StrictModel):
    id: str
    goal: str
    role: Role
    input_evidence_ids: list[str] = []    # select 게이트: 전부 실재해야 실행 가능(§2.4)
    priority: int = 100                   # 낮을수록 먼저, 동률이면 FIFO
    status: Literal["pending", "running", "ok", "error", "cancelled"] = "pending"
    result_summary: str | None = None
    result_evidence_ids: list[str] = []
    error: str | None = None


class CauseLink(StrictModel):
    component: str                        # 토폴로지의 서비스/locator 참조
    evidence_ids: list[str]
    relation: str | None = None           # 기여 요인: 근본 원인과의 관계 / 후보: 왜 후보이고 왜 최상위가 아닌가
    # 후보(alternates) 전용 — 최상위 후보의 신뢰도는 Verdict.confidence다. root_cause·
    # contributing에서는 None으로 둔다(둘을 한 필드로 합치면 보고서가 두 값을 보인다).
    confidence: Literal["high", "medium", "low"] | None = None


class Verdict(StrictModel):
    verdict_type: VerdictType
    root_cause: CauseLink | None = None
    alternates: list[CauseLink] = []      # 최상위 다음의 후보들, 유력한 순(방향 문서 §307)
    contributing: list[CauseLink] = []
    confidence: Literal["high", "medium", "low"]
    recommendations: list[str] = []
    caveats: list[str] = []
    narrative: str

    @model_validator(mode="after")
    def _conclusive_needs_root_cause(self):
        if self.verdict_type not in ("inconclusive", "degraded") and self.root_cause is None:
            raise ValueError("결론이 있는 판정에는 root_cause가 필요하다")
        return self


class Case(StrictModel):
    id: str
    gbm: str
    fct: str
    origin: Literal["human", "patrol"]
    concern: Concern = "system"
    symptom: str
    t0: datetime
    target_locator: str | None = None
    knowledge_digests: dict[str, str] = {}   # 토폴로지·룰·deployment digest 박제(§2.5-3)
