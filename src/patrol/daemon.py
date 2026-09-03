"""순찰 데몬 — 점검 스케줄링부터 게이트·큐·워커·스윕까지 한 프로세스로 조립한다
(스펙 §4.6, 계획 4b 마지막 조각).

이 모듈이 손대는 것은 "배선"뿐이다: 점검 실행(runner.run_check), 판정 승격
(gate.admit_finding), 조사(application.worker), 정리(application.close·
infrastructure.retention)는 전부 이미 있는 순수 조각이고, 여기서는 그것들을
스케줄러 콜백으로 묶어 raise 없는 잡으로 만드는 일만 한다.

콜백 계약: run_one/on_missed/heartbeat/self_check_job/sweep_job은 전부 최외곽
try/except로 감싸 절대 raise하지 않는다 — 스케줄러 콜백이 예외를 던지면 그
잡 자체가 스케줄에서 조용히 빠질 수 있고, 순찰이 스스로 죽어도 아무도 모르는
상황(§selfcheck.py 모듈 docstring)을 데몬 자신이 만들게 된다.

run_one의 후속 처리 규약: finding이면 게이트에 넘기고, opened만 큐에
넣는다(엔진을 태울 새 케이스). attached/rejected는 케이스를 새로 열지 않으므로
큐에 넣을 것이 없다 — 대신 그 판정 자체를 "gate:{check명}" 이벤트로 레저에
남긴다(attached=ok, rejected=skipped+사유). opened는 finding 이벤트가 이미
레저에 남아 있고 repo에 새 케이스가 보이므로 별도 gate 이벤트를 더 남기지
않는다.

SiteRuntime.deps.store는 PatrolDaemon이 생성 직후 자신의 store로 덮어써
고정한다: 워커(InvestigationWorker)는 lease 밖에서 evidence_refs_for_case로
케이스 Store를 직접 읽고, 엔진 노드는 deps.store로 같은 Store에 쓴다 — 둘이
다른 인스턴스면 초기 증거와 이후 증거가 갈라진다(application/worker.py
모듈 docstring, tests/application/test_graph_e2e.make_e2e_deps와 동일한
불변식). assemble_sites는 site config·topology·digest만 조립하는 순수
팩토리라 아직 운영 Store를 모른다 — 그래서 EngineDeps.store 자리에는 임시
InMemoryCaseStore를 채워두고, 실제 배선(PatrolDaemon 생성)에서 덮어쓴다.
"""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.application.close import sweep_timeouts
from src.application.deps import EngineDeps
from src.application.events import case_status_event, report_ready_event
from src.application.worker import CaseQueue, InvestigationWorker
from src.config.loader import load_app_config, load_registry, load_site_config
from src.config.schema_app import AppConfig, ReportConfig
from src.config.schema_site import CheckConfig, SiteConfig
from src.domain.patrol import CheckOutcome
from src.domain.store import InMemoryCaseStore
from src.infrastructure.factory import AdapterSet, StubSeeds, build_adapters
from src.infrastructure.llm import build_chat_model
from src.infrastructure.retention import sweep_retention
from src.knowledge.deployment import load_deployment
from src.knowledge.digest import canonical_digest
from src.knowledge.topology import load_topology
from src.patrol.gate import admit_finding
from src.patrol.ledger import LedgerPort
from src.patrol.llm_judge import LlmBudget
from src.patrol.runner import run_check
from src.patrol.scheduler import build_scheduler
from src.patrol.selfcheck import scan_self_check
from src.presentation.mail import MailSenderPort, NullSender, SmtpSender, retry_pending, send_report
from src.presentation.report import render_report, write_report

_IGNORED_JOB_IDS = {"heartbeat", "self_check", "sweep", "-/-/requeue"}


@dataclass
class SiteRuntime:
    """사이트 하나의 조립 결과 — 데몬이 (gbm, fct)로 찾아 쓰는 단위."""
    gbm: str
    fct: str
    cfg: SiteConfig
    adapters: AdapterSet
    deps: EngineDeps
    digests: dict[str, str] = field(default_factory=dict)


