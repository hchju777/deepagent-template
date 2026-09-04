import io
import json

import pytest
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import src.__main__ as main_module
from src.__main__ import main
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.events import InMemoryEventStore
from src.domain.snapshot import InMemoryVerdictSnapshotStore
from src.infrastructure.checkpointer import Persistence
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
    written = list((tmp_path / "out").glob("*.html"))
    assert len(written) == 1 and "<h2>2. 판정</h2>" in written[0].read_text(encoding="utf-8")
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
    monkeypatch.setattr("src.__main__.build_persistence",
                        lambda cfg: Persistence(store, repo, ledger, InMemoryEventStore(),
                                                InMemoryVerdictSnapshotStore()))

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
    # concern이 프로세스 경계를 넘는지도 여기서 본다 — 파킹은 chat이, 발송은
    # case resume이 하므로 축이 레코드에 남아 있지 않으면 수신자가 갈린다.
    app_path = tmp_path / "config" / "app.json"
    app_data = json.loads(app_path.read_text(encoding="utf-8"))
    app_data.setdefault("report", {})["mail"] = {
        "enabled": True, "host": "smtp", "sender": "a@x", "recipients": ["platform@y"],
        "recipients_by_concern": {"operation": ["ops@y"]}}
    app_path.write_text(json.dumps(app_data), encoding="utf-8")
    sent = []

    class _Spy:
        async def send(self, subject, body, *, recipients, html=None):
            sent.append(recipients)

    monkeypatch.setattr("src.__main__.SmtpSender", lambda cfg: _Spy())

    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    checkpointer = InMemorySaver()
    monkeypatch.setattr("src.__main__.build_persistence",
                        lambda cfg: Persistence(store, repo, ledger, InMemoryEventStore(),
                                                InMemoryVerdictSnapshotStore()))
    monkeypatch.setattr("src.__main__.build_checkpointer", lambda cfg: checkpointer)

    lead_llm = ScriptedLLM([_INTAKE_JSON, FRAME_ONE_TASK, ASK_JSON])
    subagent_llm = ToolFake(messages=iter([_mongo_call(), _report(["ev-1"])]))

    def fake_build_chat_model(profile, *, base_url=None, api_key=None):
        return {"l": lead_llm, "s": subagent_llm}.get(profile, object())

    monkeypatch.setattr("src.patrol.daemon.build_chat_model", fake_build_chat_model)

    code = main(["chat", "--gbm", "mx", "--fct", "gumi", "--symptom", "OEE가 이상하다",
                "--concern", "operation",
                "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 0
    assert "파킹된 채로 남는다" in capsys.readouterr().out

    awaiting = repo.list_by_status("awaiting_human")
    assert len(awaiting) == 1
    case_id = awaiting[0].id
    # 워커가 파킹할 때 종류를 붙여야 한다(계획 12) — 안 붙이면 접수/조사 구별이
    # "라벨이 없다"에 기대게 되고, 접수 쪽 가드가 새로 파킹된 케이스에 무력하다.
    assert awaiting[0].question_kind == "investigation"

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

    written = list((tmp_path / "out").glob("*.html"))
    assert len(written) == 1 and "<h2>2. 판정</h2>" in written[0].read_text(encoding="utf-8")
    assert repo.get(case_id).status == "closed"
    # 축이 파킹을 건너 발송까지 살아남았는가 — chat이 정한 값을 다른 호출이 읽는다.
    assert repo.get(case_id).concern == "operation"
    assert sent == [["ops@y"]], sent


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
    monkeypatch.setattr("src.__main__.build_persistence",
                        lambda cfg: Persistence(store, repo, ledger, InMemoryEventStore(),
                                                InMemoryVerdictSnapshotStore()))

    report_dir = tmp_path / "myreports"
    report_dir.mkdir()
    (report_dir / "c-1.html").write_text("<h1>저장된 보고서</h1>", encoding="utf-8")
    app_path = tmp_path / "config" / "app.json"
    data = json.loads(app_path.read_text(encoding="utf-8"))
    data["report"] = {"output_dir": str(report_dir)}
    app_path.write_text(json.dumps(data), encoding="utf-8")

    code = main(["case", "show", "c-1", "--report", "--config-root", str(tmp_path / "config")])

    assert code == 0
    assert capsys.readouterr().out == "<h1>저장된 보고서</h1>"


def test_case_show_report는_파일이_없으면_즉석_렌더한다(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))

    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    repo.save(CaseRecord(id="c-2", gbm="mx", fct="gumi", fingerprint="fp", symptom="증상", t0=T,
                         origin="human", status="closed", created_at=T, updated_at=T))
    monkeypatch.setattr("src.__main__.build_persistence",
                        lambda cfg: Persistence(store, repo, ledger, InMemoryEventStore(),
                                                InMemoryVerdictSnapshotStore()))

    code = main(["case", "show", "c-2", "--report", "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out

    assert code == 0
    assert "<h1>케이스 c-2 보고서</h1>" in out and "<h2>5. 조사 경위</h2>" in out


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


def test_이벤트_싱크는_저장한_뒤_downstream으로_넘긴다():
    from src.__main__ import _make_event_sink
    from src.domain.events import EngineEvent
    events = InMemoryEventStore()
    seen = []
    sink = _make_event_sink(events, downstream=seen.append)
    sink(EngineEvent(event="round_started", case_id="c-1", at=T))
    assert [e.seq for e in events.since("c-1")] == [1]
    assert seen[0].seq == 1          # downstream도 seq가 채워진 사본을 본다


def test_이벤트_저장이_실패해도_싱크는_raise하지_않는다():
    from src.__main__ import _make_event_sink
    from src.domain.events import EngineEvent, EventStorePort

    class BrokenStore(EventStorePort):
        def append(self, event): raise RuntimeError("스토어 장애")
        def since(self, case_id, after_seq=0, limit=200): return []
        def prune_before(self, before): return 0

    seen = []
    sink = _make_event_sink(BrokenStore(), downstream=seen.append)
    sink(EngineEvent(event="round_started", case_id="c-1", at=T))   # raise하면 실패
    assert len(seen) == 1            # 저장이 죽어도 stdout 출력은 계속된다

def test_config_show_출력은_다시_읽힌다(tmp_path, capsys, monkeypatch):
    # alias 필드(resolve의 `from`)를 파이썬 이름(`from_`)으로 찍으면 그 출력을
    # config로 되먹일 때 StrictModel이 거부한다 — 사람이 그렇게 쓴다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["rest"] = {
        "base_url": "http://x",
        "entries": {"e": {"method": "POST", "path": "/x", "body_schema": {"d": "str"}}}}
    data["patrol"] = {"checks": {"c": {
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body"},
        "resolve": {"d": {"from": "clock", "expr": "today"}}}}}
    gbm.write_text(json.dumps(data), encoding="utf-8")

    code = main(["config", "show", "--gbm", "mx", "--fct", "gumi",
                 "--config-root", str(tmp_path / "config")])
    printed = capsys.readouterr().out.split("\n# 출처")[0]
    assert code == 0
    from src.config.schema_site import SiteConfig
    SiteConfig.model_validate(json.loads(printed))     # 되먹여도 통과해야 한다
def test_실_어댑터_사이트에_시드를_주면_거부한다(tmp_path, monkeypatch, capsys):
    # 시드는 스텁에서만 쓰인다. 조용히 무시하면 사람이 "가짜 데이터로 돌고 있다"고
    # 믿는 채 실제 대상을 두드린다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["adapters"] = "real"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"mx/gumi": {"rest_responses": {}}}), encoding="utf-8")

    code = main(["patrol", "run", "--for-seconds", "0", "--stub-seeds", str(seeds),
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 1
    assert "mx/gumi" in capsys.readouterr().err


def test_시드_파일이_점검을_실제로_성공시킨다(tmp_path, monkeypatch):
    # 플래그가 하는 일이 있는지 본다 — 주면 finding, 안 주면 404 error.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["rest"] = {"base_url": "http://x"}
    data["patrol"] = {"checks": {"api.oee": {
        "judge": "rule", "schedule": {"interval": "3s"}, "target": "rest:/oee",
        "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}}}}
    gbm.write_text(json.dumps(data), encoding="utf-8")
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"mx/gumi": {"rest_responses": {"/oee": {"oee": 512}}}}),
                     encoding="utf-8")

    outcomes = []

    class SpyDaemon(main_module.PatrolDaemon):
        async def run(self, stop):
            self.build()
            real = self.ledger.record_run
            self.ledger.record_run = lambda g, f, n, o: (outcomes.append(o), real(g, f, n, o))[1]
            await self.run_one("mx", "gumi", "api.oee",
                               self.sites[0].cfg.patrol.checks["api.oee"])

    monkeypatch.setattr("src.__main__.PatrolDaemon", SpyDaemon)
    argv = ["patrol", "run", "--for-seconds", "0",
            "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)]
    assert main(argv + ["--stub-seeds", str(seeds)]) == 0
    assert outcomes[0].status == "finding", outcomes[0].error

    outcomes.clear()
    assert main(argv) == 0
    assert outcomes[0].status == "error"            # 시드 없이는 404


def test_knowledge_validate가_라이브_드리프트를_실제로_알린다(tmp_path, monkeypatch, capsys):
    # validate_boot(stub_seeds=...)가 테스트 전용 이음매로 남지 않게, CLI가 그
    # 경로를 그대로 탄다. 계획 6~10에서 "함수는 되는데 배선이 안 된다"가 다섯 번
    # 나왔다 — 배선을 지나는 테스트만이 그것을 잡는다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    spec = {"paths": {"/x": {"get": {"parameters": [
        {"name": "a", "in": "query", "schema": {"type": "string"}}]}}}}
    (tmp_path / "knowledge" / "target_api" / "mx").mkdir(parents=True)
    (tmp_path / "knowledge" / "target_api" / "mx" / "gumi.json").write_text(
        json.dumps(spec), encoding="utf-8")
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["rest"] = {"base_url": "http://x", "entries": {
        "e": {"method": "GET", "path": "/x", "query_schema": {"a": "str"}}}}
    data["patrol"] = {"checks": {}}
    gbm.write_text(json.dumps(data), encoding="utf-8")

    drifted = {"paths": {"/x": {"get": {"parameters": []}}}}      # 대상이 a를 지웠다
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"mx/gumi": {"rest_openapi": drifted}}), encoding="utf-8")
    argv = ["knowledge", "validate", "--config-root", str(tmp_path / "config"),
            "--repo-root", str(tmp_path)]
    assert main(argv) == 0                                        # --live 없으면 정적만
    assert main(argv + ["--live", "--stub-seeds", str(seeds)]) == 1
    assert "'a'" in capsys.readouterr().err


