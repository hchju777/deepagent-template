"""MongoDB 읽기 어댑터.

## 필터 연산자를 허용 목록으로 막는 이유

`$where`와 `$function`은 **서버에서 자바스크립트를 실행한다.** 읽기 전용
계정이라도 CPU를 태우고, 표현식에 따라 대상 DB를 멈출 수 있다. 우리는
데이터를 안 쓰지만 성능에도 개입하지 않아야 진짜 읽기 전용이다.

허용 목록 방식인 이유(금지 목록이 아니라): 새 연산자는 계속 생기고,
금지 목록은 항상 뒤늦다. 모르는 것은 거부하는 편이 안전하다.

## limit+1을 읽는 이유

`limit=100`으로 100건이 나왔을 때 "딱 100건"인지 "더 있는데 잘린 것"인지
구별할 수 없다. 101건을 요청해 101건이 오면 잘린 것이다.
"""
from datetime import datetime
from typing import Any

from src.config.schema_site import MongoConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import MongoReaderPort

DEFAULT_LIMIT = 50

# 읽기 질의에 필요한 것만. $where·$function·$accumulator는 서버측 JS 실행이라 없다.
ALLOWED_OPERATORS = {
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin",
    "$and", "$or", "$nor", "$not", "$exists", "$type",
    "$regex", "$options", "$size", "$all", "$elemMatch", "$mod",
}


def filter_problems(node: Any, *, path: str = "filter") -> list[str]:
    """필터에 허용되지 않은 연산자가 있는가. 어댑터와 CLI가 같은 함수를 쓴다."""
    problems: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith("$") and key not in ALLOWED_OPERATORS:
                problems.append(f"{path}: 허용되지 않은 연산자 — {key}")
            problems += filter_problems(value, path=f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            problems += filter_problems(item, path=f"{path}[{index}]")
    return problems


def to_jsonable(value: Any) -> Any:
    """ObjectId·datetime 등을 사람이 읽고 JSON으로 찍을 수 있는 형태로.

    문자열로 바꾸면서 **원래 타입 정보를 잃는다**는 점은 감수한다 — 이 값들은
    화면과 증거에 실릴 뿐, 다시 질의에 쓰이지 않는다.
    """
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)          # ObjectId, Decimal128, Binary ...


class RealMongoReader(MongoReaderPort):
    def __init__(self, cfg: MongoConfig, *, clock: Clock, default_limit: int = DEFAULT_LIMIT,
                 database_factory: Any = None):
        """`database_factory`는 테스트가 실서버 없이 질의 로직(잘림 판정 등)을
        검증하기 위한 이음매다. 프로덕션에서는 None이고 아래 실접속을 쓴다.
        """
        self._cfg = cfg
        self._clock = clock
        self._default_limit = default_limit
        self._database_factory = database_factory
        self._client: Any = None

    def _database(self) -> Any:
        if self._database_factory is not None:
            return self._database_factory()
        if self._client is None:
            from pymongo import AsyncMongoClient
            kwargs: dict[str, Any] = {"serverSelectionTimeoutMS": 5000}
            if self._cfg.user:
                kwargs.update(username=self._cfg.user,
                              password=self._cfg.password.get_secret_value(),
                              authSource=self._cfg.auth_source)
            self._client = AsyncMongoClient(self._cfg.url, **kwargs)
        return self._client[self._cfg.database]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def find(self, collection: str, filter: dict, *,
                   sort: list[tuple[str, int]] | None = None,
                   limit: int | None = None,
                   projection: list[str] | None = None) -> ProbeResult:
        limit = limit or self._default_limit
        source = f"mongo:{self._cfg.database}.{collection} find={filter} limit={limit}"
        if projection:
            source += f" fields={sorted(projection)}"
        problems = filter_problems(filter)
        if problems:
            return ProbeResult.failed("; ".join(problems), source=source, clock=self._clock)
        try:
            # _id를 빼는 이유: 쓰지 않는데 문서마다 12바이트 + 디코딩이 붙는다.
            fields = {name: 1 for name in projection} | {"_id": 0} if projection else None
            cursor = self._database()[collection].find(filter, fields)
            if sort:
                cursor = cursor.sort(sort)
            cursor = cursor.limit(limit + 1)          # 잘렸는지 알기 위해 하나 더
            rows = [to_jsonable(doc) async for doc in cursor]
            truncated = None
            if len(rows) > limit:
                rows = rows[:limit]
                truncated = f"limit={limit}에 걸림 — 더 있다"
            return ProbeResult.succeeded(rows, source=source, clock=self._clock,
                                         truncated_reason=truncated)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)

    async def count(self, collection: str, filter: dict) -> ProbeResult:
        source = f"mongo:{self._cfg.database}.{collection} count={filter}"
        problems = filter_problems(filter)
        if problems:
            return ProbeResult.failed("; ".join(problems), source=source, clock=self._clock)
        try:
            total = await self._database()[collection].count_documents(filter)
            return ProbeResult.succeeded(total, source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)
