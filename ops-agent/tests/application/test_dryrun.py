"""대본 파일 — 손으로 쓰는 것이므로 오타가 조용히 넘어가면 안 된다."""
import json

import pytest

from src.application.dryrun import Script, ScriptedPlan, load_script, strip_comments
from src.application.state import CaseState

from tests.support import set_real_config_env
from tests.application.conftest import task


def write(tmp_path, body):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def test_밑줄로_시작하는_키는_주석이라_중첩된_것까지_걷힌다():
    got = strip_comments({"_설명": "무시", "tasks": [{"_왜": "여기 있나", "id": "t-1"}]})
    assert got == {"tasks": [{"id": "t-1"}]}


def test_주석이_있어도_대본이_통과한다(tmp_path):
    script = load_script(write(tmp_path, {
        "_설명": "이 파일이 무엇인가",
        "symptom": "OEE가 512다",
        "tasks": [{"_왜": "먼저 파생값부터", "id": "t-1", "goal": "g",
                   "role": "data_prober", "action": "redis.get",
                   "params": {"key": "k"}}]}))
    assert script.symptom == "OEE가 512다"
    assert [t.id for t in script.tasks] == ["t-1"]


def test_모르는_키는_거부된다(tmp_path):
    """`"round"`(s 빠짐)를 조용히 무시하면 대본대로 돈 줄 안다.

    `app.json`의 `timezone`이 정확히 그 형태로 한동안 거짓말을 했다 — 사람이 적어도
    아무 일이 안 일어나는 칸.
    """
    with pytest.raises(SystemExit) as caught:
        load_script(write(tmp_path, {"symptom": "x", "round": [{"decision": "continue"}]}))
    assert "round" in str(caught.value)


def test_잘못된_대본은_스택트레이스_대신_읽을_수_있는_줄을_낸다(tmp_path):
    """사람이 방금 고친 파일에 대해 pydantic 스택트레이스 20줄은 어디를 고칠지를 가린다."""
    with pytest.raises(SystemExit) as caught:
        load_script(write(tmp_path, {"tasks": [{"id": "t-1", "role": "없는역할"}]}))
    message = str(caught.value)
    assert "tasks.0" in message and "대본이 잘못됐다" in message
    assert "Traceback" not in message


async def test_대본이_떨어지면_계속_continue를_낸다(case):
    """여기서 conclude로 끝내면 **라운드 상한이 지켜지는지를 못 본다.**"""
    plan = ScriptedPlan(Script(rounds=[]))
    for _ in range(3):
        assert (await plan.integrate(CaseState(case=case)))["decision"] == "continue"


async def test_라운드마다_대본_순서대로_낸다(case):
    plan = ScriptedPlan(Script(rounds=[
        {"decision": "continue", "tasks": [task("t-1")]},
        {"decision": "conclude"}]))
    first = await plan.integrate(CaseState(case=case))
    second = await plan.integrate(CaseState(case=case))
    assert [t.id for t in first["plan_tasks"]] == ["t-1"]
    assert second["decision"] == "conclude"


