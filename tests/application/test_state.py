from src.application.state import CaseState, merge_by_id
from src.domain.case import PlanTask


def _t(id, **kw):
    return PlanTask(id=id, goal="g", role="data_prober", **kw)


def test_merge_by_id는_교체와_추가_순서_유지():
    existing = [_t("t-1"), _t("t-2")]
    update = [_t("t-2", status="ok"), _t("t-3")]
    merged = merge_by_id(existing, update)
    assert [t.id for t in merged] == ["t-1", "t-2", "t-3"]
    assert merged[1].status == "ok"                    # 같은 id는 교체


def test_병렬_브랜치의_서로_다른_태스크_갱신이_합쳐진다():
    # Send 병렬 실행을 모사: 두 브랜치가 각자 자기 태스크만 갱신
    base = [_t("t-1", status="running"), _t("t-2", status="running")]
    after_branch_a = merge_by_id(base, [_t("t-1", status="ok")])
    after_both = merge_by_id(after_branch_a, [_t("t-2", status="error", error="타임아웃")])
    assert after_both[0].status == "ok" and after_both[1].status == "error"


def test_케이스스테이트_기본값():
    from datetime import datetime
    from src.domain.case import Case
    state = CaseState(case=Case(id="c", gbm="mx", fct="gumi", origin="patrol",
                                symptom="s", t0=datetime(2026, 9, 3)))
    assert state.round == 0 and state.plan_tasks == [] and state.verdict is None
    assert state.interaction_policy == "autonomous"
