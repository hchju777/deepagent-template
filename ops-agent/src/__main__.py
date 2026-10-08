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

def _shutdown_noise_handler(loop, context: dict) -> None:
    """이벤트 루프의 예외 처리기 — **transport 층의 끊김만** 거른다.

    사내 Windows(proactor)에서 명령이 끝나 내려갈 때 `ConnectionResetError(10054)`·`RuntimeError: Event loop is closed`
    트레이스백이 났다(12a 리뷰 4번 7-2). 원인(안 닫은 httpx 풀)은 어댑터의 `close()`가 없앴고, 그래도 남는 것은 끊긴 소켓을
    치우는 transport의 소음뿐이다. 태스크의 예외(`task`·`future`가 든 context)와 다른 종류의 예외는 그대로 기본 처리기로 —
    거름망이 진짜 오류를 삼키면 조사가 왜 죽었는지 아무도 모른다.
    """
    exc = context.get("exception")
    transport_level = ("transport" in context or "protocol" in context) and "task" not in context and "future" not in context
    quiet = isinstance(exc, ConnectionResetError) or (isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc))
    if transport_level and quiet:
        return
    loop.default_exception_handler(context)


def _quiet_unraisable(inner):
    """`sys.unraisablehook` 거름망 — proactor transport의 `__del__`이 닫힌 루프에서 내는 것만 삼킨다. 루프가 이미 닫힌 뒤라
    예외 처리기에는 안 오고 이 훅으로 온다. 다른 객체·다른 예외는 원래 훅으로."""
    def hook(unraisable):
        exc = getattr(unraisable, "exc_value", None)
        where = getattr(getattr(unraisable, "object", None), "__qualname__", "") or repr(getattr(unraisable, "object", ""))
        if "_ProactorBasePipeTransport" in where and (
                isinstance(exc, ConnectionResetError) or (isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc))):
            return
        inner(unraisable)
    return hook


def _run(coro):
    """`asyncio.run` — 모든 명령이 여기를 지난다. 루프에 종료 소음 거름망을 달고, Windows에서는 unraisable 훅도 건다."""
    async def wrapped():
        asyncio.get_running_loop().set_exception_handler(_shutdown_noise_handler)
        return await coro
    if sys.platform == "win32" and getattr(sys.unraisablehook, "__name__", "") != "hook":
        sys.unraisablehook = _quiet_unraisable(sys.unraisablehook)
    return asyncio.run(wrapped())


async def _close_llms(*llms) -> None:
    """같은 객체는 한 번만, 던지면 삼킨다 — 닫다가 죽는 것이 조사 결과를 지우면 안 된다."""
    seen: list = []
    for llm in llms:
        if llm is None or any(llm is s for s in seen):
            continue
        seen.append(llm)
        try:
            await llm.close()
        except Exception:                                          # noqa: BLE001
            pass


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
    return _run(_doctor(site, _clock(args, env)))


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
    return _run(_peek(site, args, _clock(args, env)))


# ── llm ──────────────────────────────────────────────────────────────

def _llm_config(args, env, role: str | None = None):
    app = load_app_config(args.config_root, env=env)
    if app.llm is None:
        raise SystemExit("app.json에 llm 설정이 없다 — STEPS/step-07-llm.md 참고")
    return app.llm_for(role) if role else app.llm


def cmd_llm_describe(args, env) -> int:
    from src.config.schema_app import LLM_ROLES

    app = load_app_config(args.config_root, env=env)
    if app.llm is None:
        raise SystemExit("app.json에 llm 설정이 없다 — STEPS/step-07-llm.md 참고")
    base = app.llm
    print(" ", base.describe())
    # 기본과 다른 역할만 — 같은 것을 네 줄 찍으면 다른 한 줄이 묻힌다.
    for role in LLM_ROLES:
        cfg = app.llm_for(role)
        if cfg is not base:
            print(f"  역할 {role}: {cfg.describe()}")
    return 0