def test_리포에_든_예제가_실제로_로드된다():
    """README가 가리키는 파일이 없거나 안 맞으면 처음 써 보는 사람이 거기서 막힌다.

    이 리포에서 실제로 났다 — 예제 트리에만 시나리오를 넣고 실제 트리에 안 넣어서
    `report window`가 "시나리오가 없다"로 막혔다.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    script = load_script(root / "examples" / "case-dryrun.json")
    assert script.tasks and script.symptom
    seeds = json.loads((root / "examples" / "stub-seeds.json").read_text(encoding="utf-8"))
    assert "redis" in seeds


def test_CLI가_실제로_돈다(tmp_path, capsys, monkeypatch):
    """**`case dryrun` 명령 자체를** 부른다 — 부품만 테스트하면 배선이 안 보인다.

    실제로 그랬다: `ProbeRunner`에 `clock`을 필수로 올렸는데 `__main__`의 호출부가
    안 따라갔고, **776개가 전부 통과했다.** 명령을 돌려 보고서야 `TypeError`가 나왔다.
    handover가 적어 둔 그 함정이다 — 시그니처를 바꾸면 전수 확인이 필요하고,
    초록불은 증거가 아니다.
    """
    from pathlib import Path

    from src.__main__ import main

    root = Path(__file__).resolve().parent.parent.parent
    set_real_config_env(monkeypatch)
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(root / "config"), "--env-file", str(tmp_path / "none"),
        "case", "dryrun", "--gbm", "mx", "--fct", "gumi",
        "--plan", str(root / "examples" / "case-dryrun.json"),
        "--stub-seeds", str(root / "examples" / "stub-seeds.json")])

    assert main() == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "끝난 이유: no_runnable" in out
    # 게이트가 붙잡았다가 2라운드에 푼 것 — 이 단계가 만든 것의 요약이다.
    assert "t-9" in out and "미등재 action" in out


def test_대본_경로는_이름_추측_검사를_끈다():
    """**`dryrun.build_deps`가 실제로 끄는지**를 본다.

    `EngineDeps(check_discovery=False)`가 동작하는지만 보면 배선이 안 보인다 —
    RED 확인에서 `build_deps`를 True로 바꿔도 테스트가 통과했다. 대본은 시스템을
    아는 사람이 이름을 알고 적은 것이라, 켜 두면 기록이 거짓 양성으로만 찬다.
    """
    from src.application.dryrun import build_deps
    from src.config.schema_app import InvestigationConfig

    deps = build_deps(Script(symptom="증상", tasks=[]), runner=None,
                      investigation=InvestigationConfig())
    assert deps.check_discovery is False


def test_대본_경로는_되묻지_않는다():
    """대본에는 "되물음"이 없다 — 켜 두면 거부 하나에 대본의 다음 라운드를 당겨 먹는다.
    위 검사와 같은 이유로 `build_deps`를 직접 본다."""
    from src.application.dryrun import build_deps
    from src.config.schema_app import InvestigationConfig

    deps = build_deps(Script(symptom="증상", tasks=[]), runner=None,
                      investigation=InvestigationConfig())
    assert deps.redo_on_rejection is False


async def test_대본으로_돈_조사에는_이름_기록이_안_남는다(tmp_path):
    """소비자로 직접 확인한다 — 예제 계획은 `redis.get key='oee:L3'`처럼 이름을 댄다."""
    from pathlib import Path as _Path

    from src.application.dryrun import build_deps, initial_state
    from src.application.fakes import ScriptedRunner
    from src.application.graph import build_engine
    from src.config.schema_app import InvestigationConfig

    root = _Path(__file__).resolve().parent.parent.parent
    script = load_script(root / "examples" / "case-dryrun.json")
    deps = build_deps(script, runner=ScriptedRunner(),
                      investigation=InvestigationConfig())
    final = await build_engine(deps).ainvoke(
        initial_state(script, case_id="c-1", gbm="mx", fct="gumi",
                      clock=lambda: __import__("datetime").datetime(2026, 9, 14, 9)))
    assert final["llm_errors"] == []


# ── 사다리 대본 — rest → code.trace → recompute.count (11b 3b-2) ─────────────────────

LADDER_SEEDS = {
    "rest": {"summary_badge": {"items": [{"group": "L1", "title": "Alarm", "alarm": 2, "caution": 0}]}},
    "mongo": {"alarm_events": [{"line": "L1", "result": "alarm"}, {"line": "L1", "result": "alarm"},
                               {"line": "L1", "result": "caution"}]},
    "code": {"trace": {"/summary/badge": "api/r.py:L6 badge\n  → api/q.py:L3 AlarmRepo.recent — reads: "
                                         "alarm_events [collection] 확실"},
             "uses": {"alarm_events": "alarm_events [collection]\n  쓰기 1:\n    sink.writer.run (sink/writer.py:L10) "
                                      "[dt-core · sink] — 진입점: 이 함수(부르는 곳 없음)"},
             "callers": {"sink.writer.run": "대상 sink.writer.run (sink/writer.py:L4) [dt-core · sink]\n"
                                            "부르는 곳이 없다 — 이 함수가 진입점이다(스크립트·스케줄·동적 호출일 수 있다)"}},
}


def _ladder_script():
    return Script.model_validate({
        "symptom": "배지가 전부 0이다",
        "tasks": [
            {"id": "t-1", "goal": "증상 재현", "role": "data_prober", "priority": 10,
             "action": "rest.query", "params": {"entry": "summary_badge", "params": {}}},
            {"id": "t-2", "goal": "그 끝점을 만드는 코드", "role": "code_tracer", "priority": 20,
             "action": "code.trace", "params": {"endpoint": "/summary/badge"}, "input_evidence_ids": ["t-1.e1"]},
            {"id": "t-3", "goal": "원천에서 alarm 수를 다시 센다", "role": "recompute_verifier", "priority": 30,
             "action": "recompute.count",
             "params": {"collection": "alarm_events", "filter": {"result": "alarm"},
                        "expect": {"evidence": "t-1.e1", "path": "response.items[0].alarm"}},
             "input_evidence_ids": ["t-2.e1"]},
            {"id": "t-4", "goal": "그 컬렉션에 누가 쓰나", "role": "code_tracer", "priority": 40,
             "action": "code.uses", "params": {"name": "alarm_events"}, "input_evidence_ids": ["t-3.e1"]},
            {"id": "t-5", "goal": "쓰는 함수를 누가 부르나", "role": "code_tracer", "priority": 50,
             "action": "code.callers", "params": {"name": "sink.writer.run"}, "input_evidence_ids": ["t-4.e1"]}],
        "rounds": [{"decision": "continue"}] * 5})


async def test_사다리_대본이_게이트를_한_칸씩_거쳐_스텁으로_끝까지_돈다(clock):
    """폭이 3이라 셋이 한 라운드에 다 돌 수 있는데도 한 라운드에 하나씩이다 — 각 칸의 입력 증거가 앞 칸이
    만든 것이기 때문이다. 마지막 칸은 rest 증거의 **원본**에서 기대값을 꺼내 스텁 mongo의 수와 대조한다."""
    from src.application.dryrun import build_deps, initial_state
    from src.application.graph import build_engine
    from src.application.runner_probe import ProbeRunner
    from src.config.schema_app import InvestigationConfig
    from src.infrastructure.factory import build_adapters
    from tests.application.conftest import site_config

    adapters = build_adapters(site_config(), clock=clock, seeds=LADDER_SEEDS)
    deps = build_deps(_ladder_script(), runner=ProbeRunner(adapters, clock=clock),
                      investigation=InvestigationConfig(max_rounds=7, parallel_width=3))
    final = await build_engine(deps).ainvoke(
        initial_state(_ladder_script(), case_id="c-1", gbm="mx", fct="gumi", clock=clock))
    assert [e.id for e in final["evidence"]] == ["t-1.e1", "t-2.e1", "t-3.e1", "t-4.e1", "t-5.e1"]
    assert final["round"] >= 5 and final["llm_errors"] == []
    # 12a — 판정이 없는 대본은 degraded로 끝나되 리드 계약 위반으로 세지 않는다.
    assert final["verdict"].verdict_type == "degraded" and "대본에 verdict가 없다" in final["verdict"].caveats
    third = next(t for t in final["plan_tasks"] if t.id == "t-3")
    assert third.status == "ok" and "'recomputed': 2" in third.result_summary and "'match': True" in third.result_summary
    # 6d-2 — 넷째·다섯째 칸(uses → callers)도 스텁으로 끝까지 간다.
    last = next(t for t in final["plan_tasks"] if t.id == "t-5")
    assert last.status == "ok" and "부르는 곳이 없다" in last.result_summary


def test_리포에_든_사다리_예제가_실제로_로드된다():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    script = load_script(root / "examples" / "case-ladder.json")
    assert [t.action for t in script.tasks] == ["rest.query", "code.trace", "recompute.count", "code.uses", "code.callers"]
    seeds = json.loads((root / "examples" / "stub-seeds.json").read_text(encoding="utf-8"))
    assert "rest" in seeds and {"trace", "uses", "callers"} <= set(seeds["code"])
    # 대본의 callers 이름은 seeds의 uses 본문에 실제로 있는 쓰는 함수다 — 사다리가 그 이름을 거기서 뽑는다.
    assert script.tasks[4].params["name"] in next(iter(seeds["code"]["uses"].values()))


def test_CLI가_사다리_대본을_돈다(tmp_path, capsys, monkeypatch):
    from pathlib import Path

    from src.__main__ import main

    root = Path(__file__).resolve().parent.parent.parent
    set_real_config_env(monkeypatch)
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(root / "config"), "--env-file", str(tmp_path / "none"),
        "case", "dryrun", "--gbm", "mx", "--fct", "gumi",
        "--plan", str(root / "examples" / "case-ladder.json"),
        "--stub-seeds", str(root / "examples" / "stub-seeds.json")])
    assert main() == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "✅ t-3 [recompute_verifier]" in out
    assert "'recomputed': 2" in out and "'expected': 0" in out and "'match': False" in out
    assert "✅ t-4 [code_tracer]" in out and "✅ t-5 [code_tracer]" in out
    assert "t-1.e1" in out and "t-2.e1" in out and "t-3.e1" in out
    assert "판정 " in out and "검증 통과" in out          # 12a — 대본의 판정이 출력까지 온다


# ── 12a — 대본의 판정 ────────────────────────────────────────────────

async def test_대본의_판정을_conclude가_내고_없으면_사유를_남긴다(case):
    from src.domain.case import Verdict

    with_verdict = Script.model_validate({
        "symptom": "x", "verdict": {"verdict_type": "inconclusive", "confidence": "low",
                                    "narrative": "대본이 정한 판정"}})
    plan = ScriptedPlan(with_verdict)
    patch = await plan.conclude(CaseState(case=case))
    assert isinstance(patch["verdict"], Verdict) and patch["verdict"].narrative == "대본이 정한 판정"

    bare = ScriptedPlan(Script.model_validate({"symptom": "x"}))
    patch = await bare.conclude(CaseState(case=case))
    assert "verdict" not in patch and "verdict" in patch["note"] and "llm_errors" not in patch


def test_대본의_판정도_StrictModel이다(tmp_path):
    with pytest.raises(SystemExit) as caught:
        load_script(write(tmp_path, {"symptom": "x", "verdict": {
            "verdict_type": "inconclusive", "confidence": "low", "narrative": "n", "score": 1}}))
    assert "score" in str(caught.value)


async def test_사다리_대본은_판정까지_가고_인용이_실재한다(clock):
    """예제 대본의 판정이 verify를 **실제로** 지난다 — 인용 id가 그 대본이 만든 증거다."""
    from pathlib import Path

    from src.application.dryrun import build_deps, initial_state
    from src.application.graph import build_engine
    from src.application.runner_probe import ProbeRunner
    from src.config.schema_app import InvestigationConfig
    from src.infrastructure.factory import build_adapters
    from tests.application.conftest import site_config

    root = Path(__file__).resolve().parent.parent.parent
    script = load_script(root / "examples" / "case-ladder.json")
    seeds = json.loads((root / "examples" / "stub-seeds.json").read_text(encoding="utf-8"))
    adapters = build_adapters(site_config(), clock=clock, seeds=seeds)
    deps = build_deps(script, runner=ProbeRunner(adapters, clock=clock),
                      investigation=InvestigationConfig(max_rounds=7, parallel_width=3))
    final = await build_engine(deps).ainvoke(
        initial_state(script, case_id="c-1", gbm="mx", fct="gumi", clock=clock))
    assert final["verdict"] is not None and final["verdict"].verdict_type != "degraded"
    assert final["verdict"].root_cause is not None
    assert final["verify_problems"] == [] and final["verify_attempts"] == 0
    cited = {i for link in [final["verdict"].root_cause, *final["verdict"].alternates,
                            *final["verdict"].contributing] for i in link.evidence_ids}
    assert cited and cited <= {e.id for e in final["evidence"]}
