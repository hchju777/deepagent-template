from datetime import datetime

import pytest
from pydantic import ValidationError
from src.domain.case import Case, CauseLink, Hypothesis, PlanTask, Verdict

T = datetime(2026, 9, 3, 8, 0)


def test_결론있는_판정은_root_cause가_필수():
    Verdict(verdict_type="stale_data", confidence="high", narrative="…",
            root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]))
    with pytest.raises(ValidationError):
        Verdict(verdict_type="stale_data", confidence="high", narrative="…", root_cause=None)
    # 미확정·조사실패는 root_cause 없음 허용
    Verdict(verdict_type="inconclusive", confidence="low", narrative="…", root_cause=None)
    Verdict(verdict_type="degraded", confidence="low", narrative="…", root_cause=None)


def test_케이스와_태스크_기본값():
    case = Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="OEE 512%", t0=T)
    assert case.knowledge_digests == {}
    task = PlanTask(id="t-1", goal="mongo 조회", role="data_prober")
    assert task.status == "pending" and task.priority == 100
    with pytest.raises(ValidationError):
        PlanTask(id="t-2", goal="x", role="ghost_role")
    hyp = Hypothesis(id="h-1", statement="계산 이상")
    assert hyp.status == "open"