def cmd_llm_ask(args, env) -> int:
    from src.infrastructure.llm_factory import build_llm

    llm = build_llm(_llm_config(args, env, getattr(args, "role", None)), clock=_clock(args, env))

    async def ask():
        try:
            return await llm.ask(args.prompt)
        finally:
            await _close_llms(llm)

    reply = _run(ask())
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

    cfg = _llm_config(args, env, getattr(args, "role", None))
    llm = build_llm(cfg, clock=_clock(args, env))
    print(f"  {cfg.describe()}\n")
    failed = 0
    reported = None

    async def run_checks():
        # 질문 셋을 **한 루프 안에서** 묻고 끝에서 한 번만 닫는다 — 질문마다 닫았더니(R2-2c) 둘째부터 닫힌 클라이언트로
        # 나가 Connection error가 났고, 모든 모델이 고장난 것처럼 보였다(사내 10-08).
        nonlocal failed, reported
        try:
            for name, prompt, ok in _CHECKS:
                reply = await llm.ask(prompt)
                reported = reported or reply.reported_model
                if reply.status == "error":
                    print(f"  {name:<8} ❌ {reply.error}")
                    failed += 1
                    continue
                mark = "✅" if ok(reply.text) else "⚠ "
                failed += 0 if ok(reply.text) else 1
                print(f"  {name:<8} {mark} ({reply.latency_s}s) {reply.text.strip()[:110]}")
        finally:
            await _close_llms(llm)

    _run(run_checks())

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

    result = _run(sender.send(subject, body))
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

    facts = _run(_collect_facts(args, env, scenario))
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
        llm = build_llm(app.llm_for("report"), clock=_clock(args, env))
    try:
        return await comment_on(facts, llm=llm, spec=scenario.comment,
                                template=load_prompt(args.config_root, scenario),
                                clock=_clock(args, env))
    finally:
        await _close_llms(llm)


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
    facts = _run(_collect_facts(args, env, scenario))

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

    facts = _run(_collect_facts(args, env, scenario))
    comments = _run(_comments(args, env, scenario, facts))
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

    probe_sets = _run(go())
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

    outcomes = _run(run_sites(
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

    outcomes = _run(run_sites(
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


def _load_lead_prompt(config_root: Path, relative: str, *, slots: frozenset,
                      required: tuple[str, ...] = ("case", "actions", "example")) -> str:
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
                for name in required if f"{{{name}}}" not in text]
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
    # **지난 실행의 파일을 지운다.** 번호가 1부터 다시 시작하므로 짧은 실행 뒤에
    # 긴 실행의 꼬리(`06-r4-…`)가 남고, `case trace`가 그걸 이번 라운드로 읽는다 —
    # 사내에서 실제로 났다. 지난 r4가 이번 r4 앞에 그대로 찍혔다.
    for stale in list(folder.glob("*.md")):
        stale.unlink()
    seq = itertools.count(1)
    written: list[Path] = []

    def trace(node: str, round_no: int, prompt: str, text, error, latency_s=None, waited_s=None) -> None:
        path = folder / f"{next(seq):02d}-r{round_no}-{node}.md"
        verdict = "읽었다" if error is None else f"**못 읽었다** — {error}"
        # 호출이 걸린 초 — 사내 실측 두 판이 시간 초과로 죽었는데 어느 호출이 몇 초였는지가 아무 데도 없었다.
        took = f"응답: {latency_s:.1f}초\n" if latency_s is not None else ""
        # 429 뒤 기다린 초 — 할당량 대기는 응답 시간과 다른 양이다(브리프가 따로 받는다). 안 기다렸으면 안 적는다.
        took += f"대기: {waited_s:.1f}초(429)\n" if waited_s else ""
        path.write_text(
            f"# {case_id} · {node} · 라운드 {round_no}\n\n"
            f"결과: {verdict}\n{took}\n"
            f"## 물어본 것 ({len(prompt):,}자)\n\n````\n{prompt}\n````\n\n"
            f"## 날것 응답\n\n````\n{'(응답 없음 — 호출 자체가 실패했다)' if text is None else text}\n````\n",
            encoding="utf-8")
        written.append(path)

    return trace, written


def cmd_case_investigate(args, env) -> int:
    """케이스 하나를 **리드 LLM으로** 조사하고 판정까지 낸다.

    "무엇을 볼지 LLM이 정하고, 보고, 다시 정한다"(10b) 뒤에 conclude가 판정을 쓰고 verify가
    인용을 검사한다(12a). 보고서·이벤트는 12b.
    """
    from src.application import briefing
    from src.application.diagnose import diagnose, llm_error_line, verdict_lines
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
                                              slots=briefing.INTEGRATE_SLOTS),
               "conclude": _load_lead_prompt(args.config_root,
                                             app.investigation.conclude_prompt,
                                             slots=briefing.CONCLUDE_SLOTS,
                                             required=briefing.CONCLUDE_REQUIRED)}

    built = {}
    tracer, traced = (None, [])
    if args.trace:
        tracer, traced = _make_tracer(record.id, folder=Path(args.trace) / record.id)

    async def go() -> dict:
        # 액션 턴과 판정 턴은 다른 LLM일 수 있다(역할별, R2-1). 설정이 같으면 어댑터도 하나다.
        lead_cfg, conclude_cfg = app.llm_for("lead"), app.llm_for("conclude")
        llm = build_llm(lead_cfg, clock=clock)
        conclude_llm = llm if conclude_cfg is lead_cfg else build_llm(conclude_cfg, clock=clock)
        built["llm"] = llm.describe() + ("" if conclude_llm is llm else f"\n  판정: {conclude_llm.describe()}")
        from src.knowledge import flow
        code, services, flow_graph, graph_note = _code_if_ready(
            site, gbm, fct, knowledge_root=_knowledge_root(args), clock=clock,
            graph_dir=_graph_dir(args, env, gbm))
        built["code"] = code.describe() if code else "코드 없음"
        built["graph"] = graph_note
        adapters = build_adapters(site, clock=clock, seeds=seeds, code=code)
        try:
            frame, integrate, conclude = make_lead(
                llm, site_config=site, prompts=prompts,
                max_rounds=app.investigation.max_rounds,
                evidence_budget=app.investigation.evidence_total_chars,
                trace=tracer, services=services,
                roles=code.service_roles() if code else {},
                flow_graph=flow_graph, code_index=code.has_index() if code else False,
                conclude_llm=conclude_llm,
                prompt_caps={"integrate": app.investigation.integrate_prompt_chars,
                             "conclude": app.investigation.conclude_prompt_chars})
            case = Case(id=record.id, gbm=gbm, fct=fct, origin="patrol",
                        symptom=record.symptom, t0=record.opened_at,
                        check=record.check, target=record.target)
            deps = EngineDeps(runner=ProbeRunner(
                adapters, clock=clock,
                detail_chars=app.investigation.evidence_chars,
                narrowed_chars=app.investigation.evidence_total_chars),
                              frame=frame, integrate=integrate, conclude=conclude,
                              max_rounds=app.investigation.max_rounds,
                              parallel_width=app.investigation.parallel_width,
                              max_tasks=app.investigation.max_tasks,
                              known_names=flow.known_names(flow_graph),
                              components=frozenset(services),
                              declared={"rediskey": flow.declared_keys(
                                  flow_graph, briefing.intake_path(case, site))})
            return await build_engine(deps).ainvoke(CaseState(case=case))
        finally:
            await adapters.close()
            await _close_llms(llm, conclude_llm)

    final = _run(go())
    print(f"  {record.site}  {record.id} — {record.symptom}")
    print(f"  {built['llm']}")
    print(f"  라운드 {final['round']} — 끝난 이유: {final['stopped_by']}\n")
    print("\n".join(verdict_lines(CaseState.model_validate(final))) + "\n")

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
        # 머리줄은 사실만 — "이 조사는 안 돌았다"는 llm_error 뒤에도 판정이 나오는 지금(R2-2a) 표준 출력과 모순됐다.
        print("\n  " + llm_error_line(CaseState.model_validate(final)), file=sys.stderr)
        for problem in final["llm_errors"]:
            print(f"    {problem}", file=sys.stderr)
    # 조용히 성공한 척하면 아무도 안 본다.
    return 1 if broken else 0


# ── 대상 코드 (11a) ────────────────────────────────────────────────

def _knowledge_root(args) -> Path:
    """지식은 **config 트리 옆에** 산다. 같이 옮겨 다녀야 짝이 안 어긋난다."""
    return args.knowledge_root or args.config_root.parent / "knowledge"


def _code_site(args, env):
    """코드 명령이 볼 사이트 하나. **`--fct`를 요구하지 않는다.**

    코드는 GBM 단위로 같다(decisions ③). 그런데 레포 선언이 층 병합을 타므로 병합된
    결과를 보려면 사이트가 하나 필요하다 — 그래서 **그 GBM의 활성 사이트 중 하나를
    코드가 고른다.** 사람에게 "어느 법인이냐"고 묻는 것은 답이 뜻이 없는 질문이다.

    `--fct`를 주면 그것을 쓴다. `config_paths`의 `{fct}` 해석이 달라지므로 특정
    법인의 층을 보고 싶을 때가 있다.
    """
    registry = load_registry(args.config_root)
    active = registry.active()
    gbm = args.gbm or ({e.gbm for e in active} == {a.gbm for a in active}
                       and len({e.gbm for e in active}) == 1
                       and next(iter({e.gbm for e in active})) or None)
    if not gbm:
        known = ", ".join(sorted({e.gbm for e in active})) or "(없음)"
        raise SystemExit(f"--gbm이 필요하다. registry의 사업부: {known}")

    sites = [e for e in active if e.gbm == gbm
             and (not args.fct or e.fct == args.fct)]
    if not sites:
        raise SystemExit(f"{gbm}에 활성 사이트가 없다"
                         + (f" — fct={args.fct}" if args.fct else ""))
    picked = sites[0]
    site, _ = load_site_config(args.config_root, picked.gbm, picked.fct, env=env)
    # 분기 검사는 **`--fct`와 무관하게 GBM 전체**를 본다. 고른 사이트만 보면
    # "한 법인만 보고 있으니 갈릴 리가 없다"가 되어 검사가 아무 일도 안 한다.
    return site, gbm, picked.fct, [e.fct for e in active if e.gbm == gbm]


def _divergent_repos(args, env, gbm: str, fcts: list[str]) -> list[str]:
    """같은 GBM의 사이트들이 **다른 레포를 선언하고 있지 않은가.**

    코드는 GBM 단위로 같다는 것이 이 설계의 전제다. 누가 `fct/` 층에 `code.repos`를
    적으면 그 전제가 조용히 깨지고, 우리는 **사이트마다 다른 코드를 읽으면서도
    같은 것을 읽는 줄 안다.**
    """
    seen: dict[str, list[str]] = {}
    for fct in fcts:
        try:
            site, _ = load_site_config(args.config_root, gbm, fct, env=env)
        except Exception:                                          # noqa: BLE001
            continue
        key = " ".join(sorted(f"{r.name}={r.url}" for r in site.code.repos))
        seen.setdefault(key, []).append(fct)
    if len(seen) <= 1:
        return []
    return [f"{', '.join(fcts_)}: {key or '(선언 없음)'}" for key, fcts_ in seen.items()]


def _report_submodules(repo, commit: str) -> int:
    """그 커밋의 submodule이 **읽을 수 있는 상태인가.** 문제 수를 돌려준다.

    안 채워진 submodule은 `code status`가 말해 주지 않으면 아무 데서도 안 보인다 —
    git 자신이 조용하기 때문이다(`git grep`은 종료코드 1에 출력이 없다).
    """
    from src.knowledge.checkout import stale, submodules_at, unpopulated

    subs = submodules_at(repo, commit)
    if not subs:
        return 0
    blind = unpopulated(repo, commit)
    behind = stale(repo, commit, subs)
    note = [f"submodule {len(subs)}개: {', '.join(subs)}"]
    if blind:
        note.append(f"안 채워짐: {', '.join(blind)}")
    if behind:
        note.append(f"그 커밋의 버전이 없음: {', '.join(behind)}")
    print("          " + " · ".join(note))
    if not blind and not behind:
        return 0
    if blind:
        # git 자신이 조용하다 — 여기서 안 말하면 아무 데서도 안 보인다.
        print(f"       ⚠ 안 채워진 submodule은 **조용히** 안 보인다 — "
              f"`git show`는 \"경로가 없다\"고 하고 `git grep`은 0건을 돌려준다")
    if behind:
        # 이쪽은 시끄럽게 실패하지만 **말이 오해를 부른다**
        # ("exists on disk, but not in …" = 파일이 없다는 뜻이 아니다).
        print(f"       ⚠ 그 배포가 쓴 submodule 버전이 로컬에 없다 — "
              f"파일이 없는 것이 아니라 우리가 그 버전을 안 가진 것이다")
    # 둘 다 필요하다. fetch만으로는 **안 채워진** submodule이 안 채워지고,
    # update만으로는 부모가 새로 가리키는 버전의 객체가 안 온다(측정함).
    print(f"       → git -C {repo.path} fetch --all --prune --recurse-submodules")
    print(f"       → git -C {repo.path} submodule update --init --recursive")
    return 1


def cmd_code_status(args, env) -> int:
    """**네트워크를 안 탄다.** 어디서든 돈다 — 진단 전용이다(decisions ⑤).

    보는 것: 경로가 있나 · `.git`이 있나 · `origin`이 config와 같나 ·
    배포가 가리키는 커밋이 로컬에 실재하나 · 그 커밋의 submodule이 채워져 있나 ·
    이름이 사는 config 층이 그 커밋에 있나.
    """
    from src.knowledge.checkout import config_layers, has_commit, plan_for, status_of
    from src.knowledge.loader import load_deployment, load_topology

    site, gbm, site_fct, fcts = _code_site(args, env)
    repos = list(site.code.repos)
    for problem in _divergent_repos(args, env, gbm, fcts):
        print(f"  ⚠ 같은 GBM인데 사이트마다 레포 선언이 다르다 — {problem}", file=sys.stderr)
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

    print(f"  {gbm} — 레포 {len(repos)}개 · 서비스 {len(topology.services)}개 · config 층은 {site_fct} 기준")
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
        # submodule은 **커밋마다** 답이 다르지만 서비스마다 다르지는 않다 —
        # 같은 커밋을 여러 서비스가 가리키면 한 번만 본다.
        checked: set[str] = set()
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
            blocked = 0
            if pin.commit not in checked:
                checked.add(pin.commit)
                blocked = _report_submodules(repo, pin.commit)
                bad += blocked
            # 이름이 사는 곳이 실재하는가. 층은 선택이지만 **하나도 없으면**
            # 경로 앞머리가 통째로 틀린 것이고, 리드는 이름을 영영 못 찾는다.
            wanted = topology.resolved_config_paths(gbm, site_fct)
            if not wanted:
                continue
            here, gone = config_layers(repo, pin.commit, wanted)
            print(f"          config 층 {len(here)}/{len(wanted)}개"
                  + (f" · 없음: {', '.join(gone)}" if gone else ""))
            if not here:
                bad += 1
                print(f"       ⚠ config_paths가 그 커밋에 **하나도** 없다")
                # submodule이 안 읽히면 그 안의 층도 전부 "없다"로 나온다.
                # 그 상태에서 "경로를 고쳐라"는 **틀린 처방**이다 — 경로는 맞았다.
                if blocked:
                    print(f"       (먼저 위의 submodule부터 — 그 안의 층은 지금 안 읽힌다)")
                else:
                    print(f"       → knowledge/topology/{gbm}.json의 config_paths를 고쳐라")
    if not bad:
        _graph_status(args, env, site=site, gbm=gbm, fct=site_fct)
    return 1 if bad else 0


def cmd_code_plan(args, env) -> int:
    """사람이 직접 칠 git 명령을 출력한다. **토큰은 안 찍는다.**"""
    from src.knowledge.checkout import plan_for, status_of

    site, gbm, _, _ = _code_site(args, env)
    repos = list(site.code.repos)
    print(f"  # {gbm} — 아래를 직접 실행하라 (이 명령은 네트워크를 안 탄다)")
    for repo in repos:
        for line in plan_for(repo, status_of(repo)):
            print(f"  {line}")
    return 0


def cmd_code_sync(args, env) -> int:
    """**여기서만 네트워크를 탄다.** 사내 밖에서는 실패하고, `code plan`을 안내한다."""
    from src.knowledge.checkout import status_of, sync

    site, gbm, fct, fcts = _code_site(args, env)
    repos = list(site.code.repos)
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
        return 1
    # 커밋이 새로 왔으니 그래프도 그 커밋으로. sync와 graph는 같은 조립을 쓴다.
    return _build_graph(args, env, site=site, gbm=gbm, fct=fct, fcts=fcts)


def _graph_dir(args, env, gbm: str) -> Path:
    """번들 자리 — GBM 단위(11e-2). 사이트는 그 안의 `sites/<fct>.json`이다."""
    from src.knowledge.graph_build import bundle_dir
    return bundle_dir(Path(load_app_config(args.config_root, env=env).output_dir), gbm)


def _resolved_commits(site, code) -> tuple[dict[str, str], list[str]]:
    """레포 → 배포 커밋의 **실제 SHA**. `main`은 움직이므로 그래프는 SHA에 박는다."""
    from src.knowledge.checkout import resolve_commit

    declared = {r.name: r for r in site.code.repos}
    out, problems = {}, []
    for repo, ref in code.pinned().items():
        sha = resolve_commit(declared[repo], ref) if repo in declared else ""
        if sha:
            out[repo] = sha
        else:
            problems.append(f"{repo}: {ref}를 SHA로 못 풀었다 — 체크아웃이 없거나 그 커밋이 없다")
    return out, problems


def _rest_entries(site) -> dict[str, tuple[str, str]]:
    rest = site.infra.rest
    return {name: (entry.method, entry.path)
            for name, entry in (rest.entries.items() if rest is not None else [])}


def _entries_across(args, env, gbm: str, fcts: list[str], site) -> dict[str, tuple[str, str]]:
    """GBM의 사이트 전부의 등재 항목 합집합 — 끝점 노드는 GBM 번들에 한 번 서므로 어느 사이트에만 있는 항목도 든다."""
    entries = dict(_rest_entries(site))
    for fct in fcts:
        try:
            other, _ = load_site_config(args.config_root, gbm, fct, env=env)
        except (ConfigError, FileNotFoundError):
            continue
        for name, pair in _rest_entries(other).items():
            entries.setdefault(name, pair)
    return entries


def _build_graph(args, env, *, site, gbm: str, fct: str, fcts: list[str] | None = None) -> int:
    """흐름 오버레이(+ graphify 심볼 그래프)를 배포 커밋에 박는다. **네트워크 없음.** 번들은 GBM 하나(11e-2) —
    이름은 GBM 층에서, 사이트(`fcts`)마다는 그 층이 덮은 값만 `sites/<fct>.json`에.

    `code sync` 끝과 `code graph`가 같은 길을 쓴다. 규율 8과 같은 이유 — 조립을 두 벌
    두면 한쪽이 빠진다.
    """
    from src.knowledge import flow
    from src.knowledge import graph_build as gb
    from src.knowledge.loader import load_topology

    clock = _clock(args, env)
    root = _knowledge_root(args)
    try:
        code = _build_code(site, gbm, fct, knowledge_root=root, clock=clock)
        topology = load_topology(root, gbm)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"  그래프: 지식 층을 읽을 수 없다 — {exc}", file=sys.stderr)
        return 1

    import shutil
    import time
    t0 = time.monotonic()

    def progress(msg: str) -> None:
        # 사내에서 몇 분을 말없이 돌자 "멈췄다"로 읽혔다. 단계마다 경과 시간과 함께 찍는다.
        print(f"  [{time.monotonic() - t0:6.1f}s] {msg}", file=sys.stderr, flush=True)

    async def gather():
        # GBM 층만으로 뽑은 이름이 그래프의 기준값이다(11e-2). 사이트 층은 값을 덮을 뿐이라 따로 입힌다.
        names, problems = await code.names_for("")
        if not names:
            names, more = await code.names_for(fct)
            problems = problems + more + [f"GBM 층에 이름이 없어 {fct} 층을 기준으로 삼았다"]
        patterns = sorted({p for name in names for p in name.patterns})
        progress(f"이름 {len(names)}개 · 패턴 {len(patterns)}개 · 레포 {len(code.pinned())}개")
        table, notes = await code.flow_hits(patterns, progress=progress)
        route_lines, route_notes = await code.route_hits(progress=progress)
        return names, problems + notes + route_notes, table, route_lines

    names, problems, table, route_lines = _run(gather())
    commits, more = _resolved_commits(site, code)
    problems += more
    overlay = flow.extract(names=names, topology=topology,
                           hits_for=lambda p: table.get(p, []), commits=commits)
    # 끝점 층 — 우리 등재 항목의 path와 코드의 라우트 선언에서. 사람이 적지 않는다.
    routes = flow.routes_from_hits(route_lines)
    fcts = list(fcts or [fct])
    overlay = flow.add_endpoints(overlay, routes=routes, entries=_entries_across(args, env, gbm, fcts, site))
    counts = flow.summary(overlay)
    progress(f"끝점 {counts['endpoints']}개 (등재 {counts['endpoints_registered']} · "
             f"서빙 미상 {counts['endpoints_unserved']})")

    # 심볼 인덱스(11d) — 레포 전체를 배포 커밋에서 2-pass로. 끝점 사슬이 여기서 나온다(6d-3) — 함수→함수 엣지도 그래프에 남는다.
    from src.knowledge import index as indexing
    from src.knowledge import index_trace
    from src.knowledge import query as qy
    symbol_index = _run(indexing.build_index(
        {name: code.source_for(name) for name in sorted(commits)}, names=names, commits=commits))
    isum = symbol_index.summary()
    progress(f"심볼 {isum['symbols']}개 · 엣지 {isum['edges_total']}개 · 파싱 실패 {isum['parse_errors']}개")
    qgraph = qy.Graph(symbol_index)
    # 서빙 서비스가 있는 끝점마다 사슬을 만든다 — 배포 커밋에서만, 조사 중에는 안 돌린다(⑥). 11b 추적기는 6d-4에서
    # 지웠다 — 두 번의 사내 대조(150/6)에서 인덱스가 못한 지점이 없었다.
    by_id = {n["id"]: n for n in overlay["nodes"]}
    served: dict[str, dict[str, dict]] = {}
    for e in overlay["links"]:
        if e["relation"] == "serves" and e.get("repo"):
            served.setdefault(e["repo"], {})[e["target"]] = by_id[e["target"]]
    for repo_name, endpoints in sorted(served.items()):
        progress(f"{repo_name}: 끝점 사슬 ({len(endpoints)}개)")
        for node in sorted(endpoints.values(), key=lambda n: n["label"]):
            overlay = flow.add_trace(overlay, node["id"],
                                     index_trace.trace(symbol_index, qgraph, repo=repo_name, target=node["label"], routes=routes))
    counts = flow.summary(overlay)
    progress(f"끝점 추적: 자원까지 이어진 {counts['endpoints_traced']}개 · 막힌 {counts['endpoints_blocked']}개")

    out_dir = _graph_dir(args, env, gbm)
    binary = gb.find_graphify()
    if not binary:
        progress("graphify 없음 — 심볼 그래프는 건너뛴다 (requirements-graph.txt)")
    symbol_graphs, states, reports = [], [], {}
    # 레포마다 독립이라 겹쳐 돌린다(11e-3, 사내 5레포 순차 296초). 합치는 순서는 config의 레포 순서 그대로다.
    jobs = [(repo.name, Path(repo.path), commits[repo.name], out_dir / "worktrees" / repo.name)
            for repo in site.code.repos if commits.get(repo.name)]
    width = gb.graphify_width(len(jobs))
    if binary and len(jobs) > 1:
        progress(f"graphify: 레포 {len(jobs)}개를 {width}개씩 겹쳐 돌린다")
    done = dict(zip([name for name, *_ in jobs],
                    gb.run_graphify_many(jobs, binary, width=width, progress=lambda r, m: progress(f"{r}: {m}"))))
    for repo in site.code.repos:
        if repo.name not in done:
            states.append(f"{repo.name} 건너뜀(커밋 없음)")
            continue
        status, detail, graph, report = done[repo.name]
        states.append(f"{repo.name} {status}" + ("" if status == "ok" else f" — {detail}"))
        if graph:
            symbol_graphs.append(graph)
        if report:
            reports[repo.name] = report
    merged = gb.merge_graphs(overlay, symbol_graphs)
    meta = gb.GraphMeta(gbm=gbm, fct="", commits=commits, built_at=gb.now_text(clock),
                        graphify=gb.graphify_version(binary), notes=problems + states, sites=fcts)
    gb.write_bundle(out_dir, overlay=overlay, merged=merged, meta=meta)
    gb.write_index(out_dir, symbol_index)
    # 사이트마다 그 층이 GBM 값을 덮은 것만 — 스냅샷에서 병합하므로 git은 안 는다(사내 28사이트 × 서비스 × 층).
    site_counts: dict[str, int] = {}
    for one in fcts:
        rows = _run(code.site_overrides(one, names))
        gb.write_site(out_dir, one, rows)
        site_counts[one] = len(rows)
    progress(f"사이트 {len(fcts)}개 덮은 값: " + " · ".join(f"{f} {n}" for f, n in site_counts.items()))
    shutil.rmtree(out_dir / "worktrees", ignore_errors=True)      # 빈 껍데기만 남는다
    progress("그래프 씀")

    # 사람용. flow.html은 항상(오버레이만으로 그린다, 외부 참조 0). 리포트와 wiki는 graphify가 있을 때.
    from src.presentation import flow_html
    (out_dir / "flow.html").write_text(
        flow_html.render(overlay, title=gbm, built_at=meta.built_at, commits=commits),
        encoding="utf-8")
    # 코드 호출 흐름(11d 6c-1b) — 심볼 인덱스를 골라 펼쳐 보는 한 장. CLI 샘플 몇 개로는 안심이 안 된다는 사내 요청.
    from src.presentation import calls_html
    (out_dir / "calls.html").write_text(
        calls_html.render(symbol_index, title=gbm, built_at=meta.built_at, commits=commits,
                          service_of=lambda s: flow.owner(s.file, s.repo, topology)[0]),
        encoding="utf-8")
    for repo_name, report in sorted(reports.items()):
        target = out_dir / "reports" / repo_name / "GRAPH_REPORT.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
    wiki_status, wiki_detail = gb.run_wiki(binary, out_dir / "graph.json", progress=progress)
    human = ["flow.html", "calls.html"]
    if reports:
        human.append(f"reports/<레포>/GRAPH_REPORT.md ({len(reports)}개)")
    if wiki_status == "ok":
        human.append("wiki/index.md")
    elif wiki_status == "failed":
        problems.append(f"wiki: {wiki_detail}")

    summary = flow.summary(overlay)
    kinds = ", ".join(f"{k} {v}" for k, v in sorted(summary["kinds"].items()))
    print(f"\n  그래프 {gbm} → {out_dir}")
    print(f"       사이트 {len(fcts)}: " + " · ".join(f"{f}(덮은 값 {n})" for f, n in site_counts.items()))
    print(f"       graphify {meta.graphify} · " + " · ".join(states))
    print(f"       사람용: {' · '.join(human)}")
    print(f"       오버레이 노드 {summary['nodes']} · 엣지 {summary['links']} ({kinds})"
          f" · 합친 그래프 노드 {len(merged['nodes'])} · 엣지 {len(merged['links'])}")
    if summary["unreferenced"]:
        print(f"       코드 줄에서 직접 못 찾은 이름 {summary['unreferenced']}개 — config에만 보인다 "
              f"(Enum·공통 헬퍼로 감싸 쓰면 여기 든다; 코드 층은 리드가 홉을 밟는다)")
    for line in problems:
        print(f"       ⚠ {line}")
    advice = flow.advise(overlay, topology)
    if advice:
        print("       권고:")
        for line in advice:
            print(f"         - {line}")
    return 1 if (not names or not commits) else 0


