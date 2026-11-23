"""그래프 전체가 돌 때 울타리가 지켜지는가.

노드 하나하나는 `test_nodes.py`가 본다. 여기서 보는 것은 **배선까지 합쳤을 때**
라운드가 실제로 상한에서 멈추는가, barrier가 라운드 경계를 만드는가다.
"""
from src.application.fakes import ExplodingRunner, ScriptedRunner
from src.application.graph import build_engine
from src.application.state import CaseState

from tests.application.conftest import deps_for, plan, task


async def run(deps, case) -> dict:
    return await build_engine(deps).ainvoke(CaseState(case=case))


async def test_라운드가_상한에서_멈춘다(case):
    """대본이 매 라운드 새 태스크를 내므로 **멈추는 것은 코드뿐이다.**"""
    runner = ScriptedRunner()
    deps = deps_for(runner, first_tasks=[task("t-0")], max_rounds=3,
                    integrate=plan([task("t-1")], [task("t-2")], [task("t-3")],
                                   [task("t-4")], [task("t-5")]))
    final = await run(deps, case)
    assert final["round"] == 3
    assert final["stopped_by"] == "max_rounds"
    assert runner.ran == ["t-0", "t-1", "t-2"]


async def test_한_라운드에_병렬_폭만큼만_돈다(case):
    runner = ScriptedRunner()
    deps = deps_for(runner, first_tasks=[task(f"t-{i}") for i in range(1, 6)],
                    max_rounds=2, parallel_width=2)
    final = await run(deps, case)
    # 라운드 2개 × 폭 2 = 4개. 다섯 번째는 pending으로 남는다.
    assert len(runner.ran) == 4
    assert [t.status for t in final["plan_tasks"]] == ["ok"] * 4 + ["pending"]


async def test_게이트가_막은_태스크는_증거가_생긴_뒤에_돈다(case):
    """**이게 select 게이트가 존재하는 이유다.**

    frame은 증거가 하나도 없는 시점에 전체 체인을 계획한다. 게이트가 없으면 그
    체인이 라운드 1에 통째로 발사되고, 재계산 태스크는 입력 없이 전멸한다.
    우선순위가 제일 앞(5)인데도 1라운드에 안 도는 것을 본다.
    """
    from src.domain.case import EvidenceRef
    from src.domain.investigation import TaskOutcome

    runner = ScriptedRunner({"t-1": TaskOutcome(
        task_id="t-1", status="ok", summary="읽었다",
        evidence=[EvidenceRef(id="t-1.e1", source="redis.get", summary="512")])})
    deps = deps_for(runner, max_rounds=4, parallel_width=3, first_tasks=[
        task("t-1", priority=10),
        task("t-9", priority=5, input_evidence_ids=["t-1.e1"])])
    final = await run(deps, case)
    assert runner.ran == ["t-1", "t-9"]
    assert final["round"] == 2
    assert final["stopped_by"] == "no_runnable"


async def test_실행기가_전부_던져도_그래프가_끝까지_간다(case):
    """superstep이 죽으면 `ainvoke`가 던지고 케이스는 investigating으로 영원히 남는다."""
    deps = deps_for(ExplodingRunner(), first_tasks=[task("t-1"), task("t-2")],
                    max_rounds=2, parallel_width=2)
    final = await run(deps, case)
    assert [t.status for t in final["plan_tasks"]] == ["error", "error"]
    assert final["stopped_by"] == "no_runnable"


async def test_한_가지가_던져도_성공한_가지의_쓰기가_남는다(case):
    """Send 가지 하나의 예외가 **같은 라운드의 성공한 가지까지** 지우면 안 된다."""
    from src.domain.case import EvidenceRef
    from src.domain.investigation import TaskOutcome

    class HalfBroken(ScriptedRunner):
        async def run(self, task_, *, case):
            self.ran.append(task_.id)
            if task_.id == "t-2":
                raise RuntimeError("이 가지만 터진다")
            return TaskOutcome(task_id=task_.id, status="ok", summary="봤다",
                               evidence=[EvidenceRef(id=f"{task_.id}.e1",
                                                     source="s", summary="x")])

    deps = deps_for(HalfBroken(), first_tasks=[task("t-1"), task("t-2"), task("t-3")],
                    max_rounds=2, parallel_width=3)
    final = await run(deps, case)
    assert [t.status for t in final["plan_tasks"]] == ["ok", "error", "ok"]
    assert [e.id for e in final["evidence"]] == ["t-1.e1", "t-3.e1"]


async def test_conclude_결정이면_상한_전에도_끝난다(case):
    async def done(state):
        return {"decision": "conclude"}

    deps = deps_for(ScriptedRunner(), first_tasks=[task("t-1")], max_rounds=9,
                    integrate=done)
    final = await run(deps, case)
    assert (final["round"], final["stopped_by"]) == (1, "decision")


# ── R2-2b-2 — scan 결과와 선언된 키의 대조는 코드가 한다 ──

class _ScanRunner:
    def __init__(self, found, *, complete=True):
        self._found, self._complete = found, complete

    def describe(self):
        return "scan"

    async def run(self, task, *, case):
        from src.domain.case import EvidenceRef
        from src.domain.investigation import TaskOutcome
        return TaskOutcome(task_id=task.id, status="ok", summary=f"{len(self._found)}건", found=list(self._found),
                           evidence=[EvidenceRef(id=f"{task.id}.e1", source=f"redis.scan {task.params}",
                                                 summary=f"{len(self._found)}건", body="", complete=self._complete)])


async def _facts_after(case, *, pattern, found, complete=True):
    from dataclasses import replace
    deps = replace(deps_for(_ScanRunner(found, complete=complete), max_rounds=1,
                            first_tasks=[task("t-1", action="redis.scan", params={"pattern": pattern})]),
                   declared={"rediskey": ("alarm:stats:{line}", "hb:{service}")})
    return await run(deps, case)


async def test_완전한_scan에_선언된_키가_없으면_사실로_남긴다(case):
    """사내 실측 c-1: r2가 쥐고 있던 "요약 키가 없다"가 r3에서 사라졌다. 코드가 대조해 State에 남긴다 — 템플릿
    (`alarm:stats:{line}`)은 `{` 앞부분으로 맞춘다."""
    state = await _facts_after(case, pattern="*", found=["hb:sink", "hb:processor"])
    assert len(state["facts"]) == 1
    fact = state["facts"][0]
    assert "선언됐는데 없는 키 1개" in fact and "alarm:stats:{line}" in fact and "hb:{service}" not in fact
    assert "t-1" in fact and "선언됐는데 없는 키" in state["plan_tasks"][0].result_summary


async def test_패턴이_안_덮는_키와_잘린_scan은_없다고_하지_않는다(case):
    narrow = await _facts_after(case, pattern="hb:*", found=["hb:sink"])
    assert narrow["facts"] == []                                   # `hb:*`는 alarm:stats에 대해 아무 말도 안 한다
    cut = await _facts_after(case, pattern="*", found=["hb:sink"], complete=False)
    assert cut["facts"] == []                                      # 잘린 표본으로는 "없다"를 주장할 수 없다
