from datetime import datetime, timezone

from src.config.schema_site import CheckConfig
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds
from src.infrastructure.llm import ScriptedLLM
from src.patrol.ledger import InMemoryLedger
from src.patrol.llm_judge import LlmBudget
from src.patrol.runner import run_check
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


def test_ledger_runs_limit_0은_빈_리스트():
    from src.domain.patrol import CheckOutcome
    ledger = InMemoryLedger()
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=T))
    assert ledger.runs("mx", "gumi", "c", limit=0) == []
    assert len(ledger.runs("mx", "gumi", "c", limit=5)) == 1
