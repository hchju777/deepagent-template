"""Redis 실구현 — redis.asyncio. TYPE 분기 읽기(string/hash), SCAN+상한, TTL. 쓰기 명령 미노출."""
import redis.asyncio as aioredis

from src.domain.envelope import Envelope
from src.domain.ports import RedisReaderPort
from src.infrastructure.guards import guarded_call


class RealRedis(RedisReaderPort):
    def __init__(self, url, password=None, *, guards, semaphore, clock):
        self._client = aioredis.from_url(url, password=password, decode_responses=True)
        self._guards, self._sem, self._clock = guards, semaphore, clock

    def _call(self, op):
        return guarded_call(op, timeout_s=self._guards.timeout_s,
                            semaphore=self._sem, clock=self._clock)

    async def get(self, key):
        async def op():
            kind = await self._client.type(key)
            if kind == "hash":
                value = await self._client.hgetall(key)
            elif kind == "none":
                value = None
            else:
                value = await self._client.get(key)
            return value, Envelope(observed_at=self._clock())
        return await self._call(op)

    async def scan(self, pattern):
        async def op():
            keys, cursor = [], 0
            while True:
                cursor, batch = await self._client.scan(cursor, match=pattern, count=200)
                keys.extend(batch)
                if cursor == 0 or len(keys) > self._guards.max_rows:
                    break
            truncated = len(keys) > self._guards.max_rows
            env = Envelope(observed_at=self._clock(), complete=not truncated,
                           truncated_reason="max_rows" if truncated else None)
            return sorted(keys)[: self._guards.max_rows], env
        return await self._call(op)

    async def ttl(self, key):
        async def op():
            return await self._client.ttl(key), Envelope(observed_at=self._clock())
        return await self._call(op)
