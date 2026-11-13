"""네트워크 진단 — DNS → TCP → TLS 단계, 그리고 실제 포트로 가는 hop.

## 왜 단계로 나누는가

"메일이 안 간다"·"LLM이 안 붙는다"의 원인은 이름 풀이·방화벽·인증서 중 하나인 경우가 대부분인데, 라이브러리의
오류 한 줄은 그 셋을 잘 가르지 않는다(`ConnectError`가 셋 다일 수 있다). 단계를 따로 재면 **어디서 멈췄는지**가
답이 된다.

## 왜 trace가 ICMP가 아니라 TCP SYN인가

방화벽은 포트를 본다. 평범한 traceroute(UDP·ICMP)는 우리가 실제로 쓰는 443·6379·27017과 다른 길로 갈 수 있다.
TTL을 1부터 올린 SYN을 **그 포트로** 보내고, 도중 장비가 돌려준 ICMP(시간 초과·도달 불가)를 소켓의 오류 큐
(`IP_RECVERR`)에서 읽으면 raw 소켓(root) 없이 hop 장비 주소가 나온다. Linux 전용이다.
"""
import asyncio
import errno
import os
import select
import socket
import ssl
import struct
import sys
import time

# 파이썬이 상수를 안 내놓는 버전이 있다(3.13에도 `socket.IP_RECVERR`가 없다) — 값은 Linux 헤더(`in.h`·`socket.h`)의 것.
IP_RECVERR = getattr(socket, "IP_RECVERR", 11)
MSG_ERRQUEUE = getattr(socket, "MSG_ERRQUEUE", 0x2000)
_SO_EE_ORIGIN_ICMP = 2
_ICMP_UNREACH = 3
_ICMP_TIME_EXCEEDED = 11
# 도달 불가의 code → 사람이 읽을 이름. 13이 방화벽(관리상 차단)이라 제일 자주 찾는 답이다.
_UNREACH = {0: "네트워크 도달 불가", 1: "호스트 도달 불가", 3: "포트 도달 불가",
            9: "네트워크 관리상 차단", 10: "호스트 관리상 차단", 13: "관리상 차단(방화벽)"}
# Ingress가 60초에 끊는다 — trace 전체가 이 안에 끝나야 응답이 나간다.
TRACE_BUDGET_S = 50.0
_DEFAULT_PORTS = {"https": 443, "http": 80, "redis": 6379, "rediss": 6379,
                  "mongodb": 27017, "mongodb+srv": 27017}


def host_port(url: str, default_port: int | None = None) -> tuple[str, int]:
    """URL에서 (호스트, 포트). 계정·경로를 떼고, Mongo의 여러 호스트(`h1:1,h2:2`)는 첫째를 쓴다.

    `urllib.parse`를 안 쓰는 이유: Mongo 다중 호스트의 netloc을 `hostname`이 `h1:27017,h2`로 돌려준다.
    """
    scheme, _, rest = url.partition("://")
    cut = min((i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0), default=len(rest))
    authority = rest[:cut].rsplit("@", 1)[-1].split(",")[0]
    port = default_port if default_port is not None else _DEFAULT_PORTS.get(scheme.lower(), 443)
    if authority.startswith("["):                                  # [IPv6]:port
        host, _, after = authority[1:].partition("]")
        return host, int(after[1:]) if after.startswith(":") else port
    host, colon, number = authority.rpartition(":")
    if colon and number.isdigit():
        return host, int(number)
    return authority, port


def _step(name: str, ok, started: float, ticker, detail: str) -> dict:
    return {"step": name, "ok": ok, "ms": round((ticker() - started) * 1000, 1), "detail": detail}


def _why(exc: BaseException, timeout: float) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"시간 초과({timeout:g}초)"
    return f"{type(exc).__name__}: {exc}"


