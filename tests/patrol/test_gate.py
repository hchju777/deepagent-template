from datetime import datetime, timezone

from src.config.schema_site import CheckConfig
from src.domain.cases import InMemoryCaseRepository
from src.domain.patrol import Finding
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds
from src.patrol.gate import admit_finding, evidence_refs_for_case
from src.patrol.runner import run_check
from tests.patrol.test_probes import _adapters

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 3, 8, 5, tzinfo=timezone.utc)


def _finding(store, summary="OEE 512%", observed_at=T):
    # observed_at을 바꾸면(두 번째 점검 실행 등) id도 함께 달라진다 — 실제
    # runner.make_finding처럼 id가 관찰 시각에 매인다는 전제를 지킨다.
    snap = store.put_evidence("patrol:mx:gumi:api.oee", "rest:/oee", {"oee": 512}, as_of=T)
    return Finding(id=f"api.oee@{observed_at.isoformat()}", gbm="mx", fct="gumi", check="api.oee",
                   target="rest:/oee", summary=summary, evidence_ids=[snap],
                   scratch_case_id="patrol:mx:gumi:api.oee", observed_at=observed_at, judge="rule")


def test_첫_finding은_케이스를_열고_스냅샷을_T0_증거로_복사한다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    result = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    assert result.action == "opened" and result.case.origin == "patrol"
    assert result.case.t0 == T and result.case.target_locator == "rest:/oee"
    assert store.list_evidence(result.case_id)[0].as_of == T       # 메타 보존 복사


def test_저장된_레코드에서_재구성한_Case는_admit이_돌려준_Case와_같다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    result = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    assert repo.get(result.case_id).to_case() == result.case


def test_evidence_refs_for_case는_opened_케이스의_T0_스냅샷을_돌려준다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    result = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    refs = evidence_refs_for_case(store, result.case_id)
    assert len(refs) == 1
    assert refs[0].as_of == T and refs[0].complete is True


def test_같은_지문의_열린_케이스에는_첨부한다():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    first = admit_finding(_finding(store), repo=repo, store=store, clock=lambda: T)
    second = admit_finding(_finding(store, "OEE 530%", observed_at=T2), repo=repo, store=store,
                           clock=lambda: T)
    assert second.action == "attached" and second.case_id == first.case_id
    assert len(repo.get(first.case_id).finding_ids) == 2
    assert len(store.list_evidence(first.case_id)) == 2


def test_같은_finding_재수신은_복사나_추가_없이_멱등하게_첨부():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    finding = _finding(store)
    first = admit_finding(finding, repo=repo, store=store, clock=lambda: T)
    before_evidence = len(store.list_evidence(first.case_id))
    second = admit_finding(finding, repo=repo, store=store, clock=lambda: T)  # 동일 finding 재수신
    assert second.action == "attached" and second.case_id == first.case_id
    assert repo.get(first.case_id).finding_ids == [finding.id]               # 중복 추가 없음
    assert len(store.list_evidence(first.case_id)) == before_evidence        # 재복사 없음


def test_인용_스냅샷이_없으면_기각():
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    f = _finding(store).model_copy(update={"evidence_ids": ["ev-99"]})
    result = admit_finding(f, repo=repo, store=store, clock=lambda: T)
    assert result.action == "rejected" and repo.list_by_status("open") == []


def _check(**kw):
    base = {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/oee",
            "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}}
    base.update(kw)
    return CheckConfig.model_validate(base)


async def test_run_check에서_admit까지_사슬이_T0_증거를_엔진용으로_이어준다():
    # 계획 3 브리지 사슬 전체: 프로브 → rule finding → 게이트 → engine EvidenceRef.
    store, repo = InMemoryCaseStore(), InMemoryCaseRepository()
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    outcome = await run_check("mx", "gumi", "api.oee", _check(), adapters=adapters,
                              store=store, clock=lambda: T)
    result = admit_finding(outcome.finding, repo=repo, store=store, clock=lambda: T)
    assert result.action == "opened" and result.case.target_locator == "rest:/oee"
    refs = evidence_refs_for_case(store, result.case_id)
    assert len(refs) == 1 and refs[0].as_of == T and refs[0].complete is True