def cmd_code_graph(args, env) -> int:
    """지금 체크아웃으로 그래프를 다시 만든다 — sync 없이. **네트워크 없음.**"""
    site, gbm, fct, fcts = _code_site(args, env)
    if not site.code.repos:
        print(f"  {gbm}: config에 target 코드 레포가 없다 — code.repos를 적어라")
        return 1
    return _build_graph(args, env, site=site, gbm=gbm, fct=fct, fcts=fcts)


def _graph_status(args, env, *, site, gbm: str, fct: str) -> int:
    """`code status`의 그래프 절. 없거나 낡았으면 **말한다** — 막지는 않는다.
    브리핑은 낡은 그래프를 싣지 않으므로(커밋 3) 조사는 그래프 없이 돈다."""
    from src.knowledge import flow
    from src.knowledge import graph_build as gb

    out_dir = _graph_dir(args, env, gbm)
    print(f"\n  그래프 {out_dir}")
    got = gb.read_bundle(out_dir)
    if got is None:
        print("       ⚠ 없음 — `python -m src code graph`(네트워크 없음) 또는 `code sync`가 만든다")
        return 0
    graph, meta = got
    try:
        code = _build_code(site, gbm, fct, knowledge_root=_knowledge_root(args),
                           clock=_clock(args, env))
        commits, problems = _resolved_commits(site, code)
    except (ConfigError, FileNotFoundError) as exc:
        commits, problems = {}, [str(exc)]
    stale = gb.check_bundle(meta, commits)
    overlay = json.loads((out_dir / "overlay.json").read_text(encoding="utf-8"))
    summary = flow.summary(overlay)
    print(f"       {'⚠ 낡음' if stale else '✅'} 만든 시각 {meta.built_at} · graphify {meta.graphify}"
          f" · 노드 {len(graph['nodes'])} · 엣지 {len(graph['links'])}"
          f" · 레포에 붙은 엣지 {summary['repo_level']}"
          f" · 끝점 {summary.get('endpoints', 0)}(등재 {summary.get('endpoints_registered', 0)}"
          f" · 서빙 미상 {summary.get('endpoints_unserved', 0)}"
          f" · 자원까지 {summary.get('endpoints_traced', 0)} · 막힘 {summary.get('endpoints_blocked', 0)})")
    human = [name for name in ("flow.html", "wiki/index.md") if (out_dir / name).exists()]
    if human:
        print(f"       사람용 {' · '.join(human)}")
    index = gb.read_index(out_dir)
    if index is None:
        print("       ⚠ 심볼 인덱스 없음 — `code graph`로 다시 만든다")
    else:
        s = index.summary()
        calls = s["edges"].get("calls", {"exact": 0, "candidate": 0})
        print(f"       심볼 {s['symbols']} · 엣지 {s['edges_total']} (calls 확실 {calls['exact']} · 추정 "
              f"{calls['candidate']}) · 파싱 실패 {s['parse_errors']} · `code check`로 검증")
    rows = gb.read_site(out_dir, fct)
    listed = ", ".join(meta.sites) if meta.sites else "(옛 번들 — 사이트 파일 없음)"
    print(f"       사이트 {fct}: " + (f"덮은 값 {len(rows)}개" if rows is not None
                                   else f"파일 없음 — `code graph --gbm {gbm}`으로 다시 만든다")
          + f" · 번들의 사이트: {listed}")
    for line in stale + problems:
        print(f"       ⚠ {line}")
    return 0


