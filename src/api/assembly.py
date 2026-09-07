"""`api` 프로세스 조립 — 어댑터 없이 (스펙 §3.1).

`assemble_sites`(`src/patrol/daemon.py`)와 닮았지만 **어댑터와 조사 LLM을 만들지
않는다.** 그 함수를 재사용하지 않는 이유가 그것이다 — `build_adapters`를 부르는
경로가 `api`에 하나라도 있으면 `api`가 대상 시스템에 붙는 프로세스가 된다.
`tests/api/test_boundary.py`가 import 그래프로 이것을 지킨다.

접수 LLM(`lead`)만 만든다. 접수는 조사가 아니고(LLM 호출 하나, 대상 접근 없음),
되묻는 질문이 응답에 바로 실려야 클라이언트가 폴링하지 않는다.
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.config.loader import load_app_config, load_registry, load_site_config
from src.config.schema_app import AppConfig
from src.infrastructure.checkpointer import build_persistence
from src.infrastructure.llm import build_chat_model
from src.knowledge.topology import Topology, load_topology


@dataclass
class ApiSite:
    """접수에 필요한 것만 — 어댑터도, 조사 LLM도, 엔진 의존도 없다."""
    gbm: str
    fct: str
    topology: Topology          # 접수의 locator 후보
    lead_llm: Any               # 접수 프롬프트용
    check_names: list[str] = field(default_factory=list)   # GET /checks가 레저를 읽을 키


@dataclass
class ApiRuntime:
    app: AppConfig
    sites: list[ApiSite]
    repo: Any
    store: Any
    events: Any
    ledger: Any
    labels: Any
    digests: Any
    clock: Callable[[], datetime]
    by_key: dict[tuple[str, str], ApiSite] = field(init=False)   # sites에서 유도

    def __post_init__(self):
        self.by_key = {(s.gbm, s.fct): s for s in self.sites}


def assemble_api(config_root: Path, repo_root: Path, env: dict, *, clock: Callable[[], datetime],
                 llm_factory: Callable[[str], Any] | None = None) -> ApiRuntime:
    """registry의 활성 사이트마다 토폴로지와 접수 LLM만 조립한다.

    영속화(`build_persistence`)는 우리 저장소(`mongo_store`)이지 대상 시스템이
    아니다 — 경계 밖이 아니다. 시계는 진입점(`__main__`)이 준다(규율 2).
    """
    app = load_app_config(config_root, env=env)
    registry = load_registry(config_root)

    def make_llm(profile: str) -> Any:
        if llm_factory is not None:
            return llm_factory(profile)
        return build_chat_model(profile, base_url=env.get("LLM_BASE_URL"),
                                api_key=env.get("LLM_API_KEY"))

    sites: list[ApiSite] = []
    for ref in registry.sites:
        if not ref.enabled:
            continue
        site_cfg, _provenance = load_site_config(config_root, ref.gbm, ref.fct, env=env)
        knowledge_root = repo_root / site_cfg.knowledge.root
        sites.append(ApiSite(gbm=ref.gbm, fct=ref.fct,
                             topology=load_topology(knowledge_root, ref.gbm, ref.fct),
                             lead_llm=make_llm(app.llm.profiles.lead),
                             check_names=sorted(site_cfg.patrol.checks)))

    p = build_persistence(app.store)
    return ApiRuntime(app=app, sites=sites, repo=p.repo, store=p.store, events=p.events,
                      ledger=p.ledger, labels=p.labels, digests=p.digests, clock=clock)
