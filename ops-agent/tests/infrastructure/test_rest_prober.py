"""REST 어댑터 — 등재제와 닫힌 파라미터 스키마.

여기서 검증하는 것은 **소켓에 나가기 전에 거부되는가**다. 실제 호출은
`python -m src peek rest`가 사내에서 확인한다.
"""
from src.config.schema_site import RestConfig
from src.infrastructure.rest_prober import (RealRestProber, param_problems,
                                            prepare_params)

CFG = RestConfig(
    base_url="http://twin.example.net:8080",
    entries={
        "oee_summary": {"method": "POST", "path": "/api/v1/oee/summary",
                        "params": {"line": {"type": "str", "required": True},
                                   "shift": {"type": "int", "required": False}}},
        "lines": {"method": "GET", "path": "/api/v1/lines"},
    })
ENTRY = CFG.entries["oee_summary"]


def test_선언된_파라미터는_통과한다():
    assert param_problems(ENTRY, {"line": "L3", "shift": 2}) == []


def test_선택_파라미터는_없어도_된다():
    assert param_problems(ENTRY, {"line": "L3"}) == []


def test_선언되지_않은_키를_거부한다():
    assert param_problems(ENTRY, {"line": "L3", "drop": 1}) == \
        ["선언되지 않은 파라미터 — drop"]


def test_필수_파라미터_누락을_잡는다():
    assert param_problems(ENTRY, {"shift": 1}) == ["필수 파라미터가 없다 — line"]


def test_타입이_다르면_거부한다():
    assert param_problems(ENTRY, {"line": "L3", "shift": "2"}) == \
        ["shift은 int인데 str이 왔다"]


def test_bool을_int로_통과시키지_않는다():
    # 파이썬에서 bool은 int의 하위 타입이라 isinstance(True, int)가 참이다.
    # True가 shift=1로 조용히 나가면 요청과 기록이 어긋난다.
    assert param_problems(ENTRY, {"line": "L3", "shift": True}) == \
        ["shift은 int인데 bool이 왔다"]


async def test_등재되지_않은_항목은_호출_자체를_안_한다(clock):
    result = await RealRestProber(CFG, clock=clock).query("delete_line", {})
    assert result.status == "error"
    assert "등재되지 않은 항목" in result.error
    assert "lines, oee_summary" in result.error       # 무엇을 부를 수 있는지 알려준다


async def test_파라미터가_틀리면_네트워크를_타지_않는다(clock):
    # base_url이 존재하지 않는 호스트인데도 연결 오류가 아니라 파라미터 거부가 나와야 한다.
    result = await RealRestProber(CFG, clock=clock).query("oee_summary", {"drop": 1})
    assert result.status == "error"
    assert "파라미터 거부" in result.error
    assert "Connect" not in result.error


def test_포트에_get_path가_없다():
    # 경로를 인자로 받는 메서드가 생기면 "임의의 경로를 부르라"가 다시 표현 가능해진다.
    from src.domain.ports import RestProberPort
    surface = {n for n in dir(RestProberPort) if not n.startswith("_")}
    assert surface == {"query"}, f"REST 포트 표면이 넓어졌다 — {surface}"


# ── list 타입과 스칼라 정규화 ──────────────────────────────────────────

BADGE_CFG = RestConfig(
    base_url="http://twin.example.net:8080",
    entries={"summary_badge": {"method": "POST", "path": "/summary/badge",
                               "params": {"part_code": {"type": "list", "required": False},
                                          "line_code": {"type": "list", "required": False}}}})
BADGE = BADGE_CFG.entries["summary_badge"]


def test_리스트를_주면_그대로_간다():
    normalized, problems, wrapped = prepare_params(BADGE, {"line_code": ["P222", "P223"]})
    assert normalized == {"line_code": ["P222", "P223"]}
    assert problems == [] and wrapped == []


def test_스칼라를_주면_리스트로_감싼다():
    """거부보다 감싸는 편이 안전하다.

    `"P222"`를 문자열로 그냥 보내면 서버가 그 필터를 **무시하고 전체를 돌려줄**
    수 있고, 그러면 "조건에 맞는 것이 이만큼 있다"는 거짓 안심이 된다.
    """
    normalized, problems, wrapped = prepare_params(BADGE, {"line_code": "P222"})
    assert normalized == {"line_code": ["P222"]}
    assert problems == []
    assert wrapped == ["line_code"], "감싼 사실이 기록돼야 한다"


