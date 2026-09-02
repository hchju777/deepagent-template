from datetime import datetime, timedelta, timezone

from src.infrastructure.llm import ScriptedLLM
from src.patrol.llm_judge import LlmBudget, judge_by_llm

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_예산은_시간_창_슬라이딩():
    now = [T]
    budget = LlmBudget(2, clock=lambda: now[0])
    assert budget.try_acquire() and budget.try_acquire() and not budget.try_acquire()
    now[0] = T + timedelta(hours=1, seconds=1)
    assert budget.try_acquire()          # 창이 지나면 회복
    assert budget.remaining() == 1


async def test_finding은_실재_id만_인용하고_환각은_기각():
    llm = ScriptedLLM(['{"status": "finding", "summary": "멈춘 라인이 생산 중", '
                       '"evidence_ids": ["ev-1", "ev-99"]}'])
    out, err = await judge_by_llm(["ev-1"], {"ev-1": "line12 STOP, output +60/h"},
                                  "twin.consistency", "모순이 있는가?", llm=llm)
    assert err is None and out.status == "finding" and out.evidence_ids == ["ev-1"]

    ghost = ScriptedLLM(['{"status": "finding", "summary": "x", "evidence_ids": ["ev-99"]}'])
    out2, err2 = await judge_by_llm(["ev-1"], {"ev-1": "..."}, "c", "q", llm=ghost)
    assert out2 is None and "환각" in err2


async def test_파싱_실패와_호출_실패는_raise가_아니라_오류_반환():
    out, err = await judge_by_llm(["ev-1"], {"ev-1": "..."}, "c", "q", llm=ScriptedLLM(["말로만"]))
    assert out is None and err
    out2, err2 = await judge_by_llm(["ev-1"], {"ev-1": "..."}, "c", "q", llm=ScriptedLLM([]))
    assert out2 is None and "실패" in err2
