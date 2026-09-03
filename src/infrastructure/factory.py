"""SiteConfig → 어댑터 세트 조립 — stub|real 전환의 유일한 지점.

세마포어는 사이트당 하나를 모든 어댑터가 공유한다: "이 사이트에 대한 동시
요청 상한"이지 어댑터별 상한이 아니다 (스펙 §4.1 guards.max_concurrent).
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.config.schema_site import SiteConfig
from src.infrastructure.code_repo import CodeRepoReader
from src.infrastructure.kafka_inspector import RealKafka
from src.infrastructure.mongo_reader import RealMongo
from src.infrastructure.redis_reader import RealRedis
from src.infrastructure.rest_prober import RealRest
from src.infrastructure.stubs import StubKafka, StubMongo, StubRedis, StubRest
from src.knowledge.topology import Topology


@dataclass
class StubSeeds:
    redis_data: dict[str, Any] = field(default_factory=dict)
    redis_ttls: dict[str, int] = field(default_factory=dict)
    mongo_collections: dict[str, list[dict]] = field(default_factory=dict)
    kafka_messages: dict[str, list[dict]] = field(default_factory=dict)
    kafka_offsets: dict[str, dict] = field(default_factory=dict)
    rest_responses: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterSet:
    redis: Any = None
    mongo: Any = None
    kafka: Any = None
    rest: Any = None
    code: CodeRepoReader | None = None
    semaphore: asyncio.Semaphore | None = None


def _rest_allowlist(topology: Topology) -> set[str]:
    return {loc.removeprefix("rest:") for loc in topology.locators()
            if loc.startswith("rest:")}


def build_adapters(cfg: SiteConfig, topology: Topology, *, clock,
                   stub_seeds: StubSeeds | None = None) -> AdapterSet:
    guards = cfg.target.guards
    sem = asyncio.Semaphore(guards.max_concurrent)
    seeds = stub_seeds or StubSeeds()
    allowed = _rest_allowlist(topology)
    out = AdapterSet(semaphore=sem)

    if cfg.target.adapters == "stub":
        if cfg.target.redis:
            out.redis = StubRedis(seeds.redis_data, seeds.redis_ttls,
                                  max_rows=guards.max_rows, clock=clock)
        if cfg.target.mongo:
            out.mongo = StubMongo(seeds.mongo_collections,
                                  max_rows=guards.max_rows, clock=clock)
        if cfg.target.kafka:
            out.kafka = StubKafka(seeds.kafka_messages, seeds.kafka_offsets,
                                  max_rows=guards.max_rows, clock=clock)
        if cfg.target.rest:
            out.rest = StubRest(seeds.rest_responses, allowed,
                                cfg.target.rest.entries, clock=clock)
    else:
        if cfg.target.redis:
            pw = cfg.target.redis.password.get_secret_value() if cfg.target.redis.password else None
            out.redis = RealRedis(cfg.target.redis.url, pw,
                                  guards=guards, semaphore=sem, clock=clock)
        if cfg.target.mongo:
            m = cfg.target.mongo
            pw = m.password.get_secret_value() if m.password else None
            out.mongo = RealMongo(m.url, username=m.username, password=pw, db=m.db,
                                  guards=guards, semaphore=sem, clock=clock)
        if cfg.target.kafka:
            out.kafka = RealKafka(cfg.target.kafka.bootstrap,
                                  guards=guards, semaphore=sem, clock=clock)
        if cfg.target.rest:
            out.rest = RealRest(cfg.target.rest.base_url, allowed,
                                cfg.target.rest.entries, cfg.target.rest.auth,
                                guards=guards, semaphore=sem, clock=clock)

    if cfg.target.code:
        out.code = CodeRepoReader({r.name: r.path for r in cfg.target.code.repos})
    return out