def test_감싼_값이_기록되는_요청이다(clock):
    # 사람이 준 것과 소켓에 나간 것이 다르면, 증거에 남는 것은 **나간 것**이어야 한다.
    normalized, _, _ = prepare_params(BADGE, {"line_code": "P222"})
    assert normalized["line_code"] == ["P222"]


def test_빈_리스트는_감싸지_않는다():
    normalized, problems, wrapped = prepare_params(BADGE, {"line_code": []})
    assert normalized == {"line_code": []} and problems == [] and wrapped == []


def test_중첩_리스트를_거부한다():
    # 원소에 리스트나 dict를 허용하면 body 모양이 사실상 자유가 되고,
    # "닫힌 스키마"라는 말의 뜻이 사라진다.
    _, problems, _ = prepare_params(BADGE, {"line_code": [["P222"]]})
    assert problems == ["line_code[0]은 스칼라여야 한다 — list이 왔다"]


def test_list_자리에_bool을_주면_거부한다():
    _, problems, _ = prepare_params(BADGE, {"line_code": True})
    assert problems == ["line_code은 list인데 bool이 왔다"]


def test_숫자_스칼라도_감싼다():
    _, problems, wrapped = prepare_params(BADGE, {"part_code": 12345})
    assert problems == [] and wrapped == ["part_code"]


def test_선언되지_않은_키는_감싸기_전에_거부된다():
    _, problems, _ = prepare_params(BADGE, {"drop_table": "x"})
    assert problems == ["선언되지 않은 파라미터 — drop_table"]


async def test_스텁도_같은_정규화를_거친다(clock):
    from src.infrastructure.stubs import StubRestProber

    stub = StubRestProber(BADGE_CFG, {"summary_badge": {"total": 2}}, clock=clock)
    result = await stub.query("summary_badge", {"line_code": "P222"})
    assert result.status == "ok"
    assert result.data["request"]["params"] == {"line_code": ["P222"]}, (
        "스텁과 실구현의 계약이 갈라지면 테스트는 통과하는데 사내에서 깨진다")


