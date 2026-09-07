"""CLI 엔트리 — 계획 1: registry / config show / knowledge validate.
계획 4b: patrol run/status, case list/show/resume 추가.
계획 5: chat(접수 대화형 조사) 추가, case show --report 추가.
"""
import argparse
import asyncio
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from dotenv import load_dotenv

from src.application.answer import answer_case
from src.application.events import collect_events
from src.application.labels import label_stats, label_texts, submit_label
from src.application.intake import intake_turn
from src.application.submit import submit_case
from src.application.worker import CaseQueue, InvestigationWorker
from src.boot import validate_boot
from src.config.loader import ConfigError, load_app_config, load_registry, load_site_config
from src.domain.concern import CONCERNS
from src.domain.events import EngineEvent, EventStorePort
from src.infrastructure.checkpointer import build_checkpointer, build_persistence
from src.infrastructure.llm import build_chat_model
from src.patrol.daemon import (PatrolDaemon, assemble_sites, load_stub_seeds,
                               seeds_problems)
from src.patrol.llm_judge import LlmBudget
from src.presentation.mail import SmtpSender
from src.presentation.report import render_md
from src.presentation.report_html import render_html
from src.domain.report_model import build_report_model

_CASE_STATUSES = ("open", "investigating", "awaiting_human", "closed")


def _add_common(parser):
    parser.add_argument("--config-root", default="config")
    parser.add_argument("--repo-root", default=".")


def _load_app(config_root: Path, env: dict):
    """load_app_config를 감싸 ConfigError를 stderr에 나열한다. 실패하면 None.

    env를 넘겨 app.json의 ${ENV} 참조를 해석한다(C1) — 안 넘기면 store.mongo_url
    같은 참조가 리터럴 문자열 "${...}"로 그냥 통과해버린다.
    """
    try:
        return load_app_config(config_root, env=env)
    except ConfigError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return None


async def _drive_daemon(daemon: PatrolDaemon, for_seconds: float | None) -> None:
    """for_seconds가 있으면 그만큼 뒤(0이면 즉시) stop을 세팅하고 데몬을 돌린다."""
    stop = asyncio.Event()
    if for_seconds is not None:
        if for_seconds <= 0:
            stop.set()                                       # build 후 즉시 stop — 기동 스모크
        else:
            asyncio.get_running_loop().call_later(for_seconds, stop.set)
    await daemon.run(stop)


def _load_seeds(args) -> tuple[dict | None, bool]:
    """`--stub-seeds` 파일을 읽는다 — (시드, 실패했는가).

    사이트를 조립하는 명령이 셋(patrol run·chat·case resume)이라 여기로 묶는다.
    하나만 배선하면 언젠가 나머지가 조용히 다르게 돈다 — 케이스 종결 세 경로가
    같은 발행 배선을 써야 하는 것과 같은 이유(CLAUDE.md 규율 8).
    """
    path = getattr(args, "stub_seeds", None)
    if not path:
        return None, False
    seeds, problems = load_stub_seeds(Path(path))
    for problem in problems:
        print(f"[stub-seeds] {problem}", file=sys.stderr)
    return (None, True) if problems else (seeds, False)


def _seeds_mismatch(seeds, sites) -> bool:
    """시드가 향한 사이트가 실제로 스텁을 쓰는지 확인하고, 어긋나면 알린다."""
    known = {f"{rt.gbm}/{rt.fct}": rt.cfg.target.adapters for rt in sites}
    problems = seeds_problems(seeds or {}, known)
    for problem in problems:
        print(f"[stub-seeds] {problem}", file=sys.stderr)
    return bool(problems)


def _add_stub_seeds(parser) -> None:
    parser.add_argument(
        "--stub-seeds", default=None,
        help="스텁 어댑터가 돌려줄 가짜 응답 파일(사이트키 → 시드). 예시 트리를 "
             "대상 시스템 없이 돌릴 때만 쓴다 — 실전환 시에는 이 플래그를 뺀다")


