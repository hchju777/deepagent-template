"""Mongo 실구현 — pymongo AsyncMongoClient. find/count/aggregate 전에 규칙 검사, 상한+1로 절단 판정.

connection_status()는 포트 밖(boot 전용) — Task 9의 롤 검사가 raw dict를 그대로 소비하므로
guarded_call로 감싸지 않는다. 기동 실패는 조용히 삼키지 않고 시끄럽게 죽어야 한다(§4.6).
"""
from pymongo import AsyncMongoClient

from src.domain.envelope import Envelope, ProbeResult
from src.domain.ports import MongoReaderPort
from src.infrastructure.guards import guarded_call
from src.infrastructure.query_rules import aggregate_problems, filter_problems


class RealMongo(MongoReaderPort):
    def __init__(self, url, username=None, password=None, db=None, *, guards, semaphore, clock):
        self._client = AsyncMongoClient(url, username=username, password=password)
        self._db = self._client[db]
        self._guards, self._sem, self._clock = guards, semaphore, clock

    def _call(self, op):
        return guarded_call(op, timeout_s=self._guards.timeout_s,
                            semaphore=self._sem, clock=self._clock)

    def _rule_violation(self, msg):
        # 위반 시 DB에 나가기 전에 error ProbeResult — guarded_call을 거치지 않는다
        # (실제 I/O가 없으므로 타임아웃·세마포어 보호가 필요 없다).
        return ProbeResult(status="error", envelope=Envelope(observed_at=self._clock()), error=msg)

    async def find(self, collection, filter, *, sort=None, limit=None):
        problems = filter_problems(filter)
        if problems:
            return self._rule_violation("; ".join(problems))

        async def op():
            cap = min(limit, self._guards.max_rows) if limit else self._guards.max_rows
            cursor = self._db[collection].find(filter, sort=sort, limit=cap + 1)
            docs = [d async for d in cursor]
            truncated = len(docs) > cap
            env = Envelope(observed_at=self._clock(), complete=not truncated,
                           truncated_reason="max_rows" if truncated else None)
            return docs[:cap], env
        return await self._call(op)

    async def count(self, collection, filter):
        problems = filter_problems(filter)
        if problems:
            return self._rule_violation("; ".join(problems))

        async def op():
            n = await self._db[collection].count_documents(filter)
            return n, Envelope(observed_at=self._clock())
        return await self._call(op)

    async def aggregate(self, collection, pipeline):
        problems = aggregate_problems(pipeline)
        if problems:
            return self._rule_violation("; ".join(problems))

        async def op():
            cap = self._guards.max_rows
            docs = []
            async for doc in self._db[collection].aggregate(pipeline):
                docs.append(doc)
                if len(docs) > cap:          # 상한+1에서 멈춰 무제한 결과 집합을 막는다
                    break
            truncated = len(docs) > cap
            env = Envelope(observed_at=self._clock(), complete=not truncated,
                           truncated_reason="max_rows" if truncated else None)
            return docs[:cap], env
        return await self._call(op)

    async def connection_status(self) -> dict:
        """포트 밖, boot 전용 — connectionStatus 커맨드 결과를 그대로 돌려준다."""
        return await self._db.command({"connectionStatus": 1, "showPrivileges": True})
