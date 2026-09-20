"""CLI — 시스템의 바깥 경계.

**여기가 `datetime.now()`를 직접 부르는 유일한 곳이다.** 안쪽은 전부 주입받은
`clock`을 쓴다(1단계 규율 ②). 진짜 시계는 여기서 한 번 만들어져 아래로 흐른다.
그 시계의 **시간대는 `app.json`이 정한다** — 기계의 시스템 TZ가 아니다(`_clock` 참고).

명령:
    python -m src boot                  기동 검증 — 설정이 온전한가
    python -m src sites                 registry의 사이트 목록
    python -m src config show           병합된 설정 + 값의 출처(비밀번호는 가려진다)
    python -m src doctor                네 시스템에 실제로 붙어 본다
    python -m src --gbm mx --fct gumi peek redis --key oee:L3
    python -m src peek redis --key oee:L3 --gbm mx --fct gumi   (뒤에 써도 된다)
    python -m src peek mongo  --collection oee --filter '{"line":"L3"}' --limit 5
    python -m src peek kafka  --topic topic1 --limit 5
    python -m src peek rest   --entry oee_summary --params '{"line":"L3"}'
    python -m src llm describe          무엇에 붙어 있는지(호출은 안 한다)
    python -m src llm ask "질문"         한 번 묻고 한 번 받는다
    python -m src llm check             간단한 질문 묶음 — 붙는가·한국어·JSON
    python -m src mail describe         누구에게 보내게 돼 있는지(발송 안 함)
    python -m src mail send --subject "연결 테스트" --dry-run   나갈 요청만 보여준다
    python -m src mail send --subject "연결 테스트"             실제로 보낸다
    python -m src report scenarios      리포트 시나리오 목록
    python -m src report window         집계 대상 날짜와 실제로 나갈 Mongo 필터
    python -m src report window --today 2026-09-07   그날 돌았다면 어떻게 되는가
    python -m src report aggregate                   실제로 읽어서 숫자를 낸다
    python -m src report aggregate --stub-seeds seeds.json   대상에 안 붙고 돌려 본다
    python -m src report render --out output/report.html     메일 본문 HTML을 만든다
    python -m src report prompt --gbm-only mx    LLM에게 나갈 프롬프트를 그대로 본다
    python -m src report run            집계→HTML→파일→메일. **스케줄에 거는 명령**
    python -m src report run --dry-run  나갈 메일만 보여주고 보내지 않는다
    python -m src schedule --list       무엇이 언제 도는지 (돌리지는 않는다)
    python -m src schedule              스케줄대로 계속 돈다 (상주 프로세스)
"""
import argparse
import asyncio
import itertools
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.boot import validate_boot
from src.config.loader import (ConfigError, load_app_config, load_prompt,
                               load_registry, load_scenarios, load_site_config)
from src.infrastructure.factory import build_adapters


def _out(obj) -> None:
    # ensure_ascii=False — 한글이 \uXXXX로 찍히면 사람이 못 읽는다.
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _load_env(env_file: Path) -> dict[str, str]:
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    return dict(os.environ)


def _resolve_site(config_root: Path, args, env: dict[str, str]):
    """registry에서 대상 사이트 하나를 고른다.

    - 둘 다 주면 그 조합. 한쪽만 줘도 **후보가 하나로 좁혀지면** 그것을 쓴다
      (사업부가 mx뿐이면 `--fct sevt`만으로 충분하다).
    - 아무것도 안 줬는데 활성 사이트가 하나면 그것.
    - 좁혀지지 않으면 **묻는다.** 임의로 첫 번째를 고르면 다른 법인의 Redis를
      들여다보게 되고, 그건 조용히 잘못된 답을 내는 형태다.
    """
    registry = load_registry(config_root)
    candidates = [e for e in registry.sites
                  if (not args.gbm or e.gbm == args.gbm)
                  and (not args.fct or e.fct == args.fct)]
    if not args.gbm and not args.fct:
        candidates = registry.active()

    if len(candidates) == 1:
        picked = candidates[0]
        return load_site_config(config_root, picked.gbm, picked.fct, env=env)

    asked = "/".join(filter(None, (args.gbm, args.fct)))
    known = ", ".join(str(e) for e in registry.sites) or "(registry가 비어 있다)"
    if not candidates:
        raise SystemExit(f"registry에 없는 사이트 — {asked}. 등록된 것: {known}")
    raise SystemExit(
        f"사이트가 여러 개다 — {', '.join(str(e) for e in candidates)}\n"
        f"  --gbm/--fct로 하나를 골라라. 예:\n"
        f"    python -m src --gbm {candidates[0].gbm} --fct {candidates[0].fct} "
        f"{args.command} ...\n"
        f"  하위 명령 뒤에 써도 된다:\n"
        f"    python -m src {args.command} ... --gbm {candidates[0].gbm} "
        f"--fct {candidates[0].fct}")


def _render(result) -> dict:
    """ProbeResult를 사람이 읽을 형태로. 실패도 같은 모양으로 보인다."""
    body = {
        "status": result.status,
        "source": result.source,
        "observed_at": result.envelope.observed_at.isoformat(),
    }
    if not result.envelope.complete:
        body["⚠ 잘림"] = result.envelope.truncated_reason
    if result.status == "error":
        body["error"] = result.error
    else:
        body["data"] = result.data
    return body


# ── 명령들 ────────────────────────────────────────────────────────────

def cmd_boot(args, env) -> int:
    errors = validate_boot(args.config_root, env=env, knowledge_root=_knowledge_root(args))
    if not errors:
        print(f"✅ 기동 검증 통과 — {args.config_root}")
        return 0
    print(f"❌ 문제 {len(errors)}건:", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)
    return 1


def cmd_sites(args, env) -> int:
    for entry in load_registry(args.config_root).sites:
        mark = "✅" if entry.enabled else "⏸ "
        try:
            site, _ = load_site_config(args.config_root, entry.gbm, entry.fct, env=env)
            systems = ", ".join(site.infra.model_dump(exclude_none=True))
            print(f"  {mark} {str(entry):<16} [{systems}]")
        except ConfigError as exc:
            print(f"  ❌ {str(entry):<16} {exc}", file=sys.stderr)
    return 0


def cmd_config_show(args, env) -> int:
    """병합된 최종 설정과, 각 값이 **어느 층에서 왔는지**를 보인다.

    층이 넷이면 "분명히 바꿨는데 안 먹는다"가 반드시 생기고, 그때 답은
    거의 항상 "아래 층이 덮고 있다"이다. 출처가 없으면 네 파일을 다 열어야 안다.
    """
    site, provenance = _resolve_site(args.config_root, args, env)
    # SecretStr은 model_dump에서도 가려진 채 나온다 — 실수로 찍어도 안 샌다.
    _out(json.loads(site.model_dump_json()))
    if not args.no_provenance:
        print("\n값의 출처 (어느 층이 이겼는가):")
        for path in sorted(provenance):
            print(f"  {path:<44} {provenance[path]}")
    return 0


async def _doctor(site, clock) -> int:
    adapters = build_adapters(site, clock=clock)
    print(f"대상: {site.site} — 붙어 있는 시스템: {', '.join(adapters.available())}\n")
    failed = 0
    try:
        if adapters.redis:
            result = await adapters.redis.scan("*")
            failed += _report("redis", result, lambda r: f"키 {len(r.data)}개 보임")
        if adapters.mongo:
            result = await adapters.mongo.count("__none__", {})
            failed += _report("mongo", result, lambda r: "인증·접속 정상")
        if adapters.kafka:
            groups = site.infra.kafka.consumer.group_ids
            if not groups:
                print("  kafka  ⏸  감시할 그룹이 없다 — group_ids가 비어 있다")
            for group in groups:
                result = await adapters.kafka.group_offsets(group)
                failed += _report("kafka", result,
                                  lambda r, g=group: f"{g} lag {r.data.get('total_lag', '?')}")
        if adapters.rest:
            entries = ", ".join(sorted(site.infra.rest.entries)) or "(등재 항목 없음)"
            print(f"  rest   ⏸  붙어 보지 않음 — 등재 항목: {entries}")
            print("         (peek rest --entry <이름> 으로 실제 호출)")
    finally:
        await adapters.close()
    return 1 if failed else 0


def _report(name: str, result, summarize) -> int:
    if result.status == "error":
        print(f"  {name:<6} ❌ {result.error}")
        return 1
    print(f"  {name:<6} ✅ {summarize(result)}")
    return 0


def cmd_doctor(args, env) -> int:
    site, _ = _resolve_site(args.config_root, args, env)
    return asyncio.run(_doctor(site, _clock(args, env)))


async def _peek(site, args, clock) -> int:
    seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8")) \
        if args.stub_seeds else None
    adapters = build_adapters(site, clock=clock, seeds=seeds)
    try:
        results = await _dispatch(adapters, site, args)
    finally:
        await adapters.close()
    for result in results:
        _out(_render(result))
    return 1 if any(r.status == "error" for r in results) else 0