def _ssl_context(verify) -> ssl.SSLContext:
    """`tls.verify_arg`가 돌려준 것(컨텍스트 또는 bool)을 소켓용 컨텍스트로 — 실제 클라이언트와 같은 방침으로 잰다."""
    if isinstance(verify, ssl.SSLContext):
        return verify
    context = ssl.create_default_context()
    if verify is False:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


async def connect_steps(url: str, *, verify, timeout: float, ticker=time.perf_counter) -> list[dict]:
    """[dns, tcp, tls] — 실패한 단계에서 멈춘다. **던지지 않는다.** http면 tls 칸은 `ok: None`."""
    host, port = host_port(url)
    secure = url.lower().startswith("https://")
    steps: list[dict] = []
    started = ticker()
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout)
        addresses = list(dict.fromkeys(info[4][0] for info in infos))
    except Exception as exc:                                       # noqa: BLE001
        steps.append(_step("dns", False, started, ticker, f"{host} — {_why(exc, timeout)}"))
        return steps
    steps.append(_step("dns", True, started, ticker, ", ".join(addresses)))

    target = f"{addresses[0]}:{port}"
    started = ticker()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(addresses[0], port), timeout)
    except Exception as exc:                                       # noqa: BLE001
        steps.append(_step("tcp", False, started, ticker, f"{target} — {_why(exc, timeout)}"))
        return steps
    steps.append(_step("tcp", True, started, ticker, target))
    try:
        if not secure:
            steps.append({"step": "tls", "ok": None, "ms": None, "detail": "http — TLS 없음"})
            return steps
        started = ticker()
        try:
            await asyncio.wait_for(writer.start_tls(_ssl_context(verify), server_hostname=host), timeout)
        except Exception as exc:                                   # noqa: BLE001
            steps.append(_step("tls", False, started, ticker, _why(exc, timeout)))
            return steps
        steps.append(_step("tls", True, started, ticker, _describe_tls(writer)))
        return steps
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2.0)
        except Exception:                                          # noqa: BLE001
            pass


def _describe_tls(writer) -> str:
    tls = writer.get_extra_info("ssl_object")
    if tls is None:
        return "TLS"
    parts = [tls.version() or "TLS"]
    cert = tls.getpeercert() or {}            # 검증을 껐으면 빈 dict다
    subject = dict(x[0] for x in cert.get("subject", ()))
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    if subject.get("commonName"):
        parts.append(f"CN={subject['commonName']}")
    if issuer.get("commonName"):
        parts.append(f"발급={issuer['commonName']}")
    if cert.get("notAfter"):
        parts.append(f"만료={cert['notAfter']}")
    return " · ".join(parts)


# ── trace ────────────────────────────────────────────────────────────────

def parse_extended_err(data: bytes) -> dict | None:
    """`struct sock_extended_err`(16바이트) + 그 뒤의 `SO_EE_OFFENDER`(sockaddr_in) — 오류를 낸 장비의 주소."""
    if len(data) < 16:
        return None
    _errno, origin, kind, code, _pad, _info, _data = struct.unpack("=IBBBBII", data[:16])
    offender = None
    if len(data) >= 24 and struct.unpack("=H", data[16:18])[0] == socket.AF_INET:
        offender = socket.inet_ntoa(data[20:24])
    return {"origin": origin, "type": kind, "code": code, "offender": offender}