def _run_api(args, env: dict) -> int:
    """기동 검증 → api 조립(어댑터 없이) → uvicorn — `api` 프로세스의 본체.

    `--stub-seeds`를 받지 않는다: 이 프로세스에는 어댑터가 없어 시드가 갈 곳이 없다.
    fastapi/uvicorn은 여기서만 지연 import한다 — `patrol run`·`chat`은 그 의존을
    필요로 하지 않는다.
    """
    config_root = Path(args.config_root)
    repo_root = Path(args.repo_root)
    errors = validate_boot(config_root, env=env, repo_root=repo_root, check_live=False)
    if errors:
        for e in errors:
            print(f"[{e.where}] {e.problem}", file=sys.stderr)
        return 1

    from src.api.app import create_app
    from src.api.assembly import assemble_api

    clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
    runtime = assemble_api(config_root, repo_root, env, clock=clock)
    app = create_app(runtime)
    if runtime.app.store.backend == "memory":
        # 메모리 백엔드는 프로세스 간 공유가 없다 — api가 연 케이스를 워커(patrol run)가
        # 절대 못 본다. 조용히 도는 것보다 시끄럽게 알리는 편이 낫다.
        print("경고: store.backend가 memory다 — 이 api가 연 케이스는 다른 프로세스의 워커에 "
              "닿지 않는다. 실운영은 mongo 백엔드가 필요하다", file=sys.stderr)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _run_patrol(args, env: dict, *, llm_factory=None) -> int:
    """기동 검증 → 사이트 조립 → 순찰 데몬 기동(포그라운드) — patrol run의 본체.

    llm_factory를 내부 함수로 분리해 받는 이유(I8): assemble_sites와 judge_llm
    생성 둘 다에 그대로 흘려보내, 테스트가 실LLM(build_chat_model → ChatOpenAI)
    없이도 patrol run의 성공 경로("사이트 조립 → 데몬 기동 → 종료")를 스모크할
    수 있게 하기 위해서다.
    """
    config_root = Path(args.config_root)
    repo_root = Path(args.repo_root)
    errors = validate_boot(config_root, env=env, repo_root=repo_root, check_live=False)
    if errors:
        for e in errors:
            print(f"[{e.where}] {e.problem}", file=sys.stderr)
        return 1

    # 시드는 config가 아니라 플래그다 — 프로덕션 명령줄에는 없고, 없으면 시드도
    # 없다. 실전환 시 "지우는 것을 잊지 마라"를 메커니즘이 대신한다.
    seeds, failed = _load_seeds(args)
    if failed:
        return 1

    clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
    app, sites = assemble_sites(config_root, repo_root, env, clock=clock,
                                stub_seeds=seeds, llm_factory=llm_factory)
    if _seeds_mismatch(seeds, sites):
        return 1

    p = build_persistence(app.store)
    store, repo, ledger, events = p.store, p.repo, p.ledger, p.events
    snapshots = p.snapshots
    checkpointer = build_checkpointer(app.store)

    needs_judge_llm = any(check.judge in ("llm", "rule+llm")
                          for rt in sites for check in rt.cfg.patrol.checks.values())
    judge_llm = None
    if needs_judge_llm:
        if llm_factory is not None:
            judge_llm = llm_factory(app.llm.profiles.judge)
        else:
            judge_llm = build_chat_model(app.llm.profiles.judge,
                                         base_url=env.get("LLM_BASE_URL"), api_key=env.get("LLM_API_KEY"))

    budget = LlmBudget(app.patrol.llm_budget.max_calls_per_hour, clock=clock)
    owner = f"daemon-{socket.gethostname()}-{os.getpid()}"
    mail_sender = SmtpSender(app.report.mail) if app.report.mail.enabled else None
    # on_event를 넘기지 않으면 usecase의 `if on_event is None: ainvoke` 분기 때문에
    # _stream_and_collect(라운드 경계를 내는 유일한 경로)이 프로덕션에서 죽고,
    # _publish_report의 이벤트 발행도 건너뛴다 — 규율 8이 요구하는 셋 중 이벤트만
    # 빠진 상태가 된다. 데몬은 _build_publisher를 쓰지 않는다(자기 _publish_report를
    # 워커의 on_closed로 이미 배선한다) — 그래서 여기서 필요한 것은 싱크 하나뿐이다.
    daemon = PatrolDaemon(app=app, sites=sites, store=store, repo=repo, ledger=ledger,
                          checkpointer=checkpointer, clock=clock, judge_llm=judge_llm,
                          budget=budget, owner=owner, timezone=app.timezone,
                          on_event=_make_event_sink(events, _make_event_printer()),
                          report_cfg=app.report,
                          mail_sender=mail_sender, events=events, snapshots=snapshots)
    asyncio.run(_drive_daemon(daemon, args.for_seconds))
    return 0


def _cmd_patrol_status(config_root: Path, env: dict) -> int:
    app = _load_app(config_root, env)
    if app is None:
        return 1
    if app.store.backend == "memory":
        print("메모리 백엔드 — 프로세스 간 상태 없음")
        return 0

    ledger = build_persistence(app.store).ledger
    hb = ledger.last_heartbeat()
    print(f"하트비트: {hb.isoformat() if hb is not None else '없음'}")

    try:
        registry = load_registry(config_root)
    except ConfigError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return 1

    for site in registry.sites:
        if not site.enabled:
            continue
        try:
            cfg, _provenance = load_site_config(config_root, site.gbm, site.fct, env=env)
        except ConfigError as exc:
            for problem in exc.problems:
                print(problem, file=sys.stderr)
            continue
        for name in cfg.patrol.checks:
            outcome = ledger.last_run(site.gbm, site.fct, name)
            if outcome is None:
                print(f"{site.gbm}/{site.fct}  {name}  (실행 이력 없음)")
            else:
                print(f"{site.gbm}/{site.fct}  {name}  {outcome.status}  "
                     f"{outcome.observed_at.isoformat()}")
    return 0


