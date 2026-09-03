"""벤치 — 스펙 부록 A의 두 간판 시나리오를 회귀 모드로 재현한다 (스펙 §5.5-4).

회귀 모드(여기, CI): 스텁 LLM(ScriptedLLM/ToolFake) + 스텁 어댑터로 각본을
결정론 검증한다. 채점 술어는 Verdict의 구조화 필드(root_cause.component,
verdict_type)만 — md 텍스트 매칭은 하지 않는다(템플릿을 고칠 때마다 깨지는
벤치는 즉시 썩는다).

평가 모드(실 LLM — 조사 품질을 사람이 판단)는 CI에서 돌리지 않는다. 돌리려면
이 시나리오의 조립부(사이트·시드·finding)를 그대로 두고 lead_llm/subagent_llm/
judge llm만 build_chat_model로 바꿔 별도 스크립트에서 수동 실행한다.

두 시나리오 모두 run_check → admit_finding → worker.run_once(→resume_once) →
실제 발행 배선(worker.on_closed=daemon._publish_report)까지 전 구간을 돈다
(M11) — 예전엔 이 파일의 _publish 헬퍼가 render_report/write_report를 직접
다시 불러 발행을 흉내냈을 뿐이라, "전 구간을 돈다"는 말이 실제로는 발행
훅(on_closed)을 한 번도 태우지 않았다. _publish_daemon이 build()/run()은
부르지 않는 최소 PatrolDaemon을 조립해 그 _publish_report만 워커에 붙인다 —
데몬·chat·case resume과 같은 발행 경로다. 보고서는 ReportConfig() 기본값
(output_dir="output", gitignore됨) 아래 시나리오별 하위 디렉터리에 남는다 —
두 시나리오 모두 자기만의 InMemoryCaseRepository에서 첫 케이스가 "c-1"이
되므로(카운터가 인스턴스별 1부터 시작), 하위 디렉터리를 안 나누면 나중에
실행되는 시나리오가 먼저 실행된 시나리오의 output/c-1.*를 덮어써 "각각
보고서 파일이 남는다"는 완료 기준을 어긴다. 완료 기준이 "보고서 파일이
5절을 갖춘 채 output/에 남는다"를 요구한다.

증거 id 예측: run_subagent은 서브에이전트가 스스로 적은 evidence_ids를
신뢰하지 않고 도구가 실제로 store.put_evidence로 만든 id로 통째로 교체한다
(subagents.py 모듈 docstring) — 그래서 아래 스크립트의 `_report(...)` 인자는
가독성용일 뿐 실제 인용을 결정하지 않는다. 실제로 맞아야 하는 건 conclude가
내는 verdict의 root_cause.evidence_ids뿐이다: InMemoryCaseStore의 증거 id는
케이스당 1부터 순번이므로(ev-1, ev-2, ...), admit_finding이 복사하는 finding
스냅샷이 항상 ev-1이고 그 뒤 라운드마다 도구 호출 하나당 하나씩 늘어난다 —
아래 각 시나리오 docstring에 그 순번을 그대로 적어둔다.
"""
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.application.deps import EngineDeps
from src.application.worker import CaseQueue, InvestigationWorker
from src.config.schema_app import AppConfig, EngineConfig, LlmConfig, LlmProfiles, ReportConfig
from src.config.schema_site import CheckConfig, SiteConfig
from src.domain.cases import InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import StubSeeds, build_adapters
from src.infrastructure.llm import ScriptedLLM
from src.knowledge.topology import Topology
from src.patrol.daemon import PatrolDaemon
from src.patrol.gate import admit_finding
from src.patrol.ledger import InMemoryLedger
from src.patrol.llm_judge import LlmBudget
from src.patrol.runner import run_check
from src.presentation.mail import NullSender
from tests.application.test_subagents import ToolFake

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
# 절 제목은 포맷마다 표기가 다르다 — 벤치가 확인하는 것은 "5절이 다 있는가"이지
# 마크다운 문법이 아니다. report.format 기본값이 바뀌어도 이 단정이 따라가야 한다.
_SECTION_TITLES = ("1. 요약", "2. 판정", "3. 조치 권고", "4. 증거", "5. 조사 경위")


def _report_headings(fmt: str) -> tuple[str, ...]:
    return tuple(f"<h2>{s}</h2>" if fmt == "html" else f"## {s}" for s in _SECTION_TITLES)


def _mongo_call(collection: str, call_id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{
        "name": "mongo_find", "id": call_id,
        "args": {"collection": collection, "filter_json": "{}"}}])


