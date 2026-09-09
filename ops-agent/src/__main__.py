"""CLI — 시스템의 바깥 경계.

**여기가 `datetime.now()`를 직접 부르는 유일한 곳이다.** 안쪽은 전부 주입받은
`clock`을 쓴다(1단계 규율 ②). 진짜 시계는 여기서 한 번 만들어져 아래로 흐른다.

명령:
    python -m src boot                  기동 검증 — 설정이 온전한가
    python -m src sites                 registry의 사이트 목록
    python -m src config show           병합된 설정 + 값의 출처(비밀번호는 가려진다)
    python -m src doctor                네 시스템에 실제로 붙어 본다
    python -m src peek redis  --key oee:L3
    python -m src peek mongo  --collection oee --filter '{"line":"L3"}' --limit 5
    python -m src peek kafka  --topic topic1 --limit 5
    python -m src peek rest   --entry oee_summary --params '{"line":"L3"}'
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from src.boot import validate_boot
from src.config.loader import ConfigError, load_registry, load_site_config
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
    """--gbm/--fct를 안 주면 활성 사이트가 하나일 때만 그것을 쓴다.

    둘 이상인데 안 골랐으면 **묻는다.** 임의로 첫 번째를 고르면 다른 법인의
    Redis를 들여다보게 된다.
    """
    registry = load_registry(config_root)
    active = registry.active()
    if args.gbm and args.fct:
        gbm, fct = args.gbm, args.fct
        if not any(e.gbm == gbm and e.fct == fct for e in registry.sites):
            names = ", ".join(str(e) for e in registry.sites) or "(없음)"
            raise SystemExit(f"registry에 없는 사이트 — {gbm}/{fct}. 등록된 것: {names}")
    elif len(active) == 1:
        gbm, fct = active[0].gbm, active[0].fct
    else:
        names = ", ".join(str(e) for e in active) or "(활성 사이트 없음)"
        raise SystemExit(f"--gbm/--fct로 하나를 골라라 — {names}")
    return load_site_config(config_root, gbm, fct, env=env)


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
    errors = validate_boot(args.config_root, env=env)
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
            result = await adapters.kafka.group_offsets(site.infra.kafka.consumer.group_id)
            failed += _report("kafka", result,
                              lambda r: f"감시 그룹 lag {r.data.get('total_lag', '?')}")
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
    return asyncio.run(_doctor(site, _clock()))


async def _peek(site, args, clock) -> int:
    seeds = json.loads(Path(args.stub_seeds).read_text(encoding="utf-8")) \
        if args.stub_seeds else None
    adapters = build_adapters(site, clock=clock, seeds=seeds)
    try:
        result = await _dispatch(adapters, site, args)
    finally:
        await adapters.close()
    if result is None:
        return 0
    _out(_render(result))
    return 1 if result.status == "error" else 0


async def _dispatch(adapters, site, args):
    if args.system == "redis":
        if adapters.redis is None:
            raise SystemExit("이 사이트에 redis 설정이 없다")
        if args.scan:
            return await adapters.redis.scan(args.scan)
        if args.ttl:
            return await adapters.redis.ttl(args.ttl)
        if not args.key:
            raise SystemExit("--key / --scan / --ttl 중 하나가 필요하다")
        return await adapters.redis.get(args.key)

    if args.system == "mongo":
        if adapters.mongo is None:
            raise SystemExit("이 사이트에 mongodb 설정이 없다")
        filter_ = json.loads(args.filter) if args.filter else {}
        if args.count:
            return await adapters.mongo.count(args.collection, filter_)
        return await adapters.mongo.find(args.collection, filter_,
                                         sort=[(args.sort, -1)] if args.sort else None,
                                         limit=args.limit)

    if args.system == "kafka":
        if adapters.kafka is None:
            raise SystemExit("이 사이트에 kafka 설정이 없다")
        if args.lag:
            group = args.group or site.infra.kafka.consumer.group_id
            return await adapters.kafka.group_offsets(group)
        if not args.topic:
            raise SystemExit("--topic 또는 --lag 이 필요하다")
        return await adapters.kafka.tail(args.topic, limit=args.limit)

    # rest
    if adapters.rest is None:
        raise SystemExit("이 사이트에 rest 설정이 없다")
    if args.list:
        for name, entry in sorted(site.infra.rest.entries.items()):
            required = [k for k, v in entry.params.items() if v.required]
            optional = [k for k, v in entry.params.items() if not v.required]
            print(f"  {name:<24} {entry.method:<5} {entry.path}")
            print(f"  {'':<24} 필수: {required or '없음'}  선택: {optional or '없음'}")
        return None
    if not args.entry:
        raise SystemExit("--entry 또는 --list 가 필요하다")
    return await adapters.rest.query(args.entry, json.loads(args.params) if args.params else {})


def cmd_peek(args, env) -> int:
    site, _ = _resolve_site(args.config_root, args, env)
    return asyncio.run(_peek(site, args, _clock()))


def _clock():
    """진짜 시계는 여기서만 만들어진다."""
    return lambda: datetime.now().astimezone()


# ── 파서 ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src", description="운영 모니터링 에이전트")
    parser.add_argument("--config-root", type=Path, default=Path("config"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--gbm", help="사업부 (예: mx). 활성 사이트가 하나면 생략 가능")
    parser.add_argument("--fct", help="법인/공장 (예: gumi)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("boot", help="기동 검증").set_defaults(run=cmd_boot)
    sub.add_parser("sites", help="사이트 목록").set_defaults(run=cmd_sites)

    config = sub.add_parser("config", help="설정 보기")
    config_sub = config.add_subparsers(dest="what", required=True)
    show = config_sub.add_parser("show")
    show.add_argument("--no-provenance", action="store_true", help="출처 표를 숨긴다")
    show.set_defaults(run=cmd_config_show)

    sub.add_parser("doctor", help="네 시스템에 실제로 붙어 본다").set_defaults(run=cmd_doctor)

    peek = sub.add_parser("peek", help="데이터를 하나 꺼내 본다")
    peek.set_defaults(run=cmd_peek)
    peek.add_argument("system", choices=["redis", "mongo", "kafka", "rest"])
    peek.add_argument("--stub-seeds", help="이 파일이 있으면 실접속 대신 가짜 데이터를 쓴다")
    # redis
    peek.add_argument("--key"), peek.add_argument("--scan"), peek.add_argument("--ttl")
    # mongo
    peek.add_argument("--collection"), peek.add_argument("--filter")
    peek.add_argument("--sort", help="이 필드로 내림차순")
    peek.add_argument("--count", action="store_true")
    # kafka
    peek.add_argument("--topic", help="config의 논리 이름(topic1) 또는 실제 토픽 이름")
    peek.add_argument("--lag", action="store_true", help="감시 그룹의 lag")
    peek.add_argument("--group", help="lag을 볼 그룹(생략하면 config의 group_id)")
    # rest
    peek.add_argument("--entry"), peek.add_argument("--params")
    peek.add_argument("--list", action="store_true", help="등재 항목 목록")
    # 공통
    peek.add_argument("--limit", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = _load_env(args.env_file)
    try:
        return args.run(args, env)
    except ConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
