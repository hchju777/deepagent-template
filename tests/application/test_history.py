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


def test_렌더는_어느_필드에_섞였든_evidence_id를_지운다():
    # 검증 리뷰 B1: 세척이 summary에만 걸려 component의 id가 리드 프롬프트까지 나갔다.
    # component는 LLM이 쓴 자유 문자열(CauseLink.component)이 스냅샷을 거쳐 온 것이다.
    from src.domain.case import HistoryHit
    text = render_history([
        HistoryHit(case_id="c-old", tier=1, reason="ev-7 때문에 매칭", verdict_type="ev-5",
                   component="plan-sync (ev-2 참조)", summary="ev-9가 근거다"),
        HistoryHit(case_id="ev-3", tier=2, reason="r", component="EV-4 대문자", summary="s")])
    assert "ev-" not in text.lower()               # 대소문자 어느 쪽도
    assert "plan-sync" in text and "c-old" in text  # 나머지 정보는 살아 있다


def test_이력_조회_실패는_브리핑에_보인다():
    # 계획의 Global Constraint: 실패는 보고서/브리핑에 보여야 한다(조용한 생략 금지).
    # "이력이 없다"와 "이력을 못 읽었다"는 다른 말이다.
    class _Broken(InMemoryCaseRepository):
        def closed_by_fingerprint(self, *a, **k):
            raise RuntimeError("mongo down")
    from src.application.history import read_history
    assert find_history(_record("now"), repo=_Broken(),
                        snapshots=InMemoryVerdictSnapshotStore(), topology=TOPO) == []
    got = read_history(_record("now"), repo=_Broken(), snapshots=InMemoryVerdictSnapshotStore(),
                       topology=TOPO)
    assert got.hits == [] and "RuntimeError" in (got.error or "")
    assert "이력 조회 실패" in render_history(got.hits, error=got.error)


def test_대상이_없는_케이스끼리는_tier2로_엮이지_않는다():
    # 리뷰 돌연변이 #4: locator=None 가드를 지워도 초록이었다 — 상대도 locator가 없어야 잡힌다.
    repo, snapshots = _fixture()
    repo.save(_record("old", fp="fp-other", locator=None))
    _snap(snapshots, "old")
    assert find_history(_record("now", locator=None), repo=repo, snapshots=snapshots,
                        topology=TOPO) == []


def test_스냅샷이_없는_종결_케이스는_이력에서_빠진다():
    # 리뷰 돌연변이 #2. 판정 재료가 없으면 리드에게 줄 것이 "옛날에도 뭔가 있었다"뿐이다.
    repo, snapshots = _fixture()
    repo.save(_record("old"))                      # 스냅샷을 안 남긴다
    assert find_history(_record("now"), repo=repo, snapshots=snapshots, topology=TOPO) == []
