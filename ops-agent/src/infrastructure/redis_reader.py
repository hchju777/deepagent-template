"""Redis 읽기 어댑터. 던지지 않고 ProbeResult로 돌려준다.

## KEYS를 쓰지 않는 이유

`KEYS *`는 Redis를 **블로킹**한다. 키가 백만 개면 그동안 다른 모든 요청이 멈춘다.
우리는 데이터를 안 쓰지만, 대상 시스템의 **성능에도 개입하지 않아야** 진짜
읽기 전용이다. SCAN은 커서로 나눠 돈다.

## 상한을 두는 이유

리스트 하나가 10만 건이면 그걸 전부 메모리에 올려 증거로 만들 수는 없다.
잘랐으면 봉투가 **잘렸다고 말한다**(`complete=False`) — 그래야 나중에
"없다"는 결론을 이 표본으로 내리지 않는다.

## 프록시 예비 주소(R3-5z ㉕)

`proxy_url`이 있고 스위치가 켜졌으면 원 주소가 소켓 연결에서 실패할 때 그 주소로 다시 읽는다(`infrastructure/fallback.py`). 원 주소는 그때만 접속 3초·재시도
0 — redis-py는 연결 실패를 기본으로 몇 번 다시 시도해, 원이 죽어 있으면 그 시간이 걷기 상한을 먹는다.
"""
from typing import Any

from src.config.schema_site import RedisConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.jsonpath import select
from src.domain.ports import RedisReaderPort
from src.infrastructure.fallback import ORIGIN_CONNECT_S, Route

DEFAULT_MAX_ROWS = 200


def connect_failure(exc: BaseException) -> bool:
    """redis-py가 **소켓 연결**에서 낸 오류인가 — `Error N connecting to …`(거부·이름 해석)·`Timeout connecting to server`. 인증·권한 실패도 `ConnectionError`의
    하위 타입이라 먼저 뺀다(대상이 답한 것이다). 읽기 타임아웃(`Timeout reading from socket`)은 붙은 뒤의 일이다."""
    from redis import exceptions as rx

    if isinstance(exc, (rx.AuthenticationError, getattr(rx, "AuthorizationError", rx.AuthenticationError))):
        return False
    return isinstance(exc, (rx.ConnectionError, rx.TimeoutError)) and "connecting to" in str(exc)


def _from_url(url: str, **kwargs) -> Any:
    import redis.asyncio as redis
    return redis.from_url(url, **kwargs)


