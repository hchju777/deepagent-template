"""R3-5 ② — 측정 도구: LLM 없이 입구 + 걷기만. 그래프 안에서와 같은 State가 나와야 같은 케이스 블록이 나온다."""
import json

from src.application import briefing
from src.application.intake_map import IntakeSpec
from src.application.fakes import ScriptedRunner
from src.application.state import CaseState
from src.application.walk_only import apply_patch, declared_for, walk_from_key, walk_only
from src.domain.case import Case, EvidenceRef
from src.domain.investigation import TaskOutcome
from tests.application.conftest import T0, deps_for, task


def _ok(task_id, data, source):
    return TaskOutcome(task_id=task_id, status="ok", summary=source,
                       evidence=[EvidenceRef(id=f"{task_id}.e1", source=source, summary="x", body="raw")])


def _patrol():
    return Case(id="c-9", gbm="mx", fct="gumi", origin="patrol", symptom="배지 0", t0=T0, check="badge_all_zero", target="L1/Alarm")


async def test_walk_only는_입구와_걷기만_돌리고_패치를_State_규칙대로_합친다():
    """측정 #12는 `case walk`의 케이스 블록을 정답 홉 표와 대조한다 — 그래프(frame부터 리드 LLM) 없이 두 노드만 직접 부르되, 패치 병합은 그래프의
    reducer와 같아야 한다(`plan_tasks`·`evidence`는 id 병합, `target`·`walk`는 교체)."""
    rest = {"response": [{"group": "L1", "title": "Alarm"}]}
    value = {"type": "string", "value": json.dumps({"info": {"group": "L1", "title": "Alarm"}, "data": [], "event_type": "Writer"})}
    res_text = "Writer (w.py:L1) [dt-core · sink]\n  읽기 1:\n    src:L1 [rediskey] — Writer._run (w.py:L3)"
    src = {"type": "string", "value": json.dumps([{"a": 1}, {"a": 2}])}
    runner = ScriptedRunner({"t-0": _ok("t-0", rest, "rest.query entry='summary_badge'"), "t-0-1": _ok("t-0-1", value, "redis.get key='alarm:stats:L1'"),
                             "t-1": _ok("t-1", value, "redis.get key='alarm:stats:L1'"), "t-2": _ok("t-2", res_text, "code.resources name='Writer'"),
                             "t-3": _ok("t-3", src, "redis.get key='src:L1'")},
                            raws={"t-0.e1": rest, "t-0-1.e1": value, "t-1.e1": value, "t-2.e1": res_text, "t-3.e1": src})
    spec = IntakeSpec(read=("rest.query", {"entry": "summary_badge", "params": {}}), fields=("group", "title"))
    deps = deps_for(runner, intake=spec, declared={"rediskey": ("alarm:stats:L1",)}, classes=frozenset({"Writer"}), code_index=True,
                    class_services={"Writer": ("sink",)}, clock=lambda: T0)
    state = await walk_only(_patrol(), deps)
    assert state.target is not None and state.target.key == "alarm:stats:L1" and state.target.writer == "Writer"
    assert [t.id for t in state.plan_tasks] == ["t-0", "t-1", "t-2", "t-3"] and [t.walked for t in state.plan_tasks] == [False, False, True, True]
    assert [e.id for e in state.evidence] == ["t-0.e1", "t-1.e1", "t-2.e1", "t-3.e1"]
    assert [h.key for h in state.walk.hops] == ["alarm:stats:L1", "src:L1"] and state.walk.stopped == "입력이 다 정상 — Writer"
    assert state.round == 0 and state.hypotheses == [] and state.verdict is None                  # 리드는 안 불렀다
    block = briefing.case_block(state, target=briefing.target_key(state), code_index=True)
    assert "  src:L1 | — | 정상 — 행 2 · 필드 1 | t-3.e1" in block and "다음 홉: code.read line=3 path='w.py' service='sink'" in block
    # 사람이 연 케이스(입구 재료 없음)면 아무것도 안 하고 그대로
    plain = await walk_only(Case(id="c-h", gbm="mx", fct="gumi", origin="human", symptom="x", t0=T0), deps_for(ScriptedRunner(), clock=lambda: T0))
    assert plain.target is None and plain.walk is None and plain.plan_tasks == []


