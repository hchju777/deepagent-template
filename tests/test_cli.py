import io
import json
from datetime import datetime, timezone

from langchain_core.messages import AIMessage

import src.__main__ as main_module
from src.__main__ import main
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.infrastructure.llm import ScriptedLLM
from src.patrol.ledger import InMemoryLedger
from tests.application.test_graph_e2e import (ASK_JSON, FRAME_ONE_TASK, INTEGRATE_CONCLUDE,
                                              ONE_EVIDENCE_VERDICT_JSON)
from tests.application.test_subagents import ToolFake
from tests.test_boot import ENV, _tree, _write   # 트리 픽스처 재사용

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_registry_출력(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["registry", "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out
    assert code == 0 and "mx/gumi" in out and "mx/off" in out


def test_config_show는_비밀을_마스킹한다(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ",
                        {**ENV, "MX_REDIS_PW": "hunter2"})
    # redis에 password 참조 추가
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["redis"]["password"] = "${MX_REDIS_PW}"
    gbm.write_text(json.dumps(data), encoding="utf-8")

    code = main(["config", "show", "--gbm", "mx", "--fct", "gumi",
                 "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out
    assert code == 0
    assert "hunter2" not in out and "**********" in out
    assert "gbm/mx" in out                       # 출처 표시


def test_knowledge_validate_실패는_exit_1(tmp_path, capsys, monkeypatch):
    _tree(tmp_path, check_target="rest:/ghost")
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["knowledge", "validate",
                 "--config-root", str(tmp_path / "config"),
                 "--repo-root", str(tmp_path)])
    assert code == 1
    assert "rest:/ghost" in capsys.readouterr().err


def test_깨진_registry는_stderr와_exit_1(tmp_path, capsys, monkeypatch):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "registry.json").write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["registry", "--config-root", str(tmp_path / "config")])
    assert code == 1
    assert "JSON 파싱 실패" in capsys.readouterr().err


def test_patrol_status_memory_백엔드_안내(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["patrol", "status", "--config-root", str(tmp_path / "config"),
                 "--repo-root", str(tmp_path)])
    assert code == 0 and "메모리 백엔드" in capsys.readouterr().out


def test_patrol_run_은_기동_검증_실패면_exit_1(tmp_path, capsys, monkeypatch):
    _tree(tmp_path, check_target="rest:/ghost")
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["patrol", "run", "--for-seconds", "0",
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 1 and "rest:/ghost" in capsys.readouterr().err


def test_patrol_run_성공_경로는_exit_0(tmp_path, capsys, monkeypatch):
    # I8: build_chat_model을 monkeypatch해 실LLM 없이도 patrol run(기동 검증 →
    # 사이트 조립 → 데몬 기동 → --for-seconds 0으로 즉시 종료)이 성공 경로를
    # 끝까지 탄다는 걸 스모크한다. ENV는 test_boot.ENV를 재사용하므로
    # LLM_API_KEY가 이미 들어있다(검사 11).
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))

    def fake_build_chat_model(model_name, *, base_url=None, api_key=None):
        return object()   # 이 트리의 점검은 judge=rule이라 실제로 호출되지 않는다

    monkeypatch.setattr("src.patrol.daemon.build_chat_model", fake_build_chat_model)
    monkeypatch.setattr("src.__main__.build_chat_model", fake_build_chat_model)
    code = main(["patrol", "run", "--for-seconds", "0",
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 0


def test_case_list_는_빈_저장소에서_빈_출력(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["case", "list", "--config-root", str(tmp_path / "config")])
    assert code == 0


def test_patrol_run은_report_cfg와_mail_sender를_daemon에_넘긴다(tmp_path, monkeypatch):
    # 계획 5 리뷰가 짚은 배선 공백: _run_patrol이 PatrolDaemon(...)에 report_cfg/
    # mail_sender를 안 넘겨 기본 ReportConfig()가 쓰이던 문제(app.report가 무시됨)의
    # 회귀 테스트 — PatrolDaemon을 스파이로 바꿔 실제로 넘어온 kwargs를 잡는다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    app_path = tmp_path / "config" / "app.json"
    data = json.loads(app_path.read_text(encoding="utf-8"))
    data["report"] = {"output_dir": str(tmp_path / "custom-out")}
    app_path.write_text(json.dumps(data), encoding="utf-8")

    def fake_build_chat_model(model_name, *, base_url=None, api_key=None):
        return object()

    monkeypatch.setattr("src.patrol.daemon.build_chat_model", fake_build_chat_model)
    monkeypatch.setattr("src.__main__.build_chat_model", fake_build_chat_model)

    captured = {}
    real_daemon_cls = main_module.PatrolDaemon

    class SpyDaemon(real_daemon_cls):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(**kwargs)

        async def run(self, stop):
            return None   # 스케줄러를 기동하지 않고 즉시 반환 — 배선만 확인하면 된다

    monkeypatch.setattr("src.__main__.PatrolDaemon", SpyDaemon)
    code = main(["patrol", "run", "--for-seconds", "0",
                "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])

    assert code == 0
    assert captured["report_cfg"].output_dir == str(tmp_path / "custom-out")
    assert captured["mail_sender"] is None   # mail.enabled 기본 False


def _chat_tree(tmp_path):
    _write(tmp_path, "config/app.json", json.dumps(
        {"llm": {"profiles": {"judge": "j", "subagent": "s", "lead": "l"}},
         "report": {"output_dir": str(tmp_path / "out")}}))
    _write(tmp_path, "config/registry.json", json.dumps(
        {"sites": [{"gbm": "mx", "fct": "gumi"}]}))
    _write(tmp_path, "config/gbm/mx.json", json.dumps(
        {"target": {"mongo": {"url": "mongodb://x:27017"}}}))
    _write(tmp_path, "knowledge/topology/common.yaml", """
services:
  twin-api:
    writes: [ { kind: rest, endpoint: /oee } ]
derivations:
  "rest:/oee": { inputs: [ { kind: mongo, collection: twin_state } ], via: twin-api }
""")
    return tmp_path


_INTAKE_JSON = '{"gbm": "mx", "fct": "gumi", "target_locator": "rest:/oee", "missing": []}'


def _mongo_call():
    return AIMessage(content="", tool_calls=[{
        "name": "mongo_find", "id": "c1", "args": {"collection": "twin_state", "filter_json": "{}"}}])


def _report(evidence_ids):
    ids = ", ".join(f'"{e}"' for e in evidence_ids)
    return AIMessage(content=f'{{"status": "ok", "summary": "확인", "evidence_ids": [{ids}]}}')


def test_chat_1왕복_대화형_흐름은_접수부터_보고서까지_완주한다(tmp_path, capsys, monkeypatch):
    # 접수(intake, LLM 호출 1) → frame → ask(interrupt, 라운드 1) → stdin으로
    # 답 하나를 받아 resume → conclude·verify까지 완주해 보고서를 낸다. "1왕복"
    # 정확히 하나(질문 하나·답 하나)만 오가는 최소 시나리오다.
    _chat_tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    monkeypatch.setattr("sys.stdin", io.StringIO("계획 변경 없음\n"))

    lead_llm = ScriptedLLM([_INTAKE_JSON, FRAME_ONE_TASK, ASK_JSON, INTEGRATE_CONCLUDE,
                            ONE_EVIDENCE_VERDICT_JSON])
    subagent_llm = ToolFake(messages=iter([_mongo_call(), _report(["ev-1"])]))

    def fake_build_chat_model(profile, *, base_url=None, api_key=None):
        return {"l": lead_llm, "s": subagent_llm}.get(profile, object())

    monkeypatch.setattr("src.patrol.daemon.build_chat_model", fake_build_chat_model)

    code = main(["chat", "--gbm", "mx", "--fct", "gumi", "--symptom", "OEE가 이상하다",
                "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "케이스" in out and "접수" in out
    assert "계획 변경이 있었나요?" in out            # question_raised 이벤트 + ask() 프롬프트
    assert "보고서:" in out
    written = list((tmp_path / "out").glob("*.md"))
    assert len(written) == 1 and "## 2. 판정" in written[0].read_text(encoding="utf-8")
    # 완료 기준: 이벤트 봉투 5종 밖의 값(그래프 내부 노드명)이 CLI 출력에 나타나지 않는다
    for node in ("frame", "select", "execute", "integrate", "ask_human", "conclude", "verify"):
        assert node not in out


def test_chat는_등록되지_않은_사이트면_exit_1(tmp_path, capsys, monkeypatch):
    _chat_tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))

    code = main(["chat", "--gbm", "mx", "--fct", "ghost", "--symptom", "s",
                "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 1
    assert "등록" in capsys.readouterr().err


def test_case_show_report는_저장된_보고서_파일을_그대로_보여준다(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))

    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         origin="human", status="closed", created_at=T, updated_at=T))
    monkeypatch.setattr("src.__main__.build_persistence", lambda cfg: (store, repo, ledger))

    report_dir = tmp_path / "myreports"
    report_dir.mkdir()
    (report_dir / "c-1.md").write_text("# 저장된 보고서\n내용", encoding="utf-8")
    app_path = tmp_path / "config" / "app.json"
    data = json.loads(app_path.read_text(encoding="utf-8"))
    data["report"] = {"output_dir": str(report_dir)}
    app_path.write_text(json.dumps(data), encoding="utf-8")

    code = main(["case", "show", "c-1", "--report", "--config-root", str(tmp_path / "config")])

    assert code == 0
    assert capsys.readouterr().out == "# 저장된 보고서\n내용"


def test_case_show_report는_파일이_없으면_즉석_렌더한다(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))

    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    repo.save(CaseRecord(id="c-2", gbm="mx", fct="gumi", fingerprint="fp", symptom="증상", t0=T,
                         origin="human", status="closed", created_at=T, updated_at=T))
    monkeypatch.setattr("src.__main__.build_persistence", lambda cfg: (store, repo, ledger))

    code = main(["case", "show", "c-2", "--report", "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out

    assert code == 0
    assert "# 케이스 c-2 보고서" in out and "## 5. 조사 경위" in out
