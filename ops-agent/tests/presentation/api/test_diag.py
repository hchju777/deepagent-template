"""앱 안에서 하는 진단 — env 모양, 네트워크 단계, egress, doctor/all, trace, LLM 원시 호출.

바깥(Redis·Mongo·게이트웨이·라우터)은 테스트에서 붙지 않는다. 판정과 모양은 이음매(가짜 프로브·httpx
MockTransport)로, 소켓 단계는 이 기계의 127.0.0.1에 실제로 띄운 서버로 본다.
"""
import asyncio
import json
import socket
import struct
import sys

import httpx
import pytest

from src.presentation.api.assembly import DIAG
from src.presentation.api.diag_env import mask_url, referenced, shape
from src.presentation.api.diag_net import (connect_steps, host_port, parse_extended_err,
                                           probe_once, trace)
from src.presentation.api.diag_sites import seen_ip

from tests.presentation.api.conftest import ENV, make_app

SECRETS = [v for k, v in ENV.items() if "SECRET" in v.upper() or k.endswith(("KEY", "PASSWORD"))]


def assert_no_secret(text: str):
    for secret in (*SECRETS, "gw-secret"):
        assert secret not in text, f"비밀이 응답에 나갔다 — {secret}"


async def call(app, method, path, body=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        if method == "GET":
            return await c.get(path)
        return await (c.post(path, json=body) if body is not None else c.post(path))


# ── env ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,masked", [
    ("https://user:pw@gw.example.com/v1", "https://***@gw.example.com/v1"),
    ("redis://:pw@h:6379/0", "redis://***@h:6379/0"),
    ("mongodb://u:p@h1:27017,h2:27017/db?authSource=admin", "mongodb://***@h1:27017,h2:27017/db?authSource=admin"),
    ("mongodb://u:p@w@h1:27017/db", "mongodb://***@h1:27017/db"),       # 비밀번호에 @가 있어도
    ("https://gw.example.com/v1", "https://gw.example.com/v1"),
    ("https://gw.example.com/v1?next=a@b", "https://gw.example.com/v1?next=a@b"),   # 경로 뒤의 @는 계정이 아니다
])
def test_URL의_계정_부분만_가린다(url, masked):
    assert mask_url(url) == masked


def test_모양은_URL이면_가린_URL_아니면_글자_수만():
    assert shape("https://u:p@h/x") == "https://***@h/x"
    assert shape("hunter2") == "7자"


def test_config가_참조하는_이름을_파일별로_모은다(cli_root):
    found = referenced(cli_root / "config")
    assert found["LLM_PASS_KEY"] == ["app.json"]
    assert found["REDIS_PASSWORD"] == ["gbm/common.json"]
    assert "HOME" not in found


async def test_env는_참조된_이름만_있음_없음_모양으로(cli_root):
    env = {**ENV, "UNRELATED_SECRET": "zzz"}
    del env["MAIL_AGENT_API_KEY"]
    body = (await call(make_app(cli_root, env=env), "GET", f"{DIAG}/env")).json()
    rows = {row["name"]: row for row in body["vars"]}
    assert set(rows) == {"LLM_BASE_URL", "LLM_PASS_KEY", "LLM_CLIENT_KEY", "MAIL_AGENT_ID",
                         "MAIL_AGENT_API_KEY", "REDIS_PASSWORD"}
    assert rows["LLM_BASE_URL"]["shape"] == "https://***@gw.example.com/v1"
    assert rows["LLM_PASS_KEY"] == {"name": "LLM_PASS_KEY", "set": True, "shape": "13자", "where": ["app.json"]}
    assert rows["MAIL_AGENT_API_KEY"]["set"] is False and rows["MAIL_AGENT_API_KEY"]["shape"] is None
    assert body["missing"] == ["MAIL_AGENT_API_KEY"]
    text = json.dumps(body, ensure_ascii=False)
    assert_no_secret(text)
    assert "zzz" not in text and "UNRELATED_SECRET" not in text


async def test_빈_값은_없는_것으로_친다(cli_root):
    body = (await call(make_app(cli_root, env={**ENV, "LLM_PASS_KEY": ""}), "GET", f"{DIAG}/env")).json()
    assert "LLM_PASS_KEY" in body["missing"]


# ── net: DNS → TCP → TLS ─────────────────────────────────────────────────

