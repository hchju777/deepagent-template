"""읽기 엔드포인트 — 목록·상세·이벤트·보고서·점검.

읽기 필터(`sites_for`)가 계획 12 인계 ②다 — 프로덕션 소비자가 0이었다. 여기서
처음 붙는다. 안 붙이면 접수만 막히고 읽기는 열린 채로 남는다.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api.app import create_app
from src.api.assembly import ApiRuntime, ApiSite
from src.application.events import case_status_event
from src.config.schema_app import AccessPolicy, AppConfig, LlmConfig, LlmProfiles, ReportConfig
from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.events import InMemoryEventStore
from src.domain.patrol import CheckOutcome
from src.domain.store import InMemoryCaseStore
from src.knowledge.topology import Topology
from src.patrol.ledger import InMemoryLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({"services": {}, "derivations": {}})
ALICE = {"Authorization": "Bearer tok-a"}
BOB = {"Authorization": "Bearer tok-b"}


def _record(cid, gbm="mx", fct="gumi", **kw):
    base = dict(id=cid, gbm=gbm, fct=fct, fingerprint="fp", symptom="OEE 512%", t0=T,
                target_locator="rest:/oee", created_at=T, updated_at=T, status="closed",
                closed_reason="조사 완료")
    base.update(kw)
    return CaseRecord(**base)


def _runtime(tmp_path, *, access=None):
    app = AppConfig(llm=LlmConfig(profiles=LlmProfiles(judge="j", subagent="s", lead="l")),
                    access=access or AccessPolicy(),
                    report=ReportConfig(output_dir=str(tmp_path / "out")))
    sites = [ApiSite(gbm="mx", fct="gumi", topology=TOPO, lead_llm=SimpleNamespace(),
                     check_names=["api.oee_range"]),
             ApiSite(gbm="mx", fct="suwon", topology=TOPO, lead_llm=SimpleNamespace(),
                     check_names=[])]
    return ApiRuntime(app=app, sites=sites, repo=InMemoryCaseRepository(),
                      store=InMemoryCaseStore(), events=InMemoryEventStore(),
                      ledger=InMemoryLedger(), clock=lambda: T)


@pytest.fixture
def rt(tmp_path):
    rt = _runtime(tmp_path)
    rt.repo.save(_record("c-1"))
    rt.repo.save(_record("c-2", fct="suwon"))
    return rt


@pytest.fixture
def client(rt):
    return TestClient(create_app(rt))


# ── 목록 ──────────────────────────────────────────────────────────────────────
def test_목록은_사이트_스코프가_필수다(client):
    # 스펙 §4.4 "사이트 스코프 필터 필수" — 전체 조회는 없다.
    assert client.get("/cases").status_code == 400
    assert client.get("/cases?gbm=mx").status_code == 400


def test_목록은_스코프로_좁혀진다(client):
    r = client.get("/cases?gbm=mx&fct=gumi")
    assert r.status_code == 200
    assert [c["case_id"] for c in r.json()["cases"]] == ["c-1"]


def test_목록은_status로_더_좁힌다(client, rt):
    rt.repo.save(_record("c-3", status="open", closed_reason=None))
    r = client.get("/cases?gbm=mx&fct=gumi&status=open")
    assert [c["case_id"] for c in r.json()["cases"]] == ["c-3"]


def test_목록은_주체의_사이트로_좁혀진다(tmp_path):
    # 읽기 필터가 없으면 접수만 막히고 읽기는 열린다 — 계획 12 인계 ②.
    access = AccessPolicy(allow={"alice": ["mx/gumi"]},
                          subjects={"alice": SecretStr("tok-a"), "bob": SecretStr("tok-b")})
    rt = _runtime(tmp_path, access=access)
    rt.repo.save(_record("c-1")); rt.repo.save(_record("c-2", fct="suwon"))
    client = TestClient(create_app(rt))
    assert client.get("/cases?gbm=mx&fct=gumi", headers=ALICE).status_code == 200
    # alice가 suwon을 물으면 "없음"이다 — 존재 여부를 숨긴다.
    r = client.get("/cases?gbm=mx&fct=suwon", headers=ALICE)
    assert r.status_code == 200 and r.json()["cases"] == []
    assert client.get("/cases?gbm=mx&fct=gumi", headers=BOB).json()["cases"] == []


# ── 상세 ──────────────────────────────────────────────────────────────────────
def test_상세에_단계_체크리스트와_판정이_있다(client, rt):
    rt.store.put_verdict("c-1", Verdict(
        verdict_type="stale_data", confidence="high", narrative="plan-sync가 멈췄다",
        root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"])))
    r = client.get("/cases/c-1")
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == "c-1" and body["status"] == "closed"
    assert [s["stage"] for s in body["stages"]] == ["frame", "select", "execute",
                                                    "integrate", "conclude", "verify"]
    assert body["verdict"]["verdict_type"] == "stale_data"
    assert body["verdict"]["root_cause"]["component"] == "plan-sync"


def test_상세는_파킹_질문을_싣는다(client, rt):
    rt.repo.save(_record("c-1", status="awaiting_human", closed_reason=None,
                         question="계획 변경이 있었나?", question_kind="investigation"))
    body = client.get("/cases/c-1").json()
    assert body["question"] == "계획 변경이 있었나?" and body["question_kind"] == "investigation"


def test_미인가_상세는_없는_케이스와_같다(tmp_path):
    access = AccessPolicy(allow={"alice": ["mx/gumi"]},
                          subjects={"alice": SecretStr("tok-a"), "bob": SecretStr("tok-b")})
    rt = _runtime(tmp_path, access=access); rt.repo.save(_record("c-1"))
    client = TestClient(create_app(rt))
    real, ghost = client.get("/cases/c-1", headers=BOB), client.get("/cases/없음", headers=BOB)
    assert real.status_code == ghost.status_code == 404 and real.json() == ghost.json()


# ── 이벤트 ────────────────────────────────────────────────────────────────────
def _emit(rt, cid, *statuses):
    for s in statuses:
        rt.events.append(case_status_event(cid, s, clock=lambda: T))


def test_이벤트는_seq_순서이고_since로_잘라_읽는다(client, rt):
    _emit(rt, "c-1", "open", "investigating", "closed")
    r = client.get("/cases/c-1/events")
    assert [e["seq"] for e in r.json()["events"]] == [1, 2, 3]
    r = client.get("/cases/c-1/events?since=2")
    assert [e["seq"] for e in r.json()["events"]] == [3]
    assert r.json()["events"][0]["event"] == "case_status_changed"


def test_SSE는_since_이후를_흘리고_종결에서_끝난다(client, rt):
    # 저장된 로그의 폴링이다 — 프로세스 내 pub/sub이 아니다(스펙 §6).
    _emit(rt, "c-1", "open", "investigating", "closed")
    with client.stream("GET", "/cases/c-1/events?since=1",
                       headers={"Accept": "text/event-stream"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert "id: 2\n" in body and "id: 3\n" in body and "id: 1\n" not in body
    assert body.count("data:") == 2


# ── 보고서 ────────────────────────────────────────────────────────────────────
def test_보고서는_저장된_파일을_돌려준다(client, rt, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "c-1.html").write_text("<h1>저장된 보고서</h1>", encoding="utf-8")
    r = client.get("/cases/c-1/report")
    assert r.status_code == 200 and "저장된 보고서" in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_보고서가_없으면_즉석_렌더한다(client, rt):
    # 발행 실패·retention 스윕 뒤에도 보고서는 볼 수 있어야 한다 — case show와 같다.
    r = client.get("/cases/c-1/report?format=md")
    assert r.status_code == 200 and "# 케이스 c-1 보고서" in r.text
    assert r.headers["content-type"].startswith("text/markdown")


def test_보고서_포맷은_둘뿐이다(client):
    assert client.get("/cases/c-1/report?format=pdf").status_code == 422


# ── 점검 이력 ─────────────────────────────────────────────────────────────────
def test_점검_이력은_레저를_읽는다(client, rt):
    rt.ledger.record_run("mx", "gumi", "api.oee_range",
                         CheckOutcome(status="ok", observed_at=T))
    r = client.get("/checks?gbm=mx&fct=gumi")
    assert r.status_code == 200
    assert r.json()["checks"][0]["check"] == "api.oee_range"
    assert r.json()["checks"][0]["runs"][0]["status"] == "ok"


def test_점검_이력도_주체의_사이트로_좁혀진다(tmp_path):
    access = AccessPolicy(allow={"alice": ["mx/gumi"]},
                          subjects={"alice": SecretStr("tok-a"), "bob": SecretStr("tok-b")})
    rt = _runtime(tmp_path, access=access)
    client = TestClient(create_app(rt))
    assert client.get("/checks?gbm=mx&fct=gumi", headers=BOB).status_code == 404


def test_비활성_사이트의_케이스는_상세도_숨긴다(tmp_path):
    # 목록은 sites_for로 안 보이는데 상세·이벤트·보고서가 보이면 갈린다(리뷰 S5-6).
    rt = _runtime(tmp_path)
    rt.repo.save(_record("c-9", fct="off"))              # registry에 없는 사이트
    client = TestClient(create_app(rt))
    assert client.get("/cases/c-9").status_code == 404
    assert client.get("/cases/c-9/report").status_code == 404


def test_openapi_스키마는_공개하지_않는다(client):
    assert client.get("/openapi.json").status_code == 404


def test_since_상한(client):
    assert client.get("/cases/c-1/events?since=99999999999999999999").status_code == 422


def test_즉석_보고서에_Timeline이_실린다(client, rt):
    from src.domain.events import EngineEvent
    rt.events.append(EngineEvent(event="case_status_changed", case_id="c-1", at=T,
                                 data={"status": "open", "reason": "finding"}))
    r = client.get("/cases/c-1/report?format=md")
    assert "| 1 | " in r.text and "상태 → open (finding)" in r.text


def test_상세는_후보_목록과_Timeline을_응답_모델로_낸다(client, rt):
    # 계획 14 + 계획 13 인계 #6: 응답이 dict가 아니라 CaseDetail이다 — 모르는 키가 섞이면
    # 여기서 잡힌다. candidates는 rank 1 = root_cause(신뢰도는 판정의 것), 이후 alternates.
    from src.api.models import CaseDetail
    from src.domain.events import EngineEvent
    rt.store.put_verdict("c-1", Verdict(
        verdict_type="stale_data", confidence="high", narrative="n",
        root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
        alternates=[CauseLink(component="twin-state", evidence_ids=["ev-2"], confidence="low",
                              relation="갱신 지연")]))
    rt.events.append(EngineEvent(event="case_status_changed", case_id="c-1", at=T,
                                 data={"status": "open", "reason": "finding"}))
    body = client.get("/cases/c-1").json()
    detail = CaseDetail.model_validate(body)
    assert [(c.rank, c.component, c.confidence, c.evidence_ids, c.rationale)
            for c in detail.candidates] == [(1, "plan-sync", "high", ["ev-1"], None),
                                            (2, "twin-state", "low", ["ev-2"], "갱신 지연")]
    assert detail.timeline[0]["seq"] == 1 and detail.timeline[0]["summary"] == "상태 → open (finding)"
    assert detail.verdict["alternates"][0]["component"] == "twin-state"


def test_판정_없는_상세의_후보는_빈_목록이다(client, rt):
    body = client.get("/cases/c-1").json()
    assert body["candidates"] == [] and body["verdict"] is None and body["timeline"] == []