class _Disconnecting:
    """소켓에서 끊긴 것처럼 — 사내에서 대상 호스트가 NO_PROXY에 없어 전사 프록시로 나갔을 때의 모양."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        import httpx
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    async def post(self, *a, **kw):
        return await self.get()


async def test_프록시_env가_있고_호스트가_NO_PROXY에_없으면_의심을_말한다(clock, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Disconnecting)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    result = await RealRestProber(CFG, clock=clock).query("lines", {})
    assert result.status == "error" and "RemoteProtocolError" in result.error
    assert "프록시 경유 의심" in result.error and "twin.example.net" in result.error and "NO_PROXY" in result.error


async def test_NO_PROXY에_있거나_프록시_env가_없으면_의심을_안_말한다(clock, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Disconnecting)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,twin.example.net")
    bypassed = await RealRestProber(CFG, clock=clock).query("lines", {})
    assert bypassed.status == "error" and "프록시 경유 의심" not in bypassed.error
    # 돌리는 기계가 소문자 `https_proxy`를 갖고 있을 수 있다(여기 샌드박스가 그렇다) — 양쪽 다 지운다.
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    plain = await RealRestProber(CFG, clock=clock).query("lines", {})
    assert plain.status == "error" and "프록시 경유 의심" not in plain.error


# ── 프록시 예비 주소(R3-5z ㉖) — `httpx.AsyncClient` 자리에 주소마다 응답·예외 ──

class _ByBase:
    """요청 url이 어느 앞머리로 시작하느냐로 응답이나 예외. 만든 인자와 부른 url을 남긴다."""
    plan: dict = {}
    made: list = []
    called: list = []

    def __init__(self, **kwargs):
        _ByBase.made.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kwargs):
        return self._answer(url)

    async def post(self, url, **kwargs):
        return self._answer(url)

    def _answer(self, url):
        _ByBase.called.append(url)
        out = next(v for k, v in _ByBase.plan.items() if url.startswith(k))
        if isinstance(out, BaseException):
            raise out
        return out


ORIGIN, PROXY = "http://twin.example.net:8080", "http://p:8080/gumi/rest/twin"


def _proxied(monkeypatch, clock, plan, *, proxy_base_url=PROXY + "/", fallback=True, pins=None):
    import httpx
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)                                         # 프록시 의심 꼬리는 다른 테스트가 본다
    monkeypatch.setattr(httpx, "AsyncClient", _ByBase)
    _ByBase.plan, _ByBase.made, _ByBase.called = plan, [], []
    cfg = CFG.model_copy(update={"proxy_base_url": proxy_base_url.rstrip("/") if proxy_base_url else None})
    return RealRestProber(cfg, clock=clock, site="mx/gumi", fallback=fallback, pinned=set() if pins is None else pins)


async def test_rest_원이_되면_원으로_부르고_원_접속은_짧은_타임아웃이고_source에_경로가_남는다(clock, monkeypatch):
    import httpx
    prober = _proxied(monkeypatch, clock, {ORIGIN: httpx.Response(200, json=[{"line": "L1"}])})
    got = await prober.query("lines", {})
    assert got.status == "ok" and got.data["response"] == [{"line": "L1"}] and got.source == "rest:lines GET /api/v1/lines {} · 경로=원"
    assert _ByBase.called == [ORIGIN + "/api/v1/lines"]
    timeout = _ByBase.made[0]["timeout"]
    assert timeout.connect == 3.0 and timeout.read == 10.0                                  # 읽기 타임아웃은 그대로


async def test_rest_원이_접속_단계에서_실패하면_프록시_접두어로_부르고_고정해_다음엔_원을_다시_안_친다(clock, monkeypatch):
    import httpx
    for failure in (httpx.ConnectError("[Errno 111] Connection refused"), httpx.ConnectTimeout("timed out")):
        pins = set()
        prober = _proxied(monkeypatch, clock, {ORIGIN: failure, PROXY: httpx.Response(200, json={"ok": 1})}, pins=pins)
        got = await prober.query("oee_summary", {"line": "L3"})
        assert got.status == "ok" and got.source == "rest:oee_summary POST /api/v1/oee/summary {'line': 'L3'} · 경로=프록시", failure
        assert _ByBase.called == [ORIGIN + "/api/v1/oee/summary", PROXY + "/api/v1/oee/summary"] and pins == {("mx/gumi", "rest")}
        assert _ByBase.made[1]["timeout"] == 10.0                                         # 프록시는 지금 타임아웃
        await prober.query("lines", {})
        assert _ByBase.called[2:] == [PROXY + "/api/v1/lines"]                             # 고정 — 원을 다시 안 친다


async def test_rest_둘_다_실패하면_두_오류를_다_든_실패이고_대상이_답한_실패는_대체하지_않는다(clock, monkeypatch):
    import httpx
    prober = _proxied(monkeypatch, clock, {ORIGIN: httpx.ConnectError("origin refused"), PROXY: httpx.ConnectError("proxy refused")})
    got = await prober.query("lines", {})
    assert got.status == "error" and got.error == "ProxyAlsoFailed: 원 ConnectError: origin refused → 프록시 ConnectError: proxy refused"
    assert got.source == "rest:lines GET /api/v1/lines {} · 경로=프록시"
    for answered in (httpx.Response(404, text="no route"), httpx.Response(401, text="unauthorized"), httpx.Response(403, text="forbidden"),
                     httpx.ReadTimeout("read timed out")):
        pins = set()
        prober = _proxied(monkeypatch, clock, {ORIGIN: answered, PROXY: httpx.Response(200, json={})}, pins=pins)
        got = await prober.query("lines", {})
        assert got.status == "error" and got.source.endswith(" · 경로=원") and _ByBase.called == [ORIGIN + "/api/v1/lines"] and pins == set(), answered


async def test_rest_예비_주소가_없거나_비었거나_스위치가_꺼지면_지금과_같다(clock, monkeypatch):
    import httpx
    for proxy_base_url, fallback in ((None, True), ("", True), (PROXY, False)):
        prober = _proxied(monkeypatch, clock, {ORIGIN: httpx.ConnectError("refused"), PROXY: httpx.Response(200, json={})},
                                        proxy_base_url=proxy_base_url, fallback=fallback, pins={("mx/gumi", "rest")})
        got = await prober.query("lines", {})
        assert got.status == "error" and got.error == "ConnectError: refused" and got.source == "rest:lines GET /api/v1/lines {}"
        assert _ByBase.called == [ORIGIN + "/api/v1/lines"] and _ByBase.made[0]["timeout"] == 10.0