def test_사이트를_조립하는_명령은_모두_시드를_받는다(tmp_path, monkeypatch, capsys):
    # assemble_sites 호출부가 셋(patrol run·chat·case resume)인데 하나만 배선하면
    # 언젠가 나머지가 조용히 다르게 돈다 — 케이스 종결 세 경로가 같은 발행 배선을
    # 써야 하는 것과 같은 이유(규율 8). 예시 트리로 chat을 돌리면 REST 프로브가
    # 전부 404가 되던 회귀가 실제로 있었다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    missing = str(tmp_path / "없는파일.json")
    common = ["--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)]
    for argv in (["patrol", "run", "--for-seconds", "0"],
                 ["chat", "--gbm", "mx", "--fct", "gumi", "--symptom", "s"],
                 ["case", "resume", "c-1", "--answer", "a"]):
        code = main([*argv, "--stub-seeds", missing, *common])
        assert code == 1, argv
        # 플래그를 모르면 argparse가 SystemExit(2)를 낸다. 1이면서 시드 파일을
        # 지목한다는 것은 세 경로가 같은 로더를 탔다는 뜻이다.
        assert "시드 파일을 읽을 수 없다" in capsys.readouterr().err, argv


def test_세_명령_모두_읽은_시드를_assemble_sites까지_넘긴다(tmp_path, monkeypatch):
    # 앞 테스트는 "시드 파일이 깨지면 셋 다 exit 1"만 본다 — 로더는 지키지만
    # **인계는 안 지킨다.** chat의 stub_seeds= 인자만 지워도 전부 초록이었다.
    # 이 리포가 반복해서 겪은 "테스트가 지키는 줄 알았던 규율"의 그 자리다.
    from src.infrastructure.factory import StubSeeds
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    seeds_file = tmp_path / "seeds.json"
    seeds_file.write_text(json.dumps({"mx/gumi": {"rest_responses": {"/oee": {"x": 1}}}}),
                          encoding="utf-8")
    expected = {"mx/gumi": StubSeeds(rest_responses={"/oee": {"x": 1}})}

    seen = []
    real = main_module.assemble_sites

    def spy(*args, **kwargs):
        seen.append(kwargs.get("stub_seeds"))
        return real(*args, **kwargs)

    monkeypatch.setattr("src.__main__.assemble_sites", spy)
    common = ["--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)]
    for argv in (["patrol", "run", "--for-seconds", "0"],
                 ["chat", "--gbm", "mx", "--fct", "없는사이트", "--symptom", "s"],
                 ["case", "resume", "c-1", "--answer", "a"]):
        seen.clear()
        main([*argv, "--stub-seeds", str(seeds_file), *common])
        assert seen == [expected], (argv, seen)


def test_세_명령_모두_실_어댑터에_시드를_주면_거부한다(tmp_path, monkeypatch, capsys):
    # patrol run만 테스트가 있었다. 가드도 셋 다 있어야 한다 — 시드가 조용히
    # 무시되면 사람이 "가짜 데이터로 돌고 있다"고 믿는 채 실제 대상을 두드린다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["adapters"] = "real"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    seeds_file = tmp_path / "seeds.json"
    seeds_file.write_text(json.dumps({"mx/gumi": {"rest_responses": {}}}), encoding="utf-8")

    common = ["--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)]
    for argv in (["patrol", "run", "--for-seconds", "0"],
                 ["chat", "--gbm", "mx", "--fct", "gumi", "--symptom", "s"],
                 ["case", "resume", "c-1", "--answer", "a"]):
        assert main([*argv, "--stub-seeds", str(seeds_file), *common]) == 1, argv
        assert 'adapters="real"' in capsys.readouterr().err, argv


def test_knowledge_validate도_실_어댑터에_시드를_주면_거부한다(tmp_path, monkeypatch, capsys):
    # 시드를 받는 네 번째 경로다. 여기만 가드를 빼면 adapters="real" 사이트에서
    # 시드가 조용히 무시되고 --live가 **실제 네트워크를 친다** — 사람은 예행이라고
    # 믿는다. "세 경로를 하나의 로더로"라고 선언한 계약이 넷째에서 깨진 자리다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["adapters"] = "real"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    seeds_file = tmp_path / "seeds.json"
    seeds_file.write_text(json.dumps({"mx/gumi": {"rest_openapi": {"paths": {}}}}),
                          encoding="utf-8")

    code = main(["knowledge", "validate", "--live", "--stub-seeds", str(seeds_file),
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 1
    assert 'adapters="real"' in capsys.readouterr().err


def test_chat이_연_케이스도_concern을_정하고_수신자를_가른다(tmp_path, capsys, monkeypatch):
    # 스펙 §3.4의 타깃 3("데이터는 있는데 운영 시스템에 안 나온다")이 바로 운영
    # 이상 질문인데, 사람이 연 케이스에 축을 정할 방법이 없으면 그 질문은 영원히
    # 플랫폼 담당에게 라우팅된다. 플래그가 파서에 있는 것과 발행까지 닿는 것은
    # 다르므로 실제 수신자를 본다.
    _chat_tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    monkeypatch.setattr("sys.stdin", io.StringIO("계획 변경 없음\n"))
    app_path = tmp_path / "config" / "app.json"
    data = json.loads(app_path.read_text(encoding="utf-8"))
    data["report"] = {"output_dir": str(tmp_path / "out"), "mail": {
        "enabled": True, "host": "smtp", "sender": "a@x", "recipients": ["platform@y"],
        "recipients_by_concern": {"operation": ["ops@y"]}}}
    app_path.write_text(json.dumps(data), encoding="utf-8")

    lead_llm = ScriptedLLM([_INTAKE_JSON, FRAME_ONE_TASK, ASK_JSON, INTEGRATE_CONCLUDE,
                            ONE_EVIDENCE_VERDICT_JSON])
    subagent_llm = ToolFake(messages=iter([_mongo_call(), _report(["ev-1"])]))
    monkeypatch.setattr("src.patrol.daemon.build_chat_model",
                        lambda profile, *, base_url=None, api_key=None:
                        {"l": lead_llm, "s": subagent_llm}.get(profile, object()))

    sent = []

    class _Spy:
        async def send(self, subject, body, *, recipients, html=None):
            sent.append(recipients)

    monkeypatch.setattr("src.__main__.SmtpSender", lambda cfg: _Spy())
    code = main(["chat", "--gbm", "mx", "--fct", "gumi", "--symptom", "데이터가 안 보인다",
                 "--concern", "operation",
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 0, capsys.readouterr().err
    assert sent == [["ops@y"]], sent


def _park_intake(repo, case_id, question="어느 라인인가?"):
    repo.save(repo.get(case_id).model_copy(update={
        "status": "awaiting_human", "question": question, "question_kind": "intake"}))


def test_접수_질문에_답하면_접수를_이어간다(tmp_path, capsys, monkeypatch):
    # 그래프를 재개하려 들면 스레드가 없어 실패하고, F3가 그 실패를 조사 실패로
    # 기록한다 — 사람 눈에는 "답했는데 조사가 깨졌다"로 보인다.
    from src.application.open_case import open_case
    _chat_tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    monkeypatch.setattr("src.__main__.build_persistence",
                        lambda cfg: Persistence(store, repo, ledger, InMemoryEventStore(),
                                                InMemoryVerdictSnapshotStore()))
    monkeypatch.setattr("src.__main__.build_checkpointer", lambda cfg: InMemorySaver())
    record = open_case(repo=repo, store=store, symptom="OEE가 이상하다", gbm="mx", fct="gumi",
                       concern="system", requested_by=None,
                       clock=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc),
                       on_event=lambda e: None)
    _park_intake(repo, record.id)

    lead = ScriptedLLM(['{"target_locator": "mongo:twin_state", "missing": []}'])
    monkeypatch.setattr("src.patrol.daemon.build_chat_model",
                        lambda profile, *, base_url=None, api_key=None:
                        lead if profile == "l" else object())

    code = main(["case", "resume", record.id, "--answer", "라인 7",
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 0, capsys.readouterr().err
    after = repo.get(record.id)
    assert after.question_kind is None and after.question is None
    assert after.target_locator == "mongo:twin_state"
    # 접수가 끝났으면 **그대로 조사가 시작돼야 한다** — open에 멈춰 있으면 사람이
    # 답한 뒤 아무 반응이 없고, 그걸 움직이는 건 데몬의 주기 재큐뿐이다.
    assert after.status != "open" and "재개 결과" in capsys.readouterr().out
    # 답이 증거로 남았다 — 그래프가 첫 라운드부터 볼 수 있다.
    assert any("라인 7" in repr(store.get_evidence(record.id, r.id))
               for r in store.list_evidence(record.id))


def test_조사_질문은_접수로_새지_않는다(tmp_path, capsys, monkeypatch):
    # 반대 방향의 같은 사고 — 그래프가 파킹한 케이스를 접수로 보내면 스레드를
    # 잃고 새 조사가 처음부터 시작된다. 워커가 파킹할 때 종류를 붙이는지까지 본다.
    from src.application.open_case import open_case
    _chat_tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    monkeypatch.setattr("src.__main__.build_persistence",
                        lambda cfg: Persistence(store, repo, ledger, InMemoryEventStore(),
                                                InMemoryVerdictSnapshotStore()))
    monkeypatch.setattr("src.__main__.build_checkpointer", lambda cfg: InMemorySaver())
    record = open_case(repo=repo, store=store, symptom="s", gbm="mx", fct="gumi",
                       concern="system", requested_by=None,
                       clock=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc),
                       on_event=lambda e: None)
    repo.save(repo.get(record.id).model_copy(update={
        "status": "awaiting_human", "question": "계획 변경이 있었나?",
        "question_kind": "investigation"}))

    # 접수 LLM은 아무것도 안 준다 — 접수로 새면 이 대본이 소진되며 티가 난다.
    lead = ScriptedLLM(['{"target_locator": "mongo:twin_state", "missing": []}'])
    monkeypatch.setattr("src.patrol.daemon.build_chat_model",
                        lambda profile, *, base_url=None, api_key=None:
                        lead if profile == "l" else object())

    main(["case", "resume", record.id, "--answer", "없다",
          "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    # 접수가 돌았다면 target_locator가 채워졌을 것이다 — 그래프 재개는 안 채운다.
    assert repo.get(record.id).target_locator is None