def _cmd_case_list(args, config_root: Path, env: dict) -> int:
    app = _load_app(config_root, env)
    if app is None:
        return 1
    repo = build_persistence(app.store).repo
    statuses = (args.status,) if args.status else _CASE_STATUSES
    records = [r for status in statuses for r in repo.list_by_status(status)]
    for r in records:
        print(f"{r.id}  {r.status}  {r.gbm}/{r.fct}  {r.symptom[:60]}")
    return 0


def _cmd_case_label(args, config_root: Path, env: dict) -> int:
    """실제 원인 되먹임 — 학습 루프의 나머지 절반(계획 15/P8)."""
    app = _load_app(config_root, env)
    if app is None:
        return 1
    p = build_persistence(app.store)
    if args.stats:
        stats = label_stats(repo=p.repo, labels=p.labels)
        # 게이트가 닫혀 있으면 퍼센트를 내지 않는다 — 12/40으로 낸 30%는 다음 주에
        # 뒤집힐 숫자이고, 한 번 보고되면 사람이 그것을 기억한다.
        print(stats.why)
        print("게이트: " + ("열림 — confidence별 적중을 계산할 수 있다" if stats.gate_open
                          else "닫힘 — 건수만 보고한다"))
        return 0
    if not args.case_id or not args.agreement:
        print("case label <id> --agreement correct|partially_correct|wrong|unknown",
              file=sys.stderr)
        return 2
    clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
    result = submit_label(args.case_id, agreement=args.agreement, resolution=args.resolution,
                          actual_component=args.actual_component,
                          actual_verdict_type=args.actual_verdict_type,
                          saw_report=args.saw_report, labeled_by=args.by,
                          repo=p.repo, labels=p.labels, clock=clock)
    if result != "recorded":
        print(f"라벨 실패: {result}", file=sys.stderr)
        return 1
    print(f"{args.case_id} 라벨 기록: {args.agreement}")
    return 0


def _cmd_case_show(args, config_root: Path, env: dict) -> int:
    app = _load_app(config_root, env)
    if app is None:
        return 1
    p = build_persistence(app.store)
    store, repo = p.store, p.repo
    try:
        record = repo.get(args.case_id)
    except KeyError:
        print(f"케이스 {args.case_id!r}를 찾을 수 없다", file=sys.stderr)
        return 1

    if args.report:
        # 저장된 보고서 파일을 그대로 보여준다. 설정 포맷을 먼저 찾고, 없으면 다른
        # 확장자도 본다 — report.format을 바꾼 뒤에도 예전 보고서를 읽을 수 있어야
        # 한다. 둘 다 없으면(발행 실패·retention 스윕) 즉석에서 다시 렌더한다.
        directory = Path(app.report.output_dir)
        suffixes = [app.report.format] + [s for s in ("html", "md") if s != app.report.format]
        found = next((directory / f"{args.case_id}.{s}" for s in suffixes
                      if (directory / f"{args.case_id}.{s}").exists()), None)
        if found is not None:
            print(found.read_text(encoding="utf-8"), end="")
        else:
            clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
            log = collect_events(p.events, args.case_id)      # Persistence.events는 항상 있다
            model = build_report_model(
                record, verdict=store.get_verdict(args.case_id),
                evidence=store.list_evidence(args.case_id),
                case_file=store.get_case_file(args.case_id), clock=clock,
                events=log.events, timeline_error=log.error,
                labels=label_texts(p.labels, args.case_id))
            print(render_html(model) if app.report.format == "html" else render_md(model), end="")
        return 0

    print(f"id: {record.id}")
    print(f"상태: {record.status}")
    print(f"사이트: {record.gbm}/{record.fct}")
    print(f"증상: {record.symptom}")
    print(f"생성: {record.created_at.isoformat()}  갱신: {record.updated_at.isoformat()}")
    print(f"소유자: {record.owner or '-'}  "
         f"임차 만료: {record.lease_until.isoformat() if record.lease_until else '-'}")
    if record.status == "awaiting_human" and record.question:
        print(f"파킹된 질문: {record.question}")
    if record.closed_reason:
        print(f"종결 사유: {record.closed_reason}")

    verdict = store.get_verdict(args.case_id)
    if verdict is None:
        print("판정: 아직 없음")
    else:
        print(f"판정: {verdict.verdict_type}  신뢰도: {verdict.confidence}")
        print(f"서술: {verdict.narrative[:200]}")
    print(f"증거 수: {len(store.list_evidence(args.case_id))}")
    return 0


