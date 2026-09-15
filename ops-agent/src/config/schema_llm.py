"""LLM 설정 — 사내 게이트웨이와, 그것이 아닌 것들.

## 왜 adapter와 provider가 둘인가

- `adapter`: **무엇으로 말하는가** — LangChain 채팅 모델인가, 평범한 HTTP인가.
- `provider`: **어떤 규약으로 말하는가** — OpenAI 호환인가.

사내 코드가 쓰는 어휘를 그대로 따랐다. 둘을 분리해 두면 "langchain을 설치할 수
없는 환경"(사내 PyPI가 막힌 경우)에서 `adapter: "http"`로 바꿔 같은 게이트웨이에
붙을 수 있다 — 규약은 같고 말하는 도구만 다르다.

## 사내 게이트웨이가 OpenAI 규약인데 인증만 다르다

`Authorization` 헤더를 보지 않고 헤더 셋이 인증한다:

| 헤더 | 값 |
|---|---|
| `X-FABRIX-CLIENT` | pass_key |
| `X-OPENAPI-TOKEN` | client_key |
| `X-LLM-MODEL-ID` | model_id (예 "339") |

그런데 `ChatOpenAI`는 api_key가 없으면 `OPENAI_API_KEY` env를 뒤지다 죽으므로
자리를 채워야만 한다. 그래서 기본값이 `"EMPTY"`인 sentinel이다 — 소켓에 나가도
아무 뜻이 없고, 진짜 인증은 헤더 셋이 한다.

## 다른 LLM으로 갈아끼우기

`headers`가 임의 헤더를 받으므로 다른 게이트웨이도 config만으로 붙는다.
OpenAI 본체·vLLM·Ollama(`/v1`)·LiteLLM은 `api_key`만 채우고 `pass_key`/
`client_key`를 비우면 된다.
"""
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator

from src.domain.base import StrictModel


class TlsConfig(StrictModel):
    """이 커넥션 **하나**의 TLS 방침. 전역에는 손대지 않는다.

    사내 루트 CA가 파이썬 신뢰 저장소에 없어서 TLS가 실패하는 것이 흔하다.
    검색하면 나오는 처방은 이것인데:

    ```python
    ssl._create_default_https_context = ssl._create_unverified_context   # 쓰지 않는다
    os.environ["REQUESTS_CA_BUNDLE"] = ""
    requests.Session.request = <verify=False를 박은 패치>
    ```

    **프로세스 전체**의 검증을 끈다. LLM 하나 붙이려고 Redis·Mongo·Kafka·대상
    REST 접속 다섯 종의 인증서 검증을 같이 끄는 셈이고, 그건 모니터링 대상보다
    모니터링 도구가 더 위험해지는 상태다.

    여기서는 세 가지 중 하나를 고른다:

    | 선택 | 언제 | 범위 |
    |---|---|---|
    | `use_system_store: true` | Windows (권장) | 이 커넥션만 |
    | `ca_bundle: "C:/certs/ca.pem"` | 번들 파일이 있을 때 | 이 커넥션만 |
    | `verify: false` | 번들을 구하기 전 임시 | 이 커넥션만 |

    `use_system_store`는 `truststore`로 **OS 신뢰 저장소**를 본다. 사내 CA가
    Windows 인증서 저장소에 이미 있으므로(브라우저가 되니까) `.pem` 내보내기가
    불필요해진다.
    """

    verify: bool = True
    ca_bundle: str | None = None
    use_system_store: bool = False

    @model_validator(mode="after")
    def _one_choice_only(self):
        # 둘을 함께 적으면 어느 쪽이 이기는지를 코드가 정하게 되고,
        # 사람은 자기가 적은 쪽이 먹는다고 믿는다.
        if self.ca_bundle and not self.verify:
            raise ValueError("ca_bundle과 verify=false를 함께 적을 수 없다 — 하나만 고르라")
        if self.ca_bundle and self.use_system_store:
            raise ValueError("ca_bundle과 use_system_store를 함께 적을 수 없다")
        if self.use_system_store and not self.verify:
            raise ValueError("use_system_store와 verify=false를 함께 적을 수 없다")
        return self


