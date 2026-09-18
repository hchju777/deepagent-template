"""finding을 케이스로 만든다 — **또는 이미 있는 케이스에 첨부한다.**

## 왜 무시가 아니라 첨부인가

3시간마다 도는데 문제가 안 고쳐지면 케이스가 하루 8개 쌓인다. 그렇다고 그냥 무시하면
**"언제부터 이랬나"가 아무 데도 안 남는다.**

첨부하면 케이스가 `관측 4회 · 9시간째`를 들고 있다. "지금 0/0/0이다"와 "9시간째
0/0/0이다"는 다른 사실이고, 후자가 조사와 보고서의 재료다.

## 닫힌 케이스를 되살리는 유일한 조건

조사가 끝나 케이스가 닫혔는데 여전히 0/0/0이면 3시간 뒤 또 열린다 — 같은 조사를
반복한다. 그래서 **닫힌 뒤 그 대상이 한 번이라도 정상으로 관측돼야** 다시 연다.
그게 "새 사건"의 정의다.

대가는 정직하게: **안 고쳐진 문제는 다시 안 알린다.** 사람은 이미 보고서를 받았으니
알고 있다. "미해결이 N일째"를 상기시키는 경로가 없는 것은 backlog ①과 같은 계열이다.

## 대상을 다시 읽지 않는다

케이스에 싣는 값은 finding이 **판정 시점에 본 것** 그대로다. 게이트 통과 시점에 다시
조회하면 그 사이 값이 바뀔 수 있고, 그러면 "0/0/0이라 열었다"는 케이스에 다른 값이
실린다. 보고서를 읽는 사람이 그 모순을 만나면 시스템 전체를 안 믿게 된다.

## 무raise

저장소가 던져도(파일이 깨졌다 등) 순찰은 계속 돈다. `rejected`로 흡수하고 이유를
남긴다 — 케이스가 안 열린 것을 사람이 출력에서 본다.
"""
from typing import Literal

from src.domain.base import Clock, StrictModel
from src.domain.cases import CaseRecord, CaseRepositoryPort, fingerprint
from src.domain.patrol import CheckOutcome, Finding


class AdmitResult(StrictModel):
    action: Literal["opened", "attached", "suppressed", "rejected"]
    case_id: str | None = None
    target: str
    reason: str


def admit(finding: Finding, *, repo: CaseRepositoryPort, clock: Clock) -> AdmitResult:
    """finding 하나를 케이스로 승격하거나 기존 케이스에 첨부한다."""
    try:
        if not finding.observed:
            # 근거 없는 케이스는 만들지 않는다. 조사가 시작 지점을 못 갖는다.
            return AdmitResult(action="rejected", target=finding.target,
                               reason="관측값이 비어 있다 — 근거 없는 케이스는 안 연다")

        now = clock()
        key = fingerprint(finding.site, finding.check, finding.target)
        existing = repo.latest(key)

        if existing is not None and existing.status == "open":
            updated = existing.model_copy(update={
                "last_seen_at": now, "observations": existing.observations + 1})
            repo.update(updated)
            return AdmitResult(action="attached", case_id=updated.id,
                               target=finding.target,
                               reason=f"{updated.observations}회째 · "
                                      f"{updated.sustained_for(now)}")

        if existing is not None and existing.cleared_at is None:
            # 닫힌 뒤로 정상을 한 번도 못 봤다 — 같은 사건이 계속되는 중이다.
            return AdmitResult(action="suppressed", case_id=existing.id,
                               target=finding.target,
                               reason=f"{existing.id}이 닫힌 뒤로 정상을 본 적이 없다")

        record = CaseRecord(
            id=repo.next_id(), site=finding.site, check=finding.check,
            target=finding.target, concern=finding.concern,
            symptom=f"{finding.target} — {finding.reason}",
            opened_at=now, last_seen_at=now, observed=dict(finding.observed))
        repo.add(record)
        return AdmitResult(action="opened", case_id=record.id, target=finding.target,
                           reason=record.symptom)
    except Exception as exc:                                        # noqa: BLE001
        return AdmitResult(action="rejected", target=finding.target,
                           reason=f"케이스를 열 수 없다 — {type(exc).__name__}: {exc}")


def note_clear(site: str, check: str, target: str, *,
               repo: CaseRepositoryPort, clock: Clock) -> None:
    """이 대상이 정상으로 관측됐다. 닫힌 케이스에 그 사실을 적는다.

    **열린 케이스는 건드리지 않는다.** 순찰이 정상을 봤다고 조사가 끝난 것은 아니다 —
    케이스를 닫는 것은 조사(12a)이지 순찰이 아니다.
    """
    try:
        existing = repo.latest(fingerprint(site, check, target))
        if existing is None or existing.status != "closed" or existing.cleared_at:
            return
        repo.update(existing.model_copy(update={"cleared_at": clock()}))
    except Exception:                                               # noqa: BLE001
        # 정상을 기록 못 한 것은 케이스를 늦게 여는 쪽으로만 틀린다.
        pass


def process(outcome: CheckOutcome, *, repo: CaseRepositoryPort,
            clock: Clock) -> list[AdmitResult]:
    """점검 결과 하나를 게이트에 통과시킨다.

    `skipped`·`unreachable`은 **아무것도 하지 않는다.** 판정을 안 했거나 못 했으므로
    정상도 이상도 관측되지 않았다 — 둘 중 어느 쪽으로도 상태를 움직이면 안 된다.
    """
    if outcome.status in ("skipped", "unreachable"):
        return []
    for target in outcome.cleared():
        note_clear(outcome.site, outcome.check, target, repo=repo, clock=clock)
    return [admit(f, repo=repo, clock=clock) for f in outcome.findings]
