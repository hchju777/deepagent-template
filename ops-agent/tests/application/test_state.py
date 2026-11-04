"""State 리듀서 — 병렬 가지들이 쓴 것을 합치는 규칙."""
from src.application.state import CaseState, merge_by_id
from src.domain.case import EvidenceRef

from tests.application.conftest import task


def test_같은_id는_교체된다():
    out = merge_by_id([task("a"), task("b")], [task("b", status="ok")])
    assert [(t.id, t.status) for t in out] == [("a", "pending"), ("b", "ok")]


def test_새_id는_뒤에_붙는다():
    out = merge_by_id([task("a")], [task("b")])
    assert [t.id for t in out] == ["a", "b"]


def test_기존_순서가_유지된다():
    """FIFO의 근거가 이 순서다.

    깨지는 쪽은 "갱신된 것을 빼고 뒤에 붙이기"다 — 짧고 맞아 보이는데 **교체된 항목이
    맨 뒤로 밀린다.** 그러면 우선순위 동률일 때 같은 입력에 다른 태스크가 실행되고,
    재현이 안 되는 조사가 된다. 교체가 일어나도 자리를 지켜야 한다.
    """
    before = [task("a"), task("b"), task("c")]
    out = merge_by_id(before, [task("c", status="ok"), task("a", status="error")])
    assert [t.id for t in out] == ["a", "b", "c"]


def test_빈_갱신은_아무것도_안_바꾼다():
    before = [task("a"), task("b")]
    assert [t.id for t in merge_by_id(before, [])] == ["a", "b"]


def test_증거_id_집합은_State에_있는_것만_센다(case):
    state = CaseState(case=case, evidence=[
        EvidenceRef(id="t-1.e1", source="s", summary="x")])
    assert state.evidence_ids() == {"t-1.e1"}


def test_증거_id는_태스크_id에서_파생된다():
    """전역 카운터가 없어야 병렬 가지끼리 충돌하지 않는다."""
    assert EvidenceRef.make_id("t-1", 1) == "t-1.e1"
    assert EvidenceRef.make_id("t-1", 2) != EvidenceRef.make_id("t-2", 2)
