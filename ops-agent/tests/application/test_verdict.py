"""12a — conclude + verify. **판정은 항상 생기고, 인용은 코드가 검사한다.**

- conclude: 조사가 어떻게 끝났든 `Verdict`가 State에 남는다. 조사가 안 돌았거나(llm_error)
  볼 것이 없었으면(증거 0) LLM을 묻지 않고 코드가 `degraded`를 찍는다.
- verify: LLM 없이 인용·불완전 증거·component 이름을 검사한다. 문제가 있으면 한 번 되묻고,
  그래도 안 되면 없는 인용을 걷어내고 낮은 확신으로 통과시킨다.
"""
import dataclasses
import json

from src.application.fakes import ScriptedRunner
from src.application.graph import build_engine
from src.application.nodes import (MAX_ALTERNATES, make_nodes, route_after_frame,
                                   route_after_integrate, route_after_verify,
                                   sanitize_verdict, verify_verdict)
from src.application.state import CaseState
from src.domain.case import CauseLink, EvidenceRef, Verdict
from src.domain.investigation import TaskOutcome

from tests.application.conftest import deps_for, task


def _ev(evidence_id: str, *, complete: bool = True, body: str = "") -> EvidenceRef:
    return EvidenceRef(id=evidence_id, source="mongo.find collection='alarm_events'",
                       summary="x", body=body, complete=complete)


def cause(component: str, *ids, **over) -> CauseLink:
    return CauseLink(component=component, evidence_ids=list(ids), **over)


def verdict(**over) -> Verdict:
    body = {"verdict_type": "data_loss", "confidence": "high", "narrative": "sink가 멈췄다",
            "root_cause": cause("sink", "t-1.e1")}
    body.update(over)
    return Verdict.model_validate(body)


class Concluder:
    """`deps.conclude` 자리의 대본. 부른 횟수와 받은 State를 남긴다."""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls: list[CaseState] = []

    async def __call__(self, state: CaseState) -> dict:
        self.calls.append(state)
        reply = self._replies.pop(0) if self._replies else {"verdict": verdict()}
        if isinstance(reply, Exception):
            raise reply
        return reply


def _state(case, **over) -> CaseState:
    base = {"round": 2, "stopped_by": "decision", "decision": "conclude",
            "evidence": [_ev("t-1.e1")]}
    base.update(over)
    return CaseState(case=case, **base)


# ── conclude: 코드가 먼저 가르는 두 degraded ─────────────────────────

async def test_조사가_안_돌았으면_LLM을_묻지_않고_degraded다(case):
    """`stopped_by=llm_error`에 **증거까지 없으면** 판정을 묻지 않는다 — 인용할 것이 없는 판정은 근거가 없다.
    (증거가 있으면 한 번 묻는다, R2-2a — 아래 `test_llm_error여도_증거가_있으면_판정을_한_번_묻는다`.)
    서술은 사실이어야 한다: 몇 라운드에 무엇을 읽었나(사내 실측에서 "돌지 않았다"가 거짓이었다)."""
    concluder = Concluder()
    nodes = make_nodes(deps_for(ScriptedRunner(), conclude=concluder))
    state = _state(case, stopped_by="llm_error", evidence=[],
                   llm_errors=["frame: 2회 시도 실패 — ConnectTimeout"])
    patch = await nodes["conclude"](state)
    got = patch["verdict"]
    assert got.verdict_type == "degraded" and got.confidence == "low"
    assert got.root_cause is None
    assert "조사 중단" in got.narrative and "증거 0건" in got.narrative
    assert any("ConnectTimeout" in c for c in got.caveats)
    assert concluder.calls == []


async def test_증거가_하나도_없으면_degraded이고_실패한_태스크가_caveat에_남는다(case):
    concluder = Concluder()
    nodes = make_nodes(deps_for(ScriptedRunner(), conclude=concluder))
    state = _state(case, stopped_by="no_runnable", evidence=[], plan_tasks=[
        task("t-1", status="error", error="ReadTimeout: 5s"),
        task("t-2", status="error")])
    patch = await nodes["conclude"](state)
    got = patch["verdict"]
    assert got.verdict_type == "degraded"
    assert "t-1: ReadTimeout: 5s" in got.caveats and "t-2: 원인 불명" in got.caveats
    assert concluder.calls == []


async def test_판정을_못_받으면_degraded이고_사유가_남는다(case):
    """리드가 JSON을 못 내면 **판정 없음이 아니라 degraded**다 — 그래야 보고서가 "판정 불가"를
    적을 수 있고, 사유가 `llm_errors`에도 남아 프롬프트가 안 먹히는 것이 보인다."""
    concluder = Concluder({"llm_errors": ["conclude: 2회 시도 실패 — JSON으로 읽을 수 없다"]})
    nodes = make_nodes(deps_for(ScriptedRunner(), conclude=concluder))
    patch = await nodes["conclude"](_state(case))
    got = patch["verdict"]
    assert got.verdict_type == "degraded"
    assert any("JSON으로 읽을 수 없다" in c for c in got.caveats)
    assert any("JSON으로 읽을 수 없다" in e for e in patch["llm_errors"])
    assert len(concluder.calls) == 1


async def test_판정자가_던져도_흡수한다(case):
    """무raise(규율 1) — 판정자는 LLM 어댑터를 품고 있어 던질 수 있다. 여기서 죽으면
    조사 전체가 `investigating`으로 남는다."""
    nodes = make_nodes(deps_for(ScriptedRunner(), conclude=Concluder(RuntimeError("폭발"))))
    patch = await nodes["conclude"](_state(case))
    assert patch["verdict"].verdict_type == "degraded"
    assert any("RuntimeError" in c for c in patch["verdict"].caveats)


async def test_판정자가_없으면_degraded다(case):
    """대본 경로가 판정을 안 실었을 때 — 조용히 None이 아니라 "판정자가 없다"가 남는다."""
    nodes = make_nodes(deps_for(ScriptedRunner()))
    patch = await nodes["conclude"](_state(case))
    assert patch["verdict"].verdict_type == "degraded"
    assert any("판정자" in c for c in patch["verdict"].caveats)


