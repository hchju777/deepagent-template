"""인메모리 대역 — 테스트가 실제 시스템 없이 돌게 한다.

같은 포트를 구현하므로 판정 로직은 어느 쪽이 꽂혔는지 모른다. 이게 성립하려면
스텁이 **실구현과 같은 계약**을 지켜야 한다: 키가 없으면 error가 아니라 None,
잘리면 봉투가 말한다, 필터 연산자 검사도 똑같이 한다.

계약이 갈라지면 테스트는 통과하는데 사내에서 깨진다 — 제일 나쁜 모양이다.
"""
from typing import Any

from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import (KafkaInspectorPort, MongoReaderPort, RedisReaderPort,
                              RestProberPort)
from src.infrastructure.mongo_reader import filter_problems, to_jsonable
from src.infrastructure.rest_prober import prepare_params


class StubRedisReader(RedisReaderPort):
    def __init__(self, values: dict[str, Any] | None = None, *, clock: Clock):
        self._values = dict(values or {})
        self._clock = clock

    async def get(self, key: str) -> ProbeResult:
        found = self._values.get(key)
        data = None if found is None else {"type": "string", "value": found}
        return ProbeResult.succeeded(data, source=f"stub-redis:{key}", clock=self._clock)

    async def scan(self, pattern: str) -> ProbeResult:
        import fnmatch
        keys = sorted(k for k in self._values if fnmatch.fnmatch(k, pattern))
        return ProbeResult.succeeded(keys, source=f"stub-redis:SCAN {pattern}", clock=self._clock)

    async def ttl(self, key: str) -> ProbeResult:
        value = -1 if key in self._values else -2      # Redis 규약 그대로
        return ProbeResult.succeeded(value, source=f"stub-redis:TTL {key}", clock=self._clock)


class _UnsupportedOperator(Exception):
    """스텁 내부 신호 — 밖으로 나가기 전에 error ProbeResult로 바뀐다."""


class StubMongoReader(MongoReaderPort):
    def __init__(self, collections: dict[str, list[dict]] | None = None, *, clock: Clock):
        self._collections = {k: list(v) for k, v in (collections or {}).items()}
        self._clock = clock

    # 스텁이 흉내내는 연산자. **여기 없는 연산자는 조용히 무시하지 않고 거부한다** —
    # 무시하면 필터가 안 걸린 채 전부 돌려주고, 테스트는 초록인데 실서버에서만
    # 다른 결과가 나온다. 거짓 초록은 스텁이 낼 수 있는 최악의 실패다.
    _OPERATORS = {
        "$eq": lambda v, want: v == want,
        "$ne": lambda v, want: v != want,
        "$gt": lambda v, want: v is not None and v > want,
        "$gte": lambda v, want: v is not None and v >= want,
        "$lt": lambda v, want: v is not None and v < want,
        "$lte": lambda v, want: v is not None and v <= want,
        "$in": lambda v, want: v in want,
        "$nin": lambda v, want: v not in want,
        "$exists": lambda v, want: (v is not None) == bool(want),
    }

    def _match_one(self, value, condition) -> bool:
        if not isinstance(condition, dict) or not any(
                isinstance(k, str) and k.startswith("$") for k in condition):
            return value == condition
        for operator, want in condition.items():
            handler = self._OPERATORS.get(operator)
            if handler is None:
                raise _UnsupportedOperator(operator)
            try:
                if not handler(value, want):
                    return False
            except TypeError:
                # 문자열과 숫자를 비교하는 등 — 실서버라면 타입별 순서 규칙이
                # 있지만 스텁은 흉내내지 않는다. 안 맞는 것으로 본다.
                return False
        return True

    def _match(self, doc: dict, filter: dict) -> bool:
        return all(self._match_one(doc.get(k), v) for k, v in filter.items())

    async def find(self, collection: str, filter: dict, *, sort=None, limit=None,
                   projection=None) -> ProbeResult:
        source = f"stub-mongo:{collection} find={filter}"
        problems = filter_problems(filter)
        if problems:
            return ProbeResult.failed("; ".join(problems), source=source, clock=self._clock)
        try:
            rows = [to_jsonable(d) for d in self._collections.get(collection, [])
                    if self._match(d, filter)]
        except _UnsupportedOperator as exc:
            return ProbeResult.failed(f"스텁이 흉내내지 않는 연산자 — {exc}. "
                                      f"이 질의는 실서버로만 검증할 수 있다",
                                      source=source, clock=self._clock)
        if projection:
            # 실구현이 필드를 좁히는데 스텁이 전부 돌려주면, 투영에서 빠진 필드를
            # 읽는 코드가 스텁으로는 통과하고 실서버에서만 깨진다.
            keep = set(projection)
            rows = [{k: v for k, v in row.items() if k in keep} for row in rows]
        truncated = None
        if limit is not None and len(rows) > limit:
            rows, truncated = rows[:limit], f"limit={limit}에 걸림 — 더 있다"
        return ProbeResult.succeeded(rows, source=source, clock=self._clock,
                                     truncated_reason=truncated)

    async def count(self, collection: str, filter: dict) -> ProbeResult:
        source = f"stub-mongo:{collection} count={filter}"
        problems = filter_problems(filter)
        if problems:
            return ProbeResult.failed("; ".join(problems), source=source, clock=self._clock)
        try:
            total = sum(1 for d in self._collections.get(collection, [])
                        if self._match(d, filter))
        except _UnsupportedOperator as exc:
            return ProbeResult.failed(f"스텁이 흉내내지 않는 연산자 — {exc}. "
                                      f"이 질의는 실서버로만 검증할 수 있다",
                                      source=source, clock=self._clock)
        return ProbeResult.succeeded(total, source=source, clock=self._clock)