@pytest.mark.parametrize("url,default,expected", [
    ("https://gw.example.com/v1", 443, ("gw.example.com", 443)),
    ("http://gw.example.com:8080", 80, ("gw.example.com", 8080)),
    ("redis://:pw@10.0.0.1:6380/0", 6379, ("10.0.0.1", 6380)),
    ("mongodb://u:p@h1:27018,h2:27017/db", 27017, ("h1", 27018)),
    ("mongodb://h1/db", 27017, ("h1", 27017)),
    ("https://[::1]:8443/x", 443, ("::1", 8443)),
])
def test_URL에서_호스트와_포트를_뽑는다(url, default, expected):
    assert host_port(url, default) == expected


async def _server(handler):
    async def closing(reader, writer):
        # 닫힘까지 기다린다 — 안 기다리면 서버가 닫힌 뒤 transport가 GC될 때 asyncio가 경고를 낸다(가끔).
        try:
            await handler(reader, writer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    server = await asyncio.start_server(closing, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_http면_DNS와_TCP를_보고_TLS는_없다고_적는다():
    async def handler(reader, writer):
        pass
    server, port = await _server(handler)
    async with server:
        steps = await connect_steps(f"http://127.0.0.1:{port}/x", verify=True, timeout=2.0)
    assert [(s["step"], s["ok"]) for s in steps] == [("dns", True), ("tcp", True), ("tls", None)]
    assert steps[1]["detail"] == f"127.0.0.1:{port}"


async def test_TCP가_안_붙으면_거기서_멈추고_던지지_않는다():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]                 # 듣지 않는 포트
    steps = await connect_steps(f"https://127.0.0.1:{port}", verify=True, timeout=2.0)
    assert [(s["step"], s["ok"]) for s in steps] == [("dns", True), ("tcp", False)]
    assert "Error" in steps[1]["detail"] or "refused" in steps[1]["detail"].lower()


async def test_TLS가_아닌_상대면_TLS_단계에서_실패를_적는다():
    async def handler(reader, writer):
        await reader.read(10)
        writer.write(b"HTTP/1.1 400 Bad\r\n\r\n")
        await writer.drain()
    server, port = await _server(handler)
    async with server:
        steps = await connect_steps(f"https://127.0.0.1:{port}", verify=True, timeout=2.0)
    assert [(s["step"], s["ok"]) for s in steps] == [("dns", True), ("tcp", True), ("tls", False)]


async def test_DNS가_안_풀리면_DNS에서_멈춘다():
    steps = await connect_steps("https://없는-호스트.invalid", verify=True, timeout=2.0)
    assert [(s["step"], s["ok"]) for s in steps] == [("dns", False)]


async def test_net은_메일_에이전트와_LLM_게이트웨이를_가린_URL로(cli_root, monkeypatch):
    seen = []

    async def fake_steps(url, *, verify, timeout):
        seen.append(url)
        return [{"step": "dns", "ok": True, "ms": 1.0, "detail": "1.2.3.4"}]
    monkeypatch.setattr("src.presentation.api.routes_diag.connect_steps", fake_steps)
    body = (await call(make_app(cli_root), "POST", f"{DIAG}/net")).json()
    assert [t["name"] for t in body["targets"]] == ["mail", "llm"]
    assert body["targets"][1]["url"] == "https://***@gw.example.com/v1"
    assert body["targets"][1]["trust_env_proxy"] is True
    assert seen == ["https://agent.example.net/api/v1/run", "https://gw-user:gw-secret@gw.example.com/v1"]
    assert_no_secret(json.dumps(body, ensure_ascii=False))


async def test_config가_안_읽히면_net은_던지지_않고_오류를_말한다(cli_root):
    body = (await call(make_app(cli_root, env={}), "POST", f"{DIAG}/net")).json()
    assert "LLM_PASS_KEY" in body["error"]


# ── trace ────────────────────────────────────────────────────────────────

def _extended_err(*, origin=2, type_=11, code=0, offender="10.1.2.3") -> bytes:
    head = struct.pack("=IBBBBII", 113, origin, type_, code, 0, 0, 0)
    sockaddr = struct.pack("=HH4s8x", socket.AF_INET, 0, socket.inet_aton(offender))
    return head + sockaddr


def test_ICMP_오류_큐에서_hop_장비_주소를_읽는다():
    parsed = parse_extended_err(_extended_err())
    assert parsed == {"origin": 2, "type": 11, "code": 0, "offender": "10.1.2.3"}


def test_로컬_오류면_장비_주소가_없다():
    head = struct.pack("=IBBBBII", 90, 1, 0, 0, 0, 0, 0) + b"\x00" * 16
    assert parse_extended_err(head)["offender"] is None
    assert parse_extended_err(b"\x00" * 4) is None


async def test_trace는_도착하면_멈추고_응답_없는_hop은_별표():
    answers = iter([
        {"kind": "hop", "addr": "10.0.0.1", "ms": 1.0},
        {"kind": "timeout", "addr": None, "ms": 1000.0},
        {"kind": "hop", "addr": "10.0.9.1", "ms": 3.0},
        {"kind": "reached", "addr": "10.9.9.9", "ms": 4.0},
        {"kind": "hop", "addr": "안 나와야 한다", "ms": 0.0},
    ])
    calls = []

    def probe(addr, port, ttl, timeout):
        calls.append((addr, port, ttl))
        return next(answers)
    result = await trace("10.9.9.9", 6379, max_hops=10, hop_timeout_s=1.0, probe=probe)
    assert [h["ttl"] for h in result["hops"]] == [1, 2, 3, 4]
    assert [h["addr"] for h in result["hops"]] == ["10.0.0.1", "*", "10.0.9.1", "10.9.9.9"]
    assert result["reached"] is True
    assert calls[0] == ("10.9.9.9", 6379, 1)


async def test_trace는_차단_응답에서_멈추고_이유를_적는다():
    answers = iter([{"kind": "hop", "addr": "10.0.0.1", "ms": 1.0},
                    {"kind": "unreachable", "addr": "10.0.0.2", "ms": 2.0, "icmp": [3, 13]}])
    result = await trace("10.9.9.9", 443, max_hops=10, hop_timeout_s=1.0,
                         probe=lambda *a: next(answers))
    assert result["reached"] is False
    assert result["hops"][-1]["note"] == "관리상 차단(방화벽) — ICMP 3/13"


async def test_trace는_예산을_넘기면_멈춘다():
    ticks = iter([0.0, 0.0, 30.0, 61.0, 99.0])
    result = await trace("10.9.9.9", 443, max_hops=10, hop_timeout_s=1.0, budget_s=50.0,
                         probe=lambda *a: {"kind": "timeout", "addr": None, "ms": 1000.0},
                         ticker=lambda: next(ticks))
    assert len(result["hops"]) == 2
    assert "예산" in result["stopped"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="IP_RECVERR는 Linux")
async def test_진짜_소켓으로_로컬_서버까지_한_hop에_닿는다():
    async def handler(reader, writer):
        pass
    server, port = await _server(handler)
    async with server:
        hop = await asyncio.to_thread(probe_once, "127.0.0.1", port, 1, 2.0)
    assert hop["kind"] == "reached" and hop["addr"] == "127.0.0.1"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="IP_RECVERR는 Linux")
