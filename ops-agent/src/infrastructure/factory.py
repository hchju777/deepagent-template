"""스텁 ↔ 실구현 전환의 **유일한 지점**.

여기 하나뿐이어야 하는 이유는 규율 8과 같다 — 조립을 여러 곳이 각자 베끼면
언젠가 한 경로만 다른 어댑터를 쓴다. "CLI로는 되는데 순찰에서는 안 된다"는
증상으로 나타나고, 원인은 조립이 두 벌이라는 사실이다.

`seeds`가 주어지면 스텁, 없으면 실구현이다. **가짜 데이터가 config가 아니라
인자인 이유**: 실제 대상에 붙일 때는 인자를 빼면 되고, 빼는 것을 잊을 수 없다.
config에 남는 설정이면 "실전환 전에 지워라"를 체크리스트에 적어야 하고,
그건 사람의 기억에 기대는 안전이다.
"""
from typing import Any

from src.config.schema_site import SiteConfig
from src.domain.base import Clock


class Adapters:
    """사이트 하나에 붙는 어댑터 묶음. 없는 시스템은 None이다."""

    def __init__(self, *, redis=None, mongo=None, kafka=None, rest=None):
        self.redis = redis
        self.mongo = mongo
        self.kafka = kafka
        self.rest = rest

    def available(self) -> list[str]:
        return [name for name in ("redis", "mongo", "kafka", "rest")
                if getattr(self, name) is not None]

    async def close(self) -> None:
        for adapter in (self.redis, self.mongo):
            if adapter is not None and hasattr(adapter, "close"):
                try:
                    await adapter.close()
                except Exception:                                  # noqa: BLE001
                    pass


def build_adapters(site: SiteConfig, *, clock: Clock,
                   seeds: dict[str, Any] | None = None) -> Adapters:
    infra = site.infra
    if seeds is not None:
        from src.infrastructure.stubs import (StubKafkaInspector, StubMongoReader,
                                              StubRedisReader, StubRestProber)
        return Adapters(
            redis=StubRedisReader(seeds.get("redis"), clock=clock) if infra.redis else None,
            mongo=StubMongoReader(seeds.get("mongo"), clock=clock) if infra.mongodb else None,
            kafka=StubKafkaInspector(seeds.get("kafka"), seeds.get("lags"),
                                     clock=clock) if infra.kafka else None,
            rest=StubRestProber(infra.rest, seeds.get("rest"),
                                clock=clock) if infra.rest else None)

    # 지연 import — 스텁만 쓰는 환경에서 redis/pymongo/aiokafka를 요구하지 않는다.
    from src.infrastructure.kafka_inspector import RealKafkaInspector
    from src.infrastructure.mongo_reader import RealMongoReader
    from src.infrastructure.redis_reader import RealRedisReader
    from src.infrastructure.rest_prober import RealRestProber
    return Adapters(
        redis=RealRedisReader(infra.redis, clock=clock) if infra.redis else None,
        mongo=RealMongoReader(infra.mongodb, clock=clock) if infra.mongodb else None,
        kafka=RealKafkaInspector(infra.kafka.consumer, clock=clock) if infra.kafka else None,
        rest=RealRestProber(infra.rest, clock=clock) if infra.rest else None)