async def test_판정자의_비LLM_사유는_caveat에만_남는다(case):
    """대본이 판정을 안 실은 것은 리드 계약 위반이 아니다 — `llm_errors`로 세면 거짓 양성이다."""
    nodes = make_nodes(deps_for(ScriptedRunner(), conclude=Concluder({"note": "대본에 verdict가 없다"})))
    patch = await nodes["conclude"](_state(case))
    assert patch["verdict"].verdict_type == "degraded"
    assert "대본에 verdict가 없다" in patch["verdict"].caveats
    assert patch["llm_errors"] == []


# ── 인과 사슬의 형태는 코드가 정한다 (규율 4·6) ──────────────────────

def test_인과_사슬의_형태는_코드가_정한다():
    many = [cause("a", "t-1.e1", confidence="low"), cause("b", "t-1.e1"), cause("a", "t-1.e1"),
            cause("  ", "t-1.e1"), cause("c", "t-1.e1"), cause("d", "t-1.e1")]
    got = sanitize_verdict(verdict(
        root_cause=cause(" sink ", "t-1.e1", confidence="high", relation="x" * 400),
        alternates=many, contributing=[cause("api", "t-1.e1", confidence="medium")]))
    assert got.root_cause.component == "sink"
    assert got.root_cause.confidence is None and got.contributing[0].confidence is None
    assert len(got.root_cause.relation) == 300 and got.root_cause.relation.endswith("…")
    assert [a.component for a in got.alternates] == ["a", "b", "c"][:MAX_ALTERNATES]
    assert "sink" not in [a.component for a in got.alternates]
    assert any("후보 정리" in c and "d" in c for c in got.caveats)


def test_바뀐_것이_없으면_같은_객체다():
    v = verdict(alternates=[cause("api", "t-1.e1", confidence="low")])
    assert sanitize_verdict(v) is v


# ── verify: LLM 없는 검사 ────────────────────────────────────────────

def _problems(v: Verdict, *, citable=("t-1.e1",), incomplete=(), ok=lambda c: True):
    # `ok`는 옛 모양(참/거짓)의 편의 — 노드는 사유 문자열을 돌려주는 `component_problem`을 쓴다.
    return verify_verdict(v, citable=set(citable), incomplete=set(incomplete), component_problem=lambda c: None if ok(c) else f"증거에도 토폴로지에도 없는 component {c!r}")


def test_verify는_리드가_본_증거만_인용으로_친다():
    """우주는 `state.evidence`다 — Store 전체가 아니다(규율 3)."""
    assert _problems(verdict()) == []
    got = _problems(verdict(root_cause=cause("sink", "t-1.e1", "t-9.e1")))
    assert got == ["없는 id t-9.e1 인용 (sink)"]


def test_후보와_기여_요인의_인용도_검사한다():
    got = _problems(verdict(alternates=[cause("api", "ghost.e1", confidence="low")],
                            contributing=[cause("processor")]))
    assert "없는 id ghost.e1 인용 (api)" in got
    assert "다리에 인용 없음: processor" in got


def test_잘린_증거로_주장하면_caveat에_그_id가_있어야_한다():
    bad = verdict(root_cause=cause("sink", "t-1.e1"))
    assert _problems(bad, incomplete=("t-1.e1",)) == ["불완전 증거 t-1.e1가 caveat에 명시되지 않음"]
    ok = verdict(root_cause=cause("sink", "t-1.e1"), caveats=["t-1.e1은 표본이 잘려 상한 밖은 못 봤다"])
    assert _problems(ok, incomplete=("t-1.e1",)) == []
    # 토큰 경계 — `t-1.e1`이 `t-1.e10` 안에 있다고 명시된 것이 아니다.
    near = verdict(root_cause=cause("sink", "t-1.e1"), caveats=["t-1.e10만 잘렸다"])
    assert _problems(near, incomplete=("t-1.e1",)) != []


def test_component는_토폴로지나_증거에_있어야_한다():
    """지어낸 서비스 이름이 판정의 최상위에 올라오면 보고서가 없는 부품을 가리킨다 —
    `찾지 않고 이름을 댔다`(태스크)의 판정판이다."""
    known = {"sink", "alarm_events"}
    good = verdict(root_cause=cause("sink", "t-1.e1"), alternates=[cause("alarm_events", "t-1.e1", confidence="low")])
    assert _problems(good, ok=lambda c: c in known) == []
    bad = verdict(root_cause=cause("alarm-svc", "t-1.e1"))
    assert _problems(bad, ok=lambda c: c in known) == ["증거에도 토폴로지에도 없는 component 'alarm-svc'"]


async def test_verify_노드의_component는_토폴로지_서비스여야_하고_external만_증거의_이름을_허용한다(case):
    """사내 10-08(c-2): 원인 칸에 Redis 키 이름이 들어갔는데 통과했다. 토폴로지를 아는 조사에서는 **서비스만** — 데이터의
    이름은 원인이 아니다. `external`은 바깥 시스템이라 토폴로지에 없으니 증거에 나온 이름이면 된다. 토폴로지가 없는 조사
    (`components` 비어 있음)는 옛 규칙(증거에 나온 이름)대로."""
    state = _state(case, evidence=[_ev("t-1.e1", body="collection alarm_events 6건 · sink 그룹 lag 1830")],
                   verdict=verdict(root_cause=cause("alarm_events", "t-1.e1"),
                                   alternates=[cause("sink", "t-1.e1", confidence="low"),
                                               cause("ghost-svc", "t-1.e1", confidence="low")]))
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink"})))
    patch = await nodes["verify"](state)
    # 후보(ghost-svc)의 서비스 아닌 component는 되묻지 않고 그 후보만 뺀다(R3-5d ①-b) — 문제로 남는 것은 최상위뿐
    assert [p.split(" — ")[0] for p in patch["verify_problems"]] == ["토폴로지 서비스가 아닌 component 'alarm_events'"]
    assert "(sink)" in patch["verify_problems"][0]                              # 어느 이름이면 되는지 같이
    fine = state.model_copy(update={"verdict": verdict(root_cause=cause("sink", "t-1.e1"))})
    good = await nodes["verify"](fine)
    assert good["verify_problems"] == [] and "verdict" not in good                 # 서비스 이름은 그대로 지난다
    external = state.model_copy(update={"verdict": verdict(verdict_type="external", root_cause=cause("alarm_events", "t-1.e1"))})
    assert (await nodes["verify"](external))["verify_problems"] == []
    blind = make_nodes(deps_for(ScriptedRunner(), components=frozenset()))
    patch = await blind["verify"](state)
    assert patch["verify_problems"] == [] and patch["verdict"].caveats == ["후보 제외(서비스도 이 케이스의 토픽도 아닌 component): ghost-svc"]
    loose = make_nodes(deps_for(ScriptedRunner(), check_discovery=False))
    assert (await loose["verify"](state))["verify_problems"] == []


