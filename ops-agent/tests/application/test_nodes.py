"""노드가 쥔 통제 경계 — 게이트·소독·상한·무raise."""
from src.application.fakes import ExplodingRunner, ScriptedRunner
from src.application.nodes import make_nodes, route_after_select, runnable_tasks
from src.application.state import CaseState
from src.domain.case import EvidenceRef, PlanTask
from src.domain.investigation import TaskOutcome

from tests.application.conftest import deps_for, ok, task


def _ev(evidence_id: str) -> EvidenceRef:
    return EvidenceRef(id=evidence_id, source="s", summary="x")


# ── select 게이트 ───────────────────────────────────────────────────

def test_입력_증거가_전부_있어야_실행_가능하다(case):
    """`any()`가 아니라 `all()`이다.

    재계산 태스크는 "원천값"과 "로직 명세" 둘 다 있어야 성립한다. 하나만 있어도
    돌리면 절반의 입력으로 기대값을 만들고, 그게 실제와 다른 것을 "이상 발견"으로
    보고한다 — **틀린 케이스가 아니라 없는 이상을 만들어 내는 것**이라 더 나쁘다.
    """
    state = CaseState(case=case, evidence=[_ev("t-1.e1")], plan_tasks=[
        task("t-9", input_evidence_ids=["t-1.e1", "t-2.e1"])])
    assert runnable_tasks(state) == []

    state = state.model_copy(update={"evidence": [_ev("t-1.e1"), _ev("t-2.e1")]})
    assert [t.id for t in runnable_tasks(state)] == ["t-9"]


def test_입력_증거가_없는_태스크는_바로_실행된다(case):
    state = CaseState(case=case, plan_tasks=[task("t-1")])
    assert [t.id for t in runnable_tasks(state)] == ["t-1"]


def test_우선순위가_낮은_것부터_동률이면_FIFO(case):
    state = CaseState(case=case, plan_tasks=[
        task("t-1", priority=50), task("t-2", priority=10),
        task("t-3", priority=50), task("t-4", priority=1)])
    assert [t.id for t in runnable_tasks(state)] == ["t-4", "t-2", "t-1", "t-3"]


def test_pending이_아닌_태스크는_다시_안_고른다(case):
    state = CaseState(case=case, plan_tasks=[
        task("t-1", status="ok"), task("t-2", status="running"),
        task("t-3", status="error"), task("t-4")])
    assert [t.id for t in runnable_tasks(state)] == ["t-4"]


async def test_select는_병렬_폭만큼만_running으로_굴린다(case):
    nodes = make_nodes(deps_for(ScriptedRunner(), parallel_width=2))
    state = CaseState(case=case, plan_tasks=[task(f"t-{i}") for i in range(1, 5)])
    patch = await nodes["select"](state)
    assert [t.id for t in patch["plan_tasks"]] == ["t-1", "t-2"]
    assert all(t.status == "running" for t in patch["plan_tasks"])


def test_라우터는_select가_굴린_것만_발사한다(case):
    """이번 라운드 몫만 정확히 잡아야 한다 — 지난 라운드의 ok/error는 안 섞인다."""
    state = CaseState(case=case, plan_tasks=[
        task("t-1", status="ok"), task("t-2", status="running"),
        task("t-3", status="running"), task("t-4", status="pending")])
    sends = route_after_select(state)
    assert [s.arg["task"]["id"] for s in sends] == ["t-2", "t-3"]
    assert all(s.arg["case"]["id"] == "c-1" for s in sends)


def test_굴린_것이_없으면_integrate로_간다(case):
    state = CaseState(case=case, plan_tasks=[task("t-1", status="ok")])
    assert route_after_select(state) == "integrate"


# ── 소독 (규율 4) ──────────────────────────────────────────────────

async def test_만들어진_태스크의_수명주기_필드는_코드가_덮어쓴다(case):
    """10b에서 이 자리에 LLM이 들어온다.

    `{"status": "ok", "result_evidence_ids": ["ev-9"]}`를 실어 보내면 그 태스크는
    실행되지 않은 채 "끝난 것"이 되어 select 게이트를 통째로 우회한다. 그리고
    있지도 않은 증거 id가 State에 들어간다.
    """
    dirty = PlanTask(id="t-1", goal="g", role="data_prober", status="ok",
                     result_summary="봤다고 치자", result_evidence_ids=["ev-9"],
                     error="아무 말")
    nodes = make_nodes(deps_for(ScriptedRunner(), first_tasks=[dirty]))
    patch = await nodes["frame"](CaseState(case=case))
    got = patch["plan_tasks"][0]
    assert (got.status, got.result_summary, got.result_evidence_ids, got.error) \
        == ("pending", None, [], None)