def _symbol_index(args, env, *, site_values: bool = False):
    """번들의 심볼 인덱스와 그것이 낡았는지 — `code check`·`callers`·`path`·`uses`가 같이 쓴다. 없으면 None(말하고).
    `site_values`면 그 사이트 층이 덮은 값을 자원 이름에 입힌다(조사와 같은 조립). `code check`는 번들 그대로를 잰다."""
    from src.knowledge import graph_build as gb

    site, gbm, fct, _ = _code_site(args, env)
    index = gb.read_index(_graph_dir(args, env, gbm))
    if index is None:
        print("  심볼 인덱스가 없다 — `python -m src code graph`로 만든다")
        return None
    if site_values:
        from src.knowledge import flow
        index = index.renamed(flow.site_renames(gb.read_site(_graph_dir(args, env, gbm), fct) or []))
    try:
        code = _build_code(site, gbm, fct, knowledge_root=_knowledge_root(args), clock=_clock(args, env))
    except (ConfigError, FileNotFoundError) as exc:
        raise SystemExit(f"지식 층을 읽을 수 없다 — {exc}")
    commits, _ = _resolved_commits(site, code)
    stale = [f"{r}: 인덱스는 {c[:12]}, 배포는 {commits.get(r, '?')[:12]}" for r, c in sorted(index.commits.items())
             if commits.get(r) != c]
    return index, code, gbm, stale


