import asyncio
from datetime import datetime, timezone

from src.config.schema_site import CheckConfig
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds
from src.infrastructure.llm import ScriptedLLM
from src.patrol.ledger import InMemoryLedger
from src.patrol.llm_judge import LlmBudget
from src.patrol.runner import run_check
from src.domain.patrol import scratch_case_id
from tests.patrol.test_probes import _adapters

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _check(**kw):
    base = {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/oee",
            "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}}
    base.update(kw)
    return CheckConfig.model_validate(base)


async def test_rule_점검은_스냅샷을_먼저_박제하고_finding을_낸다():
    store = InMemoryCaseStore()
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    out = await run_check("mx", "gumi", "api.oee", _check(), adapters=adapters,
                          store=store, clock=lambda: T)
    assert out.status == "finding" and out.finding.judge == "rule"
    snap = out.finding.evidence_ids[0]
    assert store.has_evidence(out.finding.scratch_case_id, snap)
    assert store.get_evidence(out.finding.scratch_case_id, snap)["body"] == {"oee": 512}


async def test_프로브_실패는_finding이_아니라_error():
    out = await run_check("mx", "gumi", "k", _check(judge="rule", target=None,
                                                    probe="kafka_lag", params={"rule": "exists"}),
                          adapters=_adapters(StubSeeds()), store=InMemoryCaseStore(),
                          clock=lambda: T)
    assert out.status == "error" and out.finding is None


async def test_rule_llm은_룰_통과면_LLM을_안_부르고_소진시_정책을_따른다():
    adapters_ok = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 87}}))
    llm = ScriptedLLM([])                                  # 호출되면 RuntimeError → 테스트 실패
    out = await run_check("mx", "gumi", "c", _check(judge="rule+llm"), adapters=adapters_ok,
                          store=InMemoryCaseStore(), clock=lambda: T, llm=llm,
                          budget=LlmBudget(5, clock=lambda: T))
    assert out.status == "ok" and out.llm_calls == 0

    adapters_bad = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    exhausted = LlmBudget(0, clock=lambda: T)
    skipped = await run_check("mx", "gumi", "c", _check(judge="rule+llm"), adapters=adapters_bad,
                              store=InMemoryCaseStore(), clock=lambda: T, llm=llm, budget=exhausted)
    assert skipped.status == "skipped"
    escalated = await run_check("mx", "gumi", "c",
                                _check(judge="rule+llm", on_budget_exhausted="escalate"),
                                adapters=adapters_bad, store=InMemoryCaseStore(),
                                clock=lambda: T, llm=llm, budget=exhausted)
    assert escalated.status == "finding" and "예산" in escalated.finding.summary


async def test_llm_판정은_실재_id를_인용하고_레저가_기록한다():
    store, ledger = InMemoryCaseStore(), InMemoryLedger()
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 60}}))
    llm = ScriptedLLM(['{"status": "finding", "summary": "패턴 이상", "evidence_ids": ["ev-1"]}'])
    out = await run_check("mx", "gumi", "c", _check(judge="llm", params={}), adapters=adapters,
                          store=store, clock=lambda: T, llm=llm, budget=LlmBudget(5, clock=lambda: T))
    assert out.status == "finding" and out.llm_calls == 1 and out.finding.evidence_ids == ["ev-1"]
    ledger.record_run("mx", "gumi", "c", out)
    ledger.heartbeat(T)
    assert ledger.last_run("mx", "gumi", "c").status == "finding"
    assert ledger.last_heartbeat() == T and ledger.consecutive_errors("mx", "gumi", "c") == 0


async def test_rule_llm은_룰_ok면_LLM_없이도_ok이고_스냅샷은_항상_남는다():
    store = InMemoryCaseStore()
    adapters_ok = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 87}}))
    out = await run_check("mx", "gumi", "c", _check(judge="rule+llm"), adapters=adapters_ok,
                          store=store, clock=lambda: T, llm=None, budget=None)
    assert out.status == "ok"
    assert len(store.list_evidence("patrol:mx:gumi:c")) == 1        # 판정과 무관하게 박제

    adapters_bad = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    store2 = InMemoryCaseStore()
    out2 = await run_check("mx", "gumi", "c", _check(judge="llm", params={}),
                           adapters=adapters_bad, store=store2, clock=lambda: T, llm=None)
    assert out2.status == "error" and "LLM" in out2.error
    assert len(store2.list_evidence("patrol:mx:gumi:c")) == 1       # error여도 스냅샷은 남는다


async def test_예산_자체가_없으면_skipped가_아니라_error():
    # budget=None(주입 자체가 없음)과 budget이 소진된 경우(try_acquire 실패)는
    # 다른 문제다 — 전자는 설정 오류(error), 후자만 skipped다.
    adapters = _adapters(StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    llm = ScriptedLLM([])  # 호출되면 안 됨 — budget 부재에서 이미 걸러진다
    out = await run_check("mx", "gumi", "c", _check(judge="rule+llm"), adapters=adapters,
                          store=InMemoryCaseStore(), clock=lambda: T, llm=llm, budget=None)
    assert out.status == "error" and "예산" in out.error

    out2 = await run_check("mx", "gumi", "c", _check(judge="llm", params={}), adapters=adapters,
                           store=InMemoryCaseStore(), clock=lambda: T, llm=llm, budget=None)
    assert out2.status == "error" and "예산" in out2.error


def test_ledger_runs_limit_0은_빈_리스트():
    from src.domain.patrol import CheckOutcome
    ledger = InMemoryLedger()
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=T))
    assert ledger.runs("mx", "gumi", "c", limit=0) == []
    assert len(ledger.runs("mx", "gumi", "c", limit=5)) == 1


