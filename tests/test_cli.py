import io
import json
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

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
    # I1: round_hint가 round_started.data까지 실제로 실려서 CLI가 "[라운드 N]"을
    # 찍는다 — 계획의 수용 예시("[라운드 2] …")가 자기 예시에서 깨지지 않는다.
    assert "[라운드 1]" in out
    # 완료 기준: 이벤트 봉투 5종 밖의 값(그래프 내부 노드명)이 CLI 출력에 나타나지 않는다
    for node in ("frame", "select", "execute", "integrate", "ask_human", "conclude", "verify"):
        assert node not in out


def test_접수_문답은_human_intake_증거로_박제된다(tmp_path, capsys, monkeypatch):
    """I3 회귀: intake()가 모은 qa(재질문·답)가 store에 human:intake로 남아야
    사람이 접수 때 답한 사실이 엔진에 전달된다 — 안 하면 데이터 손실이다.

    intake 1차 호출이 missing 질문 하나를 내 재질문(ask)이 실제로 발생하게 하고,
    그 답이 store.list_evidence(case_id)에 human:intake 소스로 남는지 확인한다.
    (missing=[]인 기존 1왕복 chat 테스트는 qa가 비는 경로만 지나가므로 별도다.)
    """
    _chat_tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    # 1번째 입력: 접수 재질문(intake의 ask) 답. 2번째 입력: 그래프 ask_human 답.
    monkeypatch.setattr("sys.stdin", io.StringIO("최근 배포 없음\n계획 변경 없음\n"))

    intake_missing_json = ('{"gbm": "mx", "fct": "gumi", "target_locator": null, '
                           '"missing": ["최근 배포 이력이 있나요?"]}')
    verdict_cites_ev2 = ('{"verdict_type": "stale_data", "confidence": "high", '
                         '"narrative": "plan 동기화 지연.", '
                         '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-2"]}}')
    lead_llm = ScriptedLLM([intake_missing_json, _INTAKE_JSON, FRAME_ONE_TASK, ASK_JSON,
                            INTEGRATE_CONCLUDE, verdict_cites_ev2])
    subagent_llm = ToolFake(messages=iter([_mongo_call(), _report(["ev-2"])]))

    def fake_build_chat_model(profile, *, base_url=None, api_key=None):
        return {"l": lead_llm, "s": subagent_llm}.get(profile, object())

    monkeypatch.setattr("src.patrol.daemon.build_chat_model", fake_build_chat_model)

    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    monkeypatch.setattr("src.__main__.build_persistence", lambda cfg: (store, repo, ledger))

    code = main(["chat", "--gbm", "mx", "--fct", "gumi", "--symptom", "OEE가 이상하다",
                "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "보고서:" in out

    closed = repo.list_by_status("closed")
    assert len(closed) == 1
    case_id = closed[0].id

    intake_evidence = [r for r in store.list_evidence(case_id) if r.source == "human:intake"]
    assert len(intake_evidence) == 1
    body = store.get_evidence(case_id, intake_evidence[0].id)
    # "at"은 intake() 내부의 실제 wall clock 호출이라 정확히 맞추지 않는다 —
    # 질문·답이 human:intake로 그대로 실려 있는지만 확인한다.
    assert body["qa"][0]["question"] == "최근 배포 이력이 있나요?"
    assert body["qa"][0]["answer"] == "최근 배포 없음"


def test_case_resume도_보고서를_남기고_이벤트를_찍는다(tmp_path, capsys, monkeypatch):
    """세 종결 경로가 같은 발행 배선을 쓰는지 — C1 회귀 방지.

    chat 1왕복 테스트와 같은 픽스처로 케이스를 열되, 질문에 답하지 않고 stdin을
    바로 끊어(EOFError) awaiting_human으로 파킹만 시킨다. 그다음 별도의
    `case resume` 호출(별 프로세스를 흉내 — build_persistence/build_checkpointer를
    monkeypatch해 두 호출이 같은 store/repo/checkpointer를 보게 고정)이 보고서
    파일·이벤트·"재개 결과" 한 줄을 전부 낸다는 걸 확인한다 — 예전엔(C1)
    case resume만 이 셋을 전부 건너뛰고 조용히 케이스를 닫았다.
    """
    _chat_tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))       # 바로 EOF — 답하지 않고 파킹

    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    checkpointer = InMemorySaver()
    monkeypatch.setattr("src.__main__.build_persistence", lambda cfg: (store, repo, ledger))
    monkeypatch.setattr("src.__main__.build_checkpointer", lambda cfg: checkpointer)

    lead_llm = ScriptedLLM([_INTAKE_JSON, FRAME_ONE_TASK, ASK_JSON])
    subagent_llm = ToolFake(messages=iter([_mongo_call(), _report(["ev-1"])]))

    def fake_build_chat_model(profile, *, base_url=None, api_key=None):
        return {"l": lead_llm, "s": subagent_llm}.get(profile, object())

    monkeypatch.setattr("src.patrol.daemon.build_chat_model", fake_build_chat_model)

    code = main(["chat", "--gbm", "mx", "--fct", "gumi", "--symptom", "OEE가 이상하다",
                "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 0
    assert "파킹된 채로 남는다" in capsys.readouterr().out

    awaiting = repo.list_by_status("awaiting_human")
    assert len(awaiting) == 1
    case_id = awaiting[0].id

    # 두 번째 프로세스를 흉내 — 새 스크립트로 남은 라운드(conclude·verify)를 완주시킨다.
    lead_llm2 = ScriptedLLM([INTEGRATE_CONCLUDE, ONE_EVIDENCE_VERDICT_JSON])
    subagent_llm2 = ToolFake(messages=iter([]))

    def fake_build_chat_model2(profile, *, base_url=None, api_key=None):
        return {"l": lead_llm2, "s": subagent_llm2}.get(profile, object())

    monkeypatch.setattr("src.patrol.daemon.build_chat_model", fake_build_chat_model2)

    code2 = main(["case", "resume", case_id, "--answer", "계획 변경 없음",
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    out2 = capsys.readouterr().out

    assert code2 == 0
    assert "재개 결과: closed" in out2
    # 봉투 기반 한 줄 요약이 찍힌다 — investigating → closed 상태 전이 + 보고서 준비.
    assert "[상태] investigating" in out2 and "[상태] closed" in out2
    assert "[보고서 준비]" in out2

    written = list((tmp_path / "out").glob("*.md"))
    assert len(written) == 1 and "## 2. 판정" in written[0].read_text(encoding="utf-8")
    assert repo.get(case_id).status == "closed"


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


def test_patrol_run은_이벤트_싱크를_daemon에_넘긴다(tmp_path, monkeypatch):
    # _run_patrol이 on_event를 안 넘겨 프로덕션 데몬이 엔진 이벤트를 아예 내지
    # 않았다. usecase에 `if on_event is None: ainvoke` 분기가 있어 _stream_and_collect
    # (round_started/task_finished/question_raised를 내는 유일한 경로)이 죽고,
    # daemon._publish_report의 `if self.on_event is not None` 때문에 report_ready도
    # 안 나간다 — 규율 8이 요구하는 "파일·이벤트·메일" 셋 중 이벤트가 빠진 상태다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))

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
            return None

    monkeypatch.setattr("src.__main__.PatrolDaemon", SpyDaemon)
    code = main(["patrol", "run", "--for-seconds", "0",
                "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])

    assert code == 0
    assert callable(captured.get("on_event"))