def _query_setup(args, env):
    """질의 명령의 공통 준비 — 인덱스, 호출 그래프, 심볼에 `[레포 · 서비스]`를 붙이는 함수. 낡았으면 먼저 말한다."""
    from src.knowledge import query as qy
    from src.knowledge.flow import owner
    from src.knowledge.loader import load_topology

    got = _symbol_index(args, env, site_values=True)
    if got is None:
        return None
    index, _, gbm, stale = got
    for line in stale:
        print(f"  ⚠ 낡음 {line} — `code graph`로 다시 만든다")
    try:
        topology = load_topology(_knowledge_root(args), gbm)
    except (ConfigError, FileNotFoundError):
        topology = None

    def where(sid: int) -> str:
        s = index.symbols[sid]
        svc = owner(s.file, s.repo, topology)[0] if topology is not None else None
        return f"[{s.repo}{' · ' + svc if svc else ''}]"

    return index, qy.Graph(index), where


def _pick(index, text: str) -> int | None:
    """이름 하나를 고른다. 여럿이면 후보를 보여 주고 멈춘다 — 코드가 고르면 사람이 모르는 사이에 엉뚱한 함수를 본다."""
    from src.knowledge import query as qy

    found = qy.find(index, text)
    if not found:
        print(f"  {text}: 인덱스에 없다 — 이름 끝부분(`Class.method`)이나 `파일:qualname`으로")
        return None
    if len(found) > 1:
        for line in qy.ambiguous_lines(index, text, found):
            print("  " + line)
        return None
    return found[0]


def cmd_code_callers(args, env) -> int:
    """이 함수를 누가 부르나 — 진입점까지(11d 6c-1). 11b의 추적기는 끝점에서 앞으로만 걸어서 이 질문에 답이 없었다.
    조립은 리드의 `code.callers`와 같은 함수(`query.callers_lines`)다 — 사람이 CLI로 맞춰 본 답이 리드가 받는 답이다."""
    from src.knowledge import query as qy

    got = _query_setup(args, env)
    if got is None:
        return 1
    index, graph, where = got
    sid = _pick(index, args.name)
    if sid is None:
        return 1
    for line in qy.callers_lines(index, graph, sid, where=where, cap=None if args.all else 10, more=" (`--all`로 전부)"):
        print("  " + line)
    return 0


def cmd_code_path(args, env) -> int:
    """A에서 B로 가는 호출 경로 — 짧은 것부터 셋(11d 6c-1)."""
    from src.knowledge import query as qy

    got = _query_setup(args, env)
    if got is None:
        return 1
    index, graph, _ = got
    a, b = _pick(index, args.src), _pick(index, args.dst)
    if a is None or b is None:
        return 1
    found, cut = qy.paths(graph, qy.targets(index, a), qy.targets(index, b))
    if not found:
        back, _ = qy.paths(graph, qy.targets(index, b), qy.targets(index, a), k=1)
        print(f"  {qy.short(index, a, graph.modules)} → {qy.short(index, b, graph.modules)}: "
              f"{qy.MAX_HOPS}단계 안에 호출 경로가 없다" + (" (반대 방향은 있다)" if back else ""))
        if cut:
            print(f"  ⚠ 상한에 걸려 다 못 봤다")
        return 1
    for p in found:
        print(f"  {qy.render_path(index, p)}")
    if cut:
        print(f"  ⚠ 상한에 걸려 다 못 봤다 — 더 긴 경로가 있을 수 있다")
    return 0