class LlmConfig(StrictModel):
    adapter: Literal["chat_model", "http", "echo"] = "chat_model"
    provider: Literal["openai_compatible"] = "openai_compatible"
    # 요청 body의 `model` 필드에 실린다.
    #
    # **주의: 게이트웨이에 따라 이 값이 아무 일도 안 한다.** 사내 게이트웨이는
    # `X-LLM-MODEL-ID` 헤더로 모델을 고르고 body의 model은 검증조차 하지 않는다
    # (없는 이름을 적어도 정상 응답이 온다). 그런 게이트웨이에서 이 필드는
    # **사람이 읽는 이름표**일 뿐이고, 실제로 무엇이 답했는지는
    # `expect_reported_model`로 확인한다.
    model: str
    # X-LLM-MODEL-ID 헤더. 사내 게이트웨이에서는 **이것이 진짜 선택자다** —
    # 값을 바꾸면 호출 자체가 실패한다(그게 라우팅한다는 증거다).
    model_id: str = ""
    # 게이트웨이가 응답에 실어 주는 모델 이름. **사람이 확인해서 적는다.**
    #
    # 왜 필요한가: 사내 게이트웨이는 `GET /models`에 405를 준다 — 어느 ID가 어느
    # 모델인지 **런타임에 알아낼 방법이 없다.** 확인할 수 있는 유일한 경로가
    # 응답에 실려 오는 이름이고, 그것을 여기 박제해 두면 게이트웨이가 나중에
    # 모델을 조용히 바꿨을 때 즉시 드러난다.
    #
    # 비워 두면 `model`과 같기를 기대한다(요청한 이름으로 답하는 보통의 게이트웨이).
    expect_reported_model: str | None = None
    temperature: float = 0.0
    base_url: str = ""
    timeout_s: float = 60.0
    # 일시적 실패(429·게이트웨이 재시작)에 몇 번 다시 물을지. 0이면 한 번만 시도한다.
    # 조사는 라운드마다 LLM을 여러 번 부르므로 재시도가 길면 전체가 늘어진다.
    max_retries: int = 2
    # `HTTPS_PROXY`/`NO_PROXY` env를 따를지. 기본은 따른다(httpx 기본값).
    #
    # **사내에서 끄게 되는 경우**: 전사 프록시가 env에 박혀 있는데 LLM 게이트웨이는
    # 사내망 안이라 프록시를 거치면 안 되는 상황. `NO_PROXY`에 게이트웨이 호스트를
    # 넣는 것이 정석이지만 그 env를 우리가 바꿀 수 없는 배치도 있다. 그때
    # `trust_env_proxy: false`가 **이 커넥션만** 프록시를 무시하게 한다.
    trust_env_proxy: bool = True

    # OpenAI 규약의 api_key. 사내 게이트웨이는 보지 않으므로 sentinel이 기본값이다.
    api_key: SecretStr = SecretStr("EMPTY")
    pass_key: SecretStr | None = None      # X-FABRIX-CLIENT
    client_key: SecretStr | None = None    # X-OPENAPI-TOKEN
    headers: dict[str, str] = {}           # 그 외 게이트웨이용 임의 헤더

    tls: TlsConfig = TlsConfig()

    @field_validator("temperature")
    @classmethod
    def _range(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError(f"temperature는 0~2다 — {v}")
        return v

    @model_validator(mode="after")
    def _network_adapters_need_base_url(self):
        if self.adapter in ("chat_model", "http") and not self.base_url:
            raise ValueError(f"adapter={self.adapter}에는 base_url이 필요하다")
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url은 http(s)://로 시작해야 한다 — {self.base_url}")
        return self

    @model_validator(mode="after")
    def _gateway_keys_come_in_pairs(self):
        # 하나만 채우면 인증이 반쯤 된 상태로 나가고, 게이트웨이는 보통 401을
        # 돌려준다 — 그 메시지는 "키가 틀렸다"로 보여 둘 중 어느 쪽이 빠졌는지 모른다.
        if bool(self.pass_key) != bool(self.client_key):
            raise ValueError("pass_key와 client_key는 둘 다 있거나 둘 다 없어야 한다")
        if self.pass_key and not self.model_id:
            raise ValueError("사내 게이트웨이(pass_key)를 쓰면 model_id도 필요하다 "
                             "— X-LLM-MODEL-ID 헤더에 실린다")
        return self

    @model_validator(mode="after")
    def _header_values_must_be_ascii(self):
        """헤더 값에 ASCII 밖 문자가 있으면 거부한다.

        HTTP 헤더는 ASCII만 실을 수 있어서, 키를 복사하다 전각 문자나 한글이
        섞이면 런타임에 `UnicodeEncodeError: 'ascii' codec can't encode`가 난다.
        그 메시지로는 **원인이 키라는 것을 아무도 짐작할 수 없다** — 인코딩
        버그처럼 보인다. 실제로 이 프로젝트에서 그 에러를 한 번 보고 나서 넣었다.

        값 자체는 메시지에 담지 않는다(비밀값이다). 어느 헤더인지만 말한다.
        """
        for name, value in self.gateway_headers().items():
            try:
                value.encode("ascii")
            except UnicodeEncodeError:
                raise ValueError(
                    f"{name} 헤더 값에 ASCII 밖 문자가 있다 — 키를 복사할 때 "
                    f"전각 문자나 공백이 섞였는지 확인하라") from None
        return self

    def gateway_headers(self) -> dict[str, str]:
        """소켓에 나갈 헤더. **조립은 여기 하나뿐이다.**

        어댑터마다 각자 조립하면 하나가 헤더 이름을 틀려도(`X-OPENAI-TOKEN`처럼)
        다른 쪽은 멀쩡해서, 증상이 "어떤 경로는 되고 어떤 경로는 401"이 된다.
        """
        headers = dict(self.headers)
        if self.pass_key and self.client_key:
            headers["X-FABRIX-CLIENT"] = self.pass_key.get_secret_value()
            headers["X-OPENAPI-TOKEN"] = self.client_key.get_secret_value()
            headers["X-LLM-MODEL-ID"] = self.model_id
        return headers

    def expected_model_name(self) -> str:
        """응답에 실려 오기를 기대하는 이름."""
        return self.expect_reported_model or self.model

    def reported_model_problem(self, reported: str | None) -> str | None:
        """게이트웨이가 답한 모델이 기대와 다른가. **비교는 여기 한 곳뿐이다.**

        CLI와 live 테스트가 각자 비교하면 관용 범위가 갈라지고, 느슨한 쪽이
        "통과했다"고 말한다.

        부분 일치로 보는 이유: 게이트웨이가 접두사를 붙이는 일이 흔하다
        (`gpt-oss-120b`를 요청하면 `openai/gpt-oss-120b`로 답한다). 그건 같은
        모델이므로 실패로 칠 이유가 없다.

        `reported`가 없으면 None이다 — 게이트웨이가 이름을 안 실어 주면 확인할
        방법이 없고, **확인할 수 없는 것을 실패로 만들면 그 신호는 곧 무시된다.**
        """
        if not reported:
            return None
        expected = self.expected_model_name()
        if expected in reported:
            return None
        if self.expect_reported_model:
            return (f"게이트웨이가 모델을 바꿨다 — config는 "
                    f"{self.expect_reported_model}를 박제했는데 {reported}로 응답했다. "
                    f"의도한 변경이면 config를 갱신하고, 아니면 사내에 확인하라")
        return (f"요청한 이름({self.model})과 응답한 이름({reported})이 다르다. "
                f"이 게이트웨이는 body의 model을 안 볼 수 있다 — 실제로 무엇이 "
                f"답하는지 확인한 뒤 config의 llm에 다음을 적어라:\n"
                f'    "expect_reported_model": "{reported}"')

    def describe(self) -> str:
        """사람이 읽을 한 줄 — 비밀값은 담지 않는다."""
        auth = "헤더 셋(사내 게이트웨이)" if self.pass_key else "api_key"
        tls = ("시스템 저장소" if self.tls.use_system_store
               else self.tls.ca_bundle or ("검증 켬" if self.tls.verify else "⚠ 검증 끔"))
        served = (f" 실제={self.expect_reported_model}"
                  if self.expect_reported_model and
                  self.expect_reported_model != self.model else "")
        return (f"{self.adapter}/{self.provider} {self.model}"
                f"{f'(id={self.model_id})' if self.model_id else ''}{served} "
                f"→ {self.base_url or '(네트워크 없음)'} [인증: {auth}, TLS: {tls}]")
