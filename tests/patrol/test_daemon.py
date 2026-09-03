"""데몬의 run_one→게이트→큐→워커 사슬을 스텁 위에서 결정론 검증한다."""
from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import AppConfig, ReportConfig
from src.config.schema_site import CheckConfig, SiteConfig
from src.domain.cases import InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters
from src.patrol.daemon import PatrolDaemon, SiteRuntime
from src.patrol.ledger import InMemoryLedger
from src.patrol.llm_judge import LlmBudget
from tests.application.test_graph_e2e import (FRAME_ONE_TASK, INTEGRATE_CONCLUDE,
                                              VERDICT_JSON, make_e2e_deps)
from tests.patrol.test_probes import TOPO

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
APP = AppConfig.model_validate({"llm": {"profiles": {"judge": "j", "subagent": "s", "lead": "l"}}})
CHECK = CheckConfig.model_validate({"judge": "rule", "schedule": {"interval": "5m"},
                                    "target": "rest:/oee",
                                    "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}})


def _daemon(store, repo, ledger, lead, tmp_path, *, clock=lambda: T, report_cfg=None, on_event=None):
    """report_cfg 기본값을 tmp_path 기반으로 만든다(테스트 위생) — 예전엔 기본
    ReportConfig()가 output_dir="output"(CWD 상대)을 써서, 보고서 발행을 다루지
    않는 테스트들도 그때마다 레포 루트에 output/*.md를 남겼다. tmp_path를 필수
    인자로 받아 매 테스트가 자기만의 임시 디렉터리에 쓰게 한다."""
    site = SiteConfig.model_validate({"target": {"rest": {"base_url": "http://x"}},
                                      "patrol": {"checks": {"api.oee": CHECK.model_dump()}}})
    adapters = build_adapters(site, TOPO, clock=clock,
                              stub_seeds=StubSeeds(rest_responses={"/oee": {"oee": 512}}))
    deps = make_e2e_deps(store, lead=lead)
    deps.adapters = adapters
    rt = SiteRuntime(gbm="mx", fct="gumi", cfg=site, adapters=adapters, deps=deps,
                     digests={"topology": "d-topo"})
    default_report_cfg = ReportConfig(output_dir=str(tmp_path / "output"))
    return PatrolDaemon(app=APP, sites=[rt], store=store, repo=repo, ledger=ledger,
                        checkpointer=InMemorySaver(), clock=clock, judge_llm=None,
                        budget=LlmBudget(5, clock=clock), owner="daemon-test", timezone="Asia/Seoul",
                        report_cfg=report_cfg if report_cfg is not None else default_report_cfg,
                        on_event=on_event)


async def test_run_one은_finding을_케이스로_열어_큐에_넣고_워커가_종결한다(tmp_path):
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON],
                     tmp_path=tmp_path)
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert ledger.last_run("mx", "gumi", "api.oee").status == "finding"
    assert daemon.queue.qsize() == 1 and repo.list_by_status("open")[0].id == "c-1"
    result = await daemon.worker.run_once(await daemon.queue.get())
    assert result == "closed" and store.get_verdict("c-1") is not None


async def test_같은_지문의_재발은_첨부만_하고_큐에_안_넣는다(tmp_path):
    from datetime import timedelta
    now = [T]
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path, clock=lambda: now[0])
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    now[0] = T + timedelta(minutes=5)                    # 다른 observed_at → 다른 Finding.id
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert daemon.queue.qsize() == 1 and len(repo.get("c-1").finding_ids) == 2


async def test_미등록_사이트_케이스는_워커가_닫지_않고_skipped를_남긴다(tmp_path):
    # 트리아지: registry에 없는(또는 disable된) 사이트의 케이스가 큐에 있어도
    # daemon._deps_for_site가 None을 돌려주면 워커는 F1로 오인해 닫지 않는다.
    from datetime import timezone

    from src.domain.cases import CaseRecord
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path)
    daemon.build()
    repo.save(CaseRecord(id="c-ghost", gbm="mx", fct="ghost", fingerprint="fp", symptom="s",
                         t0=datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc),
                         created_at=T, updated_at=T))
    result = await daemon.worker.run_once("c-ghost")
    assert result == "skipped"
    rec = repo.get("c-ghost")
    # deps 확인이 lease 저장보다 먼저라 아무것도 안 건드린 채 open으로 남는다.
    assert rec.status == "open" and rec.owner is None
    assert ledger.last_run("mx", "ghost", "worker:c-ghost").status == "skipped"


def test_on_missed는_skipped를_레저에_남기고_잡이_전부_등록된다(tmp_path):
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path)
    sched = daemon.build()
    ids = {j.id for j in sched.get_jobs()}
    assert {"mx/gumi/api.oee", "heartbeat", "self_check", "sweep"} <= ids
    daemon.on_missed("mx/gumi/api.oee")
    assert ledger.last_run("mx", "gumi", "api.oee").status == "skipped"


async def test_종결되면_보고서가_파일로_먼저_쓰이고_이벤트가_난다(tmp_path):
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seen = []
    daemon = _daemon(store, repo, ledger, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON],
                     tmp_path=tmp_path, report_cfg=ReportConfig(output_dir=str(tmp_path / "out")),
                     on_event=seen.append)
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert await daemon.worker.run_once(await daemon.queue.get()) == "closed"
    written = list((tmp_path / "out").glob("*.md"))
    assert len(written) == 1 and "## 2. 판정" in written[0].read_text(encoding="utf-8")
    assert [e.event for e in seen if e.event == "report_ready"]


async def test_실패_종결에서도_보고서가_먼저_쓰이고_이벤트가_난다(tmp_path, monkeypatch):
    # F1/F3 소진으로 워커가 _fail 경로(close_case discard_threads=True)로 케이스를
    # 닫아도 on_closed(daemon._publish_report)는 똑같이 불린다(worker.py의 계약:
    # _fail도 _finish의 두 종결 경로와 동일하게 case_status_event→_emit_closed를
    # 낸다) — 4b가 성공 종결만 커버했던 공백을 메운다.
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seen = []
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path,
                     report_cfg=ReportConfig(output_dir=str(tmp_path / "out")),
                     on_event=seen.append)
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    case_id = await daemon.queue.get()

    import src.application.worker as wk

    async def boom(*a, **k):
        raise RuntimeError("엔진 호출 실패")
    monkeypatch.setattr(wk, "investigate_case", boom)

    assert await daemon.worker.run_once(case_id) == "failed"
    assert repo.get(case_id).status == "closed"
    written = list((tmp_path / "out").glob("*.md"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert "판정 없음" in text
    assert [e.event for e in seen if e.event == "report_ready"]


async def test_게이트가_케이스를_열면_open_이벤트가_나간다(tmp_path):
    # Timeline의 첫 항목("이 케이스가 왜 열렸나")이 통째로 빠져 있었다.
    # 어휘는 이미 있고 호출부만 없던 문제다.
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seen = []
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path, on_event=seen.append)
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert repo.list_by_status("open")[0].id == "c-1"
    opened = [e for e in seen
              if e.event == "case_status_changed" and e.data["status"] == "open"]
    assert [e.case_id for e in opened] == ["c-1"]