class StubKafkaInspector(KafkaInspectorPort):
    def __init__(self, topics: dict[str, list[dict]] | None = None,
                 lags: dict[str, int] | None = None, *, clock: Clock):
        self._topics = {k: list(v) for k, v in (topics or {}).items()}
        self._lags = dict(lags or {})
        self._clock = clock

    async def group_offsets(self, group: str) -> ProbeResult:
        lag = self._lags.get(group, 0)
        return ProbeResult.succeeded(
            {"group": group, "total_lag": lag,
             "partitions": [{"topic": "stub", "partition": 0, "committed": 0,
                             "end": lag, "lag": lag}]},
            source=f"stub-kafka:group_offsets {group}", clock=self._clock)

    async def tail(self, topic: str, *, limit: int = 10) -> ProbeResult:
        messages = self._topics.get(topic, [])[-limit:]
        return ProbeResult.succeeded({"topic": topic, "messages": messages},
                                     source=f"stub-kafka:tail {topic}", clock=self._clock)


class StubRestProber(RestProberPort):
    def __init__(self, cfg, responses: dict[str, Any] | None = None, *, clock: Clock):
        self._cfg = cfg
        self._responses = dict(responses or {})
        self._clock = clock

    async def query(self, entry: str, params: dict) -> ProbeResult:
        source = f"stub-rest:{entry}"
        spec = self._cfg.entries.get(entry) if self._cfg else None
        if spec is None:
            return ProbeResult.failed(f"등재되지 않은 항목 — {entry}",
                                      source=source, clock=self._clock)
        params, problems, _ = prepare_params(spec, params)
        if problems:
            return ProbeResult.failed("파라미터 거부 — " + "; ".join(problems),
                                      source=source, clock=self._clock)
        if entry not in self._responses:
            return ProbeResult.failed("HTTP 404 — 스텁에 준비된 응답이 없다",
                                      source=source, clock=self._clock)
        return ProbeResult.succeeded(
            {"request": {"method": spec.method, "path": spec.path, "params": params},
             "status": 200, "response": self._responses[entry]},
            source=source, clock=self._clock)
