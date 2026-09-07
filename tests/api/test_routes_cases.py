"""쓰기 엔드포인트 — POST /cases · /intake-answers · /answers.

api는 실행자가 아니다: 여기 어느 테스트도 워커를 만들지 않는다. 답은 레코드에
실리는 것까지만 본다.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api.app import create_app
from src.api.assembly import ApiRuntime, ApiSite
from src.config.schema_app import AccessPolicy, AppConfig, LlmConfig, LlmProfiles
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.events import InMemoryEventStore
from src.domain.label import InMemoryLabelStore
from src.domain.rollup import InMemoryDigestStore
from src.domain.store import InMemoryCaseStore
from src.knowledge.topology import Topology
from src.patrol.ledger import InMemoryLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {}})
RESOLVED = '{"target_locator": "rest:/oee", "missing": []}'
ASKING = '{"target_locator": null, "missing": ["어느 라인인가?"]}'


def _llm(*replies):
    queue = list(replies)

    async def ainvoke(messages):
        return SimpleNamespace(content=queue.pop(0) if queue else RESOLVED)
    return SimpleNamespace(ainvoke=ainvoke)


def _runtime(*, sites=(("mx", "gumi"),), replies=(RESOLVED,), access=None):
    app = AppConfig(llm=LlmConfig(profiles=LlmProfiles(judge="j", subagent="s", lead="l")),
                    access=access or AccessPolicy())
    rt = ApiRuntime(app=app,
                    sites=[ApiSite(gbm=g, fct=f, topology=TOPO, lead_llm=_llm(*replies))
                           for g, f in sites],
                    repo=InMemoryCaseRepository(), store=InMemoryCaseStore(),
                    events=InMemoryEventStore(), ledger=InMemoryLedger(), labels=InMemoryLabelStore(), digests=InMemoryDigestStore(), clock=lambda: T)
    return rt


@pytest.fixture
def rt():
    return _runtime()


@pytest.fixture
def client(rt):
    return TestClient(create_app(rt))


def test_케이스_개설은_case_id를_즉시_돌려준다(client, rt):
    r = client.post("/cases", json={"symptom": "OEE가 이상하다", "gbm": "mx", "fct": "gumi"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["case_id"] and rt.repo.get(body["case_id"]).intake_done is True
    assert body["status"] == "open" and body.get("question") is None


def test_스코프_없이도_사이트가_하나면_열린다(client, rt):
    r = client.post("/cases", json={"symptom": "OEE가 이상하다"})
    assert r.status_code == 202 and rt.repo.get(r.json()["case_id"]).gbm == "mx"


def test_스코프_미확정은_케이스를_만들지_않는다():
    rt = _runtime(sites=(("mx", "gumi"), ("mx", "suwon")),
                  replies=('{"gbm": "없음", "fct": "없음"}',))
    r = TestClient(create_app(rt)).post("/cases", json={"symptom": "뭔가"})
    assert r.status_code == 400
    assert "mx/gumi" in json.dumps(r.json()["candidates"], ensure_ascii=False)
    assert rt.repo.list_open() == []


def test_접수_질문이_첫_응답에_실린다():
    rt = _runtime(replies=(ASKING,))
    r = TestClient(create_app(rt)).post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"})
    assert r.status_code == 202
    assert r.json()["status"] == "awaiting_human" and r.json()["question"] == "어느 라인인가?"


def test_접수_답은_턴_하나를_돈다():
    rt = _runtime(replies=(ASKING, RESOLVED))
    client = TestClient(create_app(rt))
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    r = client.post(f"/cases/{cid}/intake-answers", json={"answer": "라인 7"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["target_locator"] == "rest:/oee"
    assert rt.repo.get(cid).intake_done is True


def test_접수_질문이_아닌_케이스에_접수_답은_409다(client, rt):
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    rt.repo.save(rt.repo.get(cid).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "investigation",
        "question_seq": 1}))
    assert client.post(f"/cases/{cid}/intake-answers", json={"answer": "x"}).status_code == 409


def test_답은_기록되고_실행되지_않는다(client, rt):
    # api는 실행자가 아니다.
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    rt.repo.save(rt.repo.get(cid).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "investigation",
        "question_seq": 1}))
    r = client.post(f"/cases/{cid}/answers", json={"answer": "없다", "key": "k-1"})
    assert r.status_code == 202 and r.json()["result"] == "accepted"
    after = rt.repo.get(cid)
    assert after.pending_answer == "없다" and after.status == "awaiting_human"


def test_같은_키는_duplicate다(client, rt):
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    rt.repo.save(rt.repo.get(cid).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "investigation",
        "question_seq": 1}))
    client.post(f"/cases/{cid}/answers", json={"answer": "없다", "key": "k-1"})
    r = client.post(f"/cases/{cid}/answers", json={"answer": "다른", "key": "k-1"})
    assert r.status_code == 202 and r.json()["result"] == "duplicate"


def test_기다리지_않는_케이스에_답은_409다(client, rt):
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    r = client.post(f"/cases/{cid}/answers", json={"answer": "x", "key": "k"})
    assert r.status_code == 409 and r.json()["result"] == "not_waiting"


def test_미인가_주체에게는_존재_여부를_숨긴다():
    # 계획 12 인계 ③ — 404와 403을 구별하면 케이스 존재가 새어 나간다.
    access = AccessPolicy(allow={"alice": ["mx/gumi"]},
                          subjects={"alice": SecretStr("tok-a"), "bob": SecretStr("tok-b")})
    rt = _runtime(access=access)
    client = TestClient(create_app(rt))
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"},
                      headers={"Authorization": "Bearer tok-a"}).json()["case_id"]
    bob = {"Authorization": "Bearer tok-b"}
    real = client.post(f"/cases/{cid}/answers", json={"answer": "x", "key": "k"}, headers=bob)
    ghost = client.post("/cases/없음/answers", json={"answer": "x", "key": "k"}, headers=bob)
    assert real.status_code == ghost.status_code == 404
    assert real.json() == ghost.json()


def test_개설의_접근_거부는_403이다():
    # 스코프가 요청에 이미 드러나 있으므로 숨길 것이 없다.
    access = AccessPolicy(allow={"alice": ["mx/gumi"]}, subjects={"alice": SecretStr("tok-a")})
    client = TestClient(create_app(_runtime(access=access)))
    r = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"})   # 익명
    assert r.status_code == 403


def test_틀린_토큰은_401이다():
    access = AccessPolicy(subjects={"alice": SecretStr("tok-a")})
    client = TestClient(create_app(_runtime(access=access)))
    r = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_요청_주체가_레코드에_박제된다():
    access = AccessPolicy(subjects={"alice": SecretStr("tok-a")})
    rt = _runtime(access=access)
    r = TestClient(create_app(rt)).post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"},
                                        headers={"Authorization": "Bearer tok-a"})
    assert rt.repo.get(r.json()["case_id"]).requested_by == "alice"


def test_알_수_없는_키는_422다(client):
    # StrictModel — 클라이언트 오타가 조용히 무시되지 않는다.
    r = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi",
                                    "concen": "operation"})
    assert r.status_code == 422


def test_개설_이벤트가_저장된다(client, rt):
    # SSE가 읽을 로그다 — api가 낸 case_status_changed도 같은 저장소로 간다.
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    assert [e.event for e in rt.events.since(cid)] == ["case_status_changed"]


def test_접수가_포기하면_그_사실이_응답에_실린다():
    # 조용한 생략 금지 — LLM 호출이 실패해 대상 없이 열린 케이스를 "정상 개설"처럼
    # 보이면 클라이언트는 왜 조사가 대상 없이 도는지 모른다. 예시 트리(가짜 LLM
    # 호스트)를 실제로 쳐 보니 202 {"question": null}만 와서 구별이 안 됐다.
    rt = _runtime(replies=("파싱 불가",) * 5)
    r = TestClient(create_app(rt)).post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"})
    assert r.status_code == 202
    assert r.json()["intake"]["status"] == "error"
    assert r.json()["intake"]["problems"]
    assert rt.repo.get(r.json()["case_id"]).intake_done is True    # 그래도 워커는 집는다


def test_접수가_끝나면_intake_status가_done이다(client):
    r = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"})
    assert r.json()["intake"] == {"status": "done", "problems": []}


def test_접수가_끝난_케이스에_접수_답은_409다(client, rt):
    # intake_done 문이 최초 창에만 유효했다 — 끝난 접수에 또 턴을 돌면 워커가 집을 수
    # 있는 케이스와 _save의 TOCTOU 창이 다시 열린다(리뷰 S1-P1b).
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    assert rt.repo.get(cid).intake_done is True
    assert client.post(f"/cases/{cid}/intake-answers", json={"answer": "x"}).status_code == 409


def test_접수_파킹은_이벤트를_낸다():
    # 문서가 SSE를 "진행 스트림"이라 부른다 — 접수 파킹이 안 실리면 거짓이다(리뷰 S9).
    rt = _runtime(replies=(ASKING, RESOLVED))
    client = TestClient(create_app(rt))
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    client.post(f"/cases/{cid}/intake-answers", json={"answer": "라인 7"})
    statuses = [e.data.get("status") for e in rt.events.since(cid)]
    assert statuses == ["open", "awaiting_human", "open"]


def test_실행자가_잡고_있는_동안의_답은_409_busy다(client, rt):
    # 실행자(워커·case resume)가 lease를 쥔 동안 실린 답은 통째 덤프에 지워지거나 다음
    # 질문에 소비된다 — 잠시 뒤 다시 보내라는 뜻의 busy. pending(덮지 않는다)과 다르다.
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    rt.repo.save(rt.repo.get(cid).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "investigation",
        "question_seq": 1, "owner": "w-1", "lease_until": T + timedelta(seconds=60)}))
    r = client.post(f"/cases/{cid}/answers", json={"answer": "x", "key": "k"})
    assert r.status_code == 409 and r.json()["result"] == "busy"


def test_저장소_장애의_답은_503이다(client, rt):
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]

    def boom(*a, **k):
        raise RuntimeError("mongo down")
    rt.repo.attach_answer = boom
    r = client.post(f"/cases/{cid}/answers", json={"answer": "x", "key": "k"})
    assert r.status_code == 503 and r.json()["result"] == "error"


def test_라벨은_기록되고_없는_케이스는_404다(client, rt):
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    r = client.post(f"/cases/{cid}/label", json={"agreement": "wrong", "resolution": "fixed",
                                                 "actual_component": "plan-sync", "saw_report": True})
    assert r.status_code == 202 and r.json()["result"] == "recorded"
    row = rt.labels.list_for(cid)[0]
    assert row.agreement == "wrong" and row.actual_root_cause_component == "plan-sync"
    assert client.post("/cases/없음/label", json={"agreement": "correct"}).status_code == 404
    assert client.post(f"/cases/{cid}/label", json={"agreement": "아마도"}).status_code == 422


def test_미인가_주체는_라벨도_쓸_수_없다():
    # 리뷰 돌연변이 #17: visible_record를 지워도 초록이었다.
    from src.domain.label import InMemoryLabelStore
    access = AccessPolicy(allow={"alice": ["mx/gumi"]},
                          subjects={"alice": SecretStr("tok-a"), "bob": SecretStr("tok-b")})
    rt = _runtime(access=access)
    client = TestClient(create_app(rt))
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"},
                      headers={"authorization": "Bearer tok-a"}).json()["case_id"]
    r = client.post(f"/cases/{cid}/label", json={"agreement": "correct"},
                    headers={"authorization": "Bearer tok-b"})
    assert r.status_code == 404 and rt.labels.list_for(cid) == []


def test_라벨_저장소_장애는_503이다(client, rt):
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]

    def boom(label):
        raise RuntimeError("mongo down")
    rt.labels.append = boom
    r = client.post(f"/cases/{cid}/label", json={"agreement": "correct"})
    assert r.status_code == 503 and r.json()["result"] == "error"


def test_지나간_질문에_대한_답은_409_stale_question이다(client, rt):
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    rt.repo.save(rt.repo.get(cid).model_copy(update={
        "status": "awaiting_human", "question": "Q2", "question_kind": "investigation",
        "question_seq": 2}))
    r = client.post(f"/cases/{cid}/answers",
                    json={"answer": "Q1의 답", "key": "k-1", "question_seq": 1})
    assert r.status_code == 409 and r.json()["result"] == "stale_question"
    assert rt.repo.get(cid).pending_answer is None
    ok = client.post(f"/cases/{cid}/answers",
                     json={"answer": "Q2의 답", "key": "k-1", "question_seq": 2})
    assert ok.status_code == 202 and ok.json()["result"] == "accepted"


def test_번호를_안_보내면_예전처럼_받는다(client, rt):
    # 하위 호환 — 기존 클라이언트를 깨지 않는다.
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    rt.repo.save(rt.repo.get(cid).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "investigation",
        "question_seq": 2}))
    assert client.post(f"/cases/{cid}/answers",
                       json={"answer": "a", "key": "k"}).json()["result"] == "accepted"


def test_접수_답변도_질문_번호를_대조한다(client, rt):
    # 검증 리뷰 R-1: 읽기 표면은 접수 질문의 번호를 내주는데 쓰기 표면이 거부했다 —
    # 웹 UI가 접수 되묻기에 답하는 순간 그대로 노출된다.
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    rt.repo.save(rt.repo.get(cid).model_copy(update={
        "status": "awaiting_human", "question": "Q2", "question_kind": "intake",
        "question_seq": 2, "intake_done": False}))
    r = client.post(f"/cases/{cid}/intake-answers",
                    json={"answer": "Q1의 답", "question_seq": 1})
    assert r.status_code == 409 and r.json()["status"] == "stale_question"
    assert rt.repo.get(cid).intake_done is False
    ok = client.post(f"/cases/{cid}/intake-answers", json={"answer": "라인 7", "question_seq": 2})
    assert ok.status_code == 200