def probe_once(address: str, port: int, ttl: int, timeout: float) -> dict:
    """TTL을 건 SYN 하나. `kind`: hop(도중 장비) · reached(붙음) · refused(상대가 RST — 호스트까지는 닿음) ·
    unreachable(도달 불가 ICMP) · timeout(응답 없음) · error. 블로킹이다 — `asyncio.to_thread`로 부른다."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    started = time.perf_counter()

    def done(kind, addr=None, **extra):
        return {"kind": kind, "addr": addr, "ms": round((time.perf_counter() - started) * 1000, 1), **extra}
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
        sock.setsockopt(socket.IPPROTO_IP, IP_RECVERR, 1)
        sock.setblocking(False)
        sock.connect_ex((address, port))
        poller = select.poll()
        poller.register(sock, select.POLLOUT | select.POLLERR)
        if not poller.poll(int(timeout * 1000)):
            return done("timeout")
        try:
            _, ancillary, _, _ = sock.recvmsg(1, 1024, MSG_ERRQUEUE)
        except (BlockingIOError, InterruptedError, OSError):
            ancillary = []
        for level, kind, data in ancillary:
            if level != socket.IPPROTO_IP or kind != IP_RECVERR:
                continue
            err = parse_extended_err(data)
            if err is None or err["origin"] != _SO_EE_ORIGIN_ICMP:
                continue
            if err["type"] == _ICMP_TIME_EXCEEDED:
                return done("hop", err["offender"])
            if err["type"] == _ICMP_UNREACH:
                return done("unreachable", err["offender"], icmp=[err["type"], err["code"]])
            return done("hop", err["offender"], icmp=[err["type"], err["code"]])
        failure = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if failure == 0:
            return done("reached", address)
        if failure == errno.ECONNREFUSED:
            return done("refused", address)
        return done("error", None, error=os.strerror(failure))
    except Exception as exc:                                       # noqa: BLE001
        return done("error", None, error=f"{type(exc).__name__}: {exc}")
    finally:
        sock.close()


def _note(hop: dict) -> str | None:
    if hop["kind"] == "unreachable":
        kind, code = hop.get("icmp") or (3, None)
        return f"{_UNREACH.get(code, '도달 불가')} — ICMP {kind}/{code}"
    if hop["kind"] == "refused":
        return "포트가 닫혀 있다(RST) — 호스트까지는 닿았다"
    if hop["kind"] == "timeout":
        return "응답 없음 — 이 장비가 ICMP를 안 돌려주거나 여기서 막혔다"
    if hop["kind"] == "error":
        return hop.get("error")
    return None


async def trace(address: str, port: int, *, max_hops: int, hop_timeout_s: float,
                budget_s: float = TRACE_BUDGET_S, probe=probe_once, ticker=time.monotonic) -> dict:
    """TTL 1..N. 닿거나(reached·refused) 막히면(unreachable) 멈춘다. **던지지 않는다.**"""
    if probe is probe_once and not sys.platform.startswith("linux"):
        return {"supported": False, "hops": [], "reached": False,
                "stopped": f"Linux 전용이다(IP_RECVERR) — 지금 {sys.platform}"}
    hops: list[dict] = []
    stopped = f"최대 {max_hops} hop"
    start = ticker()
    for ttl in range(1, max_hops + 1):
        if ticker() - start > budget_s:
            stopped = f"시간 예산 {budget_s:g}초를 넘겨 멈췄다(Ingress 60초)"
            break
        try:
            hop = await asyncio.to_thread(probe, address, port, ttl, hop_timeout_s)
        except Exception as exc:                                   # noqa: BLE001
            hop = {"kind": "error", "addr": None, "ms": None, "error": f"{type(exc).__name__}: {exc}"}
        row = {"ttl": ttl, "kind": hop["kind"], "addr": hop.get("addr") or "*", "ms": hop.get("ms")}
        note = _note(hop)
        if note:
            row["note"] = note
        hops.append(row)
        if hop["kind"] in ("reached", "refused"):
            stopped = "닿았다"
            break
        if hop["kind"] == "unreachable":
            stopped = "도달 불가 응답에서 멈췄다"
            break
    return {"supported": True, "hops": hops, "stopped": stopped,
            "reached": bool(hops) and hops[-1]["kind"] in ("reached", "refused")}


async def resolve_v4(host: str, timeout: float = 5.0) -> str:
    """IPv4 주소 하나 — 던진다(부르는 쪽이 값으로 바꾼다). trace는 `IP_TTL`(IPv4)만 쓴다."""
    infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
        host, None, family=socket.AF_INET, type=socket.SOCK_STREAM), timeout)
    return infos[0][4][0]
