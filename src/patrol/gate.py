"""순찰 게이트 — Finding을 케이스로 승격하거나 기존 케이스에 첨부한다 (스펙 §4.6~4.7).

가드레일이 먼저다: finding.evidence_ids가 가리키는 스냅샷은 전부 스크래치
케이스(finding.scratch_case_id)에 "저장돼 있어야" 한다 — has_evidence로
저장본을 대조할 뿐, 라이브 재조회는 하지 않는다(판정 시점의 증거가 그대로
케이스에 넘어가야 하고, 게이트 통과 시점에 다시 조회하면 그 사이 값이 바뀔
수 있다). 하나라도 없으면(빈 인용 포함) 케이스를 만들지 않고 rejected다.

가드레일을 통과하면 지문(gbm/fct/check/target)으로 열린 케이스를 찾는다.
있으면 그 케이스로 스냅샷을 복사하고(as_of/complete/effective_as_of 메타
보존) finding_ids만 늘려 attached, 없으면 새 케이스를 열어 opened — 이때만
엔진에 넘길 도메인 Case를 함께 돌려준다.

절대 raise하지 않는다: 최외곽 try/except가 예상 밖 예외까지 rejected(원인)
으로 돌린다. 시계는 clock()으로만 얻는다(결정론 테스트).
"""
from typing import Callable, Literal

from src.config.schema_app import StrictModel
from src.domain.case import Case
from src.domain.cases import CaseRecord, CaseRepositoryPort
from src.domain.patrol import Finding, fingerprint
from src.domain.store import CaseStorePort


class AdmitResult(StrictModel):
    """게이트 판정 결과 — opened일 때만 엔진에 넘길 Case를 싣는다."""
    action: Literal["opened", "attached", "rejected"]
    case_id: str | None
    reason: str | None
    case: Case | None


def _copy_snapshots(finding: Finding, target_case_id: str, store: CaseStorePort) -> None:
    """finding이 인용한 스냅샷을 target_case_id로 복사한다 — 메타(as_of/
    complete/effective_as_of) 보존. 스크래치 케이스의 원본은 그대로 둔다."""
    for evidence_id in finding.evidence_ids:
        record = store.get_evidence_record(finding.scratch_case_id, evidence_id)
        body = store.get_evidence(finding.scratch_case_id, evidence_id)
        store.put_evidence(
            target_case_id, record.source, body,
            as_of=record.as_of, complete=record.complete,
            effective_as_of=record.effective_as_of,
        )


def admit_finding(
    finding: Finding, *, repo: CaseRepositoryPort, store: CaseStorePort, clock: Callable,
) -> AdmitResult:
    """Finding을 게이트에 통과시켜 케이스를 열거나(opened) 기존 케이스에
    첨부(attached)하거나, 인용 증거가 저장본에 없으면 기각(rejected)한다."""
    try:
        if not finding.evidence_ids:
            return AdmitResult(action="rejected", case_id=None,
                               reason="인용 스냅샷 부재: 인용이 비어 있다", case=None)
        missing = [
            eid for eid in finding.evidence_ids
            if not store.has_evidence(finding.scratch_case_id, eid)
        ]
        if missing:
            return AdmitResult(action="rejected", case_id=None,
                               reason=f"인용 스냅샷 부재: {missing}", case=None)

        fp = fingerprint(finding.gbm, finding.fct, finding.check, finding.target)
        existing = repo.find_open_by_fingerprint(fp)

        if existing is not None:
            _copy_snapshots(finding, existing.id, store)
            updated = existing.model_copy(update={
                "finding_ids": existing.finding_ids + [finding.id],
                "updated_at": clock(),
            })
            repo.save(updated)
            return AdmitResult(action="attached", case_id=existing.id, reason=None, case=None)

        case_id = repo.new_case_id()
        _copy_snapshots(finding, case_id, store)
        now = clock()
        record = CaseRecord(
            id=case_id, gbm=finding.gbm, fct=finding.fct, fingerprint=fp,
            status="open", created_at=now, updated_at=now, finding_ids=[finding.id],
        )
        repo.save(record)
        case = Case(
            id=case_id, gbm=finding.gbm, fct=finding.fct, origin="patrol",
            symptom=finding.summary, t0=finding.observed_at, target_locator=finding.target,
        )
        return AdmitResult(action="opened", case_id=case_id, reason=None, case=case)
    except Exception as exc:
        return AdmitResult(action="rejected", case_id=None,
                           reason=f"게이트 처리 실패 — {type(exc).__name__}: {exc}", case=None)