def _redis_call(key: str, call_id: str = "c2") -> AIMessage:
    return AIMessage(content="", tool_calls=[{
        "name": "redis_get", "id": call_id, "args": {"key": key}}])


def _report(evidence_ids: list[str], summary: str = "확인") -> AIMessage:
    # evidence_ids는 가독성용 echo다 — run_subagent이 도구가 만든 실제 id로
    # 덮어쓴다(subagents.py). 실제 인용은 verdict의 root_cause.evidence_ids만 본다.
    ids = ", ".join(f'"{e}"' for e in evidence_ids)
    return AIMessage(content=f'{{"status": "ok", "summary": "{summary}", "evidence_ids": [{ids}]}}')


def _publish_daemon(repo, store, ledger, clock, *, output_dir: str, owner: str) -> PatrolDaemon:
    """실제 발행 경로(render_report → write_report → 메일)를 쓰는 최소 PatrolDaemon(M11).

    build()/run()은 절대 부르지 않는다(스케줄러를 기동하지 않는다) — 이
    인스턴스의 _publish_report만 워커의 on_closed로 재사용해, 벤치도 데몬·
    chat·case resume과 똑같은 발행 배선을 타게 한다. sites=[]는 안전하다 —
    _publish_report는 repo/store/report_cfg만 읽고 sites는 쓰지 않는다.
    """
    app = AppConfig(llm=LlmConfig(profiles=LlmProfiles(judge="j", subagent="s", lead="l")))
    return PatrolDaemon(
        app=app, sites=[], store=store, repo=repo, ledger=ledger, checkpointer=InMemorySaver(),
        clock=clock, judge_llm=None, budget=LlmBudget(1000, clock=clock), owner=owner,
        timezone="Asia/Seoul", report_cfg=ReportConfig(output_dir=output_dir),
        mail_sender=NullSender())


