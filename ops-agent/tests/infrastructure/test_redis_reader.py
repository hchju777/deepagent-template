"""실제 Redis 리더 — 소켓 없이 가짜 클라이언트로 **봉투 계약**만 본다. 실물 접속은 `tests/live`."""
from datetime import datetime, timezone

from src.config.schema_site import RedisConfig
from src.infrastructure.redis_reader import RealRedisReader

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


class _Client:
    """`redis.asyncio` 중 리더가 쓰는 네 메서드만. 값의 파이썬 타입으로 TYPE을 흉내낸다."""

    def __init__(self, store: dict):
        self._store = store

    async def type(self, key):
        value = self._store.get(key)
        return "none" if value is None else ("hash" if isinstance(value, dict) else "string")

    async def get(self, key):
        return self._store[key]

    async def hgetall(self, key):
        return dict(self._store[key])


def _reader(store: dict) -> RealRedisReader:
    reader = RealRedisReader(RedisConfig(url="redis://example.invalid:6379", db=0), clock=lambda: T0)
    reader._client = _Client(store)          # `_connect`를 안 타게 — 소켓은 열지 않는다
    return reader


async def test_path를_주면_JSON_값_안의_그_자리만_준다():
    """사내 실측: 요약 키의 값이 커서 증거 예산 안에 첫 항목도 안 들어갔다. 고른 부분은 **통째로**, 자리 없음은 있는 키를
    적은 error, 키 없음은 전처럼 None이다 — 스텁과 같은 계약."""
    reader = _reader({"k": '{"record": [{"data": {"x": 1}}, {"data": {"x": 2}}], "metadata": {"status": "ok"}}',
                      "h": {"f": "v", "g": "w"}})
    got = await reader.get("k", path="record[1].data")
    assert got.status == "ok" and got.data == {"type": "string", "path": "record[1].data", "value": {"x": 2}}
    assert "path=record[1].data" in got.source
    plain = await reader.get("k")
    assert plain.data["type"] == "string" and "path" not in plain.data and plain.data["value"].startswith("{")
    missing = await reader.get("k", path="record[0].nope")
    assert missing.status == "error" and "nope" in missing.error and "data" in missing.error
    field = await reader.get("h", path="g")                       # hash는 필드 이름이 곧 경로다
    assert field.data == {"type": "hash", "path": "g", "value": "w"}
    absent = await reader.get("none", path="a")
    assert absent.status == "ok" and absent.data is None
