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


def test_판정은_후보를_여럿_들_수_있다():
    # 방향 문서 §307: root_cause(최상위)는 그대로 두고 alternates를 더한다 — 벤치가
    # root_cause.component로 채점하므로 안 깨진다. 후보의 신뢰도는 후보 자신의 것이고
    # 최상위의 신뢰도는 Verdict.confidence다.
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                alternates=[CauseLink(component="twin-state", evidence_ids=["ev-2"],
                                      confidence="low", relation="갱신 지연 가능성")])
    assert [a.component for a in v.alternates] == ["twin-state"]
    assert v.alternates[0].confidence == "low" and v.root_cause.confidence is None


def test_결론_없는_판정도_후보는_들_수_있다():
    # "확신은 없지만 후보는 이것들이다"가 웹 질의(방향 문서 타깃 3)의 실제 답이다.
    v = Verdict(verdict_type="inconclusive", confidence="low", narrative="n",
                alternates=[CauseLink(component="a", evidence_ids=["ev-1"])])
    assert v.root_cause is None and len(v.alternates) == 1
