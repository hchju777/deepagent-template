"""자기 감시 점검 — 순찰 자신의 연속 error를 순찰이 감시하는 대상과 같은
방식(Finding → 게이트 → 케이스)으로 올린다 (계획 4b).

"패트롤이 죽어 있어도 아무도 모른다"를 막는 마지막 방어선이다: 점검 하나가
연속 error로 threshold회를 넘기면 — 프로브가 계속 죽거나, 어댑터 설정이
잘못됐거나 — 사람이 봐야 할 신호다. check 이름 앞에 "self."를 붙여 지문을
(gbm, fct, "self.<check>", None)으로 만든다 — 대상 점검과 지문이 겹치지
않고, 이 자기 감시 자체도 게이트의 지문 중복 억제를 그대로 받는다(같은
점검이 계속 error여도 케이스는 하나만 열린다).

증거는 최근 실행 이력 요약을 스크래치 케이스("patrol:self:{gbm}:{fct}")에
박제한다 — 개별 점검의 스크래치 케이스와는 별도 네임스페이스다(자기 감시가
대상 점검의 스크래치 증거를 밀어내지 않는다). 시계는 clock()으로만 얻는다
(결정론 테스트).
"""
from typing import Callable

from src.domain.patrol import Finding
from src.domain.store import CaseStorePort
from src.patrol.ledger import CheckLedgerPort


def scan_self_check(*, ledger: CheckLedgerPort, checks: list[tuple[str, str, str]],
                    threshold: int, clock: Callable, store: CaseStorePort) -> list[Finding]:
    """(gbm, fct, check)마다 연속 error 횟수를 세어 threshold 이상이면
    최근 실행 이력을 증거로 박제하고 자기 감시 Finding을 만든다."""
    now = clock()
    findings: list[Finding] = []
    for gbm, fct, check in checks:
        n = ledger.consecutive_errors(gbm, fct, check)
        if n < threshold:
            continue
        rows = [
            {"status": outcome.status, "error": outcome.error,
             "observed_at": outcome.observed_at.isoformat()}
            for outcome in ledger.runs(gbm, fct, check)
        ]
        scratch_id = f"patrol:self:{gbm}:{fct}"
        snap = store.put_evidence(scratch_id, source=f"ledger:{check}", body=rows, as_of=now)
        findings.append(Finding(
            id=f"{gbm}/{fct}/self.{check}@{now.isoformat()}",
            gbm=gbm, fct=fct, check=f"self.{check}", target=None,
            summary=f"점검 {check} 연속 error {n}회",
            evidence_ids=[snap], scratch_case_id=scratch_id,
            observed_at=now, judge="rule",
        ))
    return findings
