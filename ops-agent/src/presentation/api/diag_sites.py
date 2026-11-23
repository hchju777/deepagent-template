"""사이트마다 Redis·Mongo — 접속만(`doctor/all`), 밖에서 보이는 우리 IP(`egress`).

## 왜 포트(`RedisReaderPort` 등)를 거치지 않는가

포트는 **등재된 읽기**만 연다(규율 9). `PING`·`CLIENT INFO`·`whatsmyuri`는 대상 데이터를 읽는 것이 아니라
연결 자체를 보는 명령이라 포트에 올릴 이유가 없고, 올리면 리드가 고를 수 있는 동사가 는다. 그래서 여기서
짧은 연결을 직접 열고 **고정된 명령 셋만** 보낸 뒤 닫는다. 접속 인자는 리더와 같다(타임아웃 5초).

## 왜 `doctor`(CLI)와 따로인가

`doctor`는 사이트 하나를 네 시스템으로 깊게 본다(60초). 28개 사이트를 그것으로 돌면 하위 프로세스 28개·수 분이다.
"오늘 어느 법인 DB가 안 붙나"는 접속만 보면 되고, 동시에 몇 개씩 보면 Ingress 60초 안에 끝난다.
"""
import asyncio
import time
from typing import Any

from src.config.loader import load_registry, load_site_config
from src.config.schema_site import MongoConfig, RedisConfig

_CONNECT_S = 5.0
# 사이트 하나의 상한 — 접속 타임아웃(5초)이 두 번 겹쳐도 넘지 않게.
_SITE_S = 15.0


class RealSiteProbes:
    async def redis_ping(self, cfg: RedisConfig) -> Any:
        async with _redis(cfg) as client:
            return await client.ping()

    async def redis_seen_as(self, cfg: RedisConfig) -> Any:
        async with _redis(cfg) as client:
            # 한 문자열로 준다 — redis-py가 첫 인자를 공백으로 나눠 보내고, 이름이 "CLIENT INFO"여야 응답을 dict로 푼다.
            return await client.execute_command("CLIENT INFO")

    async def mongo_ping(self, cfg: MongoConfig) -> Any:
        client = _mongo(cfg)
        try:
            return (await client.admin.command("ping")).get("ok")
        finally:
            await client.close()

    async def mongo_seen_as(self, cfg: MongoConfig) -> Any:
        client = _mongo(cfg)
        try:
            return (await client.admin.command("whatsmyuri")).get("you")
        finally:
            await client.close()


class _redis:
    def __init__(self, cfg: RedisConfig):
        import redis.asyncio as redis
        self._client = redis.from_url(
            cfg.url, db=cfg.db, password=cfg.password.get_secret_value() if cfg.password else None,
            decode_responses=True, socket_connect_timeout=_CONNECT_S, socket_timeout=_CONNECT_S)

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        await self._client.aclose()


def _mongo(cfg: MongoConfig):
    from pymongo import AsyncMongoClient
    kwargs: dict[str, Any] = {"serverSelectionTimeoutMS": int(_CONNECT_S * 1000),
                              "connectTimeoutMS": int(_CONNECT_S * 1000)}
    if cfg.user:
        kwargs.update(username=cfg.user, password=cfg.password.get_secret_value(),
                      authSource=cfg.auth_source)
    return AsyncMongoClient(cfg.url, **kwargs)


def seen_addr(raw: Any) -> str | None:
    """`ip:port` — `CLIENT INFO`(문자열이든 dict든)와 `whatsmyuri`(`ip:port`) 모두에서."""
    if isinstance(raw, dict):
        raw = raw.get("addr")
    if not isinstance(raw, str) or not raw:
        return None
    if "=" in raw:
        raw = dict(part.split("=", 1) for part in raw.split() if "=" in part).get("addr")
    return raw or None


def seen_ip(raw: Any) -> str | None:
    addr = seen_addr(raw)
    if addr is None:
        return None
    host = addr if addr.endswith("]") or ":" not in addr else addr.rsplit(":", 1)[0]
    return host.strip("[]") or None


async def _timed(call, ticker) -> dict:
    started = ticker()
    try:
        value = await asyncio.wait_for(call(), _SITE_S)
        return {"ok": True, "value": value, "ms": round((ticker() - started) * 1000, 1)}
    except Exception as exc:                                       # noqa: BLE001
        why = f"시간 초과({_SITE_S:g}초)" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) \
            else f"{type(exc).__name__}: {exc}"
        return {"ok": False, "error": why, "ms": round((ticker() - started) * 1000, 1)}


def _sites(config_root, *, gbm: str | None, fct: str | None):
    return [entry for entry in load_registry(config_root).active()
            if (gbm is None or entry.gbm == gbm) and (fct is None or entry.fct == fct)]


async def survey(config_root, env, probes, *, mode: str, gbm: str | None = None, fct: str | None = None,
                 concurrency: int = 4, ticker=time.perf_counter) -> dict:
    """`mode`: "ping"(doctor/all) · "egress". 켜진 사이트만, `concurrency`개씩. **던지지 않는다** — 사이트 하나의
    config가 깨져도(env 누락 등) 그 줄만 오류다."""
    try:
        entries = _sites(config_root, gbm=gbm, fct=fct)
    except Exception as exc:                                       # noqa: BLE001
        return {"error": str(exc), "sites": []}
    gate = asyncio.Semaphore(concurrency)
    redis_call = probes.redis_ping if mode == "ping" else probes.redis_seen_as
    mongo_call = probes.mongo_ping if mode == "ping" else probes.mongo_seen_as

    async def one(entry) -> dict:
        async with gate:
            row: dict = {"site": str(entry)}
            try:
                site, _ = load_site_config(config_root, entry.gbm, entry.fct, env=env)
            except Exception as exc:                               # noqa: BLE001
                row["error"] = str(exc)
                return row
            pending = {}
            if site.infra.redis is not None:
                pending["redis"] = _timed(lambda: redis_call(site.infra.redis), ticker)
            if site.infra.mongodb is not None:
                pending["mongo"] = _timed(lambda: mongo_call(site.infra.mongodb), ticker)
            for name, result in zip(pending, await asyncio.gather(*pending.values())):
                row[name] = _shape(result, mode)
            return row

    rows = await asyncio.gather(*(one(entry) for entry in entries))
    failed = sum(1 for row in rows if "error" in row
                 or any(not row[k]["ok"] for k in ("redis", "mongo") if k in row))
    return {"sites": list(rows), "ok": len(rows) - failed, "failed": failed}


def _shape(result: dict, mode: str) -> dict:
    if not result["ok"]:
        return {"ok": False, "error": result["error"], "ms": result["ms"]}
    if mode == "ping":
        return {"ok": True, "ms": result["ms"]}
    raw = result["value"]
    return {"ok": True, "ip": seen_ip(raw), "seen_as": seen_addr(raw), "ms": result["ms"]}