async def test_첫_실패는_재작성을_요구하고_두_번째_실패는_강등한다(case):
    """한 번은 되묻는다(decisions ⑯). 두 번째도 안 되면 **없는 인용을 걷어내고** 낮은 확신으로
    통과시킨다 — 세 번째는 없다. 근거가 전부 사라진 최상위는 `inconclusive`가 된다
    (`_accept_hypotheses`가 근거 잃은 supported를 open으로 되돌리는 것과 같은 규칙)."""
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink", "api"})))
    bad = verdict(root_cause=cause("sink", "ghost.e1"),
                  alternates=[cause("api", "t-1.e1", "ghost.e2", confidence="low")])
    first = await nodes["verify"](_state(case, verdict=bad))
    assert first["verify_problems"] and first["verify_attempts"] == 1
    assert "verdict" not in first
    assert any(e.startswith("verify:") for e in first["llm_errors"])
    assert route_after_verify(CaseState.model_validate({**_state(case).model_dump(), **first})) == "conclude"

    second = await nodes["verify"](_state(case, verdict=bad, verify_attempts=1,
                                          verify_problems=first["verify_problems"]))
    got = second["verdict"]
    assert second["verify_problems"] == []
    assert got.confidence == "low" and got.verdict_type == "inconclusive"
    assert got.root_cause is None
    assert got.alternates[0].evidence_ids == ["t-1.e1"]        # 없는 id만 걷어냈다
    assert any("검증 미통과" in c for c in got.caveats)
    assert any("sink" in c for c in got.caveats)               # 걷어낸 최상위를 적는다
    assert route_after_verify(CaseState.model_validate({**_state(case).model_dump(), **second})) == "__end__"


async def test_통과하면_판정을_건드리지_않는다(case):
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink"})))
    patch = await nodes["verify"](_state(case, verdict=verdict()))
    assert patch == {"verify_problems": []}


async def test_판정이_없으면_verify는_빈손으로_통과한다(case):
    nodes = make_nodes(deps_for(ScriptedRunner()))
    assert (await nodes["verify"](_state(case)))["verify_problems"] == []


# ── 배선 ─────────────────────────────────────────────────────────────

def test_라우터들(case):
    assert route_after_integrate(_state(case, decision="continue")) == "select"
    assert route_after_integrate(_state(case, decision="conclude")) == "conclude"
    assert route_after_frame(_state(case, stopped_by=None)) == "select"
    # frame이 죽어도 **판정(degraded)은 남아야** 한다 — END로 바로 가면 판정 없는 끝이 생긴다.
    assert route_after_frame(_state(case, stopped_by="llm_error")) == "conclude"


def _runner():
    return ScriptedRunner({"t-1": TaskOutcome(task_id="t-1", status="ok", summary="봤다",
                                              evidence=[_ev("t-1.e1")])})


async def _conclude_now(state):
    return {"decision": "conclude"}


async def test_그래프가_판정까지_돈다(case):
    concluder = Concluder({"verdict": verdict()})
    deps = deps_for(_runner(), first_tasks=[task("t-1")], integrate=_conclude_now,
                    conclude=concluder, components=frozenset({"sink"}))
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert final["stopped_by"] == "decision"
    assert final["verdict"].verdict_type == "data_loss"
    assert final["verify_problems"] == [] and final["verify_attempts"] == 0
    assert len(concluder.calls) == 1 and concluder.calls[0].evidence[0].id == "t-1.e1"


async def test_frame이_죽어도_판정은_남는다(case):
    async def dead(state):
        return {"llm_errors": ["frame: 2회 시도 실패 — 429"], "decision": "conclude",
                "stopped_by": "llm_error"}

    concluder = Concluder()
    deps = deps_for(_runner(), conclude=concluder)
    deps = dataclasses.replace(deps, frame=dead)
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert final["stopped_by"] == "llm_error"
    assert final["verdict"].verdict_type == "degraded"
    assert concluder.calls == []


async def test_재작성_한_번_뒤_통과한다(case):
    """첫 판정이 없는 id를 인용했다 → verify가 되묻는다 → 두 번째 판정이 통과한다.
    되물을 때 판정자는 **문제 목록이 실린 State**를 받는다."""
    concluder = Concluder({"verdict": verdict(root_cause=cause("sink", "ghost.e1"))},
                          {"verdict": verdict()})
    deps = deps_for(_runner(), first_tasks=[task("t-1")], integrate=_conclude_now,
                    conclude=concluder, components=frozenset({"sink"}))
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert len(concluder.calls) == 2
    assert concluder.calls[1].verify_problems == ["없는 id ghost.e1 인용 (sink)"]
    assert final["verdict"].root_cause.evidence_ids == ["t-1.e1"]
    assert final["verdict"].confidence == "high"
    assert final["verify_problems"] == [] and final["verify_attempts"] == 1


async def test_상한으로_끝나도_판정이_있다(case):
    concluder = Concluder({"verdict": Verdict(verdict_type="inconclusive", confidence="low",
                                              narrative="못 가렸다")})
    deps = deps_for(_runner(), first_tasks=[task("t-1")], max_rounds=2, conclude=concluder)
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert final["stopped_by"] in ("max_rounds", "no_runnable")
    assert final["verdict"].verdict_type == "inconclusive"


