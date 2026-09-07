"""과거 종결 케이스를 결정론 tier로 찾아 리드에게 먹인다(계획 15/P8).

가장 위험한 지점은 렌더러다: 과거 증거도 `ev-2` 형태이고 이번 케이스에도 `ev-2`가
있어, 리드가 과거 id를 인용하면 verify의 인용 우주(state.evidence)를 그대로 통과한다.
"""
from datetime import datetime, timedelta, timezone

from src.application.history import find_history, render_history
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.snapshot import InMemoryVerdictSnapshotStore, VerdictSnapshot
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {"rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                                  "via": "twin-api"}}})


def _record(cid, *, fp="fp-now", locator="rest:/oee", gbm="mx", fct="gumi",
            status="closed", at=None, summary="plan-sync가 멈췄다"):
    at = at or T
    return CaseRecord(id=cid, gbm=gbm, fct=fct, fingerprint=fp, symptom="s", t0=at,
                      created_at=at, updated_at=at, status_since=at, status=status,
                      target_locator=locator, verdict_summary=summary,
                      closed_reason="조사 완료" if status == "closed" else None)


def _snap(snapshots, cid, *, vtype="stale_data", component="plan-sync"):
    snapshots.put(VerdictSnapshot(case_id=cid, closed_at=T, gbm="mx", fct="gumi",
                                  fingerprint="fp", outcome="closed", verdict_type=vtype,
                                  root_cause_component=component, confidence="high"))


def _fixture():
    return InMemoryCaseRepository(), InMemoryVerdictSnapshotStore()


def test_tier_순서대로_걷고_K건에서_멈춘다():
    repo, snapshots = _fixture()
    for i in range(4):
        repo.save(_record(f"old-{i}", at=T - timedelta(days=i + 1)))
        _snap(snapshots, f"old-{i}")
    repo.save(_record("other-site", gbm="mx", fct="asan", fp="fp-other",
                      at=T - timedelta(days=9)))
    _snap(snapshots, "other-site")
    hits = find_history(_record("now"), repo=repo, snapshots=snapshots, topology=TOPO, limit=3)
    assert [h.case_id for h in hits] == ["old-0", "old-1", "old-2"]     # 최신순, K=3
    assert {h.tier for h in hits} == {1}                                 # tier 1에서 다 찼다


def test_같은_케이스는_가장_낮은_tier로_한_번만():
    repo, snapshots = _fixture()
    repo.save(_record("old", fp="fp-now"))          # 지문도 locator도 같다 — tier 1이자 2
    _snap(snapshots, "old")
    hits = find_history(_record("now", fp="fp-now"), repo=repo, snapshots=snapshots,
                        topology=TOPO, limit=3)
    assert [(h.case_id, h.tier) for h in hits] == [("old", 1)]


def test_다른_점검과_다른_사이트와_상류가_각자의_tier를_받는다():
    repo, snapshots = _fixture()
    repo.save(_record("same-target", fp="fp-other", at=T - timedelta(days=1)))
    repo.save(_record("other-site", fp="fp-x", gbm="mx", fct="asan", at=T - timedelta(days=2)))
    repo.save(_record("upstream", fp="fp-y", locator="mongo:twin_state", at=T - timedelta(days=3)))
    for cid in ("same-target", "other-site", "upstream"):
        _snap(snapshots, cid)
    hits = find_history(_record("now"), repo=repo, snapshots=snapshots, topology=TOPO, limit=5)
    assert [(h.case_id, h.tier) for h in hits] == [
        ("same-target", 2), ("other-site", 3), ("upstream", 4)]
    assert all(h.reason for h in hits)               # 왜 매칭됐는지가 행마다 있다


def test_잡음_케이스는_제외한다():
    # degraded 판정과 요약 없는 케이스는 워커 실패의 잔해다 — 이력으로 먹이면 순수 잡음.
    repo, snapshots = _fixture()
    repo.save(_record("degraded", at=T - timedelta(days=1)))
    _snap(snapshots, "degraded", vtype="degraded", component=None)
    repo.save(_record("no-summary", at=T - timedelta(days=2), summary=None))
    _snap(snapshots, "no-summary")
    assert find_history(_record("now"), repo=repo, snapshots=snapshots, topology=TOPO) == []


def test_대상이_없으면_지문_tier만_본다():
    repo, snapshots = _fixture()
    repo.save(_record("same-target", fp="fp-other"))
    _snap(snapshots, "same-target")
    hits = find_history(_record("now", locator=None), repo=repo, snapshots=snapshots, topology=TOPO)
    assert hits == []


def test_저장소가_던져도_이력은_비어_있을_뿐이다():
    class _Broken(InMemoryCaseRepository):
        def closed_by_fingerprint(self, *a, **k):
            raise RuntimeError("mongo down")
    assert find_history(_record("now"), repo=_Broken(), snapshots=InMemoryVerdictSnapshotStore(),
                        topology=TOPO) == []


def test_렌더는_evidence_id를_절대_내지_않는다():
    # 이 계획의 안전 앵커. 과거 id를 리드가 인용하면 verify의 결정론 가드레일을
    # 그대로 통과한다(인용 우주가 state.evidence라 "없는 id"로 안 걸린다).
    from src.domain.case import HistoryHit
    hits = [HistoryHit(case_id="c-old", tier=1, reason="같은 점검이 같은 대상에서 전에도",
                       verdict_type="stale_data", component="plan-sync",
                       summary="ev-2와 ev-3을 보면 plan-sync가 멈췄다")]
    text = render_history(hits)
    assert "ev-" not in text and "c-old" in text and "plan-sync" in text
    assert "같은 점검이 같은 대상에서 전에도" in text          # tier 사유가 행마다
    assert render_history([]) == ""
