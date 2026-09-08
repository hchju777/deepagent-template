"""계획 13 Task 6이 약속한 통합 테스트 — api 앱과 워커에 **같은** InMemory 저장소를 주입해
POST /cases → 워커 조사 → 파킹 → POST /answers → 워커 소비 → closed → GET /report를 한
프로세스에서 돈다. 요점은 "api는 기록만 하고 실행은 워커만 한다"는 경계가 실제 왕복에서
지켜지는가다(검증 리뷰가 작성한 것을 그대로 들였다)."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from src.api.app import create_app
from src.api.assembly import ApiRuntime, ApiSite
from src.application.worker import CaseQueue, InvestigationWorker
from src.config.schema_app import AppConfig
from src.domain.cases import InMemoryCaseRepository
from src.domain.events import InMemoryEventStore
from src.domain.report_model import build_report_model
from src.domain.label import InMemoryLabelStore
from src.domain.rollup import InMemoryDigestStore
from src.domain.store import InMemoryCaseStore
from src.knowledge.topology import Topology
from src.patrol.ledger import InMemoryLedger
from src.presentation.report_html import render_html
from tests.application.test_graph_e2e import (ASK_JSON, FRAME_ONE_TASK, INTEGRATE_CONCLUDE,
                                              _mongo_call, _report, make_e2e_deps)

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}}, "derivations": {}})
RESOLVED = '{"target_locator": "rest:/oee", "missing": []}'
VERDICT_EV3 = ('{"verdict_type": "stale_data", "confidence": "high", "narrative": "plan 동기화 지연.", '
               '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-3"]}}')


def _intake_llm(*replies):
    queue = list(replies)

    async def ainvoke(messages):
        return SimpleNamespace(content=queue.pop(0) if queue else RESOLVED)
    return SimpleNamespace(ainvoke=ainvoke)


def test_api와_워커가_같은_저장소로_전_구간을_완주한다(tmp_path):
    app = AppConfig.model_validate({
        "llm": {"gateway": {"base_url": "https://llm.test/v1", "pass_key": "p", "client_key": "c", "model_id": "m"}},
        "report": {"output_dir": str(tmp_path)}})
    repo, store, events, ledger = (InMemoryCaseRepository(), InMemoryCaseStore(),
                                   InMemoryEventStore(), InMemoryLedger())
    rt = ApiRuntime(app=app, sites=[ApiSite(gbm="mx", fct="gumi", topology=TOPO,
                                            lead_llm=_intake_llm(RESOLVED))],
                    repo=repo, store=store, events=events, ledger=ledger, labels=InMemoryLabelStore(), digests=InMemoryDigestStore(), clock=lambda: T)
    client = TestClient(create_app(rt))

    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON, INTEGRATE_CONCLUDE, VERDICT_EV3],
                         subagent=[_mongo_call("twin_state"), _report(["ev-3"])])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})

    async def on_closed(cid):
        record = repo.get(cid)
        model = build_report_model(record, verdict=store.get_verdict(cid),
                                   evidence=store.list_evidence(cid),
                                   case_file=store.get_case_file(cid), clock=lambda: T)
        (tmp_path / f"{cid}.html").write_text(render_html(model), encoding="utf-8")

    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 on_event=events.append, on_closed=on_closed)

    # 1. 개설 — 접수는 첫 응답에서 끝난다
    r = client.post("/cases", json={"symptom": "OEE가 이상하다", "gbm": "mx", "fct": "gumi"})
    assert r.status_code == 202, r.text
    cid = r.json()["case_id"]
    assert r.json()["intake"]["status"] == "done" and repo.get(cid).intake_done is True

    # 2. 워커의 requeue가 집어 조사 → 그래프가 파킹
    queue = CaseQueue()
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    assert asyncio.run(worker.consume(cid)) == "awaiting_human"
    parked = repo.get(cid)
    assert parked.question_kind == "investigation" and parked.question_seq == 1
    assert client.get(f"/cases/{cid}").json()["status"] == "awaiting_human"

    # 3. 답을 싣는다 — 기록만(202), 둘째 답은 pending(409)
    r = client.post(f"/cases/{cid}/answers", json={"answer": "계획 변경 없음", "key": "k-1"})
    assert r.status_code == 202 and r.json()["result"] == "accepted"
    r = client.post(f"/cases/{cid}/answers", json={"answer": "다른 답", "key": "k-2"})
    assert r.status_code == 409 and r.json()["result"] == "pending"
    assert repo.get(cid).status == "awaiting_human"           # api는 실행하지 않았다

    # 4. 답이 실린 파킹을 requeue가 집고, 워커가 소비해 종결
    queue = CaseQueue()
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    assert asyncio.run(worker.consume(cid)) == "closed"
    closed = repo.get(cid)
    assert closed.status == "closed" and closed.pending_answer is None and closed.answered_seq == 1
    assert store.get_verdict(cid).root_cause.component == "plan-sync"
    assert closed.answer_key == "k-1"                          # 멱등 근거는 소비 뒤에도 남는다

    # 5. 닫힌 뒤의 답은 not_waiting — 옛 답이 새 질문에 붙을 창이 없다
    r = client.post(f"/cases/{cid}/answers", json={"answer": "늦은 답", "key": "k-3"})
    assert r.status_code == 409 and r.json()["result"] == "not_waiting"

    # 6. 보고서와 이벤트 로그 — 워커가 쓴 파일을 api가 그대로 낸다
    r = client.get(f"/cases/{cid}/report")
    assert r.status_code == 200 and "판정" in r.text
    assert (tmp_path / f"{cid}.html").exists()
    statuses = [e["data"]["status"] for e in client.get(f"/cases/{cid}/events").json()["events"]
                if e["event"] == "case_status_changed"]
    assert statuses == ["open", "investigating", "awaiting_human", "investigating", "closed"]


def test_워커가_가져간_뒤_새_파킹_전의_창은_닫혀_있다():
    # take 직후(아직 awaiting_human, pending 없음)에 오는 둘째 답이 accepted되면 그래프가
    # 다음 질문으로 파킹했을 때 옛 질문의 답이 새 질문에 소비된다. seq 쌍이 막는다.
    app = AppConfig.model_validate({"llm": {"gateway": {"base_url": "https://llm.test/v1", "pass_key": "p", "client_key": "c", "model_id": "m"}}})
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    rt = ApiRuntime(app=app, sites=[ApiSite(gbm="mx", fct="gumi", topology=TOPO,
                                            lead_llm=_intake_llm(RESOLVED))],
                    repo=repo, store=store, events=InMemoryEventStore(), ledger=InMemoryLedger(), labels=InMemoryLabelStore(), digests=InMemoryDigestStore(),
                    clock=lambda: T)
    client = TestClient(create_app(rt))
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, ASK_JSON])
    deps.engine_cfg = deps.engine_cfg.model_copy(update={"autonomous_question_policy": "park"})
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=InMemoryLedger(), knowledge_digests_for_site=lambda g, f: {})
    cid = client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi"}).json()["case_id"]
    assert asyncio.run(worker.consume(cid)) == "awaiting_human"
    assert client.post(f"/cases/{cid}/answers",
                       json={"answer": "첫", "key": "k-1"}).json()["result"] == "accepted"
    assert repo.take_answer(cid, now=T) == "첫"                 # 워커가 가져갔다 — 아직 claim 전
    assert repo.get(cid).status == "awaiting_human" and repo.get(cid).pending_answer is None
    r = client.post(f"/cases/{cid}/answers", json={"answer": "둘째", "key": "k-2"})
    assert r.status_code == 409 and r.json()["result"] == "not_waiting"