def test_degraded_판정에는_검증_해당_없음이_붙는다(case):
    """degraded는 코드가 찍고 verify를 안 거친다 — 사내 실측에서 `검증 통과`로 찍혀 "검증을 지났다"로 읽혔다."""
    from src.application.diagnose import verdict_summary, verify_note
    from src.application.nodes import degraded

    state = _state(case, verdict=degraded("조사 실패 — 리드 LLM이 응답하지 못해 조사가 돌지 않았다"), stopped_by="llm_error")
    assert verify_note(state) == "검증 해당 없음" and "검증 해당 없음" in verdict_summary(state)
    normal = _state(case, verdict=Verdict(verdict_type="inconclusive", confidence="low", narrative="모름"))
    assert verify_note(normal) == "검증 통과"


# ── R2-2a — llm_error여도 증거가 있으면 판정을 한 번 묻는다, degraded 서술은 사실로 ──

async def _dead_integrate(state):
    return {"llm_errors": ["integrate: 시간 초과 (181초) — APITimeoutError"], "decision": "conclude",
            "stopped_by": "llm_error"}


async def test_llm_error여도_증거가_있으면_판정을_한_번_묻는다(case):
    """사내 실측 두 판: r1 integrate가 죽자 그때까지 모은 증거를 두고도 degraded로 끝났다. 액션 턴이 죽었다고 판정 턴까지
    죽은 것은 아니다(다른 모델일 수 있다, R2-1) — 증거가 있으면 **한 번** 묻고, 그것도 실패하면 그때 degraded."""
    concluder = Concluder({"verdict": verdict()})
    deps = deps_for(_runner(), first_tasks=[task("t-1")], integrate=_dead_integrate,
                    conclude=concluder, components=frozenset({"sink"}))
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert final["stopped_by"] == "llm_error" and len(concluder.calls) == 1
    assert final["verdict"].verdict_type == "data_loss" and final["verify_problems"] == []

    failing = Concluder({"llm_errors": ["conclude: 시간 초과 (182초) — APITimeoutError"]})
    deps = deps_for(_runner(), first_tasks=[task("t-1")], integrate=_dead_integrate,
                    conclude=failing, components=frozenset({"sink"}))
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    v = final["verdict"]
    assert v.verdict_type == "degraded" and len(failing.calls) == 1
    # 1-4: "조사가 돌지 않았다"는 거짓이다 — 몇 라운드에 무엇을 읽고 몇 건을 모았는지를 적는다.
    assert "조사 중단" in v.narrative and "1라운드" in v.narrative
    assert "읽기 1회(성공 1)" in v.narrative and "증거 1건" in v.narrative
    assert any("conclude: 시간 초과" in c for c in v.caveats) and any("integrate: 시간 초과" in c for c in v.caveats)

    exploding = Concluder(RuntimeError("폭발"))                      # 무raise(규율 1) — 이 길에서도
    deps = deps_for(_runner(), first_tasks=[task("t-1")], integrate=_dead_integrate,
                    conclude=exploding, components=frozenset({"sink"}))
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert final["verdict"].verdict_type == "degraded" and any("RuntimeError" in c for c in final["verdict"].caveats)


async def test_증거가_없으면_죽은_리드_뒤에_판정을_묻지_않는다(case):
    async def dead_frame(state):
        return {"llm_errors": ["frame: 시간 초과 (183초)"], "decision": "conclude", "stopped_by": "llm_error"}

    concluder = Concluder({"verdict": verdict()})
    deps = deps_for(_runner(), conclude=concluder)
    deps = dataclasses.replace(deps, frame=dead_frame)
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert final["verdict"].verdict_type == "degraded" and concluder.calls == []
    assert "증거 0건" in final["verdict"].narrative and "읽기 0회" in final["verdict"].narrative


# ── R2-4 ⑥ — 태스크 id 인용 ──

async def test_태스크_id를_인용하면_그_태스크의_증거가_하나일_때_코드가_고친다(case):
    """판정 턴이 `t-1`(태스크)을 인용하면 verify가 "없는 id"로 되묻는다 — 사내 판정 턴은 한 번에 4분이다. 그 태스크가 만든
    증거가 **하나뿐**이면 뜻이 하나라 코드가 그 증거 id로 바꾸고 caveat에 적는다(바꾼 id는 State에 실재 — 규율 3 안). 둘
    이상이면 고르지 않는다 — verify가 전처럼 되묻는다."""
    tasks = [task("t-1", status="ok", result_evidence_ids=["t-1.e1"]),
             task("t-2", status="ok", result_evidence_ids=["t-2.e1", "t-2.e2"])]
    evidence = [_ev("t-1.e1"), _ev("t-2.e1"), _ev("t-2.e2")]
    cited = verdict(root_cause=cause("sink", "t-1"), alternates=[cause("api", "t-2", confidence="low")])
    nodes = make_nodes(deps_for(ScriptedRunner(), conclude=Concluder({"verdict": cited}), components=frozenset({"sink", "api"})))
    patch = await nodes["conclude"](_state(case, plan_tasks=tasks, evidence=evidence))
    got = patch["verdict"]
    assert got.root_cause.evidence_ids == ["t-1.e1"]
    assert got.alternates[0].evidence_ids == ["t-2"]                            # 둘이면 그대로 — verify가 되묻는다
    assert any("t-1" in c and "t-1.e1" in c for c in got.caveats)
    plain = await nodes["conclude"](_state(case, plan_tasks=tasks, evidence=evidence))   # 대본이 기본 판정(t-1.e1)을 준다
    assert plain["verdict"].caveats == []                                      # 고칠 것이 없으면 caveat도 없다


def test_판정_예시의_caveats는_문장을_요구한다():
    """사내 측정 #3: caveats가 증거 id 나열뿐이었다 — 보고서가 그대로 싣는다. 무엇이 잘려 무엇을 단정 못 하는지 문장으로."""
    from src.application import briefing

    example = json.loads(briefing.verdict_example())
    assert "문장" in example["caveats"][0] and "id" in example["caveats"][0]


