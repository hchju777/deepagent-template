"""CLI 엔트리 — 계획 1 범위: registry / config show / knowledge validate.

계획 4~5에서 patrol, chat, case 서브커맨드가 추가된다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.boot import validate_boot
from src.config.loader import ConfigError, load_registry, load_site_config


def _add_common(parser):
    parser.add_argument("--config-root", default="config")
    parser.add_argument("--repo-root", default=".")


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
    _add_common(p_validate)

    args = parser.parse_args(argv)
    config_root = Path(args.config_root)

    if args.command == "registry":
        registry = load_registry(config_root)
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
        errors = validate_boot(config_root, env=env, repo_root=Path(args.repo_root))
        if not errors:
            print("OK")
            return 0
        for e in errors:
            print(f"[{e.where}] {e.problem}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
