"""기동 검증 — 스펙 §4.6.

하나라도 실패하면 기동 거부. 오류는 전부 모아 보고한다 —
밤에 조용히 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.

검사 7(deployment hash 실재)은 정적 — repo_root의 로컬 체크아웃만 본다.
검사 8(Mongo readonly 롤)은 live 접속이 필요해 check_live=True일 때만 돈다
(기본 False: "죽은 사이트가 기동을 막으면 역효과" 원칙과 양립).
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config.loader import ConfigError, load_app_config, load_registry, load_site_config
from src.infrastructure.code_repo import CodeRepoError, CodeRepoReader
from src.infrastructure.query_rules import mongo_role_problems
from src.knowledge.deployment import load_deployment
from src.knowledge.topology import load_topology, topology_problems
from src.patrol.probes import PROBES, resolve_probe


@dataclass
class BootError:
    where: str
    problem: str


async def _fetch_conn_status(cfg) -> dict:
    """RealMongo를 만들어 connection_status()를 부르는 짧은 헬퍼.

    실구현 의존성(pymongo)은 여기서만 지연 import한다 — 스텁 전용 환경에서도
    검사 1~7의 정적 검증은 이 모듈 import만으로 돌아야 하므로.
    """
    from src.infrastructure.mongo_reader import RealMongo

    m = cfg.target.mongo
    mongo = RealMongo(
        m.url, username=m.username,
        password=m.password.get_secret_value() if m.password else None,
        db=m.db, guards=cfg.target.guards,
        semaphore=asyncio.Semaphore(cfg.target.guards.max_concurrent),
        clock=lambda: datetime.now(timezone.utc),
    )
    return await mongo.connection_status()


def validate_boot(config_root: Path, *, env, repo_root: Path,
                  check_live: bool = False) -> list[BootError]:
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

        # 검사 9: 각 점검의 프로브가 레지스트리에서 해석되는가 (§4.6-9)
        for name, check in cfg.patrol.checks.items():
            probe_name = resolve_probe(check)
            if probe_name is None or probe_name not in PROBES:
                errors.append(BootError(
                    where, f"점검 {name!r}의 프로브를 해석할 수 없다"))

        repo_names = {r.name for r in cfg.target.code.repos} if cfg.target.code else set()
        for svc_name, svc in topo.services.items():
            if svc.code is not None and svc.code.repo not in repo_names:
                errors.append(BootError(
                    where, f"서비스 {svc_name!r}의 repo {svc.code.repo!r}가 config에 없다"))

        # 검사 7: deployment.yaml의 (repo, commit)이 실재하는가 (§4.6-7)
        deployment = load_deployment(knowledge_root, site.gbm, site.fct)
        if deployment is not None:
            reader = CodeRepoReader(
                {r.name: r.path for r in cfg.target.code.repos}
                if cfg.target.code else {})
            for svc_name, ver in deployment.services.items():
                try:
                    if not reader.hash_exists(ver.repo, ver.commit):
                        errors.append(BootError(
                            where, f"deployment: {svc_name}의 커밋 {ver.commit!r}이 "
                                   f"레포 {ver.repo!r}에 없다 (fetch 누락 또는 오타)"))
                except CodeRepoError as exc:
                    errors.append(BootError(where, f"deployment: {exc}"))

        # 검사 8: Mongo 계정이 readonly 롤인가 (§4.6-8) — live 접속이 필요해 opt-in
        if check_live and cfg.target.adapters == "real" and \
                cfg.target.mongo and cfg.target.mongo.username:
            try:
                status = asyncio.run(_fetch_conn_status(cfg))
                errors.extend(BootError(where, p) for p in mongo_role_problems(status))
            except Exception as exc:
                errors.append(BootError(where, f"Mongo 롤 확인 불가 — {exc}"))

    return errors