async def _dispatch(adapters, site, args) -> list:
    """ProbeResult **리스트**를 돌려준다 — `--lag`이 그룹 여러 개를 볼 수 있다."""
    if args.system == "redis":
        if adapters.redis is None:
            raise SystemExit("이 사이트에 redis 설정이 없다")
        if args.scan:
            return [await adapters.redis.scan(args.scan)]
        if args.ttl:
            return [await adapters.redis.ttl(args.ttl)]
        if not args.key:
            raise SystemExit("--key / --scan / --ttl 중 하나가 필요하다")
        return [await adapters.redis.get(args.key)]

    if args.system == "mongo":
        if adapters.mongo is None:
            raise SystemExit("이 사이트에 mongodb 설정이 없다")
        if args.collections:
            return [await adapters.mongo.list_collections()]
        filter_ = json.loads(args.filter) if args.filter else {}
        if args.count:
            return [await adapters.mongo.count(args.collection, filter_)]
        return [await adapters.mongo.find(args.collection, filter_,
                                          sort=[(args.sort, -1)] if args.sort else None,
                                          limit=args.limit)]

    if args.system == "kafka":
        if adapters.kafka is None:
            raise SystemExit("이 사이트에 kafka 설정이 없다")
        if args.topics:
            return [await adapters.kafka.list_topics()]
        if args.lag:
            # --group을 안 주면 config의 **모든** 감시 그룹을 본다. 하나만 보게
            # 만들면 나머지가 밀려도 모른다(법인마다 서비스가 여러 개다).
            groups = [args.group] if args.group else site.infra.kafka.consumer.group_ids
            if not groups:
                raise SystemExit("감시할 그룹이 없다 — config의 group_ids가 비어 있다")
            return [await adapters.kafka.group_offsets(g) for g in groups]
        if not args.topic:
            raise SystemExit("--topic 또는 --lag 이 필요하다")
        return [await adapters.kafka.tail(args.topic, limit=args.limit)]

    # rest
    if adapters.rest is None:
        raise SystemExit("이 사이트에 rest 설정이 없다")
    if args.list:
        for name, entry in sorted(site.infra.rest.entries.items()):
            print(f"  {name:<24} {entry.method:<5} {entry.path}")
            for key, spec in entry.params.items():
                mark = "필수" if spec.required else "선택"
                print(f"  {'':<24}   {key:<14} {spec.type:<6} {mark}")
            if not entry.params:
                print(f"  {'':<24}   (파라미터 없음)")
        return []
    if not args.entry:
        raise SystemExit("--entry 또는 --list 가 필요하다")
    return [await adapters.rest.query(args.entry,
                                      json.loads(args.params) if args.params else {})]


def cmd_peek(args, env) -> int:
    site, _ = _resolve_site(args.config_root, args, env)
    return asyncio.run(_peek(site, args, _clock(args, env)))


# ── llm ──────────────────────────────────────────────────────────────

def _llm_config(args, env):
    app = load_app_config(args.config_root, env=env)
    if app.llm is None:
        raise SystemExit("app.json에 llm 설정이 없다 — STEPS/step-07-llm.md 참고")
    return app.llm


def cmd_llm_describe(args, env) -> int:
    print(" ", _llm_config(args, env).describe())
    return 0


def cmd_llm_ask(args, env) -> int:
    from src.infrastructure.llm_factory import build_llm

    llm = build_llm(_llm_config(args, env), clock=_clock(args, env))
    reply = asyncio.run(llm.ask(args.prompt))
    _out(json.loads(reply.model_dump_json()))
    return 1 if reply.status == "error" else 0


# 간단한 질문 묶음. **JSON 항목이 제일 중요하다** — 8단계의 노드들이 전부 LLM
# 응답을 JSON으로 파싱하므로, 모델이 그걸 못 하면 조사가 도중에 조용히 멈춘다.
_CHECKS = [
    ("붙는가", "Reply with exactly: pong", lambda t: bool(t.strip())),
    ("한국어", "한국어로 한 문장만: 설비 가동률이 낮아지는 흔한 원인 하나.",
     lambda t: any("\uac00" <= ch <= "\ud7a3" for ch in t)),
    ("JSON",
     '아래 형식의 JSON만 출력하라. 설명·코드펜스 없이 JSON 객체 하나만:\n'
     '{"verdict": "ok", "score": 1}',
     lambda t: _parses_as_json(t)),
]


def _parses_as_json(text: str) -> bool:
    """코드펜스를 벗겨서라도 파싱되는가 — 8단계가 쓸 관용 범위와 같게 본다."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned[4:] if cleaned.startswith("json") else cleaned
    try:
        return isinstance(json.loads(cleaned.strip()), dict)
    except ValueError:
        return False


def cmd_llm_check(args, env) -> int:
    from src.infrastructure.llm_factory import build_llm

    cfg = _llm_config(args, env)
    llm = build_llm(cfg, clock=_clock(args, env))
    print(f"  {cfg.describe()}\n")
    failed = 0
    reported = None
    for name, prompt, ok in _CHECKS:
        reply = asyncio.run(llm.ask(prompt))
        reported = reported or reply.reported_model
        if reply.status == "error":
            print(f"  {name:<8} ❌ {reply.error}")
            failed += 1
            continue
        mark = "✅" if ok(reply.text) else "⚠ "
        failed += 0 if ok(reply.text) else 1
        print(f"  {name:<8} {mark} ({reply.latency_s}s) {reply.text.strip()[:110]}")

    # 모델 확인은 **한 번만** 찍는다. 항목마다 같은 경고를 반복하면 읽는 사람이
    # 세 줄을 하나로 뭉뚱그려 넘기고, 그러면 진짜 경고도 같이 넘어간다.
    problem = cfg.reported_model_problem(reported)
    if problem:
        print(f"\n  ⚠  {problem}")
        failed += 1
    elif reported:
        print(f"\n  모델     ✅ {reported} (게이트웨이가 응답에 실어 준 이름)")
    else:
        print("\n  모델     ⏸  게이트웨이가 응답에 모델 이름을 안 싣는다 — 확인할 방법이 없다")
    return 1 if failed else 0


# ── mail ─────────────────────────────────────────────────────────────

_TEST_BODY = """이 메일은 운영 모니터링 에이전트의 발송 경로 확인용입니다.

- 보낸 것: `python -m src mail send`
- 수신자는 config의 mail.recipients가 정합니다(본문이 바꿀 수 없습니다).

