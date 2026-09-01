"""기동 검증 — 스펙 §4.6.

하나라도 실패하면 기동 거부. 오류는 전부 모아 보고한다 —
밤에 조용히 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.
(§4.6의 7 deployment hash 실재·8 Mongo readonly 롤은 계획 2에서 추가.)
"""
from dataclasses import dataclass
from pathlib import Path

from src.config.loader import ConfigError, load_app_config, load_registry, load_site_config
from src.knowledge.topology import load_topology, topology_problems


@dataclass
class BootError:
    where: str
    problem: str


def validate_boot(config_root: Path, *, env, repo_root: Path) -> list[BootError]:
    errors: list[BootError] = []

    try:
        load_app_config(config_root)
    except ConfigError as exc:
        errors += [BootError("app", p) for p in exc.problems]

    try:
        registry = load_registry(config_root)
    except ConfigError as exc:
        return errors + [BootError("registry", p) for p in exc.problems]

    for site in registry.sites:
        if not site.enabled:
            continue
        where = f"{site.gbm}/{site.fct}"
        try:
            cfg, _ = load_site_config(config_root, site.gbm, site.fct, env=env)
        except ConfigError as exc:
            errors += [BootError(where, p) for p in exc.problems]
            continue

        knowledge_root = repo_root / cfg.knowledge.root
        try:
            topo = load_topology(knowledge_root, site.gbm, site.fct)
        except Exception as exc:   # yaml 구문 오류·스키마 위반 — 사이트 단위로 모아 보고
            errors.append(BootError(where, f"토폴로지 로드 실패: {exc}"))
            continue

        errors += [BootError(where, p) for p in topology_problems(topo)]

        known = topo.locators()
        for name, check in cfg.patrol.checks.items():
            if check.target is not None and check.target not in known:
                errors.append(BootError(
                    where, f"점검 {name!r}의 target {check.target!r}이 토폴로지로 해석되지 않는다"))

        repo_names = {r.name for r in cfg.target.code.repos} if cfg.target.code else set()
        for svc_name, svc in topo.services.items():
            if svc.code is not None and svc.code.repo not in repo_names:
                errors.append(BootError(
                    where, f"서비스 {svc_name!r}의 repo {svc.code.repo!r}가 config에 없다"))

    return errors