def _cmd_case_resume(args, config_root: Path, env: dict) -> int:
    # 트리아지: app을 두 번(여기서 한 번, assemble_sites 안에서 한 번) 읽지
    # 않도록 assemble_sites 하나로 app과 sites를 함께 얻는다(_run_patrol과
    # 동일한 패턴) — 예전엔 _load_app이 만든 app을 store/repo 조회에만 쓰고
    # assemble_sites가 돌려준 app(_app2)은 쓰지 않고 버렸다.
    seeds, failed = _load_seeds(args)
    if failed:
        return 1
    clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
    try:
        app, sites = assemble_sites(config_root, Path(args.repo_root), env, clock=clock,
                                    stub_seeds=seeds)
    except ConfigError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return 1
    if _seeds_mismatch(seeds, sites):
        return 1

    p = build_persistence(app.store)
    store, repo, ledger, events = p.store, p.repo, p.ledger, p.events
    snapshots = p.snapshots
    try:
        record = repo.get(args.case_id)
    except KeyError:
        print(f"케이스 {args.case_id!r}를 찾을 수 없다", file=sys.stderr)
        return 1

    # 개설만 막고 답변을 안 막으면 반쪽이다 — 답변 텍스트가 리드 프롬프트에
    # 직행하고 evidence로 박제된다(스펙 §3.5). lease·status 검사보다 **앞**이다:
    # 뒤에 두면 미인가 주체가 케이스의 존재·상태·데몬 가동 여부를 캐낼 수 있다.
    subject = getattr(args, "requested_by", None)
    if not app.access.can_access(subject, record.gbm, record.fct):
        print(f"주체 {subject!r}는 {record.gbm}/{record.fct}에 접근할 수 없다 "
              f"(app.json의 access.allow)", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    lease_free = record.owner is None or (record.lease_until is not None
                                          and record.lease_until < now)
    if not lease_free:
        print("데몬이 실행 중 — 잠시 후 재시도", file=sys.stderr)
        return 2
    if record.status != "awaiting_human":
        print(f"케이스가 awaiting_human 상태가 아니다(현재: {record.status}) — 재개할 수 없다",
             file=sys.stderr)
        return 1

    for rt in sites:
        rt.deps.store = store          # daemon.py와 동일한 불변식: 워커·엔진이 같은 Store를 본다
    by_key = {(rt.gbm, rt.fct): rt for rt in sites}

    if (record.gbm, record.fct) not in by_key:
        # 트리아지: 미등록/비활성 사이트 — 워커(InvestigationWorker)가 lease를
        # 잡기 전에 여기서 막는다. 그냥 진행하면 resume_once가 lease를 잡은
        # 채로 deps_for_site(None)를 만나 "skipped"를 돌려주고 lease만 풀린
        # 채 남는데, CLI는 그보다 더 명확한 exit 1로 즉시 끝내는 게 낫다.
        print(f"사이트 {record.gbm}/{record.fct}가 등록돼 있지 않다(registry에서 비활성이거나 "
             "삭제됨) — 재개할 수 없다", file=sys.stderr)
        return 1

    def deps_for_site(gbm, fct):
        return by_key[(gbm, fct)].deps

    def digests_for_site(gbm, fct):
        rt = by_key.get((gbm, fct))
        return rt.digests if rt is not None else {}

    checkpointer = build_checkpointer(app.store)
    owner = f"cli-{socket.gethostname()}-{os.getpid()}"
    # C1/M4: chat·데몬과 같은 발행 배선(on_event/on_closed)을 _build_publisher로
    # 얻어 워커에 넘긴다 — 예전엔 여기가 빠져 있어 case resume만 보고서·메일·
    # 이벤트 없이 케이스를 닫았다(§5.1 "파일 먼저"·§5.4 F6·§5.2 이벤트 구독 미충족).
    on_event, on_closed = _build_publisher(app, sites, store, repo, ledger, events, checkpointer, clock)
    worker = InvestigationWorker(
        CaseQueue(), repo=repo, store=store, deps_for_site=deps_for_site,
        checkpointer=checkpointer, clock=clock, owner=owner,
        max_concurrent=app.investigations.max_concurrent,
        lease_ttl_s=app.investigations.lease_ttl_s, ledger=ledger,
        knowledge_digests_for_site=digests_for_site,
        max_wall_clock_s=app.investigations.max_wall_clock_s, snapshots=snapshots,
        max_intake_turns=app.engine.max_intake_turns,
        on_event=on_event, on_closed=on_closed)

    # 접수 질문과 조사 질문을 가르는 것은 answer_case 하나다 — CLI와 계획 13의
    # API가 같은 함수를 쓴다(규율 8).
    rt = by_key[(record.gbm, record.fct)]
    result = asyncio.run(answer_case(
        args.case_id, args.answer, repo=repo, store=store, deps=rt.deps,
        topology=rt.deps.topology, worker=worker, clock=clock,
        max_intake_turns=app.engine.max_intake_turns, on_event=on_event,
        on_problem=lambda p: print(f"접수: {p}", file=sys.stderr)))
    if result == "busy":
        # 위의 사전 점검과 실제 획득 사이의 경합(다른 프로세스가 그 사이 lease를 잡은 경우) —
        # resume_once 내부의 repo.claim이 최종 결정권을 가지므로 여기서도 같은 exit 2로 맞춘다.
        print("데몬이 실행 중 — 잠시 후 재시도", file=sys.stderr)
        return 2
    print(f"재개 결과: {result}")
    return 0


def _round_label(state: dict) -> str:
    return f"라운드 {state['round']}" if state.get("round") is not None else "라운드"


def _format_event(event: EngineEvent, state: dict) -> str:
    """이벤트 봉투 하나를 사람이 읽을 한 줄로 바꾼다 — event/data(봉투 필드)만
    쓴다. 그래프 내부 노드명은 여기 들어오지 않는다(application/events.py의
    매핑 규칙 계약) — round_started/task_finished/question_raised/
    case_status_changed/report_ready/verdict_formed 6종 밖의 값은 CLI 출력에
    나타나지 않는다.

    task_finished는 자신의 round를 싣지 않는다(events.py _execute_events) —
    그래서 직전 round_started가 낸 round를 state에 들고 있다가 접두어로 쓴다.
    """
    data = event.data or {}
    if event.event == "round_started":
        state["round"] = data.get("round")
        dispatched = ", ".join(data.get("dispatched") or []) or "없음"
        return f"[{_round_label(state)}] 태스크 시작: {dispatched}"
    if event.event == "task_finished":
        evidence = ", ".join(data.get("evidence_ids") or []) or "없음"
        error = f" — {data['error']}" if data.get("error") else ""
        return (f"[{_round_label(state)}] 태스크 {data.get('task_id', '?')} "
               f"{data.get('status', '?')} (증거 {evidence}){error}")
    if event.event == "question_raised":
        return f"[질문] {data.get('question')}"
    if event.event == "case_status_changed":
        reason = f" ({data['reason']})" if data.get("reason") else ""
        return f"[상태] {data.get('status')}{reason}"
    if event.event == "verdict_formed":
        rewritten = " (재작성)" if data.get("rewritten") else ""
        return f"[판정] {data.get('verdict_type')} / 확신 {data.get('confidence')}{rewritten}"
    if event.event == "report_ready":
        return f"[보고서 준비] {data.get('path')}"
    return f"[{event.event}]"


def _make_event_printer(print_fn=print) -> Callable[[EngineEvent], None]:
    state: dict = {}

    def _sink(event: EngineEvent) -> None:
        print_fn(_format_event(event, state))

    return _sink


def _make_event_sink(events: EventStorePort,
                     downstream: Callable[[EngineEvent], None] | None = None
                     ) -> Callable[[EngineEvent], None]:
    """이벤트를 스토어에 적재한 뒤 downstream으로 넘긴다.

    적재 실패를 삼키는 이유: on_event는 조사를 실패시킬 수 없는 부수효과로
    설계돼 있다(worker._emit_status·daemon._publish_report·usecase의 세 군데가
    독립적으로 삼킨다). 저장소 장애가 조사를 죽이면 그 방향이 뒤집힌다.
    적재가 실패하면 seq 없는 원본이 그대로 downstream으로 간다.
    """
    def sink(event: EngineEvent) -> None:
        try:
            event = events.append(event)
        except Exception:                                          # noqa: BLE001
            pass
        if downstream is not None:
            downstream(event)
    return sink


def _build_publisher(app, sites, store, repo, ledger, events, checkpointer, clock
                     ) -> tuple[Callable[[EngineEvent], None], Callable[[str], Awaitable[None]]]:
    """발행용 PatrolDaemon 셸을 조립해 (on_event, on_closed) 쌍을 돌려준다(C1/M4).

    데몬·chat·case resume 세 종결 경로 모두 "케이스가 닫히면 보고서(파일 먼저)·
    report_ready 이벤트·메일이 나간다"는 계약을 똑같이 지켜야 한다(§5.1·§5.2·
    §5.4). build()/run()은 절대 부르지 않는다 — 스케줄러를 기동하지 않고 그
    인스턴스의 _publish_report 메서드만 워커의 on_closed로 재사용해, 발행
    배선(예산·mail_sender 선택·render→write→이벤트→메일 순서)을 여기 한
    곳에서만 조립한다. _run_chat과 _cmd_case_resume이 각자 이 조립을 따로
    베끼면(예전 case_resume이 아예 빠뜨렸던 것처럼) 언젠가 또 하나가 놓친다.
    """
    print_event = _make_event_sink(events, _make_event_printer())
    owner = f"cli-publisher-{socket.gethostname()}-{os.getpid()}"
    budget = LlmBudget(app.patrol.llm_budget.max_calls_per_hour, clock=clock)
    mail_sender = SmtpSender(app.report.mail) if app.report.mail.enabled else None
    daemon = PatrolDaemon(app=app, sites=sites, store=store, repo=repo, ledger=ledger,
                          checkpointer=checkpointer, clock=clock, judge_llm=None,
                          budget=budget, owner=owner, timezone=app.timezone,
                          on_event=print_event, report_cfg=app.report, mail_sender=mail_sender,
                          events=events)
    return print_event, daemon._publish_report


async def _drive_chat(args, rt, repo, store, worker, clock, ask, app, case_id, turn,
                      on_event) -> int:
    """남은 접수 턴 → 조사 → awaiting_human 반복 → 보고서 경로 출력.

    개설과 첫 접수 턴은 `submit_case`가 이미 했다 — `POST /cases`와 같은 함수다.
    여기는 CLI만의 부분(stdin으로 답을 받는 루프)만 남는다.
    """
    print(f"케이스 {case_id} 접수 — 대상을 확인한다")
    while True:
        if turn.status == "not_ours":
            # 다른 주체가 이 레코드를 들고 있다 — 조사를 걸면 스레드를 잃는다.
            for problem in turn.problems:
                print(f"접수: {problem}", file=sys.stderr)
            print(f"케이스 {case_id}는 다른 곳에서 처리 중이다 — 조사를 시작하지 않는다")
            return 0
        if turn.status != "asking":
            for problem in turn.problems:
                print(f"접수: {problem}", file=sys.stderr)
            break
        try:
            answer = await ask(turn.question)
        except EOFError:
            print(f"입력이 끊겼다 — 케이스 {case_id}는 파킹된 채로 남는다. "
                 f"'python -m src case resume {case_id} --answer <답변>'으로 나중에 재개할 수 있다.")
            return 0
        turn = await intake_turn(case_id, repo=repo, store=store, deps=rt.deps,
                                 topology=rt.deps.topology, clock=clock, answer=answer,
                                 max_turns=app.engine.max_intake_turns, on_event=on_event)

    result = await worker.run_once(case_id, interaction_policy="interactive")
    while result == "awaiting_human":
        current = repo.get(case_id)
        question = current.question or "(질문 없음)"
        try:
            answer = await ask(question)
        except EOFError:
            print(f"입력이 끊겼다 — 케이스 {case_id}는 파킹된 채로 남는다. "
                 f"'python -m src case resume {case_id} --answer <답변>'으로 나중에 재개할 수 있다.")
            return 0
        # 조사 재개도 answer_case를 거친다 — 조사 도중 접수 질문이 다시 뜰 수 있고,
        # 무엇보다 CLI와 계획 13의 API가 **같은 분기**를 써야 한다(규율 8).
        result = await answer_case(case_id, answer, repo=repo, store=store, deps=rt.deps,
                                   topology=rt.deps.topology, worker=worker, clock=clock,
                                   max_intake_turns=app.engine.max_intake_turns,
                                   interaction_policy="interactive", on_event=on_event)

    if result == "closed":
        path = Path(app.report.output_dir) / f"{case_id}.{app.report.format}"
        if path.exists():
            print(f"보고서: {path}")
        else:
            print(f"케이스 {case_id} 종결 — 보고서 발행 실패, 저장소를 직접 확인하라",
                 file=sys.stderr)
        return 0
    if result == "failed":
        print(f"케이스 {case_id} 조사 실패", file=sys.stderr)
        return 1
    print(f"케이스 {case_id} — 조사 결과: {result}")
    return 0


def _run_chat(args, env: dict, *, llm_factory=None) -> int:
    """접수 대화 CLI — 데몬을 띄우지 않고 사이트 하나(--gbm/--fct)에 대해 케이스를
    열고 대화형으로 조사·재개까지 한 프로세스 안에서 완주한다.

    발행(보고서·이벤트·메일)은 _build_publisher(C1/M4)가 조립한 (on_event,
    on_closed)를 그대로 워커에 넘긴다 — 데몬 없이도 _publish_report 동등
    경로를 이 CLI가 직접 타는 것이 목표라, `case resume`(_cmd_case_resume)과
    이 조립을 중복해 베끼지 않는다.
    """
    config_root = Path(args.config_root)
    repo_root = Path(args.repo_root)
    seeds, failed = _load_seeds(args)
    if failed:
        return 1
    clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
    try:
        app, sites = assemble_sites(config_root, repo_root, env, clock=clock,
                                    stub_seeds=seeds, llm_factory=llm_factory)
    except ConfigError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return 1
    if _seeds_mismatch(seeds, sites):
        return 1

    by_key = {(rt.gbm, rt.fct): rt for rt in sites}
    symptom = args.symptom or input("증상을 설명해 주세요: ")

    # 스코프→접근→개설→첫 접수 턴은 submit_case 하나다 — POST /cases와 같은 함수다
    # (규율 8: 순서를 각자 베끼면 언젠가 하나가 접근 검사를 빠뜨린다). 개설이 여기서
    # 일어나므로 저장소·발행 배선이 먼저 서 있어야 한다.
    p = build_persistence(app.store)
    store, repo, ledger, events = p.store, p.repo, p.ledger, p.events
    snapshots = p.snapshots
    checkpointer = build_checkpointer(app.store)
    for site_rt in sites:
        site_rt.deps.store = store    # daemon.py 모듈 docstring과 동일한 불변식
    on_event, on_closed = _build_publisher(app, sites, store, repo, ledger, events, checkpointer, clock)

    submitted = asyncio.run(submit_case(
        symptom, gbm=args.gbm, fct=args.fct, concern=args.concern,
        subject=getattr(args, "requested_by", None),
        sites={(s.gbm, s.fct): s.deps for s in sites}, access=app.access,
        repo=repo, store=store, clock=clock, on_event=on_event,
        max_intake_turns=app.engine.max_intake_turns))
    if submitted.status == "unresolved":
        for line in [*submitted.scope.problems, *submitted.scope.questions,
                     "--gbm/--fct로 사이트를 지정해 다시 실행하라"]:
            print(line, file=sys.stderr)
        return 1
    if submitted.status == "forbidden":
        subject = getattr(args, "requested_by", None)
        print(f"주체 {subject!r}는 {submitted.scope.gbm}/{submitted.scope.fct}에 접근할 수 없다 "
              f"(app.json의 access.allow)", file=sys.stderr)
        return 1
    rt = by_key[(submitted.scope.gbm, submitted.scope.fct)]

    def deps_for_site(gbm, fct):
        found = by_key.get((gbm, fct))
        return found.deps if found is not None else None

    def digests_for_site(gbm, fct):
        found = by_key.get((gbm, fct))
        return found.digests if found is not None else {}

    owner = f"chat-{socket.gethostname()}-{os.getpid()}"
    worker = InvestigationWorker(
        CaseQueue(), repo=repo, store=store, deps_for_site=deps_for_site,
        checkpointer=checkpointer, clock=clock, owner=owner,
        max_concurrent=app.investigations.max_concurrent,
        lease_ttl_s=app.investigations.lease_ttl_s, ledger=ledger,
        knowledge_digests_for_site=digests_for_site,
        max_wall_clock_s=app.investigations.max_wall_clock_s, snapshots=snapshots,
        max_intake_turns=app.engine.max_intake_turns,
        on_event=on_event, on_closed=on_closed)

    async def ask(question: str) -> str:
        return input(f"[질문] {question}\n> ")

    return asyncio.run(_drive_chat(args, rt, repo, store, worker, clock, ask, app,
                                   submitted.case_id, submitted.turn, on_event))


def main(argv=None) -> int:
    load_dotenv()
    env = os.environ

    parser = argparse.ArgumentParser(prog="python -m src")
    sub = parser.add_subparsers(dest="command", required=True)

    p_registry = sub.add_parser("registry", help="사이트 목록")
    _add_common(p_registry)

    p_config = sub.add_parser("config")
    config_sub = p_config.add_subparsers(dest="config_command", required=True)
    p_show = config_sub.add_parser("show", help="병합 config와 값의 출처")
    p_show.add_argument("--gbm", required=True)
    p_show.add_argument("--fct", required=True)
    _add_common(p_show)

    p_knowledge = sub.add_parser("knowledge")
    knowledge_sub = p_knowledge.add_subparsers(dest="knowledge_command", required=True)
    p_validate = knowledge_sub.add_parser("validate", help="기동 검증 단독 실행 (CI용)")
    p_validate.add_argument(
        "--stub-seeds", default=None,
        help="스텁 어댑터가 돌려줄 응답 파일. --live 드리프트 점검을 실제 접속 "
             "없이 예행할 때 쓴다(rest_openapi에 '지금 대상의 명세'를 심는다)")
    p_validate.add_argument("--live", action="store_true",
                            help="대상에 실제로 접속해 Mongo 계정 롤과 pinned 명세 드리프트까지 확인한다")
    _add_common(p_validate)

    p_patrol = sub.add_parser("patrol")
    patrol_sub = p_patrol.add_subparsers(dest="patrol_command", required=True)
    p_patrol_run = patrol_sub.add_parser(
        "run", help="기동 검증 → 사이트 조립 → 순찰 데몬 기동(포그라운드)")
    p_patrol_run.add_argument(
        "--for-seconds", type=float, default=None,
        help="N초 뒤 데몬을 내린다(스모크·개발용). 0이면 기동만 확인하고 즉시 내린다")
    _add_stub_seeds(p_patrol_run)
    _add_common(p_patrol_run)
    p_patrol_status = patrol_sub.add_parser(
        "status", help="하트비트와 사이트·점검별 최근 실행 요약. 메모리 백엔드는 안내만 한다")
    _add_common(p_patrol_status)

    p_api = sub.add_parser(
        "api", help="HTTP 표면 — 케이스를 쓰고 이벤트를 읽는다. 조사는 patrol run(워커)이 한다")
    p_api.add_argument("--host", default="127.0.0.1",
                       help="바인드 주소. TLS와 인증 회전은 리버스 프록시가 — 기본은 로컬만")
    p_api.add_argument("--port", type=int, default=8080)
    _add_common(p_api)

    p_case = sub.add_parser("case")
    case_sub = p_case.add_subparsers(dest="case_command", required=True)
    p_case_list = case_sub.add_parser("list", help="케이스 목록(기본: 전체 상태)")
    p_case_list.add_argument("--status", choices=_CASE_STATUSES, default=None)
    _add_common(p_case_list)
    p_case_show = case_sub.add_parser("show", help="케이스 레코드 + 판정 요약 + 증거 수")
    p_case_show.add_argument("case_id")
    p_case_show.add_argument("--report", action="store_true",
                             help="요약 대신 저장된 보고서를 다시 렌더해 stdout에 출력")
    _add_common(p_case_show)
    _case_resume_note = ("awaiting_human 케이스에 사람의 답변을 넣어 재개한다. v1은 데몬 프로세스와 "
                         "통신할 명령 채널이 없어, lease가 비어 있거나 만료된 경우에만 이 CLI가 "
                         "인라인으로 직접 조사를 재개한다 — 데몬이 lease를 쥐고 있으면 "
                         "'데몬이 실행 중 — 잠시 후 재시도' 안내와 함께 exit 2로 끝난다")
    p_case_label = case_sub.add_parser(
        "label", help="실제 원인을 되먹인다(append-only). --stats는 건수와 게이트 상태만 낸다")
    p_case_label.add_argument("case_id", nargs="?")
    p_case_label.add_argument("--agreement", choices=["correct", "partially_correct", "wrong", "unknown"])
    p_case_label.add_argument("--resolution",
                              choices=["fixed", "not_reproducible", "wont_fix", "false_positive"])
    p_case_label.add_argument("--actual-component", default=None)
    p_case_label.add_argument("--actual-verdict-type", default=None)
    p_case_label.add_argument("--saw-report", action="store_true",
                              help="보고서를 보고 라벨했다 — 앵커링 탐지용")
    p_case_label.add_argument("--by", default=None)
    p_case_label.add_argument("--stats", action="store_true", help="건수와 게이트 상태")
    _add_common(p_case_label)
    p_case_resume = case_sub.add_parser(
        "resume", help=_case_resume_note, description=_case_resume_note)
    p_case_resume.add_argument("case_id")
    p_case_resume.add_argument("--answer", required=True)
    p_case_resume.add_argument(
        "--requested-by", default=None,
        help="요청 주체 — access.allow가 비어 있지 않으면 필수. awaiting_human은 "
             "답변 텍스트가 리드 프롬프트에 직행하는 주입구다(스펙 §3.5)")
    _add_stub_seeds(p_case_resume)
    _add_common(p_case_resume)

    p_chat = sub.add_parser(
        "chat", help="접수 대화로 케이스를 열고 대화형(interactive)으로 조사를 완주한다")
    # 웹 사용자는 이 쌍을 주지 않는다 — 안 주면 증상에서 해석한다(계획 12).
    p_chat.add_argument("--gbm", default=None)
    p_chat.add_argument("--fct", default=None)
    p_chat.add_argument("--symptom", default=None, help="미지정이면 stdin으로 받는다")
    p_chat.add_argument(
        "--requested-by", default=None,
        help="요청 주체 — app.json의 access.allow가 비어 있지 않으면 필수다. "
             "레코드에 박제돼 감사의 근거가 된다")
    p_chat.add_argument(
        "--concern", choices=CONCERNS, default="system",
        help="무엇이 이상한가 — system(파이프라인 고장) 또는 operation(데이터는 "
             "흐르는데 현장 상태가 이상). 메일 수신자와 리드의 조사 방향을 가른다")
    _add_stub_seeds(p_chat)
    _add_common(p_chat)

    args = parser.parse_args(argv)
    config_root = Path(args.config_root)

    if args.command == "registry":
        try:
            registry = load_registry(config_root)
        except ConfigError as exc:
            for problem in exc.problems:
                print(problem, file=sys.stderr)
            return 1
        for site in registry.sites:
            flag = "enabled" if site.enabled else "disabled"
            print(f"{site.gbm}/{site.fct}  [{flag}]")
        return 0

    if args.command == "config":
        try:
            cfg, provenance = load_site_config(config_root, args.gbm, args.fct, env=env)
        except ConfigError as exc:
            for problem in exc.problems:
                print(problem, file=sys.stderr)
            return 1
        print(json.dumps(cfg.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2))
        print("\n# 출처")
        for path, source in sorted(provenance.items()):
            print(f"{path} = {source}")
        return 0

    if args.command == "knowledge":
        # --stub-seeds로 "지금 대상의 명세"를 심으면 실제 접속 없이 드리프트 점검을
        # 예행할 수 있다(스텁 어댑터 경로). 실운영에서는 플래그 없이 real 어댑터가
        # config의 openapi_path로 받아 온다.
        seeds = None
        if args.stub_seeds:
            seeds, seed_problems = load_stub_seeds(Path(args.stub_seeds))
            if seed_problems:
                for problem in seed_problems:
                    print(f"[stub-seeds] {problem}", file=sys.stderr)
                return 1
        errors = validate_boot(config_root, env=env, repo_root=Path(args.repo_root),
                               check_live=args.live, stub_seeds=seeds)
        if not errors:
            print("OK")
            return 0
        for e in errors:
            print(f"[{e.where}] {e.problem}", file=sys.stderr)
        return 1

    if args.command == "api":
        return _run_api(args, env)

    if args.command == "patrol":
        if args.patrol_command == "run":
            return _run_patrol(args, env)
        if args.patrol_command == "status":
            return _cmd_patrol_status(config_root, env)

    if args.command == "chat":
        return _run_chat(args, env)

    if args.command == "case":
        if args.case_command == "list":
            return _cmd_case_list(args, config_root, env)
        if args.case_command == "show":
            return _cmd_case_show(args, config_root, env)
        if args.case_command == "label":
            return _cmd_case_label(args, config_root, env)
        if args.case_command == "resume":
            return _cmd_case_resume(args, config_root, env)

    return 2


if __name__ == "__main__":
    sys.exit(main())