async def test_태스크_개수_상한을_넘겨_만들면_잘린다(case):
    nodes = make_nodes(deps_for(ScriptedRunner(),
                                first_tasks=[task(f"t-{i}") for i in range(10)],
                                max_tasks=3))
    patch = await nodes["frame"](CaseState(case=case))
    assert len(patch["plan_tasks"]) == 3


# ── execute 무raise ────────────────────────────────────────────────

async def test_실행기가_던져도_라운드가_살아남는다(case):
    """**대본이 아니라 실제로 던지는 실행기**로 본다.

    대본 실행기는 예약된 `status="error"`를 돌려줄 뿐이라 "얌전히 실패를 보고한
    것"이고, 그걸로는 방어를 지워도 초록이다. 이 리포에서 `ScriptedAdapter`가
    똑같은 거짓 초록을 만든 적이 있다.

    LangGraph에서 Send 가지 하나의 예외는 superstep 전체를 실패시켜 **같은 라운드의
    성공한 가지까지 지운다.**
    """
    nodes = make_nodes(deps_for(ExplodingRunner("대상이 터졌다")))
    patch = await nodes["execute"]({"task": task("t-1").model_dump(mode="json"),
                                    "case": case.model_dump(mode="json")})
    done = patch["plan_tasks"][0]
    assert done.status == "error"
    assert "RuntimeError" in done.error and "대상이 터졌다" in done.error
    assert patch["evidence"] == []


async def test_실행기가_낸_증거만_State에_오른다(case):
    """LLM이 "ev-9를 봤다"고 말해도 도구가 안 만들었으면 없어야 한다(규율 3)."""
    made = EvidenceRef(id="t-1.e1", source="redis.get", summary="512")
    runner = ScriptedRunner({"t-1": TaskOutcome(task_id="t-1", status="ok",
                                                summary="읽었다", evidence=[made])})
    nodes = make_nodes(deps_for(runner))
    patch = await nodes["execute"]({"task": task("t-1").model_dump(mode="json"),
                                    "case": case.model_dump(mode="json")})
    assert [e.id for e in patch["evidence"]] == ["t-1.e1"]
    assert patch["plan_tasks"][0].result_evidence_ids == ["t-1.e1"]


# ── integrate의 상한 ───────────────────────────────────────────────

async def test_상한에_닿으면_계속하자는_결정을_무시한다(case):
    nodes = make_nodes(deps_for(ScriptedRunner(), max_rounds=3))
    patch = await nodes["integrate"](CaseState(case=case, round=3,
                                               plan_tasks=[task("t-1")]))
    assert patch["decision"] == "conclude"
    assert patch["stopped_by"] == "max_rounds"


async def test_상한_전이면_계속하고_라운드가_오른다(case):
    nodes = make_nodes(deps_for(ScriptedRunner(), max_rounds=3))
    patch = await nodes["integrate"](CaseState(case=case, round=1,
                                               plan_tasks=[task("t-1")]))
    assert (patch["decision"], patch["round"]) == ("continue", 2)


async def test_돌릴_것이_없으면_빈_라운드를_안_돈다(case):
    """계속하자는데 실행 가능 태스크가 없으면 상한까지 빈 라운드를 돈다.

    그러면 보고서가 "N라운드 조사했다"고 적는데 실제로는 아무것도 안 했다 —
    **한 일이 없는 것이 한 일이 많은 것처럼 보이는** 형태라 조용히 거짓말이 된다.
    """
    nodes = make_nodes(deps_for(ScriptedRunner(), max_rounds=9))
    patch = await nodes["integrate"](CaseState(case=case, round=1,
                                               plan_tasks=[task("t-1", status="ok")]))
    assert patch["stopped_by"] == "no_runnable"


async def test_integrate가_새_태스크를_내면_계속한다(case):
    """"돌릴 것 없음" 판정은 방금 만들어진 태스크까지 합쳐서 본다."""
    async def adds(state):
        return {"decision": "continue", "plan_tasks": [task("t-2")]}

    nodes = make_nodes(deps_for(ScriptedRunner(), integrate=adds, max_rounds=9))
    patch = await nodes["integrate"](CaseState(case=case, round=1,
                                               plan_tasks=[task("t-1", status="ok")]))
    assert patch["decision"] == "continue"