async def test_등재_항목_점검의_증거_출처는_보낸_body를_식별한다():
    # 어댑터만 항목의 method/path를 알므로 ProbeResult.data의 request를 읽어야
    # runner가 출처를 만들 수 있다. 같은 끝점에 다른 필터를 보낸 두 증거가
    # 구별되지 않으면 0/0/0이 "멈췄다"인지 "잘못 물었다"인지 알 수 없다.
    from src.config.schema_site import RestEntry
    from src.domain.store import InMemoryCaseStore
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubRest
    entries = {"summary_prod": RestEntry(method="POST", path="/summary/prod",
                                         body_schema={"part_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /summary/prod": {"badge": [1, 2, 3]}}, set(), entries,
                             clock=lambda: T)
    store = InMemoryCaseStore()
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge", "body": {"part_code": ["P001"]}}})
    outcome = await run_check("mx", "gumi", "prod.badge", check, adapters=adapters,
                              store=store, clock=lambda: T, llm=None, budget=None)
    assert outcome.status == "ok"
    records = store.list_evidence(scratch_case_id("mx", "gumi", "prod.badge"))
    assert records[-1].source.startswith("rest:POST:/summary/prod#")


async def test_대상_데이터의_request_키가_증거_출처를_위조하지_못한다():
    # runner가 result.data["request"]를 프로브 종류와 무관하게 믿으면, 대상
    # 시스템이 돌려준 데이터에 그 키가 있을 때 증거 출처가 위조된다.
    from src.domain.store import InMemoryCaseStore
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubRedis
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.redis = StubRedis(
        {"plan:7": {"request": {"method": "POST", "path": "/wiped", "params": {}}}},
        ttls={}, max_rows=10, clock=lambda: T)
    store = InMemoryCaseStore()
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "redis:plan:7",
        "params": {"rule": "exists", "field": "request"}})
    outcome = await run_check("mx", "gumi", "plan.exists", check, adapters=adapters,
                              store=store, clock=lambda: T, llm=None, budget=None)
    assert outcome.status in ("ok", "finding")
    records = store.list_evidence(scratch_case_id("mx", "gumi", "plan.exists"))
    assert records[-1].source == "redis:plan:7"      # 대상 데이터가 아니라 target이다


async def test_잘린_이유가_증거까지_이어진다():
    # 이유를 만들어 놓고 증거 직전에 버리면 "5,000개 중 50개만 확인"이 어디에도
    # 안 남는다 — 조용한 생략 금지에 정면으로 걸린다.
    from src.config.schema_site import RestEntry
    from src.domain.store import InMemoryCaseStore
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubMongo, StubRest
    entries = {"e": RestEntry(method="POST", path="/x",
                              body_schema={"line_code": "list[str]"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /x": {"ok": 1}}, set(), entries, clock=lambda: T)
    adapters.mongo = StubMongo({"lines": [{"line_code": f"L{i}"} for i in range(10)]},
                               max_rows=100, clock=lambda: T)
    store = InMemoryCaseStore()
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body.ok"},
        "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                  "field": "line_code", "cardinality": "first:3"}}})
    outcome = await run_check("mx", "gumi", "c", check, adapters=adapters, store=store,
                              clock=lambda: T, llm=None, budget=None)
    assert outcome.status == "ok"
    rec = store.list_evidence(scratch_case_id("mx", "gumi", "c"))[-1]
    assert rec.complete is False
    assert "10" in (rec.truncated_reason or "")


async def test_사이트_시간대가_해석기까지_도달한다():
    # 시간대를 resolve_params 인자로만 열어 두고 배선하지 않으면 프로덕션에서
    # 기본값 UTC로 떨어져 픽스가 무효가 된다 — 함수 인자가 아니라 실제 경로를 본다.
    from src.config.schema_site import RestEntry
    from src.domain.store import InMemoryCaseStore
    from src.infrastructure.factory import AdapterSet
    from src.infrastructure.stubs import StubRest
    entries = {"e": RestEntry(method="POST", path="/x", body_schema={"date": "str"})}
    adapters = AdapterSet(semaphore=asyncio.Semaphore(1))
    adapters.rest = StubRest({"POST /x": {"ok": 1}}, set(), entries, clock=lambda: T)
    kst_morning = datetime(2026, 9, 3, 23, 30, tzinfo=timezone.utc)   # 09-04 08:30 KST
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body.ok"},
        "resolve": {"date": {"from": "clock", "expr": "today"}}})
    store = InMemoryCaseStore()
    outcome = await run_check("mx", "gumi", "c", check, adapters=adapters, store=store,
                              clock=lambda: kst_morning, llm=None, budget=None,
                              timezone_name="Asia/Seoul")
    assert outcome.status == "ok"
    body = store.get_evidence(scratch_case_id("mx", "gumi", "c"),
                              store.list_evidence(scratch_case_id("mx", "gumi", "c"))[-1].id)
    assert body["request"]["params"] == {"date": "2026-09-04"}
