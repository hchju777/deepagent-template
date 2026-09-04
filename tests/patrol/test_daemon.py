"""데몬의 run_one→게이트→큐→워커 사슬을 스텁 위에서 결정론 검증한다."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import AppConfig, ReportConfig
from src.config.schema_site import CheckConfig, SiteConfig
from src.domain.cases import CaseRecord, InMemoryCaseRepository
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
    않는 테스트들도 그때마다 레포 루트에 output/*를 남겼다. tmp_path를 필수
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
    written = list((tmp_path / "out").glob("*.html"))
    assert len(written) == 1 and "<h2>2. 판정</h2>" in written[0].read_text(encoding="utf-8")
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
    written = list((tmp_path / "out").glob("*.html"))
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


async def test_주기_재큐는_나중에_생긴_open_케이스를_집어온다(tmp_path):
    # 기동 후에 다른 프로세스(api·다른 워커)가 연 케이스를 데몬이 보려면
    # 재스캔이 주기적이어야 한다. build()의 1회 스캔만으로는 영원히 못 본다.
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path)
    daemon.build()
    assert daemon.queue.qsize() == 0

    repo.save(CaseRecord(id="c-late", gbm="mx", fct="gumi", fingerprint="fp-late",
                         symptom="다른 프로세스가 연 케이스", t0=T,
                         created_at=T, updated_at=T))
    await daemon.requeue_job()
    assert daemon.queue.qsize() == 1


async def test_데몬이_사이트_시간대를_해석기까지_넘긴다(tmp_path):
    # 이 배선이 없으면 clock 해석기가 UTC로 떨어져 아침 cron이 매일 전날 날짜를
    # 보낸다. 함수 인자만 검증하는 테스트는 이 홉을 못 잡는다 — 실제로 한 번
    # 그렇게 초록이었다.
    from datetime import timedelta

    from src.config.schema_site import RestEntry
    from src.domain.patrol import scratch_case_id
    from src.infrastructure.stubs import StubRest
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    kst_morning = T.replace(hour=23, minute=30) - timedelta(days=1)   # 다음날 08:30 KST
    daemon = _daemon(store, repo, ledger, lead=[], tmp_path=tmp_path,
                     clock=lambda: kst_morning)
    entries = {"e": RestEntry(method="POST", path="/x", body_schema={"date": "str"})}
    daemon.sites[0].adapters.rest = StubRest({"POST /x": {"ok": 1}}, set(), entries,
                                             clock=lambda: kst_morning)
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
        "params": {"rule": "exists", "field": "body.ok"},
        "resolve": {"date": {"from": "clock", "expr": "today"}}})
    daemon.build()
    await daemon.run_one("mx", "gumi", "tz.check", check)

    cid = scratch_case_id("mx", "gumi", "tz.check")
    rec = store.list_evidence(cid)[-1]
    body = store.get_evidence(cid, rec.id)
    expected = (kst_morning.astimezone(ZoneInfo(daemon.timezone))).date().isoformat()
    assert body["request"]["params"] == {"date": expected}
    assert expected != kst_morning.date().isoformat(), "UTC와 같으면 테스트가 무의미하다"


def test_스키마에_기본값_필드를_더해도_규칙_digest가_안_바뀐다():
    # 계획 9가 CheckConfig에 resolve를 더했을 때 손대지 않은 전 사이트의 rules
    # digest가 바뀌었다. 지금은 아무도 비교하지 않아 무해하지만, 드리프트 판정이
    # 이 값을 쓰는 순간 "설정을 안 바꿨는데 드리프트"가 뜬다 — 신호가 태어나자마자
    # 소음이 된다. 그래서 미래의 필드 추가를 여기서 흉내 낸다.
    from src.patrol.daemon import rules_digest
    class CheckConfigPlus(CheckConfig):
        훗날_생길_필드: str = "기본값"

    raw = {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/x",
           "params": {"rule": "exists", "field": "body"}}
    assert (rules_digest({"c": CheckConfig.model_validate(raw)})
            == rules_digest({"c": CheckConfigPlus.model_validate(raw)}))


def test_규칙_digest는_실제_변경에는_반응한다():
    # 기본값을 빼는 것이 "아무것도 구별 못 한다"가 되면 안 된다.
    from src.patrol.daemon import rules_digest
    raw = {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/x",
           "params": {"rule": "exists", "field": "body"}}
    one = CheckConfig.model_validate(raw)
    two = CheckConfig.model_validate({**raw, "resolve": {"d": {"from": "clock",
                                                               "expr": "today"}}})
    assert rules_digest({"c": one}) != rules_digest({"c": two})


def test_명세_digest가_사이트_조립에_실린다(tmp_path):
    # as_of의 네 번째 축. 없으면 "그때 그 API가 어떤 모양이었나"를 사후에 알 수 없다.
    import json
    from src.patrol.daemon import assemble_sites
    (tmp_path / "config" / "gbm").mkdir(parents=True)
    (tmp_path / "knowledge" / "topology" / "gbm").mkdir(parents=True)
    (tmp_path / "knowledge" / "target_api" / "gbm").mkdir(parents=True)
    (tmp_path / "config" / "app.json").write_text(
        json.dumps({"llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}}}),
        encoding="utf-8")
    (tmp_path / "config" / "registry.json").write_text(
        json.dumps({"sites": [{"gbm": "gbm", "fct": "gumi"}]}), encoding="utf-8")
    (tmp_path / "config" / "gbm" / "gbm.json").write_text(
        json.dumps({"target": {"adapters": "stub"},
                    "knowledge": {"root": str(tmp_path / "knowledge")}}), encoding="utf-8")
    (tmp_path / "knowledge" / "topology" / "common.yaml").write_text(
        "services: {}\nderivations: {}\n", encoding="utf-8")
    (tmp_path / "knowledge" / "target_api" / "gbm" / "gumi.json").write_text(
        json.dumps({"paths": {}}), encoding="utf-8")

    _app, sites = assemble_sites(tmp_path / "config", tmp_path, {"LLM_API_KEY": "k"},
                                 clock=lambda: T, llm_factory=lambda name: object())
    assert len(sites[0].digests["target_api"]) == 64      # 실제 digest가 실렸다


def test_명세가_없으면_digest는_absent다(tmp_path):
    # deployment가 쓰는 관례를 그대로 쓴다 — 빈 문자열이면 "없다"와 "계산 실패"가 같아진다.
    import json
    from src.patrol.daemon import assemble_sites
    (tmp_path / "config" / "gbm").mkdir(parents=True)
    (tmp_path / "knowledge" / "topology").mkdir(parents=True)
    (tmp_path / "config" / "app.json").write_text(
        json.dumps({"llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}}}),
        encoding="utf-8")
    (tmp_path / "config" / "registry.json").write_text(
        json.dumps({"sites": [{"gbm": "gbm", "fct": "gumi"}]}), encoding="utf-8")
    (tmp_path / "config" / "gbm" / "gbm.json").write_text(
        json.dumps({"target": {"adapters": "stub"},
                    "knowledge": {"root": str(tmp_path / "knowledge")}}), encoding="utf-8")
    (tmp_path / "knowledge" / "topology" / "common.yaml").write_text(
        "services: {}\nderivations: {}\n", encoding="utf-8")

    _app, sites = assemble_sites(tmp_path / "config", tmp_path, {"LLM_API_KEY": "k"},
                                 clock=lambda: T, llm_factory=lambda name: object())
    assert sites[0].digests["target_api"] == "absent"
