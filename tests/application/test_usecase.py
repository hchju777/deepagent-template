from datetime import datetime, timezone

from src.application.usecase import _initial_state
from src.domain.case import Case, EvidenceRef

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _case():
    return Case(id="c-1", gbm="mx", fct="gumi", origin="patrol",
               symptom="OEE 512%", t0=T, target_locator="rest:/oee")


def test_initial_evidence가_있으면_초기_state에_실린다():
    # 순찰 게이트가 연 케이스의 T0 스냅샷(gate.evidence_refs_for_case)이
    # investigate_case를 거쳐 그대로 초기 evidence로 들어가야 한다(계획 3 브리지).
    ref = EvidenceRef(id="ev-1", source="rest:/oee", summary="{'oee': 512}", as_of=T)
    state = _initial_state(_case(), "autonomous", "default_and_log", [ref])
    assert state["evidence"] == [ref]


def test_initial_evidence가_없으면_evidence_키_자체가_없다():
    # None이면 CaseState 기본값(빈 리스트)이 그대로 쓰이도록 evidence 키를
    # 아예 넣지 않는다 — 사람이 새로 연 케이스처럼 T0 증거가 없는 경우.
    state = _initial_state(_case(), "autonomous", "default_and_log", None)
    assert "evidence" not in state
    assert state["case"] == _case()
    assert state["interaction_policy"] == "autonomous"
    assert state["autonomous_question_policy"] == "default_and_log"