# ── R2-5 ② — 강등은 검사와 같은 규칙으로 걷어낸다 ──

async def test_강등은_서비스_아닌_component_다리도_걷어낸다(case):
    """사내 측정 #4: c-2 강등 출력에 `후보 external [low]`가 남았다 — verify가 규칙 3(서비스 이름)으로 되물었는데 강등은 인용만
    걸러, 인용이 멀쩡한 그 다리를 통과시켰다. 검사하는 규칙과 걷어내는 규칙이 같아야 한다. 무엇을 뺐는지는 caveat에 남는다."""
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink", "api"})))
    bad = verdict(alternates=[cause("external", "t-1.e1", confidence="low"), cause("api", "t-1.e1", confidence="low")],
                  contributing=[cause("alarm:stats", "t-1.e1")])
    got = (await nodes["verify"](_state(case, verdict=bad, verify_attempts=1)))["verdict"]
    assert [a.component for a in got.alternates] == ["api"] and got.contributing == []
    assert got.root_cause.component == "sink" and got.verdict_type == "data_loss"      # 멀쩡한 최상위는 그대로
    # 후보 external은 되묻기 전에 그 후보만 뺐고(R3-5d ①-b), 기여 요인은 강등이 걷어냈다 — 둘 다 caveat에
    assert any("후보 제외" in c and "external" in c for c in got.caveats)
    assert any("서비스 아닌 component 제외" in c and "기여 요인 alarm:stats" in c for c in got.caveats)
    wrong_root = verdict(root_cause=cause("alarm:stats", "t-1.e1"))
    got = (await nodes["verify"](_state(case, verdict=wrong_root, verify_attempts=1)))["verdict"]
    assert got.root_cause is None and got.verdict_type == "inconclusive"
    assert any("최상위 alarm:stats" in c for c in got.caveats)


