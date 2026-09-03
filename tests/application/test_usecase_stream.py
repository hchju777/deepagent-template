"""on_event 싱크가 그래프 updates를 봉투로 받아보는지 — 가짜 엔진으로 결정론 검증."""
from datetime import datetime, timezone

from src.application.usecase import investigate_case
from src.domain.case import Case, PlanTask
from tests.application.test_nodes_frame import _deps

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CASE = Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T)


class FakeStreamEngine:
    """astream은 updates 덩어리를, aget_state는 최종 state를 준다."""

    def __init__(self, chunks, final):
        self._chunks, self._final = chunks, final
        self.ainvoke_calls = 0

    async def astream(self, state, config=None, stream_mode=None):
        assert stream_mode == "updates"
        for chunk in self._chunks:
            yield chunk

    async def aget_state(self, config):
        class S:
            values = self._final
            tasks = ()
        return S()

    async def ainvoke(self, state, config=None):
        self.ainvoke_calls += 1
        return self._final


async def test_싱크가_있으면_스트리밍하고_최종_state를_그대로_돌려준다():
    running = PlanTask(id="t-1", goal="g", role="data_prober", status="running")
    done = running.model_copy(update={"status": "ok", "result_evidence_ids": ["ev-1"]})
    engine = FakeStreamEngine([{"select": {"plan_tasks": [running]}},
                               {"execute": {"plan_tasks": [done]}}],
                              {"verdict": None, "round": 1})
    seen = []
    result = await investigate_case(CASE, deps=_deps([]), engine=engine, on_event=seen.append)
    assert result == {"verdict": None, "round": 1} and engine.ainvoke_calls == 0
    assert [e.event for e in seen] == ["round_started", "task_finished"]


async def test_싱크가_없으면_ainvoke_경로_그대로():
    engine = FakeStreamEngine([], {"verdict": None})
    result = await investigate_case(CASE, deps=_deps([]), engine=engine)
    assert result == {"verdict": None} and engine.ainvoke_calls == 1


async def test_싱크가_터져도_조사는_계속된다():
    engine = FakeStreamEngine([{"select": {"plan_tasks": []}}], {"ok": True})
    def boom(event):
        raise RuntimeError("싱크 고장")
    assert await investigate_case(CASE, deps=_deps([]), engine=engine, on_event=boom) == {"ok": True}
