"""`api`의 응답 모델 — 계획 13 인계 #6(응답이 dict였다).

요청 모델은 각 라우트 파일에 있고(StrictModel), 응답은 상세(`GET /cases/{id}`)만 모델을
세운다 — 계획 14가 그 payload를 바꾸는 시점이 "응답 모델을 세우면 그때 같이"였다.
목록·이벤트·점검은 모양이 단순해 dict 그대로다(YAGNI).

`candidates`는 방향 문서 §385의 RCA 응답 형태다: rank 1이 `Verdict.root_cause`(신뢰도는
판정의 것), 그 뒤가 `alternates`(각자의 신뢰도). 유도는 여기 한 곳이다 — 렌더러마다
따로 만들면 언젠가 다른 순서를 낸다.
"""
from src.config.schema_app import StrictModel
from src.domain.case import Verdict


class Candidate(StrictModel):
    rank: int                       # 1 = 가장 유력한 후보. 판정에 root_cause가 있으면 그것이고,
                                    # inconclusive처럼 없으면 첫 후보다 — "결론인가"는 verdict.root_cause로 본다
    component: str
    confidence: str | None
    evidence_ids: list[str]
    rationale: str | None           # CauseLink.relation — 왜 후보이고 왜 최상위가 아닌가


class CaseDetail(StrictModel):
    case_id: str
    gbm: str
    fct: str
    status: str
    concern: str
    origin: str
    symptom: str
    t0: str
    updated_at: str
    verdict_summary: str | None
    question: str | None
    question_kind: str | None
    question_seq: int          # 답을 보낼 때 되돌려 실어야 하는 번호(계획 17)
    requested_by: str | None
    intake_done: bool
    target_locator: str | None
    stages: list[dict]
    verdict: dict | None            # Verdict.model_dump(mode="json") 그대로 — alternates 포함
    candidates: list[Candidate]
    timeline: list[dict]            # TimelineEntry.model_dump(mode="json")
    timeline_source: str            # events / none / unavailable — 빈 timeline의 세 가지 뜻
    timeline_error: str | None
    task_error_rate: str
    knowledge_digests: dict[str, str]


def candidates_of(verdict: Verdict | None) -> list[Candidate]:
    if verdict is None:
        return []
    out = []
    if verdict.root_cause is not None:
        rc = verdict.root_cause
        out.append(Candidate(rank=1, component=rc.component, confidence=verdict.confidence,
                             evidence_ids=list(rc.evidence_ids), rationale=rc.relation))
    for link in verdict.alternates:
        out.append(Candidate(rank=len(out) + 1, component=link.component,
                             confidence=link.confidence, evidence_ids=list(link.evidence_ids),
                             rationale=link.relation))
    return out