def test_판정_프롬프트는_component가_데이터를_만드는_서비스라고_말한다():
    """사내 c-2는 판정 종류 값(`external`)을 component 칸에 넣었다 — 두 칸을 헷갈렸다. 원인이 데이터면 그것을 만드는 서비스, 바깥이면
    종류를 external로. 사내 서비스 이름 꼴은 적지 않는다(⑮) — 목록은 `{components}`가 준다."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "config/prompts/investigate-conclude.md").read_text(encoding="utf-8")
    rule = re.search(r"^- `component`는.*?(?=^- )", text, re.M | re.S).group(0)
    assert "데이터를 **만드는** 서비스" in rule and "판정 종류" in rule and "external" in rule and "{components}" in rule


# ── R3-0 ② — 대상과 연결 안 된 키로 high 금지 ──

def _linked_state(case, *, key_read: str | None, target="picked", **over) -> CaseState:
    """입구 노드가 고른 대상 키(`state.target`)와, 있으면 키 읽기 하나(t-2.e1)."""
    from src.domain.case import TargetPick

    evidence = [_ev("t-1.e1")]
    if key_read is not None:
        evidence.append(EvidenceRef(id="t-2.e1", source=f"redis.get key={key_read!r}", summary="(nil)", body="(nil)"))
    picks = {"picked": TargetPick(key="alarm:stats:{line}", basis="키 값의 x에 대상 identity가 그대로 있다 (t-0-1.e1)"),
             "unpicked": TargetPick(problem="후보 2개 중 identity가 든 키가 없다"), None: None}
    return _state(case, evidence=evidence, target=picks[target], **over)


def _citing(*ids, confidence="high") -> Verdict:
    return verdict(root_cause=cause("sink", *ids), confidence=confidence)


def _linked_deps(**over):
    return deps_for(ScriptedRunner(), components=frozenset({"sink"}), **over)


async def test_최상위가_인용한_키가_대상_키가_아니면_확신_상한_medium과_caveat(case):
    """사내 측정 #5 c-1: 이름이 비슷한 다른 배지의 키를 읽어 `None`을 유실의 증거로 삼아 high로 틀렸다. 되묻지 않는다 — 한도다
    (R3 verify (b)의 싼 절반). 대상 키를 읽었으면 그대로, 키를 인용하지 않은 최상위는 이 규칙 밖(R3-2)."""
    nodes = make_nodes(_linked_deps())
    got = await nodes["verify"](_linked_state(case, key_read="alarm:scan:L1", verdict=_citing("t-2.e1")))
    assert got["verify_problems"] == [] and got["verdict"].confidence == "medium"
    assert any("대상과 키의 연결이 확인되지 않았다" in c and "alarm:scan:L1" in c and "alarm:stats:{line}" in c
               for c in got["verdict"].caveats)
    assert await nodes["verify"](_linked_state(case, key_read="alarm:stats:L1", verdict=_citing("t-2.e1"))) == {"verify_problems": []}
    assert await nodes["verify"](_linked_state(case, key_read="alarm:scan:L1", verdict=_citing("t-1.e1"))) == {"verify_problems": []}
    low = await nodes["verify"](_linked_state(case, key_read="alarm:scan:L1", verdict=_citing("t-2.e1", confidence="low")))
    assert low["verdict"].confidence == "low" and any("연결이 확인되지 않았다" in c for c in low["verdict"].caveats)
    # scan은 키 하나를 읽은 것이 아니다 — 패턴을 키로 세면 훑기만 한 최상위까지 낮아진다.
    scanned = _linked_state(case, key_read=None, verdict=_citing("t-3.e1"))
    scanned = scanned.model_copy(update={"evidence": scanned.evidence + [
        EvidenceRef(id="t-3.e1", source="redis.scan pattern='alarm:*'", summary="2건", body="alarm:scan:L1, alarm:stats:L1")]})
    assert await nodes["verify"](scanned) == {"verify_problems": []}


async def test_대상_키를_못_골랐어도_키를_인용한_high는_medium이고_대본_경로는_안_건다(case):
    """연결이 "확인되지 않은" 것은 입구 노드가 안 돌았을 때(`target` 없음)나 못 골랐을 때도 같다. 대본
    (`check_discovery=False`)은 사람이 이름을 알고 쓴 것이라 안 건다 — `_component_problem`과 같은 스위치."""
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink"})))
    got = await nodes["verify"](_linked_state(case, key_read="alarm:scan:L1", target=None, verdict=_citing("t-2.e1")))
    assert got["verdict"].confidence == "medium" and any("입구 노드가 고르지 않았다" in c for c in got["verdict"].caveats)
    got = await nodes["verify"](_linked_state(case, key_read="alarm:stats:L1", target="unpicked", verdict=_citing("t-2.e1")))
    assert got["verdict"].confidence == "medium" and any("identity가 든 키가 없다" in c for c in got["verdict"].caveats)
    loose = make_nodes(deps_for(ScriptedRunner(), check_discovery=False))
    assert await loose["verify"](_linked_state(case, key_read="alarm:scan:L1", verdict=_citing("t-2.e1"))) == {"verify_problems": []}


# ── R3-3 ⑤ — 클래스 이름 component는 서비스로 ──

async def test_verify는_클래스_이름_component를_인덱스의_서비스로_바꾸고_caveat을_단다(case):
    """측정 #9 P1 7(c-2): 리드가 쓴 쪽 **클래스 이름**을 원인 칸에 적었고 verify가 "서비스 아닌 component"로 걷어냈다. 클래스는 서비스 안에
    있으니 걷어낼 것이 아니라 번역할 것이다 — 인덱스의 클래스→서비스로 바꾸고 caveat. 두 레포면 걷기가 가른 서비스(홉 표의 쓴 쪽), 못 갈랐으면
    첫 것에 "둘 다 후보". 클래스 이름이 아니면 전처럼(서비스 아님 → 문제)."""
    from src.application.nodes import translate_classes
    from src.domain.case import Hop, Walk

    classes = {"SummaryRefresh": ("sink",), "Dual": ("api", "sink"), "Shared": ("p1", "p2", "p3")}
    v = verdict(root_cause=cause("SummaryRefresh", "t-1.e1"), alternates=[cause("Dual", "t-1.e1", confidence="low")],
                contributing=[cause("api", "t-1.e1", relation="r")])
    got = translate_classes(v, class_services=classes, walk=None)
    assert (got.root_cause.component, got.alternates[0].component, got.contributing[0].component) == ("sink", "api", "api")
    assert got.caveats == ["component SummaryRefresh는 클래스 이름 — 인덱스로 서비스 sink로 바꿨다",
                           "component Dual는 클래스 이름 — 인덱스로 서비스 api로 바꿨다 (api, sink에 다 있다 — 못 가른다, 전부 후보)"]
    walked = Walk(hops=[Hop(key="k", writer="Dual", service="sink", candidates=["sink"], status="정상")])
    picked = translate_classes(v, class_services=classes, walk=walked)
    assert picked.alternates[0].component == "sink"                                                            # 걷기가 가른 쪽(홉의 후보)
    assert picked.caveats[1] == "component Dual는 클래스 이름 — 인덱스로 서비스 sink로 바꿨다 (api, sink 중 걷기가 고른 것)"
    # 한 레포를 여러 서비스가 공유하면(R3-4 ①) 걷기가 가른 것은 레포다 — 그 서비스 전부가 후보, 첫 것으로 바꾸고 caveat에 전부
    byrepo = Walk(hops=[Hop(key="k", writer="Shared", service="dt-proc(서비스 3개 공유)", candidates=["p1", "p2", "p3"], status="정상")])
    shared = translate_classes(verdict(root_cause=cause("Shared", "t-1.e1")), class_services=classes, walk=byrepo)
    assert shared.root_cause.component == "p1"
    assert shared.caveats == ["component Shared는 클래스 이름 — 인덱스로 서비스 p1로 바꿨다 (레포를 p1, p2, p3가 공유 — 첫 것, 전부 후보)"]
    assert translate_classes(v, class_services={}, walk=None) is v                                             # 바뀐 것이 없으면 같은 객체
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink", "api"}), class_services=classes))
    patch = await nodes["verify"](_state(case, verdict=v))
    assert patch["verify_problems"] == [] and patch["verdict"].root_cause.component == "sink"
    assert any("클래스 이름" in c for c in patch["verdict"].caveats)
    # 클래스 이름이 아닌 것은 전처럼 문제다
    plain = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink", "api"})))
    assert "verdict" not in await plain["verify"](_state(case, verdict=v)) and (await plain["verify"](_state(case, verdict=v)))["verify_problems"]



# ── R3-5c ③ — 인용한 코드 증거의 레포가 component의 레포와 다르면 caveat ──

async def test_component의_근거로_인용한_코드가_다른_레포의_것이면_그_레포의_서비스를_후보로_caveat에_적는다(case):
    """측정 #12b P1-4: 서술은 배치 레포의 `<요약 처리기>`인데 component가 처리기 서비스였고, 서비스 이름이라 검증을 지났다. 근거로 인용한 t-11은 배치
    서비스의 `code.read`였다 — 인용한 코드의 레포가 component의 레포와 다르면 caveat, 그 레포의 서비스를 후보로 같이(사람 결정 10-16). component는 안
    바꾼다(사실만 적는다)."""
    from src.application.nodes import caveat_code_repos

    repos = {"processor": "core", "batchsvc": "batch-repo", "batchsink": "batch-repo", "api": "api-repo"}
    tasks = [task("t-11", action="code.read", params={"service": "batchsvc", "path": "b/x.py", "line": 3}, status="ok", result_evidence_ids=["t-11.e1"]),
             task("t-12", action="code.read", params={"service": "processor", "path": "p/y.py", "line": 1}, status="ok", result_evidence_ids=["t-12.e1"]),
             task("t-13", action="redis.get", params={"key": "k"}, status="ok", result_evidence_ids=["t-13.e1"]),
             task("t-14", action="code.resources", params={"name": "X", "repo": "api-repo"}, status="ok", result_evidence_ids=["t-14.e1"])]
    v = verdict(root_cause=cause("processor", "t-11.e1", "t-13.e1"), contributing=[cause("processor", "t-14.e1", relation="r")])
    got = caveat_code_repos(v, plan_tasks=tasks, service_repos=repos)
    assert got.caveats == ["component processor(레포 core)의 근거로 인용한 t-11.e1은 레포 batch-repo의 코드다 — 그 레포의 서비스 batchsink, batchsvc도 후보",
                           "component processor(레포 core)의 근거로 인용한 t-14.e1은 레포 api-repo의 코드다 — 그 레포의 서비스 api도 후보"]
    assert got.root_cause.component == "processor"
    same = verdict(root_cause=cause("processor", "t-12.e1", "t-13.e1"))
    assert caveat_code_repos(same, plan_tasks=tasks, service_repos=repos) is same                  # 같은 레포·코드 아닌 증거면 그대로
    assert caveat_code_repos(v, plan_tasks=tasks, service_repos={}) is v                           # 지도가 없으면 그대로
    # verify 노드가 붙인다 — 검증 문제는 아니다
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset(repos), service_repos=repos))
    patch = await nodes["verify"](_state(case, verdict=v, plan_tasks=tasks, evidence=[_ev(f"t-{n}.e1") for n in (11, 12, 13, 14)]))
    assert patch["verify_problems"] == [] and any("레포 batch-repo의 코드다" in c for c in patch["verdict"].caveats)



# ── R3-5d ①-b — 검증: 번역의 공유/못 가름, 연결 확인에 걷기 키, 걷기와 어긋남, 후보의 토픽 ──

def test_번역은_레포_지도로_한_레포_공유와_두_레포_못_가름을_가른다():
    """측정 #13 고침 1: 번역이 걷기가 안 가른 홉의 후보(레포 둘)를 "레포를 …가 공유"라 적고 첫 것(`<수집기>`)을 골랐다 — 거짓. 후보의 레포가 하나면
    공유, 둘 이상이면 못 가른다(첫 것으로 바꾸되 전부 후보)."""
    from src.application.nodes import translate_classes
    from src.domain.case import Hop, Walk

    repos = {"col": "dt-col", "p1": "dt-proc", "p2": "dt-proc", "p3": "dt-proc"}
    classes = {"Both": ("col", "p1", "p2", "p3"), "Shared": ("p1", "p2", "p3")}
    unsplit = Walk(hops=[Hop(key="k", writer="Both", service="col, dt-proc(서비스 3개 공유)", candidates=["col", "p1", "p2", "p3"], status="정상")])
    got = translate_classes(verdict(root_cause=cause("Both", "t-1.e1")), class_services=classes, walk=unsplit, service_repos=repos)
    assert got.root_cause.component == "col"
    assert got.caveats == ["component Both는 클래스 이름 — 인덱스로 서비스 col로 바꿨다 (col, p1, p2, p3에 다 있다 — 못 가른다, 전부 후보)"]
    split = Walk(hops=[Hop(key="k", writer="Both", service="dt-proc(서비스 3개 공유)", candidates=["p1", "p2", "p3"], status="정상")])
    got = translate_classes(verdict(root_cause=cause("Both", "t-1.e1")), class_services=classes, walk=split, service_repos=repos)
    assert got.root_cause.component == "p1" and got.caveats[0].endswith("p1로 바꿨다 (레포를 p1, p2, p3가 공유 — 첫 것, 전부 후보)")
    # 걷기에 없는 클래스라도 레포 지도가 한 레포라 하면 공유다
    got = translate_classes(verdict(root_cause=cause("Shared", "t-1.e1")), class_services=classes, walk=None, service_repos=repos)
    assert got.caveats[0].endswith("p1로 바꿨다 (레포를 p1, p2, p3가 공유 — 첫 것, 전부 후보)")


async def test_verify_노드가_번역에_레포_지도를_넘긴다(case):
    from src.domain.case import Hop, Walk

    repos = {"col": "dt-col", "p1": "dt-proc", "p2": "dt-proc", "p3": "dt-proc"}
    unsplit = Walk(hops=[Hop(key="k", writer="Both", service="col, dt-proc(서비스 3개 공유)", candidates=["col", "p1", "p2", "p3"], status="정상")])
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset(repos), class_services={"Both": ("col", "p1", "p2", "p3")}, service_repos=repos))
    got = (await nodes["verify"](_state(case, verdict=verdict(root_cause=cause("Both", "t-1.e1")), walk=unsplit)))["verdict"]
    assert got.caveats[0].endswith("(col, p1, p2, p3에 다 있다 — 못 가른다, 전부 후보)")


async def test_걷기_홉의_키를_인용한_최상위는_연결이_확인된_것이다(case):
    """측정 #13 고침 2: 걷기 홉 표에 있는 키를 인용해도 "연결이 확인되지 않았다"(5/8)였다 — 걷기가 이은 키는 연결된 것이다."""
    from src.application.nodes import cap_unlinked_key
    from src.domain.case import Hop, TargetPick, Walk

    target = TargetPick(key="alarm:stats:L1", basis="b")
    evidence = [EvidenceRef(id="t-3.e1", source="redis.get key='alarm:source:L1'", summary="x", body=""),
                EvidenceRef(id="t-9.e1", source="redis.get key='other:L1'", summary="x", body="")]
    walk = Walk(hops=[Hop(key="alarm:stats:L1", writer="W", status="빈 배열 (data)", bad=True, evidence_id="t-1.e1"),
                      Hop(key="alarm:source:L1", depth=1, status="0행", bad=True, evidence_id="t-3.e1", parent="t-1.e1")])
    linked = verdict(root_cause=cause("sink", "t-3.e1"))
    assert cap_unlinked_key(linked, target=target, evidence=evidence, walk=walk) is linked
    other = verdict(root_cause=cause("sink", "t-9.e1"))
    capped = cap_unlinked_key(other, target=target, evidence=evidence, walk=walk)
    assert capped.confidence == "medium" and "other:L1" in capped.caveats[0]
    # verify 노드가 걷기를 넘긴다
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink"})))
    patch = await nodes["verify"](_state(case, verdict=linked, target=target, walk=walk, evidence=evidence))
    assert patch["verify_problems"] == [] and "verdict" not in patch                     # 바뀐 것이 없다 — 상한도 caveat도 아니다


