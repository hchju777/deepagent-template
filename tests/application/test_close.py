from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from src.application.close import close_case, sweep_timeouts
from src.application.lifecycle import transition
from src.domain.cases import CaseRecord, InMemoryCaseRepository

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _waiting(repo, cid, updated_at):
    r = CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint=cid, symptom="s", t0=T,
                   created_at=T, updated_at=T, thread_ids=[f"{cid}#1"],
                   thread_versions={f"{cid}#1": 1})
    r = transition(r, "investigating", clock=lambda: T)
    r = transition(r, "awaiting_human", clock=lambda: updated_at)
    repo.save(r)
    return r


async def test_close_case는_종결과_스레드_폐기를_한_동작으로():
    repo, saver = InMemoryCaseRepository(), InMemorySaver()
    _waiting(repo, "c-1", T)
    saver.put({"configurable": {"thread_id": "c-1#1", "checkpoint_ns": ""}},
              empty_checkpoint(), {"source": "input", "step": -1, "parents": {}}, {})
    closed = await close_case("c-1", repo=repo, checkpointer=saver, clock=lambda: T,
                              reason="테스트 종결", discard_threads=True)
    assert closed.status == "closed" and closed.closed_reason == "테스트 종결"
    assert saver.get({"configurable": {"thread_id": "c-1#1"}}) is None      # 스레드 폐기


async def test_sweep은_타임아웃된_awaiting만_닫는다():
    repo = InMemoryCaseRepository()
    _waiting(repo, "c-old", T - timedelta(hours=80))
    _waiting(repo, "c-new", T - timedelta(hours=1))
    closed = await sweep_timeouts(repo=repo, checkpointer=None, clock=lambda: T, timeout_h=72)
    assert closed == ["c-old"]
    assert repo.get("c-old").status == "closed" and "타임아웃" in repo.get("c-old").closed_reason
    assert repo.get("c-new").status == "awaiting_human"
