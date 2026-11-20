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


# ── 프록시 예비 주소(R3-5z ㉕) — 소켓 없이 `client_factory`로 주소마다 가짜 클라이언트 ──

def _refused(host="h:6379"):
    from redis import exceptions as rx
    return rx.ConnectionError(f"Error 111 connecting to {host}. Connection refused.")


class _Down:
    """모든 명령이 그 오류를 낸다 — 붙지 못한(또는 답한) 클라이언트. 불린 수를 센다."""

    def __init__(self, exc):
        self._exc, self.calls = exc, 0

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            self.calls += 1
            raise self._exc
        return fail


class _Opened:
    """`client_factory(url, **kwargs)` — 주소마다 미리 정한 클라이언트. 연 주소와 인자를 남긴다."""

    def __init__(self, by_url):
        self.by_url, self.opened = by_url, []

    def __call__(self, url, **kwargs):
        self.opened.append((url, kwargs))
        return self.by_url[url]


def _fallback_reader(origin, proxy=None, *, proxy_url="redis://p:16379", fallback=True, pins=None):
    opened = _Opened({"redis://h:6379": origin, "redis://p:16379": proxy})
    cfg = RedisConfig(url="redis://h:6379", proxy_url=proxy_url)
    reader = RealRedisReader(cfg, clock=lambda: T0, site="mx/gumi", fallback=fallback, pinned=set() if pins is None else pins, client_factory=opened)
    return reader, opened


async def test_redis_원이_되면_원으로_읽고_원_접속은_짧은_타임아웃에_재시도_없음이고_source에_경로가_남는다():
    reader, opened = _fallback_reader(_Client({"k": "v"}))
    got = await reader.get("k")
    assert got.status == "ok" and got.data == {"type": "string", "value": "v"} and got.source == "redis:redis://h:6379/0:k · 경로=원"
    [(url, kwargs)] = opened.opened
    assert url == "redis://h:6379" and kwargs["socket_connect_timeout"] == 3.0 and kwargs["retry"].get_retries() == 0
    missing = await reader.get("nope")                                                   # 키 없음은 대상의 답 — 대체하지 않는다
    assert missing.status == "ok" and missing.data is None and missing.source.endswith(" · 경로=원") and len(opened.opened) == 1


async def test_redis_원이_접속_단계에서_실패하면_프록시로_읽고_고정해_다음엔_원을_다시_안_친다():
    origin, pins = _Down(_refused()), set()
    reader, opened = _fallback_reader(origin, _Client({"k": "v", "h": {"f": "1"}}), pins=pins)
    got = await reader.get("k")
    assert got.status == "ok" and got.data == {"type": "string", "value": "v"} and got.source == "redis:redis://h:6379/0:k · 경로=프록시"
    assert pins == {("mx/gumi", "redis")} and origin.calls == 1
    url, kwargs = opened.opened[1]
    assert url == "redis://p:16379" and kwargs["socket_connect_timeout"] == 5 and "retry" not in kwargs        # 프록시는 지금 타임아웃
    again = await reader.get("h", path="f")
    assert again.status == "ok" and again.source.endswith(" · 경로=프록시") and origin.calls == 1               # 고정 — 원을 다시 안 친다
    fresh, _ = _fallback_reader(_Down(_refused()), _Client({"k": "v"}), pins=pins)                             # 새 어댑터도(케이스마다 만든다)
    assert (await fresh.ttl("k")).source == "redis:redis://h:6379/0:TTL k · 경로=프록시"


async def test_redis_둘_다_실패하면_두_오류를_다_든_실패이고_대상이_답한_실패는_대체하지_않는다():
    from redis import exceptions as rx

    reader, _ = _fallback_reader(_Down(_refused()), _Down(_refused("p:16379")))
    got = await reader.get("k")
    assert got.status == "error" and got.source == "redis:redis://h:6379/0:k · 경로=프록시"
    assert got.error == ("ProxyAlsoFailed: 원 ConnectionError: Error 111 connecting to h:6379. Connection refused. → "
                         "프록시 ConnectionError: Error 111 connecting to p:16379. Connection refused.")
    for answered in (rx.AuthenticationError("invalid username-password pair"), rx.ResponseError("NOPERM this user has no permissions"),
                     rx.TimeoutError("Timeout reading from socket"),
                     rx.AuthenticationError("Error connecting to h:6379: invalid password")):   # 인증은 글에 connecting to가 들어도 답이다
        pins = set()
        reader, opened = _fallback_reader(_Down(answered), _Client({"k": "v"}), pins=pins)
        got = await reader.get("k")
        assert got.status == "error" and got.error.startswith(type(answered).__name__) and got.source.endswith(" · 경로=원"), answered
        assert pins == set() and [u for u, _ in opened.opened] == ["redis://h:6379"]


async def test_redis_예비_주소가_없거나_비었거나_스위치가_꺼지면_지금과_같다():
    for proxy_url, fallback in ((None, True), ("", True), ("redis://p:16379", False)):
        pins = {("mx/gumi", "redis")}                                                    # 고정돼 있어도
        reader, opened = _fallback_reader(_Down(_refused()), _Client({"k": "v"}), proxy_url=proxy_url, fallback=fallback, pins=pins)
        got = await reader.get("k")
        assert got.status == "error" and got.error == "ConnectionError: Error 111 connecting to h:6379. Connection refused."
        assert got.source == "redis:redis://h:6379/0:k"                                  # 경로 표시 없음 — 옛 녹화와 같은 글자
        [(url, kwargs)] = opened.opened
        assert url == "redis://h:6379" and kwargs["socket_connect_timeout"] == 5 and "retry" not in kwargs