def test_판정이_걷기가_멈춘_자리와_어긋나면_caveat에_멈춘_홉의_서비스를_후보로_적는다():
    """측정 #13 고침 4: c-8은 멈춘 홉(`<처리기>`) 아래(`<배치>`)를, c-9는 걷기가 정상으로 본 홉의 쓴 쪽을 원인으로 댔다 — 둘 다 걷기가 맞았다.
    걷기가 그 클래스의 입력까지 본 멈춤(`입력…`)에서만, external과 쓴 쪽을 모르는 멈춤은 뺀다."""
    from src.application.nodes import caveat_walk_disagreement
    from src.domain.case import Hop, Walk

    hops = [Hop(key="sum:L1", writer="Batch", service="batch", candidates=["batch"], status="빈 배열 (data)", bad=True, evidence_id="t-1.e1"),
            Hop(key="dom:L1", depth=1, writer="Proc", service="processor", candidates=["processor"], status="빈 배열 (record)", bad=True,
                evidence_id="t-3.e1", parent="t-1.e1"),
            Hop(key="pal", depth=1, writer="Palette", service="api", candidates=["api"], status="정상 — colors: 행 2", evidence_id="t-4.e1", parent="t-1.e1"),
            Hop(key="src:L1", depth=2, status="정상 — 행 9", evidence_id="t-6.e1", parent="t-3.e1")]
    walk = Walk(hops=hops, stopped="입력이 다 정상 — Proc", stop_at="t-3.e1")
    services = frozenset({"batch", "processor", "api", "sink"})
    below = caveat_walk_disagreement(verdict(root_cause=cause("batch", "t-1.e1")), walk=walk, components=services)
    assert below.caveats == ["판정의 component batch는 걷기가 멈춘 자리(dom:L1, 쓴 쪽 Proc [processor])의 서비스가 아니다 — processor도 후보"]
    normal = caveat_walk_disagreement(verdict(root_cause=cause("api", "t-4.e1")), walk=walk, components=services)
    assert normal.caveats == ["판정의 component api는 걷기가 멈춘 자리(dom:L1, 쓴 쪽 Proc [processor])의 서비스가 아니다 — processor도 후보 "
                              "(걷기는 pal을 정상으로 봤다 — 그 쓴 쪽이 api)"]
    agree = verdict(root_cause=cause("processor", "t-3.e1"))
    assert caveat_walk_disagreement(agree, walk=walk, components=services) is agree
    external = verdict(verdict_type="external", root_cause=cause("feed-x", "t-6.e1"))
    assert caveat_walk_disagreement(external, walk=walk, components=services) is external
    unknown = walk.model_copy(update={"stopped": "쓴 쪽을 모른다 — src:L1(값에 인덱스의 클래스 이름이 없다)", "stop_at": "t-6.e1"})
    assert caveat_walk_disagreement(below, walk=unknown, components=services) is below
    capped = walk.model_copy(update={"stopped": "깊이 4 상한 — Proc부터는 리드가"})
    fresh = verdict(root_cause=cause("batch", "t-1.e1"))
    assert caveat_walk_disagreement(fresh, walk=capped, components=services) is fresh       # 상한에 걸린 걷기는 어긋났다 할 근거가 없다


