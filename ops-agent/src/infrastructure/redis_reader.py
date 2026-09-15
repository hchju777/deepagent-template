"""Redis 읽기 어댑터. 던지지 않고 ProbeResult로 돌려준다.

## KEYS를 쓰지 않는 이유

`KEYS *`는 Redis를 **블로킹**한다. 키가 백만 개면 그동안 다른 모든 요청이 멈춘다.
우리는 데이터를 안 쓰지만, 대상 시스템의 **성능에도 개입하지 않아야** 진짜
읽기 전용이다. SCAN은 커서로 나눠 돈다.

## 상한을 두는 이유

리스트 하나가 10만 건이면 그걸 전부 메모리에 올려 증거로 만들 수는 없다.
잘랐으면 봉투가 **잘렸다고 말한다**(`complete=False`) — 그래야 나중에
"없다"는 결론을 이 표본으로 내리지 않는다.
"""
from typing import Any

from src.config.schema_site import RedisConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import RedisReaderPort

DEFAULT_MAX_ROWS = 200


class RealRedisReader(RedisReaderPort):
    def __init__(self, cfg: RedisConfig, *, clock: Clock, max_rows: int = DEFAULT_MAX_ROWS):
        self._cfg = cfg
        self._clock = clock
        self._max_rows = max_rows
        self._client: Any = None

    def _connect(self) -> Any:
        # 지연 연결 — 어댑터를 만드는 것만으로 소켓이 열리면 `config show` 같은
        # 명령도 대상 시스템에 붙게 된다.
        if self._client is None:
            import redis.asyncio as redis
            self._client = redis.from_url(
                self._cfg.url,
                db=self._cfg.db,
                password=(self._cfg.password.get_secret_value() if self._cfg.password else None),
                decode_responses=True,          # bytes가 아니라 str로 받는다
                socket_connect_timeout=5,
                socket_timeout=5)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── 포트 구현 ────────────────────────────────────────────────

    async def get(self, key: str) -> ProbeResult:
        source = f"redis:{self._cfg.url}/{self._cfg.db}:{key}"
        try:
            client = self._connect()
            kind = await client.type(key)
            if kind == "none":
                # 키가 없는 것은 오류가 아니라 **사실**이다. error로 만들면
                # "붙지 못했다"와 "키가 없다"가 같은 상태가 되어 구별이 사라진다.
                return ProbeResult.succeeded(None, source=source, clock=self._clock)
            data, truncated = await self._read_by_type(client, key, kind)
            return ProbeResult.succeeded({"type": kind, "value": data},
                                         source=source, clock=self._clock,
                                         truncated_reason=truncated)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)

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
        try:
            client = self._connect()
            keys: list[str] = []
            truncated = None
            async for key in client.scan_iter(match=pattern, count=100):
                keys.append(key)
                if len(keys) >= self._max_rows:
                    truncated = f"{self._max_rows}개에서 끊음 — 더 있을 수 있다"
                    break
            return ProbeResult.succeeded(sorted(keys), source=source, clock=self._clock,
                                         truncated_reason=truncated)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)

    async def ttl(self, key: str) -> ProbeResult:
        source = f"redis:{self._cfg.url}/{self._cfg.db}:TTL {key}"
        try:
            # -1(만료 없음)과 -2(키 없음)를 그대로 보존한다. None이나 0으로
            # 정규화하면 두 사실이 같은 값이 되어 구별이 사라진다.
            return ProbeResult.succeeded(await self._connect().ttl(key),
                                         source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)