def test_진짜_소켓으로_닫힌_포트는_refused():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    hop = probe_once("127.0.0.1", port, 5, 2.0)
    assert hop["kind"] == "refused"


async def test_trace_경로는_config의_대상만_받는다(cli_root):
    async def run(body):
        return await call(make_app(cli_root, trace_probe=lambda addr, port, ttl, timeout: {
            "kind": "reached", "addr": addr, "ms": 1.0}), "POST", f"{DIAG}/net/trace", body)

    assert (await run({"target": "http://evil.example:22"})).status_code == 422
    mail = (await run({"target": "mail"})).json()
    assert (mail["host"], mail["port"]) == ("agent.example.net", 443)
    redis = (await run({"target": "redis", "gbm": "mx", "fct": "gumi"})).json()
    assert (redis["host"], redis["port"]) == ("gumi-mx.example", 6379)
    vague = (await run({"target": "redis"})).json()
    assert "하나로" in vague["error"]


# ── egress · doctor/all ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,ip", [
    ("id=3 addr=10.0.0.5:51234 laddr=10.0.0.9:6379 fd=8 name=", "10.0.0.5"),
    ({"id": "3", "addr": "10.0.0.5:51234"}, "10.0.0.5"),
    ("10.0.0.5:51234", "10.0.0.5"),
    ("[::1]:5000", "::1"),
    ("id=3 laddr=x", None),
])
def test_밖에서_보이는_주소에서_IP만(raw, ip):
    assert seen_ip(raw) == ip


class FakeProbes:
    def __init__(self, *, fail=(), delay=0.0):
        self.fail, self.delay = set(fail), delay
        self.now = self.peak = 0

    async def _go(self, kind, cfg):
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(self.delay)
            if any(f in cfg.url for f in self.fail):
                raise ConnectionError(f"{kind} 거부")
            return {"redis_ping": "PONG", "mongo_ping": "ok",
                    "redis_seen_as": "10.0.0.5:4000", "mongo_seen_as": "10.0.0.6:4001"}[kind]
        finally:
            self.now -= 1

    async def redis_ping(self, cfg):
        return await self._go("redis_ping", cfg)

    async def mongo_ping(self, cfg):
        return await self._go("mongo_ping", cfg)

    async def redis_seen_as(self, cfg):
        return await self._go("redis_seen_as", cfg)

    async def mongo_seen_as(self, cfg):
        return await self._go("mongo_seen_as", cfg)