class PatrolDaemon:
    """사이트별 점검 스케줄 + 자기 감시 + 보존 스윕 + 조사 워커를 한 프로세스로 묶는다."""

    def __init__(self, *, app: AppConfig, sites: list[SiteRuntime], store, repo, ledger: LedgerPort,
                checkpointer, clock: Callable, judge_llm, budget: LlmBudget, owner: str,
                timezone: str, on_event: Callable[[Any], None] | None = None,
                report_cfg: ReportConfig = ReportConfig(), mail_sender: MailSenderPort | None = None):
        self.app = app
        self.sites = sites
        self.store = store
        self.repo = repo
        self.ledger = ledger
        self.checkpointer = checkpointer
        self.clock = clock
        self.judge_llm = judge_llm
        self.budget = budget
        self.owner = owner
        self.timezone = timezone
        self.on_event = on_event   # 계획 5(보고·채널)의 발송 훅 — 워커에도 그대로 넘기고, 여기 자신의
        # _publish_report도 report_ready 이벤트를 낼 때 같은 싱크를 쓴다
        self.report_cfg = report_cfg
        self.mail_sender = mail_sender   # None이면 report_cfg.mail.enabled로 SmtpSender/NullSender를 고른다
        self.queue = CaseQueue()
        self.worker: InvestigationWorker | None = None
        self.scheduler: AsyncIOScheduler | None = None
        self._by_key: dict[tuple[str, str], SiteRuntime] = {(rt.gbm, rt.fct): rt for rt in sites}

    def _site(self, gbm: str, fct: str) -> SiteRuntime | None:
        return self._by_key.get((gbm, fct))

    def _deps_for_site(self, gbm: str, fct: str) -> Any:
        """워커의 deps_for_site 콜백 — 미등록 사이트는 None을 돌려준다(트리아지).

        레지스트리에서 사이트가 disable/삭제된 뒤에도 그 사이트의 케이스가
        큐에 남아있을 수 있다. 예전에는 여기서 AttributeError가 나 워커가
        F1(그래프 밖 실패)로 오인해 케이스를 닫아버렸다 — None을 계약으로
        명시해 워커가 "skipped"로 구분 처리하게 한다(닫지 않고 다음 회차에
        다시 시도).
        """
        rt = self._site(gbm, fct)
        return rt.deps if rt is not None else None

    def _digests_for_site(self, gbm: str, fct: str) -> dict[str, str]:
        rt = self._site(gbm, fct)
        return rt.digests if rt is not None else {}

    async def run_one(self, gbm: str, fct: str, name: str, check: CheckConfig) -> None:
        """점검 하나를 실행하고 finding이면 게이트를 태워 opened만 큐에 넣는다.

        스케줄러 잡 콜백이므로 절대 raise하지 않는다(모듈 docstring).
        """
        try:
            rt = self._site(gbm, fct)
            if rt is None:
                return
            outcome = await run_check(gbm, fct, name, check, adapters=rt.adapters,
                                      store=self.store, clock=self.clock,
                                      llm=self.judge_llm, budget=self.budget)
            self.ledger.record_run(gbm, fct, name, outcome)
            if outcome.status != "finding" or outcome.finding is None:
                return

            admit = admit_finding(outcome.finding, repo=self.repo, store=self.store, clock=self.clock)
            if admit.action == "opened":
                self._emit_case_opened(admit.case_id)
                await self.queue.put(admit.case_id)
            elif admit.action == "attached":
                self.ledger.record_run(gbm, fct, f"gate:{name}", CheckOutcome(
                    status="ok", observed_at=self.clock()))
            else:  # rejected
                self.ledger.record_run(gbm, fct, f"gate:{name}", CheckOutcome(
                    status="skipped", observed_at=self.clock(),
                    skipped_reason=admit.reason or "게이트 기각"))
        except Exception:                                          # noqa: BLE001 — 데몬은 raise하지 않는다
            pass

    def on_missed(self, job_id: str) -> None:
        """스케줄러가 misfire/겹침으로 건너뛴 잡을 레저에 skipped로 남긴다.

        scheduler.py의 EVENT_JOB_MISSED|EVENT_JOB_MAX_INSTANCES 리스너가 동기
        호출하는 콜백이다(build_scheduler 모듈 docstring) — 여기서 코루틴을
        만들면 그 실행을 누가 기다릴지 애매해지므로 동기 포트만 쓴다.
        heartbeat/self_check/sweep 잡은 점검이 아니라 무시한다.
        """
        if job_id in _IGNORED_JOB_IDS:
            return
        try:
            gbm, fct, name = job_id.split("/", 2)
            self.ledger.record_run(gbm, fct, name, CheckOutcome(
                status="skipped", observed_at=self.clock(), skipped_reason="misfire/중복 실행 스킵"))
        except Exception:                                          # noqa: BLE001
            pass

    async def heartbeat(self) -> None:
        try:
            self.ledger.heartbeat(self.clock())
        except Exception:                                          # noqa: BLE001
            pass

    async def self_check_job(self) -> None:
        """모든 사이트·점검의 연속 error를 훑어 threshold를 넘긴 것만 케이스로 올린다."""
        try:
            checks = [(rt.gbm, rt.fct, name) for rt in self.sites for name in rt.cfg.patrol.checks]
            findings = scan_self_check(ledger=self.ledger, checks=checks,
                                       threshold=self.app.patrol.self_check_errors,
                                       clock=self.clock, store=self.store)
            for finding in findings:
                admit = admit_finding(finding, repo=self.repo, store=self.store, clock=self.clock)
                if admit.action == "opened":
                    self._emit_case_opened(admit.case_id)
                    await self.queue.put(admit.case_id)
        except Exception:                                          # noqa: BLE001
            pass

    async def requeue_job(self) -> None:
        """열린 케이스와 만료 lease를 다시 큐에 넣는다.

        중복 투입은 해롭지 않다 — run_once가 claim에 실패하면 "busy"를 돌려주고
        끝난다. 반대로 재스캔이 없으면 다른 프로세스가 연 케이스를 영원히 못 본다.
        다른 잡과 같이 절대 raise하지 않는다.
        """
        try:
            self.queue.requeue_open(self.repo, clock=self.clock)
        except Exception:                                          # noqa: BLE001
            pass

    async def sweep_job(self) -> None:
        try:
            await sweep_timeouts(repo=self.repo, checkpointer=self.checkpointer, clock=self.clock,
                                 timeout_h=self.app.investigations.awaiting_human_timeout_h)
            await sweep_retention(repo=self.repo, store=self.store, ledger=self.ledger,
                                  checkpointer=self.checkpointer, clock=self.clock,
                                  retention=self.app.store.retention)
            await retry_pending(sender=self._mail_sender(), ledger=self.ledger,
                                cfg=self.report_cfg.mail, clock=self.clock,
                                render=self._render_pending)
        except Exception:                                          # noqa: BLE001
            pass

    def _mail_sender(self) -> MailSenderPort:
        """`mail_sender`가 주입됐으면(테스트 등) 그것을, 아니면 `report_cfg.mail.enabled`로
        SmtpSender/NullSender 중 고른다 — 매번 다시 고르므로 런타임에 설정이 재로딩돼도
        따라간다."""
        if self.mail_sender is not None:
            return self.mail_sender
        return SmtpSender(self.report_cfg.mail) if self.report_cfg.mail.enabled else NullSender()

    @staticmethod
    def _report_subject(case_id: str) -> str:
        return f"[순찰] 케이스 {case_id} 조사 보고서"

    def _render_case_report(self, case_id: str) -> str:
        """repo+store에서 케이스 판정·증거·케이스 파일을 다시 읽어 보고서 본문을 조립한다.
        _publish_report와 재시도 스윕(_render_pending)이 공유한다 — 재시도 시점에는 파일이
        아니라 이 함수로 다시 렌더링해 최신 상태를 반영한다(스펙 §5.4).

        evidence_summaries(I4): §4 "요지" 열을 body_digest가 아니라 실제 본문에서
        채우려고 store.get_evidence로 각 증거 본문을 다시 읽어 repr을 120자로
        자른다. 개별 증거 하나가 조회에 실패해도(예: 저장소 이상) 그 id만
        건너뛴다 — render_report는 딕셔너리에 없는 id를 digest로 폴백하므로
        보고서 조립 자체를 막지 않는다.
        """
        record = self.repo.get(case_id)
        verdict = self.store.get_verdict(case_id)
        evidence = self.store.list_evidence(case_id)
        case_file = self.store.get_case_file(case_id)
        evidence_summaries: dict[str, str] = {}
        for r in evidence:
            try:
                evidence_summaries[r.id] = repr(self.store.get_evidence(case_id, r.id))[:120]
            except Exception:                                      # noqa: BLE001 — 개별 실패만 건너뛴다
                pass
        return render_report(record, verdict=verdict, evidence=evidence,
                             case_file=case_file, clock=self.clock,
                             evidence_summaries=evidence_summaries)

    def _render_pending(self, record: dict) -> tuple[str, str]:
        """retry_pending의 render 콜백 — send_id("report:{case_id}")에서 case_id를
        복원해 보고서를 다시 렌더링한다. 여기서 raise해도 retry_pending이 그 레코드만
        건너뛰므로(mail.py F2) 방어적으로 감싸지 않는다."""
        case_id = record["send_id"].removeprefix("report:")
        return self._report_subject(case_id), self._render_case_report(case_id)

    def _emit_case_opened(self, case_id: str) -> None:
        """게이트가 케이스를 연 직후 부른다 — Timeline의 첫 항목이다.

        싱크가 raise해도 순찰을 죽이지 않는다(worker._emit_status와 같은 계약).
        """
        if self.on_event is None:
            return
        try:
            self.on_event(case_status_event(case_id, "open", clock=self.clock))
        except Exception:                                          # noqa: BLE001
            pass

    async def _publish_report(self, case_id: str) -> None:
        """워커가 케이스를 닫은 직후 InvestigationWorker.on_closed로 불린다(계획 5).

        파일 먼저(render_report → write_report) → report_ready 이벤트를 on_event
        싱크로 → send_report로 메일 발송(2상 레저) 순서를 지킨다. 전부 try/except다 —
        발행이 실패해도 이미 끝난 종결 결과를 뒤집지 않는다(worker.py의 on_closed 계약과
        동일한 이유). write_report가 실패(빈 문자열)하면 report_ready도 메일도 내지
        않는다 — "항상 파일 먼저" 원칙(report.py 모듈 docstring)이 여기서도 그대로다.
        """
        try:
            text = self._render_case_report(case_id)
            path = write_report(text, output_dir=self.report_cfg.output_dir, case_id=case_id)
            if not path:
                return
            if self.on_event is not None:
                try:
                    self.on_event(report_ready_event(case_id, path, clock=self.clock))
                except Exception:                                  # noqa: BLE001
                    pass
            await send_report(case_id, self._report_subject(case_id), text,
                              sender=self._mail_sender(), ledger=self.ledger,
                              cfg=self.report_cfg.mail, clock=self.clock)
        except Exception:                                          # noqa: BLE001 — 발행 실패가 종결을 뒤집지 않는다
            pass

    def build(self) -> AsyncIOScheduler:
        """점검·하트비트·자기 감시·스윕 잡을 전부 등록하고 워커를 생성한다.

        start()는 호출하지 않는다(scheduler.py와 동일한 이유 — 기동 시점은
        run()이 결정한다). 워커 생성 직전에 requeue_open으로 재시작 내구성을
        확보한다 — 워커가 아직 돌기 전에도 큐가 채워져 있어야 run() 이후
        바로 소비할 수 있다.
        """
        for rt in self.sites:
            rt.deps.store = self.store    # 모듈 docstring: 워커와 엔진이 같은 Store를 봐야 한다

        site_tuples = [(rt.gbm, rt.fct, rt.cfg) for rt in self.sites]
        scheduler = build_scheduler(site_tuples, run_one=self.run_one, heartbeat=self.heartbeat,
                                    on_missed=self.on_missed, timezone=self.timezone)
        scheduler.add_job(self.self_check_job, IntervalTrigger(minutes=10), id="self_check",
                          misfire_grace_time=None)
        scheduler.add_job(self.sweep_job, IntervalTrigger(hours=1), id="sweep",
                          misfire_grace_time=None)
        # 잡 id를 3세그먼트로 맞춘다 — on_missed가 split("/", 2)로 언팩하므로
        # 2세그먼트면 ValueError가 except에 삼켜져 misfire가 기록 없이 사라진다.
        scheduler.add_job(self.requeue_job,
                          IntervalTrigger(seconds=self.app.investigations.requeue_interval_s),
                          id="-/-/requeue", misfire_grace_time=None)

        self.worker = InvestigationWorker(
            self.queue, repo=self.repo, store=self.store, deps_for_site=self._deps_for_site,
            checkpointer=self.checkpointer, clock=self.clock, owner=self.owner,
            max_concurrent=self.app.investigations.max_concurrent,
            lease_ttl_s=self.app.investigations.lease_ttl_s, ledger=self.ledger,
            max_wall_clock_s=self.app.investigations.max_wall_clock_s,
            knowledge_digests_for_site=self._digests_for_site, on_event=self.on_event,
            on_closed=self._publish_report)
        self.queue.requeue_open(self.repo, clock=self.clock)

        self.scheduler = scheduler
        return scheduler

    async def run(self, stop: asyncio.Event) -> None:
        """스케줄러를 기동하고 워커 루프가 stop까지 돌게 한 뒤 스케줄러를 내린다."""
        if self.scheduler is None or self.worker is None:
            self.build()
        self.scheduler.start()
        try:
            await self.worker.run_forever(stop)
        finally:
            self.scheduler.shutdown(wait=False)


