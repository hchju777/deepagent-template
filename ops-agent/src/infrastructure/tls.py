"""TLS 방침을 **이 커넥션 하나에만** 적용한다.

## 절대 하지 않는 것

```python
ssl._create_default_https_context = ssl._create_unverified_context   # 전역
os.environ["REQUESTS_CA_BUNDLE"] = ""                                 # 전역
requests.Session.request = <verify=False를 박은 패치>                  # 전역 + 원숭이 패치
```

세 줄 다 **프로세스 전체**를 바꾼다. LLM 게이트웨이 하나 붙이려고
Redis·Mongo·Kafka·대상 REST 접속 다섯 종의 인증서 검증을 같이 끄는 셈이고,
그러면 **모니터링 대상보다 모니터링 도구가 더 위험해진다.**

세 번째 줄은 특히 위험하다. `requests`를 쓰는 **모든** 라이브러리가
(사내 SDK, 클라우드 SDK, 텔레메트리) 조용히 검증 없이 나가고, 그 사실은
어디에도 기록되지 않는다.

대신 여기서는 `httpx` 클라이언트 하나에 `verify=`를 지정한다. 범위가 그
커넥션으로 닫히고, 전역·env·다른 라이브러리에 흔적을 남기지 않는다.
"""
import ssl
import sys

from src.config.schema_llm import TlsConfig


def verify_arg(tls: TlsConfig):
    """httpx의 `verify=` 인자 — SSLContext 또는 bool.

    경로 문자열을 그대로 넘기지 않는 이유: httpx 0.28이 `verify=<str>`을
    deprecate했고, 무엇보다 `ssl.create_default_context`는 **여기서 만든
    컨텍스트 하나만** 설정한다. `ssl._create_default_https_context`를
    갈아끼우는 것과 달리 전역에 아무 흔적을 남기지 않는다.
    """
    if tls.use_system_store:
        # OS 신뢰 저장소(Windows는 CryptoAPI)를 본다. 사내 CA가 거기 이미 있다.
        # truststore의 inject_into_ssl()은 쓰지 않는다 — 그건 전역이고,
        # 라이브러리 문서 자체가 "앱에서만 쓰라"고 경고한다.
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if tls.ca_bundle:
        return ssl.create_default_context(cafile=tls.ca_bundle)
    return tls.verify


def tls_problems(tls: TlsConfig) -> list[str]:
    """기동 검증이 부른다 — 번들 경로 오타를 런타임까지 미루지 않는다.

    미루면 밤에 첫 조사가 TLS로 죽는다. "읽히는 PEM인가"를 확인하는 방법은
    실제로 로드해 보는 것 말고 없다.
    """
    try:
        verify_arg(tls)
    except Exception as exc:                                       # noqa: BLE001
        if tls.use_system_store:
            return [f"시스템 신뢰 저장소를 쓸 수 없다 — {type(exc).__name__}: {exc}. "
                    f"truststore가 설치돼 있는지 확인하라"]
        return [f"ca_bundle을 읽을 수 없다 — {tls.ca_bundle}: {exc}"]
    return []


def warn_if_unverified(tls: TlsConfig, *, warn=None) -> None:
    """검증이 꺼진 채 조용히 도는 것이 이 리포에서 제일 위험한 상태다.

    기동 거부 철학의 형제 — **못 막을 것은 최소한 시끄럽게 만든다.**
    """
    if not tls.verify:
        (warn or _stderr)(
            "[llm] ⚠ TLS 검증이 꺼져 있다 (tls.verify=false). "
            "Windows면 tls.use_system_store=true로 바꾸면 사내 CA를 그대로 쓴다")


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)