class RealRedisReader(RedisReaderPort):
    def __init__(self, cfg: RedisConfig, *, clock: Clock, max_rows: int = DEFAULT_MAX_ROWS,
                 site: str = "", fallback: bool = True, pinned: set | None = None, client_factory: Any = None):
        """`client_factory(url, **kwargs)`는 테스트가 실서버 없이 경로 고르기를 보기 위한 이음매다 — 프로덕션에서는 None이고 `redis.asyncio.from_url`을 쓴다."""
        self._cfg = cfg
        self._clock = clock
        self._max_rows = max_rows
        self._route = Route(site, "redis", cfg.proxy_url, enabled=fallback, pinned=pinned)
        self._factory = client_factory or _from_url
        self._client: Any = None
        self._proxy_client: Any = None

    def _connect(self, proxy: bool = False) -> Any:
        # 지연 연결 — 어댑터를 만드는 것만으로 소켓이 열리면 `config show` 같은
        # 명령도 대상 시스템에 붙게 된다.
        if proxy:
            if self._proxy_client is None:
                self._proxy_client = self._open(self._cfg.proxy_url, short=False)
            return self._proxy_client
        if self._client is None:
            self._client = self._open(self._cfg.url, short=self._route.armed)
        return self._client

    def _open(self, url: str, *, short: bool) -> Any:
        kwargs: dict[str, Any] = dict(
            db=self._cfg.db,
            password=(self._cfg.password.get_secret_value() if self._cfg.password else None),
            decode_responses=True,          # bytes가 아니라 str로 받는다
            socket_connect_timeout=ORIGIN_CONNECT_S if short else 5,
            socket_timeout=5)
        if short:
            from redis.asyncio.retry import Retry
            from redis.backoff import NoBackoff
            kwargs["retry"] = Retry(NoBackoff(), 0)
        return self._factory(url, **kwargs)

    async def close(self) -> None:
        for name in ("_client", "_proxy_client"):
            client = getattr(self, name)
            if client is not None:
                await client.aclose()
                setattr(self, name, None)

    async def _read(self, source: str, read) -> tuple[Any, str]:
        """원 → (접속 단계 실패면) 프록시. → (읽은 것, 경로 표시가 붙은 source). 실패는 그대로 올린다 — 부르는 쪽이 `failed`로 바꾼다."""
        got, proxy = await self._route.attempt(lambda on_proxy: read(self._connect(on_proxy)), connect_failure=connect_failure)
        return got, source + self._route.mark(proxy)

    def _failed(self, exc: BaseException, source: str) -> ProbeResult:
        return ProbeResult.failed(f"{type(exc).__name__}: {exc}", source=source + self._route.mark(self._route.on_proxy), clock=self._clock)

    # ── 포트 구현 ────────────────────────────────────────────────

    async def get(self, key: str, *, path: str | None = None) -> ProbeResult:
        source = f"redis:{self._cfg.url}/{self._cfg.db}:{key}" + (f" path={path}" if path else "")

        async def read(client):
            kind = await client.type(key)
            if kind == "none":
                return None
            return kind, *(await self._read_by_type(client, key, kind))
        try:
            got, source = await self._read(source, read)
            if got is None:
                # 키가 없는 것은 오류가 아니라 **사실**이다. error로 만들면
                # "붙지 못했다"와 "키가 없다"가 같은 상태가 되어 구별이 사라진다.
                return ProbeResult.succeeded(None, source=source, clock=self._clock)
            kind, data, truncated = got
            if path is None:
                return ProbeResult.succeeded({"type": kind, "value": data},
                                             source=source, clock=self._clock,
                                             truncated_reason=truncated)
            # 값 전체를 받은 뒤 **우리 쪽에서** 고른다 — Redis의 JSON 모듈은 대상에 있다고 가정할 수 없다.
            # hash·list는 그 구조 그대로 경로를 탄다(`field`, `[n]`); string은 JSON으로 풀어서.
            ok, picked = select(data, path)
            if not ok:
                return ProbeResult.failed(picked, source=source, clock=self._clock)
            return ProbeResult.succeeded({"type": kind, "path": path, "value": picked},
                                         source=source, clock=self._clock,
                                         truncated_reason=truncated)
        except Exception as exc:                                   # noqa: BLE001
            return self._failed(exc, source)

    async def _read_by_type(self, client, key: str, kind: str) -> tuple[Any, str | None]:
        limit = self._max_rows
        if kind == "string":
            return await client.get(key), None
        if kind == "hash":
            return await client.hgetall(key), None
        if kind == "list":
            total = await client.llen(key)
            rows = await client.lrange(key, 0, limit - 1)
            return rows, (f"list {total}건 중 {limit}건만" if total > limit else None)
        if kind == "set":
            total = await client.scard(key)
            rows = list(await client.srandmember(key, limit))
            return rows, (f"set {total}건 중 {limit}건만" if total > limit else None)
        if kind == "zset":
            total = await client.zcard(key)
            rows = await client.zrange(key, 0, limit - 1, withscores=True)
            return rows, (f"zset {total}건 중 {limit}건만" if total > limit else None)
        # stream 등 — 모르는 타입을 추측해서 읽지 않는다.
        return f"<지원하지 않는 타입: {kind}>", None

    async def scan(self, pattern: str) -> ProbeResult:
        source = f"redis:{self._cfg.url}/{self._cfg.db}:SCAN {pattern}"

        async def read(client):
            keys: list[str] = []
            truncated = None
            async for key in client.scan_iter(match=pattern, count=100):
                keys.append(key)
                if len(keys) >= self._max_rows:
                    truncated = f"{self._max_rows}개에서 끊음 — 더 있을 수 있다"
                    break
            return sorted(keys), truncated
        try:
            (keys, truncated), source = await self._read(source, read)
            return ProbeResult.succeeded(keys, source=source, clock=self._clock,
                                         truncated_reason=truncated)
        except Exception as exc:                                   # noqa: BLE001
            return self._failed(exc, source)

    async def ttl(self, key: str) -> ProbeResult:
        source = f"redis:{self._cfg.url}/{self._cfg.db}:TTL {key}"
        try:
            # -1(만료 없음)과 -2(키 없음)를 그대로 보존한다. None이나 0으로
            # 정규화하면 두 사실이 같은 값이 되어 구별이 사라진다.
            ttl, source = await self._read(source, lambda client: client.ttl(key))
            return ProbeResult.succeeded(ttl, source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return self._failed(exc, source)