def cmd_code_uses(args, env) -> int:
    """이 자원(컬렉션·토픽·키·그룹)을 쓰고 읽는 함수와 그 진입점(11d 6c-1)."""
    from src.knowledge import query as qy

    got = _query_setup(args, env)
    if got is None:
        return 1
    index, graph, where = got
    found = qy.uses(index, args.name)
    if not found:
        print(f"  {args.name}: 이 이름의 자원을 쓰거나 읽는 함수가 인덱스에 없다 — `code flow`의 허브 목록에서 이름을 본다")
        return 1
    for line in qy.uses_lines(index, graph, found, where=where, cap=None if args.all else 10, more=" (`--all`로 전부)"):
        print("  " + line)
    return 0


def cmd_code_check(args, env) -> int:
    """심볼 인덱스의 정확도를 **숫자로** 낸다(11d 하네스) — 불변식·정밀도 표본·재현율 표본, 여덟 줄 이내.
    불변식이 깨지면 1. 정밀도·재현율은 숫자일 뿐이다(사람이 옮겨 적는다)."""
    from src.knowledge import index as indexing
    from src.knowledge import index_check as chk

    got = _symbol_index(args, env)
    if got is None:
        return 1
    index, code, _, stale = got

    async def go():
        files, counts, cache = {}, {}, {}
        for repo in sorted(index.commits):
            if repo not in code.pinned():
                continue
            source = code.source_for(repo)
            names = [f for f in await source.files() if indexing.is_indexed(f)]
            files[repo] = set(names)
            for path in names:
                text = await source.read(path)
                cache[(repo, path)] = text
                counts[(repo, path)] = len(text.splitlines()) if text else 0

        async def read(repo: str, path: str) -> str | None:
            if (repo, path) not in cache:
                cache[(repo, path)] = await code.source_for(repo).read(path) if repo in code.pinned() else None
            return cache[(repo, path)]

        problems = chk.invariants(index, files=files, line_counts=counts)
        precision = await chk.precision_sample(index, read, n=args.sample, seed=args.seed)
        recall = await chk.recall_sample(index, read)
        lines = chk.report(index, problems, precision, recall)
        if getattr(args, "unresolved", False):
            lines += chk.unresolved_report(index)
        return lines, problems

    lines, problems = _run(go())
    for line in stale:
        print(f"  ⚠ 낡음 {line} — `code graph`로 다시 만든다")
    for line in lines:
        print(f"  {line}")
    return 1 if problems else 0


def cmd_code_trace(args, env) -> int:
    """사람이 끝점 하나의 함수 사슬을 본다 — 리드가 `code.trace`로 받는 것과 같은 줄들(11b)."""
    from src.knowledge import flow
    from src.knowledge import graph_build as gb

    _, gbm, fct, _ = _code_site(args, env)
    got = gb.read_bundle(_graph_dir(args, env, gbm))
    if got is None:
        raise SystemExit("그래프가 없다 — `python -m src code graph`로 만든다")
    graph, _ = got
    graph = flow.apply_site(graph, gb.read_site(_graph_dir(args, env, gbm), fct) or [])
    path = flow.endpoint_path(_shell_path(args.path, graph) or "")
    node_id = flow.endpoint_id(path)
    if not any(n["id"] == node_id for n in graph["nodes"]):
        print(f"  {path}: 그래프의 끝점에 없다 — `code flow`나 등재 항목의 path 그대로 쓴다"
              + (" (Git Bash가 `/`로 시작하는 인자를 바꿨다 — 명령 앞에 `MSYS_NO_PATHCONV=1`을 붙인다)"
                 if flow.shell_mangled(path) else ""))
        return 1
    lines = flow.trace_lines(graph, node_id)
    if lines is None:
        print(f"  {path}: 추적이 안 된 끝점이다 — 라우트 선언을 못 찾았거나 서빙 서비스를 모른다")
        return 1
    for line in lines:
        print("  " + line)
    return 0


def _shell_path(arg: str | None, graph: dict) -> str | None:
    """Git Bash가 바꾼 끝점 path를 되돌린다(`flow.unmangle`) — 되돌렸으면 무엇으로 읽었는지 한 줄 말한다."""
    from src.knowledge import flow

    back = flow.unmangle(arg, (n.get("label", "") for n in graph["nodes"] if n.get("type") == "endpoint")) if arg else None
    if back is None:
        return arg
    print(f"  Git Bash가 바꾼 인자를 {back}로 읽었다 (명령 앞에 `MSYS_NO_PATHCONV=1`을 붙이면 안 바뀐다)")
    return back


def cmd_code_flow(args, env) -> int:
    """사람이 그래프를 본다. 이름 하나면 이웃, `--to`가 있으면 흐름 경로, 없으면 허브."""
    from src.knowledge import flow
    from src.knowledge import graph_build as gb

    _, gbm, fct, _ = _code_site(args, env)
    got = gb.read_bundle(_graph_dir(args, env, gbm))
    if got is None:
        raise SystemExit("그래프가 없다 — `python -m src code graph`로 만든다")
    graph, meta = got
    graph = flow.apply_site(graph, gb.read_site(_graph_dir(args, env, gbm), fct) or [])
    args.name, args.to = _shell_path(args.name, graph), _shell_path(args.to, graph)

    def line(e) -> str:
        return (f"  {flow.describe(graph, e['source'])} —{e['relation']}→ "
                f"{flow.describe(graph, e['target'])}   [{e.get('confidence', '?')}] "
                f"{e.get('source_file', '?')}:{e.get('source_location', '?')}")

    if args.to:
        path = flow.shortest_path(graph, args.name or "", args.to)
        if not path:
            print(f"  {args.name} → {args.to}: 데이터가 흐르는 경로가 없다"
                  + ("" if flow.shortest_path(graph, args.name or "", args.to, undirected=True) is None
                     else " (방향을 무시하면 관계는 있다)"))
            return 1
        print("  " + flow.render_path(graph, path))
        for e in path:
            print(line(e))
        return 0
    if args.name:
        near = flow.neighbors(graph, args.name, depth=args.depth)
        if not near:
            print(f"  {args.name}: 그래프에 없다")
            return 1
        for e in near:
            print(line(e))
        return 0
    # 허브 — 연결이 많은 자원부터. god node의 우리 판이다.
    degree: dict[str, int] = {}
    for e in graph["links"]:
        for end in (e["source"], e["target"]):
            degree[end] = degree.get(end, 0) + 1
    resources = [n for n in graph["nodes"] if n.get("type") in ("topic", "group", "collection", "rediskey")]
    print(f"  {gbm}/{fct} · 커밋 " + ", ".join(f"{r}@{c[:12]}" for r, c in sorted(meta.commits.items())))
    for n in sorted(resources, key=lambda n: -degree.get(n["id"], 0))[:15]:
        print(f"  {degree.get(n['id'], 0):3d}  {n.get('type', '?'):10} {n['label']}")
    return 0