def assemble_sites(
    config_root: Path, repo_root: Path, env: dict, *, clock: Callable,
    stub_seeds: StubSeeds | None = None, llm_factory: Callable[[str], Any] | None = None,
) -> tuple[AppConfig, list[SiteRuntime]]:
    """registry의 활성 사이트마다 config·topology·deployment·digest·어댑터·엔진
    의존을 조립한다.

    EngineDeps.store는 여기서 확정하지 않는다(모듈 docstring) — 임시
    InMemoryCaseStore로만 채워 dataclass 필수 필드를 만족시키고, 운영 Store는
    PatrolDaemon.build()가 덮어쓴다. llm_factory가 주어지면(테스트의
    ScriptedLLM/ToolFake 등) 그것으로 lead/subagent를 만들고, 아니면
    build_chat_model(profile, base_url=env["LLM_BASE_URL"], api_key=env["LLM_API_KEY"])로
    실LLM을 만든다.
    """
    app = load_app_config(config_root, env=env)
    registry = load_registry(config_root)

    def make_llm(profile: str) -> Any:
        if llm_factory is not None:
            return llm_factory(profile)
        return build_chat_model(profile, base_url=env.get("LLM_BASE_URL"), api_key=env.get("LLM_API_KEY"))

    sites: list[SiteRuntime] = []
    for ref in registry.sites:
        if not ref.enabled:
            continue
        site_cfg, _provenance = load_site_config(config_root, ref.gbm, ref.fct, env=env)
        knowledge_root = repo_root / site_cfg.knowledge.root
        topology = load_topology(knowledge_root, ref.gbm, ref.fct)
        deployment = load_deployment(knowledge_root, ref.gbm, ref.fct)

        checks_dump = {name: chk.model_dump(mode="json") for name, chk in site_cfg.patrol.checks.items()}
        digests = {
            "topology": canonical_digest(topology.model_dump(mode="json")),
            "rules": canonical_digest(checks_dump),
            "deployment": canonical_digest(deployment.model_dump(mode="json")) if deployment is not None
                         else "absent",
        }

        adapters = build_adapters(site_cfg, topology, clock=clock, stub_seeds=stub_seeds)
        deps = EngineDeps(
            lead_llm=make_llm(app.llm.profiles.lead),
            subagent_llm=make_llm(app.llm.profiles.subagent),
            adapters=adapters, store=InMemoryCaseStore(), topology=topology,
            engine_cfg=app.engine,
        )
        sites.append(SiteRuntime(gbm=ref.gbm, fct=ref.fct, cfg=site_cfg, adapters=adapters,
                                 deps=deps, digests=digests))

    return app, sites
