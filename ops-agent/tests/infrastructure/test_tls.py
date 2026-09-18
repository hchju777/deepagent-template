"""TLS 방침이 **이 커넥션에만** 적용되는가 — 전역을 건드리지 않는가.

사내에서 흔히 쓰는 처방은 전역을 바꾼다:

```python
ssl._create_default_https_context = ssl._create_unverified_context
os.environ["REQUESTS_CA_BUNDLE"] = ""
requests.Session.request = <verify=False를 박은 패치>
```

그러면 LLM 게이트웨이 하나 때문에 Redis·Mongo·Kafka·대상 REST 접속의 인증서
검증까지 같이 꺼진다. **산문으로 "하지 말자"고 적어도 지켜지지 않으므로
테스트가 지킨다.**
"""
import os
import ssl

import pytest

from src.config.schema_llm import LlmConfig, TlsConfig
from src.infrastructure.tls import tls_problems, verify_arg, warn_if_unverified


def test_기본값은_검증을_켠다():
    assert verify_arg(TlsConfig()) is True


def test_검증을_끄면_False를_돌려준다():
    assert verify_arg(TlsConfig(verify=False)) is False


def test_번들을_주면_SSLContext를_만든다(tmp_path):
    # 실제 PEM이 필요하므로 표준 신뢰 번들을 복사해 쓴다.
    import certifi
    bundle = tmp_path / "corp-ca.pem"
    bundle.write_text(certifi.contents(), encoding="utf-8")
    context = verify_arg(TlsConfig(ca_bundle=str(bundle)))
    assert isinstance(context, ssl.SSLContext)


def test_시스템_저장소를_쓰면_SSLContext를_만든다():
    # Windows면 CryptoAPI, Linux면 OpenSSL의 기본 저장소를 본다.
    assert isinstance(verify_arg(TlsConfig(use_system_store=True)), ssl.SSLContext)


# ── 전역을 건드리지 않는다 ────────────────────────────────────────────

def test_어떤_방침이든_전역_ssl_컨텍스트를_바꾸지_않는다():
    before = ssl._create_default_https_context                     # noqa: SLF001
    for tls in (TlsConfig(), TlsConfig(verify=False), TlsConfig(use_system_store=True)):
        verify_arg(tls)
    assert ssl._create_default_https_context is before, (          # noqa: SLF001
        "전역 SSL 컨텍스트가 바뀌었다 — 대상 시스템 접속의 검증까지 같이 꺼진다")


def test_env에_CA_번들_변수를_심지_않는다():
    before = {k: os.environ.get(k) for k in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                                             "SSL_CERT_FILE")}
    verify_arg(TlsConfig(verify=False))
    after = {k: os.environ.get(k) for k in before}
    assert after == before, "env를 건드렸다 — requests를 쓰는 모든 라이브러리가 영향받는다"


def test_requests의_메서드를_패치하지_않는다():
    requests = pytest.importorskip("requests")
    before = requests.Session.request
    verify_arg(TlsConfig(verify=False))
    assert requests.Session.request is before, (
        "requests를 원숭이 패치했다 — 사내 SDK·클라우드 SDK까지 검증 없이 나간다")


# ── 기동 검증과 경고 ──────────────────────────────────────────────────

def test_없는_번들_경로는_기동에서_잡는다():
    problems = tls_problems(TlsConfig(ca_bundle="/없는/경로/ca.pem"))
    assert problems and "ca_bundle을 읽을 수 없다" in problems[0]


def test_정상_설정에는_문제가_없다():
    assert tls_problems(TlsConfig()) == []


def test_검증이_꺼졌으면_시끄럽게_경고한다():
    warnings = []
    warn_if_unverified(TlsConfig(verify=False), warn=warnings.append)
    assert warnings and "TLS 검증이 꺼져 있다" in warnings[0]
    assert "use_system_store" in warnings[0], "대안을 같이 말해야 한다"


def test_검증이_켜져_있으면_조용하다():
    warnings = []
    warn_if_unverified(TlsConfig(), warn=warnings.append)
    assert warnings == []


# ── 선택지를 섞어 적으면 거부 ─────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"ca_bundle": "/a.pem", "verify": False},
    {"ca_bundle": "/a.pem", "use_system_store": True},
    {"use_system_store": True, "verify": False},
])
def test_두_방침을_함께_적으면_거부한다(bad):
    # 어느 쪽이 이기는지를 코드가 정하게 두면, 사람은 자기가 적은 쪽이 먹는다고 믿는다.
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TlsConfig(**bad)


def test_describe는_TLS_상태를_보인다():
    cfg = LlmConfig(model="m", base_url="https://g", tls=TlsConfig(verify=False))
    assert "검증 끔" in cfg.describe()
