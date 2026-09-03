from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import RetentionConfig
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.patrol import CheckOutcome
from src.domain.store import InMemoryCaseStore
from src.infrastructure.retention import sweep_retention
from src.patrol.ledger import InMemoryLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


async def test_스윕은_오래된_종결_케이스와_레저와_스크래치를_정리한다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    old, fresh = T - timedelta(days=100), T - timedelta(days=1)
    for cid, at in (("c-old", old), ("c-fresh", fresh)):
        repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint=cid, symptom="s", t0=T,
                             created_at=at, updated_at=at, status="closed", thread_ids=[f"{cid}#1"]))
        store.put_evidence(cid, "s", {"x": 1}, as_of=at)
    # I7: closed_case_evidence_d(90d)보다 짧은 checkpoint_ttl_d(14d) 구간에 있는
    # 닫힌 케이스 — 증거는 아직 살아있어야 하지만 스레드는 ④가 폐기해야 한다.
    mid = T - timedelta(days=20)
    repo.save(CaseRecord(id="c-mid", gbm="mx", fct="gumi", fingerprint="c-mid", symptom="s", t0=T,
                         created_at=mid, updated_at=mid, status="closed", thread_ids=["c-mid#1"],
                         status_since=mid))
    store.put_evidence("c-mid", "s", {"x": 1}, as_of=mid)
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=old))
    ledger.record_run("mx", "gumi", "c", CheckOutcome(status="ok", observed_at=fresh))
    store.put_evidence("patrol:mx:gumi:c", "s", {"p": 1}, as_of=old)
    store.put_evidence("patrol:mx:gumi:c", "s", {"p": 2}, as_of=fresh)
    # F6/F5: 발송 레저(sends)도 다른 항목들과 같은 스윕으로 정리돼야 한다 —
    # 오래된 건(sent 여부 무관)은 지워지고, 최근 건은 pending으로 남는다.
    ledger.record_send("report:c-old", kind="report", target="a@x", at=old)
    ledger.record_send("report:c-fresh", kind="report", target="a@x", at=fresh)
    counts = await sweep_retention(repo=repo, store=store, ledger=ledger, checkpointer=InMemorySaver(),
                                   clock=lambda: T, retention=RetentionConfig())
    assert counts["closed_cases"] == 1 and store.list_evidence("c-old") == [] and store.list_evidence("c-fresh")
    assert counts["ledger_runs"] == 1 and len(ledger.runs("mx", "gumi", "c")) == 1
    assert counts["scratch_evidence"] == 1 and len(store.list_evidence("patrol:mx:gumi:c")) == 1
    assert counts["sends"] == 1
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-fresh"]
    assert repo.get("c-old").thread_ids == []
    assert repo.get("c-old").purged_at == T                       # I7: 재선택 방지 스탬프
    assert repo.get("c-fresh").purged_at is None                  # 아직 안 닫힌(최근) 건 안 건드림
    # I7: c-mid는 90일이 안 지나 증거는 남지만(purge 대상 아님), 14일은 지나 스레드는 폐기된다
    assert repo.get("c-mid").thread_ids == [] and store.list_evidence("c-mid")
    assert repo.get("c-mid").purged_at is None
    assert counts["expired_threads"] >= 1


async def test_오래된_열린_케이스의_스레드는_폐기되고_기록에서_제거된다():
    from langgraph.checkpoint.base import empty_checkpoint
    from src.application.lifecycle import transition
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    saver = InMemorySaver()
    stale = T - timedelta(days=20)
    rec = CaseRecord(id="c-wait", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                     created_at=stale, updated_at=stale, thread_ids=["c-wait#1"],
                     thread_versions={"c-wait#1": 1})
    rec = transition(rec, "investigating", clock=lambda: stale)
    rec = transition(rec, "awaiting_human", clock=lambda: stale)
    repo.save(rec)
    saver.put({"configurable": {"thread_id": "c-wait#1", "checkpoint_ns": ""}},
              empty_checkpoint(), {"source": "input", "step": -1, "parents": {}}, {})
    counts = await sweep_retention(repo=repo, store=store, ledger=ledger, checkpointer=saver,
                                   clock=lambda: T, retention=RetentionConfig())
    assert counts["expired_threads"] == 1
    after = repo.get("c-wait")
    assert after.status == "awaiting_human"                       # 케이스는 그대로
    assert after.thread_ids == [] and after.thread_versions == {}
    assert saver.get({"configurable": {"thread_id": "c-wait#1"}}) is None


async def test_보존_스윕은_오래된_이벤트를_지운다():
    from src.domain.events import EngineEvent, InMemoryEventStore
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    events = InMemoryEventStore()
    events.append(EngineEvent(event="round_started", case_id="c-1", at=T - timedelta(days=40)))
    events.append(EngineEvent(event="round_started", case_id="c-1", at=T))

    counts = await sweep_retention(repo=repo, store=store, ledger=ledger, events=events,
                                   checkpointer=None, clock=lambda: T,
                                   retention=RetentionConfig(events_d=30))
    assert counts["events"] == 1
    assert [e.seq for e in events.since("c-1")] == [2]
