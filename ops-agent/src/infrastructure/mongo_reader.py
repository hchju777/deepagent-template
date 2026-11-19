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

## 프록시 예비 주소(R3-5z ㉕)

`proxy_url`이 있고 스위치가 켜졌으면 원 주소가 서버를 못 고를 때(연결 거부·이름 해석·접속 타임아웃) 그 주소로 다시 읽는다(`infrastructure/fallback.py`).
원 주소는 그때만 서버 선택·접속 3초. 프록시 경로에서 멤버 탐색이 원 주소로 돌아가는지는 사람이 실측한다 — 그 결과에 따라 프록시 경로에만 직접 접속 옵션을 둔다.
그전에는 프록시 경로의 실패 글(멤버 주소가 든 토폴로지 설명)을 그대로 남긴다.
"""
from datetime import datetime
from typing import Any

from src.config.schema_site import MongoConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import MongoReaderPort
from src.infrastructure.fallback import ORIGIN_CONNECT_S, Route

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


def connect_failure(exc: BaseException) -> bool:
    """pymongo가 서버를 못 골랐다 — 연결 거부·이름 해석 실패·접속 타임아웃이 전부 이것이다. 인증 실패(`OperationFailure`)·권한 없음은 대상이 답한 것이라 아니다."""
    from pymongo.errors import ServerSelectionTimeoutError

    return isinstance(exc, ServerSelectionTimeoutError)


def _async_client(url: str, **kwargs) -> Any:
    from pymongo import AsyncMongoClient
    return AsyncMongoClient(url, **kwargs)


class RealMongoReader(MongoReaderPort):
    def __init__(self, cfg: MongoConfig, *, clock: Clock, default_limit: int = DEFAULT_LIMIT,
                 database_factory: Any = None, site: str = "", fallback: bool = True, pinned: set | None = None,
                 client_factory: Any = None):
        """`database_factory`는 테스트가 실서버 없이 질의 로직(잘림 판정 등)을
        검증하기 위한 이음매다. 프로덕션에서는 None이고 아래 실접속을 쓴다.
        `client_factory(url, **kwargs)`는 경로 고르기(원 → 프록시)를 실서버 없이 보기 위한 이음매다.
        """
        self._cfg = cfg
        self._clock = clock
        self._default_limit = default_limit
        self._database_factory = database_factory
        self._route = Route(site, "mongo", cfg.proxy_url, enabled=fallback, pinned=pinned)
        self._client_factory = client_factory or _async_client
        self._client: Any = None
        self._proxy_client: Any = None

    def _database(self, proxy: bool = False) -> Any:
        if self._database_factory is not None:
            return self._database_factory()
        name = "_proxy_client" if proxy else "_client"
        if getattr(self, name) is None:
            kwargs: dict[str, Any] = {"serverSelectionTimeoutMS": 5000}
            if not proxy and self._route.armed:
                kwargs.update(serverSelectionTimeoutMS=int(ORIGIN_CONNECT_S * 1000), connectTimeoutMS=int(ORIGIN_CONNECT_S * 1000))
            if self._cfg.user:
                kwargs.update(username=self._cfg.user,
                              password=self._cfg.password.get_secret_value(),
                              authSource=self._cfg.auth_source)
            setattr(self, name, self._client_factory(self._cfg.proxy_url if proxy else self._cfg.url, **kwargs))
        return getattr(self, name)[self._cfg.database]

    def _db(self, proxy: bool) -> Any:
        # 원 경로는 인자 없이 — 테스트가 `_database`를 인자 없는 함수로 바꿔 끼운다.
        return self._database(True) if proxy else self._database()

    async def _read(self, read) -> tuple[Any, str]:
        """원 → (서버를 못 골랐으면) 프록시. → (읽은 것, `source` 끝 경로 표시). 실패는 그대로 올린다."""
        got, proxy = await self._route.attempt(lambda on_proxy: read(self._db(on_proxy)), connect_failure=connect_failure)
        return got, self._route.mark(proxy)

    def _failed(self, exc: BaseException, source: str) -> ProbeResult:
        return ProbeResult.failed(f"{type(exc).__name__}: {exc}", source=source + self._route.mark(self._route.on_proxy), clock=self._clock)

    async def close(self) -> None:
        for name in ("_client", "_proxy_client"):
            client = getattr(self, name)
            if client is not None:
                await client.close()
                setattr(self, name, None)

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
        # _id를 빼는 이유: 쓰지 않는데 문서마다 12바이트 + 디코딩이 붙는다.
        fields = {name: 1 for name in projection} | {"_id": 0} if projection else None

        async def read(database):
            cursor = database[collection].find(filter, fields)
            if sort:
                cursor = cursor.sort(sort)
            cursor = cursor.limit(limit + 1)          # 잘렸는지 알기 위해 하나 더
            return [to_jsonable(doc) async for doc in cursor]
        try:
            rows, mark = await self._read(read)
            truncated = None
            if len(rows) > limit:
                rows = rows[:limit]
                truncated = f"limit={limit}에 걸림 — 더 있다"
            return ProbeResult.succeeded(rows, source=source + mark, clock=self._clock,
                                         truncated_reason=truncated)
        except Exception as exc:                                   # noqa: BLE001
            return self._failed(exc, source)

    async def list_collections(self) -> ProbeResult:
        """이 DB의 컬렉션 이름들. 계정 권한이 없으면 error로 흡수된다 — 그때는
        그 길이 막힌 것이고, 조사 기록에 "못 찾았다"가 남는다."""
        source = f"mongo:{self._cfg.database} collections"
        try:
            names, mark = await self._read(lambda database: database.list_collection_names())
            return ProbeResult.succeeded(sorted(names), source=source + mark, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return self._failed(exc, source)

    async def count(self, collection: str, filter: dict) -> ProbeResult:
        source = f"mongo:{self._cfg.database}.{collection} count={filter}"
        problems = filter_problems(filter)
        if problems:
            return ProbeResult.failed("; ".join(problems), source=source, clock=self._clock)
        try:
            total, mark = await self._read(lambda database: database[collection].count_documents(filter))
            return ProbeResult.succeeded(total, source=source + mark, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return self._failed(exc, source)