def cmd_code_read(args, env) -> int:
    """**배포된 커밋의 파일 하나를 실제로 읽는다.**

    `code status`는 파일이 *있는지*만 본다(`git cat-file -e`). 이 명령이 없으면
    "`git show <배포커밋>:경로`가 실제로 내용을 돌려주는가" — 11a의 핵심 계약 —
    를 확인할 방법이 없고, 2차에서 문제가 나면 1차 탓인지 2차 탓인지 섞인다.

    3b의 `peek redis`가 한 역할과 같다. 2차의 `code.read` action이 같은 포트를 쓴다.
    """
    from src.infrastructure.git_reader import RealCodeReader
    from src.knowledge.loader import load_deployment, load_topology

    site, gbm, site_fct, _ = _code_site(args, env)
    root = _knowledge_root(args)
    try:
        topology = load_topology(root, gbm)
        deployment = load_deployment(root, gbm)
    except (ConfigError, FileNotFoundError) as exc:
        raise SystemExit(f"지식 층을 읽을 수 없다 — {exc}")

    service = topology.services.get(args.service)
    if service is None:
        raise SystemExit(f"없는 서비스 — {args.service}. "
                         f"아는 것: {', '.join(sorted(topology.services)) or '없음'}")
    pin = deployment.pin_for(args.service, fct=site_fct)
    path = args.path.replace("{gbm}", gbm).replace("{fct}", site_fct)

    reader = RealCodeReader(list(site.code.repos), clock=_clock(args, env))
    result = _run(reader.show(service.repo, pin.commit, path))

    note = "" if pin.how == "declared" else "  (배포 커밋을 가정했다)"
    print(f"  {args.service} → {service.repo} @ {pin.commit}{note}")
    print(f"  {path}\n")
    if result.status == "error":
        print(f"  ❌ {result.error}", file=sys.stderr)
        return 1
    print(result.data)
    if not result.envelope.complete:
        # 잘린 것을 조용히 두면 "그 파일에 그 문자열이 없다"를 단정하게 된다.
        print(f"\n  ⚠ 잘렸다: {result.envelope.truncated_reason}", file=sys.stderr)
    return 0


def _build_code(site, gbm: str, fct: str, *, knowledge_root, clock):
    """`DeployedCode` 하나. **CLI와 조사가 같은 조립을 쓴다.**

    따로 조립하면 CLI로는 되는데 조사에서는 안 되는(또는 반대의) 상태가 생기고,
    그때 원인이 1차인지 2차인지 섞인다 — 규율 8이 막는 그 실패다.
    """
    from src.infrastructure.deployed_code import DeployedCode
    from src.infrastructure.git_reader import RealCodeReader
    from src.knowledge.loader import load_deployment, load_topology

    topology = load_topology(knowledge_root, gbm)
    deployment = load_deployment(knowledge_root, gbm)
    reader = RealCodeReader(list(site.code.repos), clock=clock)
    return DeployedCode(reader, topology, deployment, gbm=gbm, fct=fct, clock=clock)


def _code_if_ready(site, gbm: str, fct: str, *, knowledge_root, clock, graph_dir=None):
    """조사용. `(code, 서비스 이름들, 흐름 그래프 또는 None, 그래프 한 줄)`. 코드를 못 읽는
    사이트면 **`(None, (), None, …)`** — 던지지 않는다.

    토폴로지를 안 적은 사이트가 정상이다(코드 확보는 선택이다). 여기서 죽으면
    코드와 무관한 조사까지 통째로 못 돈다. 대신 비어 있으면 `code.*`가 목록에도
    예시에도 안 나가므로, 리드가 없는 문을 두드릴 일도 없다.

    흐름 그래프는 **배포 커밋과 같을 때만** 붙인다. 낡은 배선을 실으면 리드가 떠 있지도
    않은 코드의 흐름을 믿는다 — 그래서 없음과 낡음은 같은 취급(None)이다.
    """
    if not site.code.repos:
        return None, (), None, "코드 없음"
    try:
        code = _build_code(site, gbm, fct, knowledge_root=knowledge_root, clock=clock)
    except Exception:                                              # noqa: BLE001
        return None, (), None, "코드 없음"
    graph, note = (None, "그래프 없음")
    index = None
    if graph_dir is not None:
        commits, _ = _resolved_commits(site, code)
        graph, note = _flow_graph_if_fresh(Path(graph_dir), commits)
        if graph is not None:
            # 번들은 GBM 하나다(11e-2) — 이 사이트 층이 덮은 값을 입혀야 리드가 보는 이름(컨슈머 그룹 등)이 이 사이트 것이다.
            from src.knowledge import flow as flowmod
            from src.knowledge import graph_build as gbm_bundle
            rows = gbm_bundle.read_site(Path(graph_dir), fct)
            if rows is None:
                note += f" · 사이트 덮어쓰기 없음(`code graph --gbm {gbm}`으로 다시 만든다)"
            else:
                graph = flowmod.apply_site(graph, rows)
                note += f" · 사이트 값 {len(rows)}개"
            # 심볼 인덱스(11d)는 같은 번들에 같은 커밋으로 박혀 있다 — 그래프가 신선하면 인덱스도 신선하다.
            # 옛 번들(6a 전)엔 없다 — 그러면 역질문 action이 목록에서 빠진다(`briefing._hidden`).
            from src.knowledge import graph_build as gb
            index = gb.read_index(Path(graph_dir))
            if index is None:
                note += " · 심볼 인덱스 없음(`code graph`로 다시 만든다)"
            elif rows:
                # 인덱스의 함수별 자원도 그 사이트의 이름으로 — 리드가 <데이터 흐름>에서 본 이름으로 `code.uses`를 묻는다.
                index = index.renamed(flowmod.site_renames(rows))
    code.attach_flow_graph(graph)
    code.attach_index(index)
    return code, code.service_names(), graph, note