async def test_후보의_component가_케이스에_나온_토픽과_같으면_통과하고_그_밖의_서비스_아닌_후보는_재작성_없이_제외한다(case):
    """측정 #13 추가분 5(사람 결정 10-19): c-11 첫 답의 후보 토픽은 증거에 있었고 진짜 원인 방향이었는데 external이 아니라고 걷어내 재작성 뒤 강등됐다.
    케이스에 나온 토픽(kafka 읽기의 topic)과 완전 일치하면 component로 허용. 그 밖(자유 문장·오타)의 후보는 되묻지 않고 그 후보만 뺀다 — 최상위·기여
    요인의 문제는 전처럼 재작성."""
    tasks = [task("t-5", action="kafka.tail", params={"topic": "mx.alarm.raw", "limit": 5}, status="ok", result_evidence_ids=["t-5.e1"])]
    evidence = [_ev("t-1.e1"), EvidenceRef(id="t-5.e1", source="kafka.tail topic='mx.alarm.raw'", summary="x", body="5건")]
    nodes = make_nodes(deps_for(ScriptedRunner(), components=frozenset({"sink", "processor"})))
    v = verdict(root_cause=cause("sink", "t-1.e1"),
                alternates=[cause("mx.alarm.raw", "t-5.e1", confidence="low"), cause("외부 피드가 멈춤", "t-5.e1", confidence="low")])
    patch = await nodes["verify"](_state(case, verdict=v, plan_tasks=tasks, evidence=evidence))
    assert patch["verify_problems"] == [] and "verify_attempts" not in patch
    assert [a.component for a in patch["verdict"].alternates] == ["mx.alarm.raw"]
    assert patch["verdict"].caveats == ["후보 제외(서비스도 이 케이스의 토픽도 아닌 component): 외부 피드가 멈춤"]
    typo = verdict(root_cause=cause("sink", "t-1.e1"), alternates=[cause("mx.alarm.row", "t-5.e1", confidence="low")])
    patch = await nodes["verify"](_state(case, verdict=typo, plan_tasks=tasks, evidence=evidence))
    assert patch["verdict"].alternates == [] and "mx.alarm.row" in patch["verdict"].caveats[0]
    root = verdict(root_cause=cause("mx.alarm.raw", "t-5.e1"))                                     # 최상위의 토픽도 케이스에 나온 것이면 허용
    assert (await nodes["verify"](_state(case, verdict=root, plan_tasks=tasks, evidence=evidence)))["verify_problems"] == []
    bad_root = verdict(root_cause=cause("외부 피드가 멈춤", "t-1.e1"))                              # 최상위는 전처럼 되묻는다
    assert (await nodes["verify"](_state(case, verdict=bad_root, plan_tasks=tasks, evidence=evidence)))["verify_problems"]
