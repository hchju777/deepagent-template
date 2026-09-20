"""대본 파일 — 손으로 쓰는 것이므로 오타가 조용히 넘어가면 안 된다."""
import json

import pytest

from src.application.dryrun import Script, ScriptedPlan, load_script, strip_comments
from src.application.state import CaseState

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
    for key in ("REDIS_PASSWORD", "MONGO_PASSWORD", "LLM_BASE_URL", "LLM_CLIENT_KEY",
                "LLM_PASS_KEY", "MAIL_AGENT_API_KEY", "MAIL_AGENT_ID"):
        monkeypatch.setenv(key, "https://x/v1" if key.endswith("URL") else "x")
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(root / "config"), "--env-file", str(tmp_path / "none"),
        "case", "dryrun", "--gbm", "mx", "--fct", "gumi",
        "--plan", str(root / "examples" / "case-dryrun.json"),
        "--stub-seeds", str(root / "examples" / "stub-seeds.json")])

    assert main() == 0
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