def _flow_graph_if_fresh(graph_dir: Path, commits: dict[str, str]) -> tuple[dict | None, str]:
    """번들의 오버레이와 그 상태 한 줄. 없거나 깨졌거나 **배포 커밋과 다르면 None**."""
    from src.knowledge import graph_build as gb

    got = gb.read_bundle(graph_dir)
    if got is None:
        return None, "그래프 없음 — `code graph`로 만든다"
    _, meta = got
    stale = gb.check_bundle(meta, commits)
    if stale:
        return None, "그래프 낡음 — 브리핑에 안 싣는다: " + " · ".join(stale)
    try:
        overlay = json.loads((graph_dir / "overlay.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"그래프 못 읽음 — {exc}"
    return overlay, f"그래프 실림 ({meta.built_at})"


def _deployed_code(args, env):
    site, gbm, fct, _ = _code_site(args, env)
    try:
        code = _build_code(site, gbm, fct, knowledge_root=_knowledge_root(args),
                           clock=_clock(args, env))
    except (ConfigError, FileNotFoundError) as exc:
        raise SystemExit(f"지식 층을 읽을 수 없다 — {exc}")
    return code, gbm, fct


def _show_probe(result) -> int:
    """`ProbeResult` 하나를 화면에 옮긴다. **잘린 것을 조용히 두지 않는다.**"""
    print(f"  {result.source}\n")
    if result.status == "error":
        print(f"  ❌ {result.error}", file=sys.stderr)
        return 1
    if isinstance(result.data, str):
        print(result.data)
    else:
        print(json.dumps(result.data, ensure_ascii=False, indent=2))
    if not result.envelope.complete:
        print(f"\n  ⚠ {result.envelope.truncated_reason}", file=sys.stderr)
    return 0


def cmd_code_services(args, env) -> int:
    """무엇을 조사할 수 있나 — 리드가 `code.services`로 보는 것과 같은 답."""
    code, gbm, fct = _deployed_code(args, env)
    print(f"  {gbm}/{fct}")
    return _show_probe(_run(code.services()))


def cmd_code_config(args, env) -> int:
    """그 서비스가 배포 시점에 **실제로 보는 설정** — 층을 전부 합친 결과.

    `code read`가 파일 하나를 보여 준다면 이건 "이름이 무엇인가"에 답한다.
    층 하나만 읽으면 위 층이 덮어쓴 값을 사실로 단정하기 때문에 둘이 다른 명령이다.
    """
    code, gbm, fct = _deployed_code(args, env)
    print(f"  {gbm}/{fct} — 층은 {fct} 기준")
    return _show_probe(_run(code.config(args.service)))


def cmd_case_trace(args, env) -> int:
    """트레이스를 **붙여넣을 수 있는 크기**로 줄인다.

    사내에서 돌리는 사람과 고치는 사람이 다르고 그 사이가 손으로 옮기는 대화라,
    프롬프트 전문은 건너올 수가 없다. 그래서 지금까지 "리드가 무엇을 보고 무엇을
    뱉었는지"가 한 번도 안 건너왔고, 매번 코드에서 역추적했다.
    """
    from src.application.trace_digest import digest

    folder = Path(args.trace) / args.case_id
    if not folder.is_dir():
        raise SystemExit(f"트레이스 폴더가 없다 — {folder}. "
                         f"`case investigate {args.case_id} --trace {args.trace}`로 남긴다")
    entries = [(path.name, path.read_text(encoding="utf-8"))
               for path in sorted(folder.glob("*.md"))]
    print("\n".join(digest(entries, brief=getattr(args, "brief", False))))
    return 0


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

    final = _run(go())
    cfg = app.investigation
    print(f"  {site.site} — {final['case'].symptom}")
    print(f"  울타리: max_rounds={cfg.max_rounds} parallel_width={cfg.parallel_width} "
          f"max_tasks={cfg.max_tasks}")
    print(f"  라운드 {final['round']} — 끝난 이유: {final['stopped_by']}")
    from src.application.diagnose import verdict_lines
    from src.application.state import CaseState
    print("\n".join(verdict_lines(CaseState.model_validate(final))))
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
    return _run(_schedule_forever(wanted, args, env, clock))


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

    facts = _run(_collect_facts(args, env, scenario))
    comments = _run(_comments(args, env, scenario, facts))
    html = render(build_blocks(facts, comments), title=scenario.title,
                  generated_at=_clock(args, env)().strftime("%Y-%m-%d %H:%M"))

    mail = None
    if not args.no_mail:
        from src.infrastructure.mail_factory import build_mail
        mail = build_mail(load_app_config(args.config_root, env=env).mail,
                          clock=_clock(args, env))

    # 기간은 `facts`가 들고 있는 것을 쓴다 — 여기서 다시 계산하면 집계한 날과
    # 제목의 날이 갈라질 수 있다(자정 직전에 돌면 실제로 갈라진다).
    published = _run(publish(
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
    ask.add_argument("--role", choices=("lead", "conclude", "report"),
                     help="그 역할의 실효 설정으로(llm_roles). 생략하면 기본 llm")
    ask.set_defaults(run=cmd_llm_ask)
    check_llm = llm_sub.add_parser("check", help="간단한 질문 묶음")
    check_llm.add_argument("--role", choices=("lead", "conclude", "report"),
                           help="그 역할의 실효 설정으로")
    check_llm.set_defaults(run=cmd_llm_check)

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

    services = code_sub.add_parser("services", help="무엇을 조사할 수 있나")
    _add_site_options(services, sub=True)
    services.set_defaults(run=cmd_code_services)

    config = code_sub.add_parser(
        "config", help="그 서비스가 실제로 보는 설정 — 층을 전부 합친 결과")
    config.add_argument("--service", required=True, help="토폴로지의 서비스 이름")
    _add_site_options(config, sub=True)
    config.set_defaults(run=cmd_code_config)

    graph = code_sub.add_parser(
        "graph", help="흐름 그래프(+graphify)를 배포 커밋에 박는다 — 네트워크 없음")
    _add_site_options(graph, sub=True)
    graph.set_defaults(run=cmd_code_graph)

    flow_cmd = code_sub.add_parser("flow", help="그래프를 본다 — 이름 하나면 이웃, --to면 흐름 경로")
    flow_cmd.add_argument("name", nargs="?", help="자원이나 서비스 이름")
    flow_cmd.add_argument("--to", help="이 이름까지 데이터가 흐르는 경로")
    flow_cmd.add_argument("--depth", type=int, default=1, help="이웃 몇 단계 (기본 1)")
    _add_site_options(flow_cmd, sub=True)
    flow_cmd.set_defaults(run=cmd_code_flow)

    check = code_sub.add_parser("check", help="심볼 인덱스 검증 — 불변식·정밀도·재현율 몇 줄 (네트워크 없음)")
    check.add_argument("--sample", type=int, default=100, help="정밀도 표본 크기 (기본 100)")
    check.add_argument("--seed", type=int, default=1, help="표본 추출 씨앗 — 같은 씨앗이면 같은 표본")
    check.add_argument("--unresolved", action="store_true",
                       help="못 푼 호출의 모양을 덧붙인다 — 공유 라이브러리 비중·수신자 묶음·버린 메서드 상위")
    _add_site_options(check, sub=True)
    check.set_defaults(run=cmd_code_check)

    callers_cmd = code_sub.add_parser("callers", help="이 함수를 누가 부르나 — 진입점까지 (심볼 인덱스, 네트워크 없음)")
    callers_cmd.add_argument("name", help="qualname · 끝부분(`Class.method`) · `파일:qualname` · `레포:qualname`")
    callers_cmd.add_argument("--all", action="store_true", help="자르지 않고 전부")
    _add_site_options(callers_cmd, sub=True)
    callers_cmd.set_defaults(run=cmd_code_callers)

    path_cmd = code_sub.add_parser("path", help="A에서 B로 가는 호출 경로 — 짧은 것부터 셋 (심볼 인덱스)")
    path_cmd.add_argument("src", help="출발 함수 (callers와 같은 이름 꼴)")
    path_cmd.add_argument("dst", help="도착 함수")
    _add_site_options(path_cmd, sub=True)
    path_cmd.set_defaults(run=cmd_code_path)

    uses_cmd = code_sub.add_parser("uses", help="이 자원을 쓰고 읽는 함수와 진입점 (심볼 인덱스)")
    uses_cmd.add_argument("name", help="컬렉션·토픽·키·그룹 이름 (정확한 이름이 없으면 부분 일치)")
    uses_cmd.add_argument("--all", action="store_true", help="자르지 않고 전부")
    _add_site_options(uses_cmd, sub=True)
    uses_cmd.set_defaults(run=cmd_code_uses)

    trace_cmd = code_sub.add_parser("trace", help="끝점 하나의 함수 사슬 — 리드가 code.trace로 받는 것")
    trace_cmd.add_argument("path", help="끝점 path (`code flow`나 등재 항목의 path 그대로)")
    _add_site_options(trace_cmd, sub=True)
    trace_cmd.set_defaults(run=cmd_code_trace)

    read = code_sub.add_parser("read", help="배포된 커밋의 파일 하나를 실제로 읽는다")
    read.add_argument("--service", required=True, help="토폴로지의 서비스 이름")
    read.add_argument("--path", required=True,
                      help="레포 안의 경로. {gbm}·{fct}를 쓸 수 있다")
    _add_site_options(read, sub=True)
    read.set_defaults(run=cmd_code_read)

    investigate = case_sub.add_parser("investigate", help="리드 LLM으로 조사한다")
    investigate.add_argument("case_id")
    investigate.add_argument("--stub-seeds", help="대상에 안 붙고 돌려 본다")
    investigate.add_argument("--trace", nargs="?", const="output/traces",
                             help="프롬프트와 날것 응답을 남긴다 (기본 output/traces)")
    investigate.set_defaults(run=cmd_case_investigate)

    trace = case_sub.add_parser(
        "trace", help="트레이스를 붙여넣을 수 있는 크기로 줄인다")
    trace.add_argument("case_id")
    trace.add_argument("--trace", default="trace", help="`investigate --trace`에 준 폴더")
    trace.add_argument("--brief", action="store_true",
                       help="손으로 옮길 때 — 이미 물은 것·예시 줄을 뺀다")
    trace.set_defaults(run=cmd_case_trace)

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


def _utf8_console() -> None:
    """stdout·stderr를 UTF-8로, 못 그리는 글자는 `?`로.

    콘솔에 직접 찍을 때는 괜찮은데 파이프나 파일로 넘기면 Windows는 로케일(cp949)을 타서
    한글이 깨지고 `—`(U+2014)에서 `UnicodeEncodeError`로 죽는다(사내에서 `code graph … | tee`가
    마지막 줄에서 죽었다, windows.md 함정 ②). `PYTHONUTF8=1`은 그 변수를 건 사람에게만
    유효하고 서비스로 등록하면 안 따라간다 — 그래서 CLI 경계에서 코드가 정한다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass            # 대체된 스트림(테스트의 캡처 등)이면 그대로 둔다


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = parse_args(argv)
    env = _load_env(args.env_file)
    try:
        return args.run(args, env)
    except ConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