def test_apply_patch는_id_병합과_이어_붙이기와_교체를_그래프와_같게_한다():
    state = CaseState(case=_patrol(), plan_tasks=[task("t-1", status="pending")], facts=["a"], llm_errors=["e1"])
    patched = apply_patch(state, {"plan_tasks": [task("t-1", status="ok"), task("t-2")], "facts": ["b"], "llm_errors": ["e2"], "round": 3})
    assert [(t.id, t.status) for t in patched.plan_tasks] == [("t-1", "ok"), ("t-2", "pending")]        # 같은 id는 교체, 새 id는 뒤에
    assert patched.facts == ["a", "b"] and patched.llm_errors == ["e1", "e2"] and patched.round == 3
    assert apply_patch(state, {}) == state



# ── R3-5b ③ — `case walk --key`: 입구 없이 그 키부터 ──

def test_키의_선언_이름은_흐름_그래프의_선언_키와_자리가_맞는_하나다():
    templates = ("dom:{line}", "src:{line}", "feed:ref", "a:{x}", "a:{y}")
    assert declared_for("dom:L1", templates) == "dom:{line}" and declared_for("feed:ref", templates) == "feed:ref"
    assert declared_for("zzz:1", templates) == "zzz:1"                       # 맞는 게 없으면 키 그대로 — 입력 채우기는 scan으로 간다
    assert declared_for("a:1", templates) == "a:1"                           # 둘 이상이면 고르지 않는다


async def test_walk_from_key는_입구_없이_그_키를_t_1로_읽어_대상_키로_삼고_걷는다():
    """측정 #12: 배지 밖 끝점은 라우트가 리터럴 → config로 키를 정해 입구가 막힌다(B는 아직 없다). 사람이 키를 대면 그 키부터 걷는다 — 쓴 쪽은 값에서,
    빈 자리는 걷기와 같은 판정(값의 맨 위), 선언 이름은 흐름 그래프에서."""
    value = {"type": "string", "value": json.dumps({"record": {}, "metadata": {"event_type": "Writer"}})}
    res_text = "Writer (w.py:L1) [dt-core · sink]\n  읽기 1:\n    src:{line} [rediskey] — Writer._run (w.py:L3)"
    src = {"type": "string", "value": json.dumps([{"a": 1}, {"a": 2}])}
    runner = ScriptedRunner({"t-1": _ok("t-1", value, "redis.get key='dom:L1'"), "t-2": _ok("t-2", res_text, "code.resources name='Writer'"),
                             "t-3": _ok("t-3", src, "redis.get key='src:L1'")},
                            raws={"t-1.e1": value, "t-2.e1": res_text, "t-3.e1": src})
    deps = deps_for(runner, classes=frozenset({"Writer"}), code_index=True, class_services={"Writer": ("sink",)},
                    declared_by={"dom:{line}": ("sink",), "src:{line}": ("sink",)}, clock=lambda: T0)
    case = Case(id="walk-key", gbm="mx", fct="gumi", origin="human", symptom="dom:L1 — case walk --key(측정)", t0=T0)
    state = await walk_from_key(case, "dom:L1", deps)
    pick = state.target
    assert (pick.key, pick.declared, pick.writer, pick.empty_objects) == ("dom:L1", "dom:{line}", "Writer", ["record"])
    assert pick.line() == "대상 키: dom:L1 — --key로 댄 키 (t-1.e1)"
    assert [t.id for t in state.plan_tasks] == ["t-1", "t-2", "t-3"] and [e.id for e in state.evidence] == ["t-1.e1", "t-2.e1", "t-3.e1"]
    assert [(h.key, h.status) for h in state.walk.hops] == [("dom:L1", "빈 객체 (record)"), ("src:L1", "정상 — 행 2 · 필드 1")]
    assert state.walk.stopped == "입력이 다 정상 — Writer"
    # 키가 없거나 읽기가 실패하면 그 사실만 — 걷지 않는다
    gone = ScriptedRunner({"t-1": _ok("t-1", None, "redis.get key='dom:L1'")}, raws={"t-1.e1": None})
    state = await walk_from_key(case, "dom:L1", deps_for(gone, classes=frozenset({"Writer"}), code_index=True, clock=lambda: T0))
    assert state.target.key is None and state.target.problem == "키가 없다 — dom:L1 (t-1.e1)" and state.walk is None
    failed = ScriptedRunner({"t-1": TaskOutcome(task_id="t-1", status="error", error="연결 거부")})
    state = await walk_from_key(case, "dom:L1", deps_for(failed, clock=lambda: T0))
    assert state.target.problem == "대상 키 읽기 실패 — 연결 거부" and state.walk is None