받으셨다면 Agent API 연결이 정상입니다."""


def cmd_mail_describe(args, env) -> int:
    app = load_app_config(args.config_root, env=env)
    print(" ", app.mail.describe())
    for address in app.mail.recipients:
        print(f"    → {address}")
    return 0


def cmd_mail_send(args, env) -> int:
    from src.infrastructure.mail_factory import build_mail

    app = load_app_config(args.config_root, env=env)
    body = Path(args.file).read_text(encoding="utf-8") if args.file else (
        args.body or _TEST_BODY)
    sender = build_mail(app.mail, clock=_clock(args, env))
    subject = sender.full_subject(args.subject)

    if args.dry_run:
        # 보내기 **전에** 눈으로 확인할 수 있어야 한다 — 수신자가 맞는지,
        # 본문의 어느 줄이 무력화됐는지.
        if not app.mail.enabled:
            print("  mail.enabled=false — 실제 발송은 건너뛴다. 아래는 켰을 때 나갈 요청이다.\n")
        _out(sender.preview(subject, body))
        return 0

    result = asyncio.run(sender.send(subject, body))
    _out(json.loads(result.model_dump_json()))
    for warning in result.warnings:
        # 성공 판정이 HTTP 200뿐이므로, Agent가 낸 경고가 유일한 추가 신호다.
        print(f"\n  ⚠  Agent 경고 — {warning}", file=sys.stderr)
    if result.neutralized_lines:
        print(f"\n  ⚠  본문에서 필드 머리글처럼 보이는 줄 "
              f"{result.neutralized_lines}개를 인용 표시(| )로 무력화했다", file=sys.stderr)
    return 1 if result.status == "error" else 0


def _pick_scenario(args):
    """이름을 안 주면 **하나일 때만** 그것을 쓴다.

    `_resolve_site`와 같은 규율이다 — 여러 개 중 임의로 첫 번째를 고르면
    다른 리포트의 기간을 보고 "맞네" 하고 넘어간다.
    """
    scenarios = load_scenarios(args.config_root)
    if not scenarios:
        raise ConfigError(f"{args.config_root / 'scenarios'}에 시나리오가 없다 — "
                          f"그 아래에 <이름>.json을 두면 그 이름이 시나리오 이름이 된다")
    if args.scenario:
        if args.scenario not in scenarios:
            raise ConfigError(f"모르는 시나리오 — {args.scenario}. "
                              f"있는 것: {', '.join(scenarios)}")
        return args.scenario, scenarios[args.scenario]
    if len(scenarios) == 1:
        return next(iter(scenarios.items()))
    raise ConfigError(f"시나리오가 여럿이다 — --scenario로 하나를 골라라: "
                      f"{', '.join(scenarios)}")


def cmd_report_scenarios(args, env) -> int:
    scenarios = load_scenarios(args.config_root)
    if not scenarios:
        print(f"  {args.config_root / 'scenarios'}에 시나리오가 없다 — "
              f"그 아래에 <이름>.json을 두면 그 이름이 시나리오 이름이 된다")
        return 0
    for name, scenario in scenarios.items():
        mark = "on " if scenario.enabled else "off"
        print(f"  [{mark}] {name}  {scenario.title}  ({scenario.kind})")
        print(f"        읽는 곳: {scenario.source.collection}."
              f"{scenario.source.date_field}  형식 {scenario.source.date_format!r}")
        print(f"        기간   : 직전 {scenario.window.business_days} 평일"
              f"{' + 전주 동요일 비교' if scenario.window.compare_previous_week else ''}")
        for gbm in scenario.scope.gbms:
            print(f"        {gbm}: {', '.join(scenario.scope.sites_of(gbm))}")
    return 0


def cmd_report_window(args, env) -> int:
    from src.report.window import build_window, describe

    name, scenario = _pick_scenario(args)
    today = _report_today(args, env)
    if today is None:
        return 1
    print(f"  시나리오: {name}  ({scenario.title})\n")
    _out(describe(build_window(scenario.window, today=today), scenario.source))
    return 0


def _report_today(args, env) -> "date | None":
    """`--today`를 날짜로. 형식이 틀리면 None(호출부가 1을 돌려준다)."""
    if not args.today:
        return _clock(args, env)().date()
    try:
        return datetime.strptime(args.today, "%Y-%m-%d").date()
    except ValueError:
        print(f"❌ --today는 YYYY-MM-DD 형식이다 — {args.today!r}", file=sys.stderr)
        return None


def cmd_report_aggregate(args, env) -> int:
    from src.report.sheet import fact_sheet

    name, scenario = _pick_scenario(args)
    today = _report_today(args, env)
    if today is None:
        return 1
    args._today = today

    facts = asyncio.run(_collect_facts(args, env, scenario))
    print(f"  시나리오: {name}  ({scenario.title})")
    if not facts.complete:
        print("  ⚠  표본이 잘렸다 — 아래 건수는 전부 **하한**이다", file=sys.stderr)
    for outcome in facts.unavailable:
        print(f"  ⚠  {outcome.site}: {outcome.error or outcome.reason}", file=sys.stderr)
    print()
    _out(fact_sheet(facts))
    # 읽지 못한 법인이 있으면 종료 코드로도 말한다 — 스케줄러가 조용한 실패를
    # 알아챌 수 있는 유일한 신호다.
    return 1 if facts.unavailable else 0


async def _collect_facts(args, env, scenario):
    from src.report.collect import collect
    from src.report.window import build_window

    seeds = None
    if args.stub_seeds:
        seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8"))
    window = build_window(scenario.window, today=args._today)
    return await collect(scenario, config_root=args.config_root, env=env,
                         window=window, clock=_clock(args, env), seeds=seeds)


async def _comments(args, env, scenario, facts):
    """LLM 서술. **LLM이 없거나 죽어도 빈 튜플을 돌려주고 리포트는 나간다.**"""
    from src.report.comment import comment_on

    if not scenario.comment.enabled:
        return ()
    app = load_app_config(args.config_root, env=env)
    llm = None
    if app.llm is not None:
        from src.infrastructure.llm_factory import build_llm
        llm = build_llm(app.llm, clock=_clock(args, env))
    return await comment_on(facts, llm=llm, spec=scenario.comment,
                            template=load_prompt(args.config_root, scenario),
                            clock=_clock(args, env))


def cmd_report_prompt(args, env) -> int:
    """나갈 프롬프트를 그대로 찍는다.

    `report window`·`report aggregate`와 같은 성격의 검토 도구다. 사내 게이트웨이로
    무엇이 나가는지 **사람이 읽어 보고** 판단할 수 있어야 한다 — 프롬프트에 접속
    정보나 예상 밖의 데이터가 섞여 있으면 여기서 드러난다.
    """
    from src.report.comment import allowed_numbers, build_prompt, facts_block

    name, scenario = _pick_scenario(args)
    today = _report_today(args, env)
    if today is None:
        return 1
    args._today = today
    facts = asyncio.run(_collect_facts(args, env, scenario))

    try:
        template = load_prompt(args.config_root, scenario)
    except ConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    targets = [args.gbm_only] if args.gbm_only else list(facts.gbm_order())
    print(f"  시나리오: {name}  ({scenario.title})")
    print(f"  프롬프트: {scenario.comment.prompt_file}  "
          f"· 상한 {scenario.comment.max_chars}자  "
          f"· enabled={scenario.comment.enabled}\n")
    for gbm in targets:
        # `max_chars`를 넘겨야 실제로 나가는 것과 같은 글을 본다. 안 넘기면
        # `{max_chars}`가 그대로 찍혀서 "치환이 안 되네"를 검토 도구가 숨긴다.
        prompt = build_prompt(template, facts, gbm,
                              max_chars=scenario.comment.max_chars)
        allowed = sorted(allowed_numbers(facts_block(facts, gbm)))
        print("─" * 78)
        print(prompt)
        print("─" * 78)
        print(f"  {gbm}: {len(prompt):,}자 · 허용 숫자 {len(allowed)}개 "
              f"→ {', '.join(f'{v:g}' for v in allowed[:20])}"
              f"{' …' if len(allowed) > 20 else ''}\n")
    return 0


def cmd_report_render(args, env) -> int:
    from src.presentation.report_html import render
    from src.report.blocks import build_blocks

    name, scenario = _pick_scenario(args)
    today = _report_today(args, env)
    if today is None:
        return 1
    args._today = today

    facts = asyncio.run(_collect_facts(args, env, scenario))
    comments = asyncio.run(_comments(args, env, scenario, facts))
    blocks = build_blocks(facts, comments)
    html = render(blocks, title=scenario.title,
                  generated_at=_clock(args, env)().strftime("%Y-%m-%d %H:%M"))

    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")

    print(f"  시나리오: {name}  ({scenario.title})")
    print(f"  블록 {len(blocks)}개 · {len(html.encode('utf-8')):,}바이트 "
          f"→ {destination}")
    for block in blocks:
        mark = "·" if block.has_content else "○"
        rows = len(block.table.rows) if block.table else 0
        print(f"    {mark} {block.key:10} {block.title or '(머리말)':14} "
              f"{'행 ' + str(rows) if rows else ''}")
    for comment in comments:
        if comment.failed:
            print(f"  ⚠  코멘트 {comment.gbm}: {comment.status} — {comment.reason}",
                  file=sys.stderr)
    for outcome in facts.unavailable:
        print(f"  ⚠  {outcome.site}: {outcome.error or outcome.reason}", file=sys.stderr)
    if not facts.complete:
        print("  ⚠  표본이 잘렸다 — 본문 숫자는 하한이다", file=sys.stderr)
    return 1 if facts.unavailable else 0


# ── patrol ───────────────────────────────────────────────────────────

def cmd_patrol_probe(args, env) -> int:
    """점검이 **무엇을 읽는가**를 실제로 읽어서 보여 준다. 판정은 안 한다(5단계).

    rule을 쓰기 전에 응답 실물을 봐야 한다 — 필드 이름 하나가 틀리면 판정이 조용히
    엉뚱해진다(`caution`을 `cuation`으로 적은 응답을 실제로 만났다).
    """
    from src.patrol.probes import run_probes

    site, _ = _resolve_site(args.config_root, args, env)
    clock = _clock(args, env)
    seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8")) \
        if args.stub_seeds else None

    checks = site.patrol.active()
    if args.check:
        if args.check not in site.patrol.checks:
            raise SystemExit(f"모르는 점검 — {args.check}. "
                             f"있는 것: {', '.join(sorted(site.patrol.checks)) or '(없음)'}")
        checks = {args.check: site.patrol.checks[args.check]}
    if not checks:
        print("  활성 점검이 없다 — config의 patrol.checks를 보라", file=sys.stderr)
        return 1

    async def go():
        adapters = build_adapters(site, clock=clock, seeds=seeds)
        try:
            return [await run_probes(name, check, adapters=adapters,
                                     site=str(site.site), clock=clock)
                    for name, check in checks.items()]
        finally:
            await adapters.close()

    probe_sets = asyncio.run(go())
    for probes in probe_sets:
        check = checks[probes.check]
        mark = "✅" if probes.status == "ok" else "⚠"
        print(f"{mark} {probes.site}  {probes.check} [{check.concern}] — {probes.reason()}")
        for name, result in probes.results.items():
            print(f"    {name}: {result.source}")
            _out(_render(result))
    # 하나라도 못 읽었으면 0을 주지 않는다 — 판정할 수 없는 것을 "이상 없음"으로
    # 접으면 감시가 자기 실패를 숨긴다.
    return 0 if all(p.status == "ok" for p in probe_sets) else 1


def cmd_patrol_check(args, env) -> int:
    """점검을 돌려 **지금 무엇이 걸리는가**를 보여 준다.

    N회 연속이나 케이스 개설은 안 한다(6단계) — 여기까지가 "이상인가"다.
    """
    from src.patrol.runner import run_sites

    clock = _clock(args, env)
    seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8")) \
        if args.stub_seeds else None

    if args.all_sites:
        entries = load_registry(args.config_root).active()
    else:
        site, _ = _resolve_site(args.config_root, args, env)
        entries = [site.site]

    def load(entry):
        cfg, _ = load_site_config(args.config_root, entry.gbm, entry.fct, env=env)
        return cfg

    outcomes = asyncio.run(run_sites(
        entries, load=load,
        build=lambda cfg: build_adapters(cfg, clock=clock, seeds=seeds),
        clock=clock, only=args.check))

    if not outcomes:
        print("  활성 점검이 없다 — config의 patrol.checks를 보라", file=sys.stderr)
        return 1

    MARK = {"ok": "✅", "finding": "❌", "skipped": "⬜", "unreachable": "⚠"}
    for outcome in outcomes:
        print(f"{MARK[outcome.status]} {outcome.site}  {outcome.check} "
              f"[{outcome.concern}] — {outcome.reason}")
        for item in outcome.findings:
            observed = json.dumps(item.observed, ensure_ascii=False) if item.observed else ""
            print(f"    {item.target}  {item.reason}  {observed}")

    findings = sum(len(o.findings) for o in outcomes)
    unreachable = [o for o in outcomes if o.status == "unreachable"]
    if findings or unreachable:
        print(f"\n  finding {findings}건 · 판정 못 한 점검 {len(unreachable)}개",
              file=sys.stderr)
        return 1
    return 0


def _case_repo(args, env):
    from src.infrastructure.case_store_file import FileCaseRepository
    return FileCaseRepository(Path(load_app_config(args.config_root, env=env).case_store))


def cmd_patrol_list(args, env) -> int:
    """어느 점검이 **어느 주기로** 도는가. 돌리지는 않는다."""
    site, _ = _resolve_site(args.config_root, args, env)
    checks = site.patrol.checks
    if not checks:
        print("  선언된 점검이 없다 — config의 patrol.checks를 보라", file=sys.stderr)
        return 1
    for name, check in sorted(checks.items()):
        mark = "✅" if check.enabled else "⬜"
        hours = check.interval_minutes / 60
        every = (f"{check.interval_minutes}분" if check.interval_minutes < 60
                 else f"{hours:g}시간")
        print(f"  {mark} {name} [{check.concern}] — {every}마다 · "
              f"rule={check.rule} · 프로브 {len(check.probes)}개")
    # 아직 이 주기로 도는 것은 없다(6b). 값이 config에 있고 확인만 된다.
    print("\n  (아직 스케줄에 올라가 있지 않다 — 6b에서 붙는다)", file=sys.stderr)
    return 0


def cmd_patrol_open(args, env) -> int:
    """점검을 돌리고, 걸린 것을 케이스로 만든다(또는 이미 있는 케이스에 첨부)."""
    from src.patrol.gate import process
    from src.patrol.runner import run_sites

    clock = _clock(args, env)
    seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8")) \
        if args.stub_seeds else None

    if args.all_sites:
        entries = load_registry(args.config_root).active()
    else:
        site, _ = _resolve_site(args.config_root, args, env)
        entries = [site.site]

    outcomes = asyncio.run(run_sites(
        entries,
        load=lambda e: load_site_config(args.config_root, e.gbm, e.fct, env=env)[0],
        build=lambda cfg: build_adapters(cfg, clock=clock, seeds=seeds),
        clock=clock, only=args.check))

    repo = _DryRunRepo(_case_repo(args, env)) if args.dry_run else _case_repo(args, env)
    MARK = {"opened": "🆕", "attached": "↻ ", "suppressed": "⬛", "rejected": "❌"}
    results = []
    for outcome in outcomes:
        if outcome.status in ("skipped", "unreachable"):
            print(f"⚠ {outcome.site}  {outcome.check} — {outcome.reason}")
            continue
        for result in process(outcome, repo=repo, clock=clock):
            results.append(result)
            print(f"{MARK[result.action]} {result.case_id or '-':6} {outcome.site}  "
                  f"{result.target}  {result.reason}")
    if not results:
        print("  케이스로 만들 것이 없다")
    if args.dry_run:
        print("\n  (--dry-run — 저장하지 않았다)", file=sys.stderr)
    return 1 if any(r.action == "rejected" for r in results) else 0


class _DryRunRepo:
    """읽기는 진짜 저장소에서, 쓰기는 버린다.

    쓰기만 막으면 `next_id`가 진짜 저장소를 증가시킨다 — dry-run을 돌릴 때마다
    케이스 번호가 건너뛴다. 사람이 "c-5가 어디 갔지"를 묻게 되는 자리다.
    """

    def __init__(self, inner):
        self._inner = inner
        self._fake = 0

    def latest(self, key):
        return self._inner.latest(key)

    def all(self):
        return self._inner.all()

    def add(self, record):
        pass

    def update(self, record):
        pass

    def next_id(self) -> str:
        self._fake += 1
        return f"(새 케이스 {self._fake})"


def _load_lead_prompt(config_root: Path, relative: str, *, slots: frozenset) -> str:
    """리드 프롬프트 하나. **자리가 안 맞으면 기동을 막는다.**

    두 방향 다 조용히 틀린다:

    - `{actions}`가 **없으면** LLM은 무엇을 부를 수 있는지 모르고 있지도 않은 것을
      계속 지어낸다. 증상은 "조사가 항상 빈손"이고 원인은 안 보인다.
    - 오타 난 `{max_round}`처럼 **우리가 안 채우는 자리**가 있으면 그 `{...}`가
      치환되지 않은 채 LLM에게 나간다. 9e에서 `{max_chars}`가 실제로 그랬고,
      리포트는 정상으로 보여서 아무도 못 봤다.

    기동 검증 철학(`boot.py`)대로 **발견한 것을 전부 모아서** 한 번에 알린다.
    """
    from src.application.lead import slots_in

    path = config_root / relative
    if not path.exists():
        raise SystemExit(f"프롬프트 파일이 없다 — {path}")
    text = path.read_text(encoding="utf-8")

    # `{example}`도 필수다. 사내 모델은 **예시의 틀을 채우는** 식으로 답하므로,
    # 예시가 없으면 베낄 것이 없어 형식이 매번 달라진다.
    problems = [f"{{{name}}} 자리가 없다 — 리드가 그 재료를 못 받는다"
                for name in ("case", "actions", "example") if f"{{{name}}}" not in text]
    problems += [f"{{{name}}}는 우리가 채우지 않는 자리다 — 그대로 LLM에게 나간다. "
                 f"쓸 수 있는 것: {', '.join(sorted(slots))}"
                 for name in sorted(slots_in(text) - slots)]
    if problems:
        raise SystemExit(f"{relative}:\n  " + "\n  ".join(problems))
    return text


def _make_tracer(case_id: str, *, folder: Path):
    """리드에게 **무엇을 물었고 무엇이 왔는지**를 그대로 파일에 남긴다.

    왜 필요한가: 10b를 끝낼 때 이게 없었고, 그래서 프롬프트 설계가 전부 추측 위에
    있었다. 사내에서 한 번 돌려 보고서야 "모델이 예시를 그대로 베낀다"는 것을 알았고,
    그건 **최종 State만 봐서는 절대 안 보이는 사실**이었다(파싱된 결과는 멀쩡해 보인다).

    날것 응답에는 대상 데이터가 실릴 수 있다. `output/`은 `.gitignore`에 있으므로
    리포에 들어가지 않는다 — 옮길 때도 이 폴더는 빼라.
    """
    folder.mkdir(parents=True, exist_ok=True)
    seq = itertools.count(1)
    written: list[Path] = []

    def trace(node: str, round_no: int, prompt: str, text, error) -> None:
        path = folder / f"{next(seq):02d}-r{round_no}-{node}.md"
        verdict = "읽었다" if error is None else f"**못 읽었다** — {error}"
        path.write_text(
            f"# {case_id} · {node} · 라운드 {round_no}\n\n"
            f"결과: {verdict}\n\n"
            f"## 물어본 것 ({len(prompt):,}자)\n\n````\n{prompt}\n````\n\n"
            f"## 날것 응답\n\n````\n{'(응답 없음 — 호출 자체가 실패했다)' if text is None else text}\n````\n",
            encoding="utf-8")
        written.append(path)

    return trace, written


def cmd_case_investigate(args, env) -> int:
    """케이스 하나를 **리드 LLM으로** 조사한다.

    판정은 아직 없다(12a) — 여기까지는 "무엇을 볼지 LLM이 정하고, 보고, 다시
    정한다"이다.
    """
    from src.application import briefing
    from src.application.diagnose import diagnose
    from src.application.graph import build_engine
    from src.application.lead import make_lead
    from src.application.nodes import EngineDeps
    from src.application.runner_probe import ProbeRunner
    from src.application.state import CaseState
    from src.domain.case import Case
    from src.infrastructure.llm_factory import build_llm

    app = load_app_config(args.config_root, env=env)
    if app.llm is None:
        raise SystemExit("app.json에 llm 설정이 없다 — STEPS/step-07-llm.md 참고")

    found = [c for c in _case_repo(args, env).all() if c.id == args.case_id]
    if not found:
        raise SystemExit(f"없는 케이스 — {args.case_id}")
    record = found[0]

    gbm, fct = record.site.split("/", 1)
    site, _ = load_site_config(args.config_root, gbm, fct, env=env)
    clock = _clock(args, env)
    seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8")) \
        if args.stub_seeds else None
    prompts = {"frame": _load_lead_prompt(args.config_root, app.investigation.frame_prompt,
                                         slots=briefing.FRAME_SLOTS),
               "integrate": _load_lead_prompt(args.config_root,
                                              app.investigation.integrate_prompt,
                                              slots=briefing.INTEGRATE_SLOTS)}

    built = {}
    tracer, traced = (None, [])
    if args.trace:
        tracer, traced = _make_tracer(record.id, folder=Path(args.trace) / record.id)

    async def go() -> dict:
        llm = build_llm(app.llm, clock=clock)
        built["llm"] = llm.describe()     # config가 뭐라고 적혔는지가 아니라 실제로 붙은 것
        adapters = build_adapters(site, clock=clock, seeds=seeds)
        try:
            frame, integrate = make_lead(
                llm, site_config=site, prompts=prompts,
                max_rounds=app.investigation.max_rounds,
                evidence_budget=app.investigation.evidence_total_chars,
                trace=tracer)
            deps = EngineDeps(runner=ProbeRunner(
                adapters, clock=clock,
                detail_chars=app.investigation.evidence_chars),
                              frame=frame, integrate=integrate,
                              max_rounds=app.investigation.max_rounds,
                              parallel_width=app.investigation.parallel_width,
                              max_tasks=app.investigation.max_tasks)
            state = CaseState(case=Case(
                id=record.id, gbm=gbm, fct=fct, origin="patrol",
                symptom=record.symptom, t0=record.opened_at))
            return await build_engine(deps).ainvoke(state)
        finally:
            await adapters.close()

    final = asyncio.run(go())
    print(f"  {record.site}  {record.id} — {record.symptom}")
    print(f"  {built['llm']}")
    print(f"  라운드 {final['round']} — 끝난 이유: {final['stopped_by']}\n")

    if final["hypotheses"]:
        print("  가설")
        for h in final["hypotheses"]:
            cited = " ".join(h.supporting_ids + h.refuting_ids)
            print(f"    {h.id} [{h.status}] {h.statement}"
                  + (f"  ({cited})" if cited else ""))
    print("\n  태스크" + ("" if final["plan_tasks"] else " — 없다(계획이 안 세워졌다)"))
    for task in final["plan_tasks"]:
        mark = {"ok": "✅", "error": "❌", "pending": "⬜", "running": "…"}.get(task.status, "?")
        detail = task.error or task.result_summary or "(실행 안 됨)"
        print(f"    {mark} {task.id} {task.goal} — {detail}")
    if final["evidence"]:
        print(f"\n  증거 {len(final['evidence'])}건")
        for ref in final["evidence"]:
            cut = "" if ref.complete else "  ⚠ 잘림"
            print(f"    {ref.id}  {ref.source}{cut}")
    # 두 가지는 **등급이 다르다.** 조사가 아예 못 돈 것(`llm_error`)과, 돌긴 했는데
    # 리드가 없는 증거를 인용한 것은 같은 사실이 아니다. 둘 다 시끄럽게 알리되
    # 종료 코드는 전자에만 준다 — 후자까지 1로 주면 "빨간불이 원래 그렇다"가 되고,
    # 그러면 진짜 빨간불도 안 보이게 된다.
    report = diagnose(CaseState.model_validate(final))
    print("\n" + "\n".join(report))
    if traced:
        # 진단도 파일로 남긴다 — 사람이 터미널에서 옮겨 적지 않아도 되게.
        summary = traced[0].parent / "summary.md"
        summary.write_text("# " + record.id + "\n\n```\n"
                           + "\n".join(report) + "\n```\n", encoding="utf-8")
        print(f"\n  트레이스 {len(traced)}건 · 진단 {summary} — {traced[0].parent}")

    broken = final["stopped_by"] == "llm_error"
    if final["llm_errors"]:
        print(f"\n  ⚠ LLM 오류 {len(final['llm_errors'])}건 — "
              + ("**이 조사는 안 돌았다**" if broken else "**리드가 계약을 어겼다**"),
              file=sys.stderr)
        for problem in final["llm_errors"]:
            print(f"    {problem}", file=sys.stderr)
    # 조용히 성공한 척하면 아무도 안 본다.
    return 1 if broken else 0


# ── 대상 코드 (11a) ────────────────────────────────────────────────

def _knowledge_root(args) -> Path:
    """지식은 **config 트리 옆에** 산다. 같이 옮겨 다녀야 짝이 안 어긋난다."""
    return args.knowledge_root or args.config_root.parent / "knowledge"


def _repos_of(args, env) -> tuple[str, list]:
    """이 GBM의 레포 목록. 코드는 **GBM 단위로 같다** — 사이트마다 안 다르다.

    그래도 사이트를 골라 읽는 이유: 레포 선언이 `gbm/{gbm}.json`에 있어도 층 병합을
    타므로, 병합된 결과를 봐야 "이 사이트에서 실제로 무엇이 보이는가"가 나온다.
    """
    site, _ = _resolve_site(args.config_root, args, env)
    return site.site.gbm, list(site.code.repos)


def cmd_code_status(args, env) -> int:
    """**네트워크를 안 탄다.** 어디서든 돈다 — 진단 전용이다(decisions ⑤).

    보는 넷: 경로가 있나 · `.git`이 있나 · `origin`이 config와 같나 ·
    배포가 가리키는 커밋이 로컬에 실재하나.
    """
    from src.knowledge.checkout import has_commit, missing_paths, plan_for, status_of
    from src.knowledge.loader import load_deployment, load_topology

    site, _ = _resolve_site(args.config_root, args, env)
    gbm, site_fct, repos = site.site.gbm, site.site.fct, list(site.code.repos)
    if not repos:
        print(f"  {gbm}: config에 target 코드 레포가 없다 — code.repos를 적어라")
        return 1

    try:
        root = _knowledge_root(args)
        topology = load_topology(root, gbm)
        deployment = load_deployment(root, gbm)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"  지식 층을 읽을 수 없다 — {exc}", file=sys.stderr)
        return 1

    print(f"  {gbm} — 레포 {len(repos)}개 · 서비스 {len(topology.services)}개")
    bad = 0
    for repo in repos:
        state = status_of(repo)
        mark = "✅" if state.ready else "❌"
        print(f"\n  {mark} {repo.name}  {repo.path}")
        if state.origin:
            print(f"       origin {state.origin}")
        for problem in state.problems:
            print(f"       ⚠ {problem}")
        if not state.ready:
            bad += 1
            for line in plan_for(repo, state):
                print(f"       → {line}")
            continue
        # 배포가 가리키는 커밋이 실재하는가(⑤-4).
        for name, service in sorted(topology.services.items()):
            if service.repo != repo.name:
                continue
            pin = deployment.pin_for(name, fct=site_fct)
            here = has_commit(repo, pin.commit)
            note = "" if pin.how == "declared" else "  (가정)"
            when = f"  배포 {pin.deployed_at}" if pin.deployed_at else ""
            print(f"       {'✅' if here else '❌'} {name} @ {pin.commit}{note}{when}")
            if not here:
                bad += 1
                print(f"       → git -C {repo.path} fetch --all --prune")
                continue
            # 이름이 사는 곳이 실재하는가. 틀리면 리드는 아무것도 못 찾고,
            # 증상은 "조사가 빈손"이라 원인이 안 보인다.
            gone = missing_paths(repo, pin.commit, topology.config_paths)
            if gone:
                bad += 1
                print(f"       ⚠ config_paths가 그 커밋에 없다 — {', '.join(gone)}")
                print(f"       → knowledge/topology/{gbm}.json의 config_paths를 고쳐라")
    return 1 if bad else 0


def cmd_code_plan(args, env) -> int:
    """사람이 직접 칠 git 명령을 출력한다. **토큰은 안 찍는다.**"""
    from src.knowledge.checkout import plan_for, status_of

    gbm, repos = _repos_of(args, env)
    print(f"  # {gbm} — 아래를 직접 실행하라 (이 명령은 네트워크를 안 탄다)")
    for repo in repos:
        for line in plan_for(repo, status_of(repo)):
            print(f"  {line}")
    return 0


def cmd_code_sync(args, env) -> int:
    """**여기서만 네트워크를 탄다.** 사내 밖에서는 실패하고, `code plan`을 안내한다."""
    from src.knowledge.checkout import status_of, sync

    gbm, repos = _repos_of(args, env)
    failed = 0
    for repo in repos:
        outcome, detail = sync(repo, status_of(repo))
        mark = {"cloned": "🆕", "fetched": "✅", "skipped": "⏭", "failed": "❌"}[outcome]
        print(f"  {mark} {repo.name}  {outcome} — {detail}")
        if outcome == "failed":
            failed += 1
    if failed:
        print("\n  붙을 수 없으면 `python -m src code plan`이 직접 칠 명령을 알려 준다",
              file=sys.stderr)
    return 1 if failed else 0


def cmd_case_list(args, env) -> int:
    repo = _case_repo(args, env)
    now = _clock(args, env)()
    cases = [c for c in repo.all() if args.all or c.status == "open"]
    if not cases:
        print("  케이스가 없다")
        return 0
    for case in cases:
        mark = "🔴" if case.status == "open" else "⚪"
        print(f"  {mark} {case.id:6} {case.site:10} {case.target:24} "
              f"{case.observations}회 · {case.sustained_for(now)}")
        print(f"        {case.symptom}")
    return 0


def cmd_case_show(args, env) -> int:
    found = [c for c in _case_repo(args, env).all() if c.id == args.case_id]
    if not found:
        raise SystemExit(f"없는 케이스 — {args.case_id}")
    _out(json.loads(found[0].model_dump_json()))
    return 0


# ── case ─────────────────────────────────────────────────────────────

def cmd_case_dryrun(args, env) -> int:
    """**LLM 없이** 조사 루프를 한 번 돌려 본다.

    대본 파일이 `frame`·`integrate` 자리를 대신한다. 보는 것은 조사의 내용이 아니라
    **울타리**다 — 라운드가 상한에서 멈추는가, 한 라운드에 병렬 폭만큼만 도는가,
    입력 증거가 없는 태스크가 걸러지는가.
    """
    from src.application.dryrun import build_deps, initial_state, load_script
    from src.application.graph import build_engine
    from src.application.runner_probe import ProbeRunner

    site, _ = _resolve_site(args.config_root, args, env)
    app = load_app_config(args.config_root, env=env)
    script = load_script(Path(args.plan))
    clock = _clock(args, env)
    seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8")) \
        if args.stub_seeds else None

    async def go() -> dict:
        adapters = build_adapters(site, clock=clock, seeds=seeds)
        try:
            deps = build_deps(script, runner=ProbeRunner(adapters, clock=clock),
                              investigation=app.investigation)
            engine = build_engine(deps)
            state = initial_state(script, case_id=args.case_id, gbm=site.site.gbm,
                                  fct=site.site.fct, clock=clock)
            return await engine.ainvoke(state)
        finally:
            await adapters.close()

    final = asyncio.run(go())
    cfg = app.investigation
    print(f"  {site.site} — {final['case'].symptom}")
    print(f"  울타리: max_rounds={cfg.max_rounds} parallel_width={cfg.parallel_width} "
          f"max_tasks={cfg.max_tasks}")
    print(f"  라운드 {final['round']} — 끝난 이유: {final['stopped_by']}")
    for task in final["plan_tasks"]:
        mark = {"ok": "✅", "error": "❌", "pending": "⬜", "running": "…"}.get(task.status, "?")
        detail = task.error or task.result_summary or "(실행 안 됨)"
        print(f"    {mark} {task.id} [{task.role}] {task.goal} — {detail}")
    if final["evidence"]:
        print(f"  증거 {len(final['evidence'])}건")
        for ref in final["evidence"]:
            partial = "" if ref.complete else "  ⚠ 표본이 잘렸다"
            print(f"    {ref.id}  {ref.source}{partial}")
    # 실행된 태스크가 하나도 없으면 배선을 확인해 볼 일이다 — 0을 주면 그게 묻힌다.
    return 0 if any(t.status == "ok" for t in final["plan_tasks"]) else 1


# ── schedule ─────────────────────────────────────────────────────────

def cmd_schedule(args, env) -> int:
    """**항상 떠 있으면서 스스로 시간을 재는 프로세스.**

    바깥 스케줄러(cron·작업 스케줄러·CronJob)에 거는 것과 다른 선택이다. 대신
    "언제 도는가"가 config에 있어서 git에 남고, 배포 환경이 바뀌어도 같은 파일을 본다.
    """
    from src.schedule.loop import install_fast_loop
    from src.schedule.runner import Fire, Job, run_all

    scenarios = load_scenarios(args.config_root)
    if args.scenario:
        if args.scenario not in scenarios:
            raise ConfigError(f"모르는 시나리오 — {args.scenario}. "
                              f"있는 것: {', '.join(scenarios)}")
        scenarios = {args.scenario: scenarios[args.scenario]}

    clock = _clock(args, env)
    wanted = {name: sc for name, sc in scenarios.items()
              if sc.enabled and sc.schedule.enabled}
    print(f"  시계: {clock():%Y-%m-%d %H:%M:%S %Z} "
          f"({load_app_config(args.config_root, env=env).timezone})")
    for name, scenario in scenarios.items():
        mark = "on " if name in wanted else "off"
        print(f"  [{mark}] {name:14} {scenario.schedule.describe()}")
    if not wanted:
        print("\n  돌릴 것이 없다 — 시나리오나 schedule이 전부 꺼져 있다",
              file=sys.stderr)
        return 1

    if args.list:
        _print_next_fires(wanted, clock=clock, count=args.list_count)
        return 0

    print(f"  루프: {install_fast_loop()}")
    print(f"  {'(dry-run — 메일을 보내지 않는다)' if args.dry_run else ''}\n"
          f"  Ctrl+C로 멈춘다.\n")
    return asyncio.run(_schedule_forever(wanted, args, env, clock))


def _print_next_fires(scenarios, *, clock, count: int) -> None:
    """언제 도는지 **눈으로 확인**할 수 있어야 한다. cron 식은 사람이 자주 틀린다."""
    now = clock()
    print()
    for name, scenario in scenarios.items():
        moment, anchor = now, now
        print(f"  {name} ({scenario.schedule.describe()})")
        for _ in range(count):
            moment = scenario.schedule.next_after(moment, anchor=anchor)
            gap = moment - now
            print(f"    {moment:%Y-%m-%d(%a) %H:%M}  (지금부터 {_gap(gap)})")
        print()


def _gap(delta) -> str:
    total = int(delta.total_seconds())
    if total < 3600:
        return f"{total // 60}분"
    if total < 86400:
        return f"{total // 3600}시간 {total % 3600 // 60}분"
    return f"{total // 86400}일 {total % 86400 // 3600}시간"


async def _schedule_forever(scenarios, args, env, clock) -> int:
    from src.schedule.runner import Job, run_all

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    def announce(fire) -> None:
        mark = "✅" if fire.ok else "❌"
        print(f"  {fire.started:%Y-%m-%d %H:%M:%S} {mark} {fire.job} — {fire.detail}"
              f"  ({fire.took_seconds:.1f}초"
              f"{f', {fire.late_seconds:.0f}초 늦게 시작' if fire.late_seconds > 30 else ''})",
              flush=True)          # 로그로 넘길 때 버퍼에 갇히지 않게

    jobs = [Job(name=name, spec=scenario.schedule,
                run=_job_for(name, scenario, args, env, clock))
            for name, scenario in scenarios.items()]
    fires = await run_all(jobs, clock=clock, stop=stop, on_fire=announce,
                          max_fires=args.max_runs)

    failed = sum(1 for fire in fires if not fire.ok)
    print(f"\n  멈춘다 — 발사 {len(fires)}회, 실패 {failed}회")
    # 한 번이라도 실패했으면 0이 아니다. 프로세스가 조용히 사라지는 것과, 실패를
    # 안고 끝난 것을 바깥(파드 재시작 정책·감시)이 구별할 수 있어야 한다.
    return 1 if failed else 0


def _job_for(name, scenario, args, env, clock):
    """한 시나리오를 한 번 돌리는 코루틴. **던지지 않는 것이 계약**이다."""
    from src.presentation.report_html import render
    from src.report.blocks import build_blocks
    from src.report.publish import publish

    async def run() -> tuple[bool, str]:
        today = clock().date()
        facts = await _collect_facts_at(args, env, scenario, today)
        comments = await _comments(args, env, scenario, facts)
        html = render(build_blocks(facts, comments), title=scenario.title,
                      generated_at=clock().strftime("%Y-%m-%d %H:%M"))

        mail = None
        if not args.no_mail:
            from src.infrastructure.mail_factory import build_mail
            mail = build_mail(load_app_config(args.config_root, env=env).mail,
                              clock=clock)
        published = await publish(html, scenario=scenario, scenario_name=name,
                                  window=facts.window, output_dir=_output_dir(args, env),
                                  mail=mail, clock=clock, dry_run=args.dry_run)
        detail = " · ".join(published.describe())
        if facts.unavailable:
            detail += f" · 못 읽은 법인 {len(facts.unavailable)}개"
        return not (published.failed or facts.unavailable), detail

    return run


async def _collect_facts_at(args, env, scenario, today):
    """`_collect_facts`와 같은 일을 하되 **날짜를 인자로 받는다.**

    CLI 경로는 `args._today`에 날짜를 실어 나르는데, 스케줄러는 매 발사마다 날짜가
    달라지므로 그 자리를 재사용하면 첫 발사의 날짜가 굳는다.
    """
    args._today = today
    return await _collect_facts(args, env, scenario)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """SIGTERM/SIGINT를 받으면 **자는 중이라도** 깨서 정리하고 끝낸다.

    Windows에는 `add_signal_handler`가 없다(NotImplementedError). 그쪽에서는 Ctrl+C가
    KeyboardInterrupt로 올라오므로 그대로 둔다 — 못 하는 일을 하려다 죽는 것보다 낫다.
    """
    import signal

    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        received = getattr(signal, name, None)
        if received is None:
            continue
        try:
            loop.add_signal_handler(received, stop.set)
        except NotImplementedError:
            pass


def _preview_head(preview: dict) -> dict:
    """dry-run 미리보기에서 **본문을 잘라낸다.**

    본문은 HTML 리포트 전체(수만 자)라서 그대로 찍으면 정작 봐야 할 수신자와
    제목이 화면 위로 밀려 올라간다. 본문은 같은 실행이 이미 파일로 써 뒀으므로
    그것을 열어 보면 된다 — 여기서 봐야 하는 것은 **어디로 나가는가**다.
    """
    marker = "\nbody : \n"
    body = dict(preview.get("body") or {})
    value = str(body.get("input_value", ""))
    cut = value.find(marker)
    if cut >= 0:
        body["input_value"] = (
            value[:cut + len(marker)]
            + f"…(본문 {len(value) - cut - len(marker):,}자는 파일에서 본다)")
    return {**preview, "body": body}


def cmd_report_run(args, env) -> int:
    """**스케줄에 걸리는 명령.** 집계 → 서술 → HTML → 파일 → 메일을 한 번에.

    `render` 다음에 `mail send --file`을 부르는 두 단계로도 같은 일이 되지만,
    스케줄러에 두 줄을 걸면 **앞줄이 실패해도 뒷줄이 돈다** — 어제 파일을 오늘
    제목으로 다시 보내는 사고가 거기서 나온다. 한 명령이면 그 틈이 없다.

    조립은 `publish()`가 한다(규율 8과 같은 이유). 여기는 CLI 경계일 뿐이다.
    """
    from src.presentation.report_html import render
    from src.report.blocks import build_blocks
    from src.report.publish import publish

    name, scenario = _pick_scenario(args)
    today = _report_today(args, env)
    if today is None:
        return 1
    args._today = today

    facts = asyncio.run(_collect_facts(args, env, scenario))
    comments = asyncio.run(_comments(args, env, scenario, facts))
    html = render(build_blocks(facts, comments), title=scenario.title,
                  generated_at=_clock(args, env)().strftime("%Y-%m-%d %H:%M"))

    mail = None
    if not args.no_mail:
        from src.infrastructure.mail_factory import build_mail
        mail = build_mail(load_app_config(args.config_root, env=env).mail,
                          clock=_clock(args, env))

    # 기간은 `facts`가 들고 있는 것을 쓴다 — 여기서 다시 계산하면 집계한 날과
    # 제목의 날이 갈라질 수 있다(자정 직전에 돌면 실제로 갈라진다).
    published = asyncio.run(publish(
        html, scenario=scenario, scenario_name=name, window=facts.window,
        output_dir=_output_dir(args, env), mail=mail, clock=_clock(args, env),
        dry_run=args.dry_run))

    print(f"  시나리오: {name}  ({scenario.title})")
    print(f"  제목: {published.subject}")
    # 글자가 아니라 **바이트**를 센다 — 한국어 본문은 글자 수의 두세 배라서,
    # 글자로 세면 게이트웨이 크기 제한에 걸릴지를 판단할 수 없다.
    print(f"  {len(html.encode('utf-8')):,}바이트")
    for line in published.describe():
        print(f"  {line}")
    if published.preview is not None:
        print()
        _out(_preview_head(published.preview))
    if published.mail is not None:
        for warning in published.mail.warnings:
            print(f"\n  ⚠  Agent 경고 — {warning}", file=sys.stderr)
        if published.mail.neutralized_lines:
            print(f"\n  ⚠  본문에서 필드 머리글처럼 보이는 줄 "
                  f"{published.mail.neutralized_lines}개를 인용 표시(| )로 "
                  f"무력화했다", file=sys.stderr)
    for comment in comments:
        if comment.failed:
            print(f"  ⚠  코멘트 {comment.gbm}: {comment.status} — {comment.reason}",
                  file=sys.stderr)
    for outcome in facts.unavailable:
        print(f"  ⚠  {outcome.site}: {outcome.error or outcome.reason}", file=sys.stderr)
    if not facts.complete:
        print("  ⚠  표본이 잘렸다 — 본문 숫자는 하한이다", file=sys.stderr)

    # 종료 코드는 스케줄러가 **조용한 실패**를 알아챌 유일한 신호다. 무엇을 1로
    # 셀지는 "사람이 오늘 봐야 하는가"로 가른다: 파일·발송 실패와 못 읽은 법인은
    # 리포트 자체가 반쪽이므로 1. 코멘트 실패는 리포트가 이미 나갔고 본문에 그
    # 자리가 비어 보이므로 경고까지만 — 경보가 잦아지면 아무도 안 본다.
    return 1 if (published.failed or facts.unavailable) else 0


def _output_dir(args, env) -> Path:
    """리포트 파일을 둘 곳. **config가 정하고 CLI가 덮어쓴다.**

    기본값을 코드에 박아 두면 `app.json`의 `output_dir`이 선언만 되고 아무도 안 읽는
    칸이 된다 — 사람이 거기 적어도 아무 일이 안 일어나는데, 그 실패는 조용하다.
    실제로 `timezone`이 한동안 그랬다.
    """
    return Path(args.out_dir or load_app_config(args.config_root, env=env).output_dir)


def _clock(args, env):
    """진짜 시계는 여기서만 만들어진다. **시간대는 배포 환경이 아니라 config가 정한다.**

    `datetime.now().astimezone()`은 그 기계의 시스템 TZ를 따른다. 컨테이너의 기본
    TZ는 UTC이고, 그러면 `0 8 * * *`이 **UTC 8시**에 돈다 — 한국 시각 오후 5시다.
    스케줄러가 생긴 지금 이건 배포 환경의 문제가 아니라 기능의 핵심이다.

    집계 쪽에도 같은 문제가 있다: 화요일 08:00 KST(= 월요일 23:00 UTC)에 돌면 로컬
    날짜가 월요일이라 "어제(직전 평일)"가 월요일이 아니라 **금요일**로 계산된다.
    매일 한 평일씩 밀린 리포트가 나가는데 제목의 날짜도 같이 밀려서 **정상으로
    보인다.** 사내 Windows가 KST라 우연히 맞았을 뿐이다.

    그래서 `app.json`의 `timezone`을 쓴다(기동이 이미 검증하는 값이다).

    `args`에 한 번만 만들어 둔다 — 한 실행 안에서 시계가 두 종류면 그것 자체가
    버그의 씨앗이다.
    """
    made = getattr(args, "_clock_cached", None)
    if made is None:
        zone = ZoneInfo(load_app_config(args.config_root, env=env).timezone)
        made = lambda: datetime.now(zone)                          # noqa: E731
        args._clock_cached = made
    return made


# ── 파서 ──────────────────────────────────────────────────────────────

def _add_site_options(target, *, sub: bool) -> None:
    """`--gbm/--fct`를 전역과 하위 명령 **양쪽**에 단다.

    argparse의 전역 옵션은 하위 명령 **앞**에만 올 수 있다. 그런데 사람은
    `peek redis --key x --gbm mx`처럼 뒤에 쓰는 쪽이 자연스럽다.

    **같은 dest를 양쪽에 달지 않는다.** 하위 파서가 결과를 부모 namespace에
    합치는 방식이 파이썬 버전 사이에서 바뀌었고(별도 namespace에 파싱해 복사하는
    버전과 부모에 직접 파싱하는 버전이 있다), 그래서 `default=SUPPRESS` 트릭이
    어떤 버전에서는 듣고 어떤 버전에서는 하위 파서의 "안 줬음"이 전역 값을
    덮어쓴다. 실제로 이 리포에서 Linux는 통과하고 Windows(다른 파이썬)에서는
    깨지는 형태로 드러났다.

    그래서 dest를 `gbm_sub`/`fct_sub`로 분리하고 `_merge_site_options`가
    **명시적으로** 합친다. argparse의 내부 동작에 기대지 않으면 버전에
    상관없이 같게 동작한다.
    """
    suffix = "_sub" if sub else ""
    target.add_argument("--gbm", dest=f"gbm{suffix}", default=None,
                        help="사업부 (예: mx). 후보가 하나면 생략 가능")
    target.add_argument("--fct", dest=f"fct{suffix}", default=None,
                        help="법인/공장 (예: gumi)")


def _merge_site_options(args):
    """하위 명령 뒤에 쓴 값이 있으면 그것이 이긴다."""
    args.gbm = getattr(args, "gbm_sub", None) or args.gbm
    args.fct = getattr(args, "fct_sub", None) or args.fct
    return args


def parse_args(argv: list[str] | None = None):
    """**명령줄 해석의 단일 입구.** main과 테스트가 같은 경로를 탄다.

    테스트가 `build_parser().parse_args()`를 직접 부르면 병합 단계를 건너뛰어,
    프로덕션에서 깨지는 조합이 테스트에서는 통과한다.
    """
    return _merge_site_options(build_parser().parse_args(argv))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src", description="운영 모니터링 에이전트")
    parser.add_argument("--config-root", type=Path, default=Path("config"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    # 대상 시스템의 **구조** 지식. `config/`(접속 정보)와 고치는 사람도 주기도 다르다.
    # 기본값을 고정 경로로 두지 않는다 — `--config-root`만 바꿔 쓰는 호출부(테스트·
    # 사내 이관 트리)가 **엉뚱한 knowledge를 읽는다.** 실제로 그렇게 깨졌다.
    parser.add_argument("--knowledge-root", type=Path, default=None,
                        help="기본: --config-root 옆의 knowledge/")
    _add_site_options(parser, sub=False)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("boot", help="기동 검증").set_defaults(run=cmd_boot)
    sub.add_parser("sites", help="사이트 목록").set_defaults(run=cmd_sites)

    config = sub.add_parser("config", help="설정 보기")
    config_sub = config.add_subparsers(dest="what", required=True)
    show = config_sub.add_parser("show")
    show.add_argument("--no-provenance", action="store_true", help="출처 표를 숨긴다")
    _add_site_options(show, sub=True)
    show.set_defaults(run=cmd_config_show)

    doctor = sub.add_parser("doctor", help="네 시스템에 실제로 붙어 본다")
    _add_site_options(doctor, sub=True)
    doctor.set_defaults(run=cmd_doctor)

    llm = sub.add_parser("llm", help="LLM에 묻는다")
    llm_sub = llm.add_subparsers(dest="what", required=True)
    llm_sub.add_parser("describe", help="무엇에 붙어 있는지").set_defaults(run=cmd_llm_describe)
    ask = llm_sub.add_parser("ask", help="한 번 묻고 한 번 받는다")
    ask.add_argument("prompt")
    ask.set_defaults(run=cmd_llm_ask)
    llm_sub.add_parser("check", help="간단한 질문 묶음").set_defaults(run=cmd_llm_check)

    mail = sub.add_parser("mail", help="보고서를 메일로 보낸다")
    mail_sub = mail.add_subparsers(dest="what", required=True)
    mail_sub.add_parser("describe", help="누구에게 보내게 돼 있는지").set_defaults(
        run=cmd_mail_describe)
    send = mail_sub.add_parser("send", help="보낸다")
    send.add_argument("--subject", default="연결 테스트",
                      help="제목(앞에 mail.subject_prefix가 붙는다)")
    send.add_argument("--body", help="본문. 생략하면 확인용 본문")
    send.add_argument("--file", help="본문을 파일에서 읽는다")
    send.add_argument("--dry-run", action="store_true",
                      help="나갈 요청만 보여주고 보내지 않는다")
    send.set_defaults(run=cmd_mail_send)

    report = sub.add_parser("report", help="운영 리포트")
    report_sub = report.add_subparsers(dest="what", required=True)
    scenarios_cmd = report_sub.add_parser("scenarios", help="시나리오 목록")
    scenarios_cmd.set_defaults(run=cmd_report_scenarios)
    window = report_sub.add_parser("window", help="집계 대상 날짜와 나갈 Mongo 필터")
    window.add_argument("--scenario", default=None, help="시나리오 이름(하나뿐이면 생략 가능)")
    window.add_argument("--today", default=None,
                        help="이 날 돌았다고 치고 계산한다(YYYY-MM-DD). 검토용")
    window.set_defaults(run=cmd_report_window)
    aggregate = report_sub.add_parser("aggregate", help="읽어서 숫자를 낸다")
    aggregate.add_argument("--scenario", default=None)
    aggregate.add_argument("--today", default=None, help="이 날 돌았다고 치고(YYYY-MM-DD)")
    aggregate.add_argument("--stub-seeds", help="이 파일이 있으면 실접속 대신 가짜 데이터를 쓴다")
    aggregate.set_defaults(run=cmd_report_aggregate)
    render_cmd = report_sub.add_parser("render", help="메일 본문 HTML을 만든다")
    render_cmd.add_argument("--scenario", default=None)
    render_cmd.add_argument("--today", default=None, help="이 날 돌았다고 치고(YYYY-MM-DD)")
    render_cmd.add_argument("--stub-seeds", help="이 파일이 있으면 실접속 대신 가짜 데이터를 쓴다")
    render_cmd.add_argument("--out", default="output/report.html", help="쓸 파일 경로")
    render_cmd.set_defaults(run=cmd_report_render)
    prompt_cmd = report_sub.add_parser("prompt", help="LLM에게 나갈 프롬프트를 본다")
    prompt_cmd.add_argument("--scenario", default=None)
    prompt_cmd.add_argument("--today", default=None, help="이 날 돌았다고 치고(YYYY-MM-DD)")
    prompt_cmd.add_argument("--stub-seeds", help="실접속 대신 가짜 데이터를 쓴다")
    prompt_cmd.add_argument("--gbm-only", default=None, help="이 GBM 하나만")
    prompt_cmd.set_defaults(run=cmd_report_prompt)
    run_cmd = report_sub.add_parser("run", help="집계·서술·파일·메일을 한 번에")
    run_cmd.add_argument("--scenario", default=None)
    run_cmd.add_argument("--today", default=None, help="이 날 돌았다고 치고(YYYY-MM-DD)")
    run_cmd.add_argument("--stub-seeds", help="이 파일이 있으면 실접속 대신 가짜 데이터를 쓴다")
    run_cmd.add_argument("--out-dir", default=None,
                         help="app.json의 output_dir을 덮어쓴다(이름은 기준일로 정해진다)")
    run_cmd.add_argument("--dry-run", action="store_true",
                         help="나갈 요청만 보여주고 보내지 않는다(파일은 쓴다)")
    run_cmd.add_argument("--no-mail", action="store_true",
                         help="메일을 아예 조립하지 않는다 — 파일만 만든다")
    run_cmd.set_defaults(run=cmd_report_run)

    patrol = sub.add_parser("patrol", help="순찰")
    patrol_sub = patrol.add_subparsers(dest="what", required=True)
    probe_cmd = patrol_sub.add_parser("probe", help="점검이 읽는 것을 실제로 읽어 본다")
    probe_cmd.add_argument("--check", help="점검 하나만. 생략하면 활성 점검 전부")
    probe_cmd.add_argument("--stub-seeds", help="대상에 안 붙고 돌려 본다")
    _add_site_options(probe_cmd, sub=True)
    probe_cmd.set_defaults(run=cmd_patrol_probe)

    check_cmd = patrol_sub.add_parser("check", help="판정까지 — 지금 뭐가 걸리나")
    check_cmd.add_argument("--check", help="점검 하나만. 생략하면 활성 점검 전부")
    check_cmd.add_argument("--all-sites", action="store_true",
                           help="registry의 활성 사이트 전부 (한 사이트가 터져도 나머지가 돈다)")
    check_cmd.add_argument("--stub-seeds", help="대상에 안 붙고 돌려 본다")
    _add_site_options(check_cmd, sub=True)
    check_cmd.set_defaults(run=cmd_patrol_check)

    list_cmd = patrol_sub.add_parser("list", help="어느 점검이 어느 주기로 도는가")
    _add_site_options(list_cmd, sub=True)
    list_cmd.set_defaults(run=cmd_patrol_list)

    open_cmd = patrol_sub.add_parser("open", help="걸린 것을 케이스로 만든다")
    open_cmd.add_argument("--check", help="점검 하나만")
    open_cmd.add_argument("--all-sites", action="store_true")
    open_cmd.add_argument("--stub-seeds")
    open_cmd.add_argument("--dry-run", action="store_true",
                          help="열릴 케이스를 보여만 준다 (저장 안 함)")
    _add_site_options(open_cmd, sub=True)
    open_cmd.set_defaults(run=cmd_patrol_open)

    case = sub.add_parser("case", help="조사 엔진")
    case_sub = case.add_subparsers(dest="what", required=True)
    dryrun = case_sub.add_parser("dryrun", help="대본으로 라운드를 돌려 본다(LLM 없음)")
    dryrun.add_argument("--plan", required=True, help="대본 JSON")
    dryrun.add_argument("--case-id", default="case-dryrun")
    dryrun.add_argument("--stub-seeds", help="대상에 안 붙고 돌려 본다")
    _add_site_options(dryrun, sub=True)
    dryrun.set_defaults(run=cmd_case_dryrun)

    list_cases = case_sub.add_parser("list", help="케이스 목록")
    list_cases.add_argument("--all", action="store_true", help="닫힌 것까지")
    list_cases.set_defaults(run=cmd_case_list)

    code = sub.add_parser("code", help="대상 코드 체크아웃")
    code_sub = code.add_subparsers(dest="what", required=True)
    for name, helptext, fn in (
            ("status", "읽을 수 있는 상태인가 (네트워크 없음)", cmd_code_status),
            ("plan", "사람이 직접 칠 git 명령 (네트워크 없음)", cmd_code_plan),
            ("sync", "clone/fetch — **사내에서만**", cmd_code_sync)):
        command = code_sub.add_parser(name, help=helptext)
        _add_site_options(command, sub=True)
        command.set_defaults(run=fn)

    investigate = case_sub.add_parser("investigate", help="리드 LLM으로 조사한다")
    investigate.add_argument("case_id")
    investigate.add_argument("--stub-seeds", help="대상에 안 붙고 돌려 본다")
    investigate.add_argument("--trace", nargs="?", const="output/traces",
                             help="프롬프트와 날것 응답을 남긴다 (기본 output/traces)")
    investigate.set_defaults(run=cmd_case_investigate)

    show_case = case_sub.add_parser("show", help="케이스 한 건")
    show_case.add_argument("case_id")
    show_case.set_defaults(run=cmd_case_show)

    schedule = sub.add_parser("schedule", help="스케줄대로 계속 돈다(상주 프로세스)")
    schedule.add_argument("--scenario", default=None, help="이 시나리오 하나만")
    schedule.add_argument("--list", action="store_true",
                          help="다음 발사 시각만 보여주고 끝낸다")
    schedule.add_argument("--list-count", type=int, default=5,
                          help="--list가 보여줄 횟수")
    schedule.add_argument("--stub-seeds", help="실접속 대신 가짜 데이터를 쓴다")
    schedule.add_argument("--out-dir", default=None,
                          help="app.json의 output_dir을 덮어쓴다")
    schedule.add_argument("--dry-run", action="store_true",
                          help="나갈 요청만 보여주고 보내지 않는다")
    schedule.add_argument("--no-mail", action="store_true", help="파일만 만든다")
    schedule.add_argument("--max-runs", type=int, default=None,
                          help="이 횟수만 돌고 끝낸다(확인용)")
    schedule.set_defaults(run=cmd_schedule)

    peek = sub.add_parser("peek", help="데이터를 하나 꺼내 본다")
    peek.set_defaults(run=cmd_peek)
    peek.add_argument("system", choices=["redis", "mongo", "kafka", "rest"])
    _add_site_options(peek, sub=True)
    peek.add_argument("--stub-seeds", help="이 파일이 있으면 실접속 대신 가짜 데이터를 쓴다")
    # redis
    peek.add_argument("--key"), peek.add_argument("--scan"), peek.add_argument("--ttl")
    # mongo
    peek.add_argument("--collection"), peek.add_argument("--filter")
    # 발견용 — 이름을 미리 알아야 읽을 수 있는 것에 "무엇이 있나"를 연다.
    peek.add_argument("--collections", action="store_true", help="컬렉션 목록")
    peek.add_argument("--sort", help="이 필드로 내림차순")
    peek.add_argument("--count", action="store_true")
    # kafka
    peek.add_argument("--topic", help="config의 논리 이름(topic1) 또는 실제 토픽 이름")
    peek.add_argument("--topics", action="store_true", help="토픽 목록")
    peek.add_argument("--lag", action="store_true", help="감시 그룹의 lag")
    peek.add_argument("--group", help="lag을 볼 그룹 하나(생략하면 config의 group_ids 전부)")
    # rest
    peek.add_argument("--entry"), peek.add_argument("--params")
    peek.add_argument("--list", action="store_true", help="등재 항목 목록")
    # 공통
    peek.add_argument("--limit", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = _load_env(args.env_file)
    try:
        return args.run(args, env)
    except ConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
