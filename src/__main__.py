"""CLI 엔트리 — 계획 1: registry / config show / knowledge validate.
계획 4b: patrol run/status, case list/show/resume 추가. 계획 5에서 chat이 더해진다.
"""
import argparse
import asyncio
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.application.worker import CaseQueue, InvestigationWorker
from src.boot import validate_boot
from src.config.loader import ConfigError, load_app_config, load_registry, load_site_config
from src.infrastructure.checkpointer import build_checkpointer, build_persistence
from src.infrastructure.llm import build_chat_model
from src.patrol.daemon import PatrolDaemon, assemble_sites
from src.patrol.llm_judge import LlmBudget

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

    clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
    app, sites = assemble_sites(config_root, repo_root, env, clock=clock, llm_factory=llm_factory)
    store, repo, ledger = build_persistence(app.store)
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
    daemon = PatrolDaemon(app=app, sites=sites, store=store, repo=repo, ledger=ledger,
                          checkpointer=checkpointer, clock=clock, judge_llm=judge_llm,
                          budget=budget, owner=owner, timezone=app.timezone)
    asyncio.run(_drive_daemon(daemon, args.for_seconds))
    return 0


def _cmd_patrol_status(config_root: Path, env: dict) -> int:
    app = _load_app(config_root, env)
    if app is None:
        return 1
    if app.store.backend == "memory":
        print("메모리 백엔드 — 프로세스 간 상태 없음")
        return 0

    _store, _repo, ledger = build_persistence(app.store)
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
    _store, repo, _ledger = build_persistence(app.store)
    statuses = (args.status,) if args.status else _CASE_STATUSES
    records = [r for status in statuses for r in repo.list_by_status(status)]
    for r in records:
        print(f"{r.id}  {r.status}  {r.gbm}/{r.fct}  {r.symptom[:60]}")
    return 0


def _cmd_case_show(args, config_root: Path, env: dict) -> int:
    app = _load_app(config_root, env)
    if app is None:
        return 1
    store, repo, _ledger = build_persistence(app.store)
    try:
        record = repo.get(args.case_id)
    except KeyError:
        print(f"케이스 {args.case_id!r}를 찾을 수 없다", file=sys.stderr)
        return 1

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
    clock = lambda: datetime.now(timezone.utc)   # CLI 경계에서만 now()를 직접 부른다
    try:
        app, sites = assemble_sites(config_root, Path(args.repo_root), env, clock=clock)
    except ConfigError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return 1

    store, repo, ledger = build_persistence(app.store)
    try:
        record = repo.get(args.case_id)
    except KeyError:
        print(f"케이스 {args.case_id!r}를 찾을 수 없다", file=sys.stderr)
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
    worker = InvestigationWorker(
        CaseQueue(), repo=repo, store=store, deps_for_site=deps_for_site,
        checkpointer=checkpointer, clock=clock, owner=owner,
        max_concurrent=app.investigations.max_concurrent,
        lease_ttl_s=app.investigations.lease_ttl_s, ledger=ledger,
        knowledge_digests_for_site=digests_for_site)

    result = asyncio.run(worker.resume_once(args.case_id, args.answer))
    if result == "busy":
        # 위의 사전 점검과 실제 획득 사이의 경합(다른 프로세스가 그 사이 lease를 잡은 경우) —
        # resume_once 내부의 acquire_lease가 최종 결정권을 가지므로 여기서도 같은 exit 2로 맞춘다.
        print("데몬이 실행 중 — 잠시 후 재시도", file=sys.stderr)
        return 2
    print(f"재개 결과: {result}")
    return 0


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
    p_validate.add_argument("--live", action="store_true",
                            help="검사 8(Mongo readonly 롤)까지 live 접속으로 확인한다")
    _add_common(p_validate)

    p_patrol = sub.add_parser("patrol")
    patrol_sub = p_patrol.add_subparsers(dest="patrol_command", required=True)
    p_patrol_run = patrol_sub.add_parser(
        "run", help="기동 검증 → 사이트 조립 → 순찰 데몬 기동(포그라운드)")
    p_patrol_run.add_argument(
        "--for-seconds", type=float, default=None,
        help="N초 뒤 데몬을 내린다(스모크·개발용). 0이면 기동만 확인하고 즉시 내린다")
    _add_common(p_patrol_run)
    p_patrol_status = patrol_sub.add_parser(
        "status", help="하트비트와 사이트·점검별 최근 실행 요약. 메모리 백엔드는 안내만 한다")
    _add_common(p_patrol_status)

    p_case = sub.add_parser("case")
    case_sub = p_case.add_subparsers(dest="case_command", required=True)
    p_case_list = case_sub.add_parser("list", help="케이스 목록(기본: 전체 상태)")
    p_case_list.add_argument("--status", choices=_CASE_STATUSES, default=None)
    _add_common(p_case_list)
    p_case_show = case_sub.add_parser("show", help="케이스 레코드 + 판정 요약 + 증거 수")
    p_case_show.add_argument("case_id")
    _add_common(p_case_show)
    _case_resume_note = ("awaiting_human 케이스에 사람의 답변을 넣어 재개한다. v1은 데몬 프로세스와 "
                         "통신할 명령 채널이 없어, lease가 비어 있거나 만료된 경우에만 이 CLI가 "
                         "인라인으로 직접 조사를 재개한다 — 데몬이 lease를 쥐고 있으면 "
                         "'데몬이 실행 중 — 잠시 후 재시도' 안내와 함께 exit 2로 끝난다")
    p_case_resume = case_sub.add_parser(
        "resume", help=_case_resume_note, description=_case_resume_note)
    p_case_resume.add_argument("case_id")
    p_case_resume.add_argument("--answer", required=True)
    _add_common(p_case_resume)

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
        print(json.dumps(cfg.model_dump(mode="json"), ensure_ascii=False, indent=2))
        print("\n# 출처")
        for path, source in sorted(provenance.items()):
            print(f"{path} = {source}")
        return 0

    if args.command == "knowledge":
        errors = validate_boot(config_root, env=env, repo_root=Path(args.repo_root),
                               check_live=args.live)
        if not errors:
            print("OK")
            return 0
        for e in errors:
            print(f"[{e.where}] {e.problem}", file=sys.stderr)
        return 1

    if args.command == "patrol":
        if args.patrol_command == "run":
            return _run_patrol(args, env)
        if args.patrol_command == "status":
            return _cmd_patrol_status(config_root, env)

    if args.command == "case":
        if args.case_command == "list":
            return _cmd_case_list(args, config_root, env)
        if args.case_command == "show":
            return _cmd_case_show(args, config_root, env)
        if args.case_command == "resume":
            return _cmd_case_resume(args, config_root, env)

    return 2


if __name__ == "__main__":
    sys.exit(main())
