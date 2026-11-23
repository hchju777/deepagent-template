"""판정 모델 — **결론이 있으면 근본 원인이 있어야 한다**는 것을 모델이 지킨다."""
import pytest
from pydantic import ValidationError

from src.domain.case import CauseLink, Verdict


def test_결론이_있는_판정에는_root_cause가_필요하다():
    with pytest.raises(ValidationError):
        Verdict(verdict_type="logic_bug", confidence="high", narrative="x")


def test_미확정과_degraded는_root_cause_없이_선다():
    for kind in ("inconclusive", "degraded"):
        v = Verdict(verdict_type=kind, confidence="low", narrative="모른다")
        assert v.root_cause is None and v.alternates == [] and v.contributing == []


def test_판정_모델은_모르는_키를_거부한다():
    """규율 5 — LLM 응답은 `validate`가 걷어내지만, 도메인 객체 자체는 엄격해야 한다."""
    with pytest.raises(ValidationError):
        Verdict(verdict_type="inconclusive", confidence="low", narrative="x", score=0.9)
    with pytest.raises(ValidationError):
        CauseLink(component="sink", evidence_ids=["t-1.e1"], why="x")


def test_degraded는_판정_어휘에_있지만_LLM_어휘에는_없다():
    """`degraded`는 코드만 찍는 낙인이다(조사 실패). 리드가 낼 수 있는 집합은 `lead.ConcludeReply`가 좁힌다."""
    from typing import get_args

    from src.application.lead import ConcludeReply
    from src.domain.case import VerdictType

    assert "degraded" in get_args(VerdictType)
    assert "degraded" not in get_args(ConcludeReply.model_fields["verdict_type"].annotation)
    assert "not_a_defect" in get_args(VerdictType) and "not_a_defect" in get_args(ConcludeReply.model_fields["verdict_type"].annotation)


def test_결함_아님은_root_cause_없이_basis로_서고_root_cause가_있으면_거부한다():
    """R3-5d ⑤(사람 결정 10-19 (3)): 정상 공백은 원인이 없다 — 근거는 `basis`(증거 id)에. root_cause를 실으면 모순이라 모델이 거부한다(리드면 수리
    재시도). 조건 (a)~(d)는 verify가 걷기와 대조한다."""
    v = Verdict(verdict_type="not_a_defect", confidence="medium", narrative="정상 공백", basis=["t-2.e1", "t-3.e1"])
    assert v.root_cause is None and v.basis == ["t-2.e1", "t-3.e1"]
    with pytest.raises(ValidationError):
        Verdict(verdict_type="not_a_defect", confidence="medium", narrative="x", root_cause=CauseLink(component="sink", evidence_ids=["t-1.e1"]))
    assert Verdict(verdict_type="inconclusive", confidence="low", narrative="x").basis == []