async def test_doctor_all은_켜진_사이트마다_접속만_보고_동시_수를_지킨다(cli_root):
    probes = FakeProbes(fail=["sevt-mx"], delay=0.05)
    body = (await call(make_app(cli_root, site_probes=probes), "POST", f"{DIAG}/doctor/all",
                       {"concurrency": 1})).json()
    rows = {row["site"]: row for row in body["sites"]}
    assert set(rows) == {"mx/gumi", "mx/sevt", "nw/sevt"}            # 꺼진 mx/off는 안 본다
    assert rows["mx/gumi"]["redis"]["ok"] and rows["mx/gumi"]["mongo"]["ok"]
    assert not rows["mx/sevt"]["redis"]["ok"] and "거부" in rows["mx/sevt"]["redis"]["error"]
    assert (body["ok"], body["failed"]) == (2, 1)
    assert probes.peak <= 2                                         # 사이트 하나 안의 redis·mongo 둘까지만


async def test_doctor_all은_gbm으로_좁힌다(cli_root):
    body = (await call(make_app(cli_root, site_probes=FakeProbes()), "POST", f"{DIAG}/doctor/all",
                       {"gbm": "nw"})).json()
    assert [row["site"] for row in body["sites"]] == ["nw/sevt"]


async def test_사이트_config가_깨져도_그_줄만_오류다(cli_root):
    env = {k: v for k, v in ENV.items() if k != "REDIS_PASSWORD"}
    body = (await call(make_app(cli_root, env=env, site_probes=FakeProbes()), "POST",
                       f"{DIAG}/doctor/all")).json()
    assert all("REDIS_PASSWORD" in row["error"] for row in body["sites"])
    assert body["failed"] == 3


async def test_egress는_사이트마다_밖에서_보이는_IP(cli_root):
    body = (await call(make_app(cli_root, site_probes=FakeProbes(fail=["gumi-mx.example:27017"])), "POST",
                       f"{DIAG}/net/egress", {"gbm": "mx", "fct": "gumi"})).json()
    [row] = body["sites"]
    assert row["site"] == "mx/gumi"
    assert row["redis"] == {"ok": True, "ip": "10.0.0.5", "seen_as": "10.0.0.5:4000", "ms": row["redis"]["ms"]}
    assert row["mongo"]["ok"] is False


# ── llm/raw ──────────────────────────────────────────────────────────────

async def test_llm_raw는_SDK_없이_한_번_보내고_원문을_돌려준다(cli_root):
    sent = []

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(200, json={"model": "openai/gpt-oss-120b",
                                         "choices": [{"message": {"content": "pong"}}]})
    app = make_app(cli_root, llm_transport=httpx.MockTransport(handler))
    body = (await call(app, "POST", f"{DIAG}/llm/raw", {"prompt": "핑"})).json()
    [request] = sent
    assert (request.url.host, request.url.path) == ("gw.example.com", "/v1/chat/completions")
    assert request.headers["X-FABRIX-CLIENT"] == "pass-secret-1"
    assert request.headers["X-LLM-MODEL-ID"] == "339"
    assert json.loads(request.content)["messages"] == [{"role": "user", "content": "핑"}]
    assert (body["ok"], body["status"], body["reported_model"]) == (True, 200, "openai/gpt-oss-120b")
    assert "pong" in body["body"]
    assert "X-FABRIX-CLIENT" in body["sent_headers"]
    assert body["url"] == "https://***@gw.example.com/v1/chat/completions"
    assert_no_secret(json.dumps(body, ensure_ascii=False))


async def test_llm_raw는_실패도_값으로(cli_root):
    def handler(request):
        raise httpx.ConnectError("연결 거부")
    app = make_app(cli_root, llm_transport=httpx.MockTransport(handler))
    body = (await call(app, "POST", f"{DIAG}/llm/raw")).json()
    assert body["ok"] is False and "ConnectError" in body["error"]


async def test_llm_raw는_4xx도_원문_그대로(cli_root):
    app = make_app(cli_root, llm_transport=httpx.MockTransport(
        lambda request: httpx.Response(401, text="invalid token")))
    body = (await call(app, "POST", f"{DIAG}/llm/raw")).json()
    assert (body["ok"], body["status"], body["body"]) == (False, 401, "invalid token")