# ── A.1: 룰 탐지 — "OEE 512%"(있을 수 없는 값) ──────────────────────────────
# 부록 A.1: rest:/oee 룰(0~100) 위반 → finding → 케이스 개설 → mongo twin_state
# (실측 5.12 — 정상 범위지만 OEE=512%는 잘못된 분모 탓) → redis plan:6:today
# (line 7이 필요로 하는 plan:7:today는 시드에 없다 — "키 없음"과 동형인 미니
# 시드) → plan-sync가 옛 값을 폴백해 분모가 축소됐다는 결론.
#
# 증거 순번(이 케이스 store 기준): ev-1=admit_finding이 복사한 rest:/oee 스냅샷,
# ev-2=라운드1 mongo_find(twin_state), ev-3=라운드2 redis_get(plan:6:today).
_CHECK_A1 = CheckConfig.model_validate({
    "judge": "rule", "schedule": {"interval": "10m"}, "target": "rest:/oee",
    "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}})
_SITE_A1 = SiteConfig.model_validate({"target": {
    "rest": {"base_url": "http://x"}, "mongo": {"url": "mongodb://x:27017"},
    "redis": {"url": "redis://x:6379"}}})
_TOPO_A1 = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {"rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                                  "via": "twin-api"}}})

_FRAME_A1 = ('{"hypotheses": [{"id": "h-1", "statement": "OEE 계산 이상"}, '
            '{"id": "h-2", "statement": "plan 동기화 지연"}], '
            '"tasks": [{"id": "t-1", "goal": "twin_state 조회", "role": "data_prober"}, '
            '{"id": "t-2", "goal": "plan:6:today 조회", "role": "data_prober"}]}')
_INTEGRATE_CONTINUE = '{"decision": "continue"}'
_INTEGRATE_CONCLUDE = '{"decision": "conclude"}'
_VERDICT_A1 = ('{"verdict_type": "stale_data", "confidence": "high", '
              '"narrative": "plan-sync가 line 7의 plan:7:today 키를 못 써 aggregator가 '
              '옛 계획값을 폴백으로 썼다 — 분모가 축소돼 OEE가 폭등했다.", '
              '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-2", "ev-3"]}, '
              '"recommendations": ["plan:7:today 키 재생성", "plan-sync 실패 로그 확인(스코프 밖)"]}')


async def test_A1_OEE_512퍼센트는_plan_sync_stale_data로_귀결되고_보고서를_남긴다():
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seeds = StubSeeds(
        rest_responses={"/oee": {"oee": 512}},
        mongo_collections={"twin_state": [{"line": 7, "oee": 5.12, "planned_time": 75}]},
        redis_data={"plan:6:today": "480"})   # plan:7:today는 시드에 없다("키 없음")
    adapters = build_adapters(_SITE_A1, _TOPO_A1, clock=lambda: T, stub_seeds=seeds)

    outcome = await run_check("mx", "gumi", "api.oee_range", _CHECK_A1, adapters=adapters,
                              store=store, clock=lambda: T)
    assert outcome.status == "finding"

    admit = admit_finding(outcome.finding, repo=repo, store=store, clock=lambda: T)
    assert admit.action == "opened"
    case_id = admit.case_id

    deps = EngineDeps(
        lead_llm=ScriptedLLM([_FRAME_A1, _INTEGRATE_CONTINUE, _INTEGRATE_CONCLUDE, _VERDICT_A1]),
        subagent_llm=ToolFake(messages=iter([
            _mongo_call("twin_state"), _report(["ev-2"]),
            _redis_call("plan:6:today"), _report(["ev-3"])])),
        adapters=adapters, store=store, topology=_TOPO_A1, engine_cfg=EngineConfig(parallel_width=1))
    daemon = _publish_daemon(repo, store, ledger, lambda: T,
                             output_dir=str(Path(ReportConfig().output_dir) / "bench-a1"),
                             owner="bench-a1-publish")
    worker = InvestigationWorker(
        CaseQueue(), repo=repo, store=store, deps_for_site=lambda g, f: deps,
        checkpointer=InMemorySaver(), clock=lambda: T, owner="bench-a1", max_concurrent=1,
        lease_ttl_s=900, ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
        on_closed=daemon._publish_report)

    result = await worker.run_once(case_id, interaction_policy="autonomous")
    assert result == "closed"

    verdict = store.get_verdict(case_id)
    assert verdict.root_cause.component == "plan-sync"
    assert verdict.verdict_type == "stale_data"
    assert verdict.confidence == "high"                # M12: verify 가드레일을 명시 술어로

    fmt = daemon.report_cfg.format
    path = Path(daemon.report_cfg.output_dir) / f"{case_id}.{fmt}"
    text = path.read_text(encoding="utf-8")
    for heading in _report_headings(fmt):
        assert heading in text


# ── A.2: LLM 탐지 — "멈춘 라인이 생산을 한다"(모든 값이 각자 정상) ────────────
# 부록 A.2: twin.consistency(judge=llm)가 라인 12의 (상태 STOP, 생산량 증가)
# 모순을 잡는다 → 조사는 equip-sync 파티션 12가 오프셋 정지해 상태 캐시가
# 멎었다는 결론. park→resume 구간은 테스트 둘로 나눈다(M12 — 예전엔 A.2가
# resume_case를 항상 강제 실패시켜 "정상 재개"를 한 번도 돌지 않았다):
#   1) 정상 재개(아래) — park → answer → resume_once가 그대로 성공해 conclude까지
#      이어지는, F3 없는 해피패스.
#   2) F3 강제(그 아래) — resume_case 자체가 실패하는 상황을 강제해(체크포인트
#      역직렬화 실패 등을 흉내) worker의 F3(재개 실패 복구, I4) 경로 — 스레드
#      폐기 → 사람의 답을 evidence(human:answer)로 박제 → 새 스레드로
#      investigate_case 재시작 — 를 실제로 통과시킨다. 계획 4b가 이 경로를
#      손대지 않고 파킹해뒀던 지점이다(worker.py 모듈 docstring 참고).
#
# 증거 순번(정상 재개): ev-1=admit_finding이 복사한 mongo:twin_state 스냅샷,
# ev-2=라운드1 mongo_find — conclude가 인용한다(라운드2는 새 태스크 없이
# 곧장 conclude라 추가 증거가 없다).
# 증거 순번(F3 강제): ev-1=admit_finding이 복사한 스냅샷, ev-2=1차 시도
# 라운드1 mongo_find, ev-3=F3 재시작이 박제한 human:answer, ev-4=재시작된
# 조사(2차 시도) 라운드1 mongo_find — conclude가 인용한다.
_CHECK_A2 = CheckConfig.model_validate({
    "judge": "llm", "schedule": {"interval": "12h"}, "target": "mongo:twin_state", "params": {}})
_SITE_A2 = SiteConfig.model_validate({"target": {"mongo": {"url": "mongodb://x:27017"}}})
_TOPO_A2 = Topology.model_validate({
    "services": {"equip-sync": {"reads": [{"kind": "kafka", "topic": "edge.raw.12"}],
                                "writes": [{"kind": "redis", "key": "equip:12:status"}]}}})

_JUDGE_A2 = ('{"status": "finding", "summary": '
            '"line 12: STOP인데 같은 구간 생산량이 꾸준히 증가 — 상태 캐시가 거짓", '
            '"evidence_ids": ["ev-1"]}')
_FRAME_A2_1 = ('{"hypotheses": [{"id": "h-1", "statement": "라인 12 설비 상태 캐시가 멎었다"}], '
              '"tasks": [{"id": "t-1", "goal": "edge.raw.12 최근 이벤트 확인", '
              '"role": "data_prober"}]}')
_ASK_A2 = '{"decision": "ask", "question": "라인 12가 계획된 점검 중인가요?"}'
_FRAME_A2_2 = ('{"hypotheses": [{"id": "h-2", "statement": "equip-sync 파티션 12 소비 정지"}], '
              '"tasks": [{"id": "t-2", "goal": "equip-sync 파티션별 오프셋 확인", '
              '"role": "data_prober"}]}')
_INTEGRATE_CONCLUDE_A2 = '{"decision": "conclude"}'
_VERDICT_A2_NORMAL = ('{"verdict_type": "stale_data", "confidence": "high", '
                      '"narrative": "equip-sync가 파티션 12 소비를 멈춰 설비 상태 캐시 갱신이 '
                      '중단됐다 — since와 오프셋 정지 시각이 일치한다.", '
                      '"root_cause": {"component": "equip-sync", "evidence_ids": ["ev-2"]}, '
                      '"recommendations": ["파티션 재기동/재할당"]}')
_VERDICT_A2 = ('{"verdict_type": "stale_data", "confidence": "high", '
              '"narrative": "equip-sync가 파티션 12 소비를 멈춰 설비 상태 캐시 갱신이 '
              '중단됐다 — since와 오프셋 정지 시각이 일치한다.", '
              '"root_cause": {"component": "equip-sync", "evidence_ids": ["ev-4"]}, '
              '"recommendations": ["파티션 재기동/재할당", "kafka.lag 룰에 파티션별 정체 검사 추가"]}')


async def test_A2_멈춘_라인은_정상_재개로도_equip_sync_stale_data로_귀결된다():
    # M12: 정상 park→answer→resume 1회 — resume_case가 강제 실패 없이 그대로
    # 성공하는 해피패스를 실제로 돈다(F3 경로는 아래 테스트가 따로 유도한다).
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seeds = StubSeeds(mongo_collections={
        "twin_state": [{"line": 12, "status": "STOP", "since": "04:10", "qty_per_hour": 60}]})
    adapters = build_adapters(_SITE_A2, _TOPO_A2, clock=lambda: T, stub_seeds=seeds)
    judge_llm = ScriptedLLM([_JUDGE_A2])

    outcome = await run_check("mx", "gumi", "twin.consistency", _CHECK_A2, adapters=adapters,
                              store=store, clock=lambda: T, llm=judge_llm,
                              budget=LlmBudget(1000, clock=lambda: T))
    assert outcome.status == "finding"

    admit = admit_finding(outcome.finding, repo=repo, store=store, clock=lambda: T)
    assert admit.action == "opened"
    case_id = admit.case_id

    # 라운드1(t-1)이 ev-2를 남기고 ask → interrupt. resume하면 integrate가 새
    # 태스크 없이 곧장 conclude를 낸다 — root_cause는 실제로 존재하는 ev-2를
    # 인용한다(ev-1은 admit_finding이 복사한 finding 스냅샷).
    deps = EngineDeps(
        lead_llm=ScriptedLLM([_FRAME_A2_1, _ASK_A2, _INTEGRATE_CONCLUDE_A2, _VERDICT_A2_NORMAL]),
        subagent_llm=ToolFake(messages=iter([_mongo_call("twin_state"), _report(["ev-2"])])),
        adapters=adapters, store=store, topology=_TOPO_A2, engine_cfg=EngineConfig(parallel_width=1))
    daemon = _publish_daemon(repo, store, ledger, lambda: T,
                             output_dir=str(Path(ReportConfig().output_dir) / "bench-a2-normal"),
                             owner="bench-a2-normal-publish")
    worker = InvestigationWorker(
        CaseQueue(), repo=repo, store=store, deps_for_site=lambda g, f: deps,
        checkpointer=InMemorySaver(), clock=lambda: T, owner="bench-a2-normal", max_concurrent=1,
        lease_ttl_s=900, ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
        on_closed=daemon._publish_report)

    parked = await worker.run_once(case_id, interaction_policy="interactive")
    assert parked == "awaiting_human"
    question = repo.get(case_id).question
    assert question == "라인 12가 계획된 점검 중인가요?"

    resumed = await worker.resume_once(case_id, "계획된 점검 아님")
    assert resumed == "closed"

    restart_events = [o for o in ledger.runs("mx", "gumi", f"worker:{case_id}")
                      if o.error and "F3 재시작" in o.error]
    assert restart_events == []                          # 강제 실패 없이 정상 재개했다

    verdict = store.get_verdict(case_id)
    assert verdict.root_cause.component == "equip-sync"
    assert verdict.verdict_type == "stale_data"
    assert verdict.confidence == "high"                  # M12: verify 가드레일을 명시 술어로

    fmt = daemon.report_cfg.format
    path = Path(daemon.report_cfg.output_dir) / f"{case_id}.{fmt}"
    text = path.read_text(encoding="utf-8")
    for heading in _report_headings(fmt):
        assert heading in text


async def test_A2_멈춘_라인은_park_resume의_F3_경로를_거쳐_equip_sync_stale_data로_귀결된다(
        monkeypatch):
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seeds = StubSeeds(mongo_collections={
        "twin_state": [{"line": 12, "status": "STOP", "since": "04:10", "qty_per_hour": 60}]})
    adapters = build_adapters(_SITE_A2, _TOPO_A2, clock=lambda: T, stub_seeds=seeds)
    judge_llm = ScriptedLLM([_JUDGE_A2])

    outcome = await run_check("mx", "gumi", "twin.consistency", _CHECK_A2, adapters=adapters,
                              store=store, clock=lambda: T, llm=judge_llm,
                              budget=LlmBudget(1000, clock=lambda: T))
    assert outcome.status == "finding"

    admit = admit_finding(outcome.finding, repo=repo, store=store, clock=lambda: T)
    assert admit.action == "opened"
    case_id = admit.case_id

    deps = EngineDeps(
        lead_llm=ScriptedLLM([_FRAME_A2_1, _ASK_A2, _FRAME_A2_2, _INTEGRATE_CONCLUDE_A2,
                              _VERDICT_A2]),
        subagent_llm=ToolFake(messages=iter([
            _mongo_call("twin_state"), _report(["ev-2"]),
            _mongo_call("twin_state"), _report(["ev-4"])])),
        adapters=adapters, store=store, topology=_TOPO_A2, engine_cfg=EngineConfig(parallel_width=1))
    daemon = _publish_daemon(repo, store, ledger, lambda: T,
                             output_dir=str(Path(ReportConfig().output_dir) / "bench-a2-f3"),
                             owner="bench-a2-f3-publish")
    worker = InvestigationWorker(
        CaseQueue(), repo=repo, store=store, deps_for_site=lambda g, f: deps,
        checkpointer=InMemorySaver(), clock=lambda: T, owner="bench-a2", max_concurrent=1,
        lease_ttl_s=900, ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
        on_closed=daemon._publish_report)

    parked = await worker.run_once(case_id, interaction_policy="interactive")
    assert parked == "awaiting_human"
    question = repo.get(case_id).question
    assert question == "라인 12가 계획된 점검 중인가요?"

    # F3(재개 실패 복구, I4): resume_case 자체가 실패하는 상황(체크포인트 역직렬화
    # 실패 등)을 흉내낸다 — worker가 스레드를 폐기하고 답을 human:answer로 박제한
    # 뒤 새 스레드로 investigate_case를 재시작해야 한다.
    import src.application.worker as wk
    real_resume_case = wk.resume_case
    calls = {"n": 0}

    async def boom_once(*a, **k):
        calls["n"] += 1
        raise RuntimeError("체크포인트 역직렬화 실패(F3 유도)")
    monkeypatch.setattr(wk, "resume_case", boom_once)

    resumed = await worker.resume_once(case_id, "계획된 점검 아님")
    monkeypatch.setattr(wk, "resume_case", real_resume_case)   # 이후 아무도 안 쓰지만 명시적으로 되돌린다

    assert calls["n"] == 1                                     # resume_case는 딱 한 번 시도되고 실패했다
    assert resumed == "closed"

    restart_events = [o for o in ledger.runs("mx", "gumi", f"worker:{case_id}")
                      if o.error and "F3 재시작" in o.error]
    assert len(restart_events) == 1
    human_answers = [r for r in store.list_evidence(case_id) if r.source == "human:answer"]
    assert len(human_answers) == 1
    assert store.get_evidence(case_id, human_answers[0].id)["answer"] == "계획된 점검 아님"

    verdict = store.get_verdict(case_id)
    assert verdict.root_cause.component == "equip-sync"
    assert verdict.verdict_type == "stale_data"
    assert verdict.confidence == "high"                  # M12: verify 가드레일을 명시 술어로

    fmt = daemon.report_cfg.format
    path = Path(daemon.report_cfg.output_dir) / f"{case_id}.{fmt}"
    text = path.read_text(encoding="utf-8")
    for heading in _report_headings(fmt):
        assert heading in text
