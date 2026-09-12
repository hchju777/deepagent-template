"""사이트 하나(법인/공장)의 대상 시스템 접속 설정.

키 이름은 **사내에서 이미 쓰는 형태**를 그대로 따랐다(`infra.redis`/`mongodb`/
`kafka.consumer`). 우리 취향대로 바꾸면 기존 config를 복사해 올 때마다 사람이
손으로 번역해야 하고, 번역은 언젠가 틀린다.

## 비밀번호는 왜 SecretStr인가

`print(config)`, 예외 메시지, 로그 한 줄. 평문 `str`이면 이 셋 중 하나에서
반드시 샌다. `SecretStr`은 repr이 `**********`라 실수로 찍어도 안 새고,
값을 꺼내려면 `.get_secret_value()`라고 **명시적으로** 써야 한다 — 그 한 줄이
코드 리뷰에서 눈에 띈다.

## 비밀번호를 config에 ${...}로 남기는 이유

어댑터가 `os.environ`을 몰래 읽으면 config만 봐서는 "이 사이트를 띄우려면
무엇이 필요한지" 알 수 없다. `${MX_GUMI_REDIS_PASSWORD}`라고 적혀 있으면
**기동 검증이 그 키가 비어 있다고 미리 말해 줄 수 있다.**
"""
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator

from src.domain.base import StrictModel


class RedisConfig(StrictModel):
    url: str
    db: int = 0
    password: SecretStr | None = None      # ACL username은 쓰지 않는다(사내 확인)

    @field_validator("url")
    @classmethod
    def _scheme(cls, v: str) -> str:
        if not v.startswith(("redis://", "rediss://")):
            raise ValueError(f"redis url은 redis:// 또는 rediss://로 시작해야 한다 — {v}")
        return v

    @field_validator("db")
    @classmethod
    def _db_range(cls, v: int) -> int:
        if not 0 <= v <= 15:
            raise ValueError(f"redis db는 0~15다 — {v}")
        return v


class MongoConfig(StrictModel):
    url: str
    database: str
    user: str | None = None
    password: SecretStr | None = None
    # `use admin` 후 db.createUser로 만든 계정은 admin DB에 산다 — 그래서 기본값이 admin이다.
    # 대상 DB에서 직접 만들었다면 그 DB 이름을 적어야 인증이 통과한다.
    auth_source: str = "admin"

    @field_validator("url")
    @classmethod
    def _scheme(cls, v: str) -> str:
        if not v.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError(f"mongodb url은 mongodb://로 시작해야 한다 — {v}")
        return v

    @model_validator(mode="after")
    def _user_needs_password(self):
        # 사내 규칙: user가 있으면 비밀번호가 있다. user만 적고 비밀번호를 빠뜨리면
        # 인증 없이 붙으려다 런타임에 죽는데, 그 에러는 "권한 없음"으로 보여
        # 계정 문제로 오해하기 쉽다.
        if self.user and not self.password:
            raise ValueError(f"mongodb user({self.user})가 있으면 password도 있어야 한다")
        if self.password and not self.user:
            raise ValueError("mongodb password만 있고 user가 없다")
        return self


class KafkaConsumerConfig(StrictModel):
    """**주의: group_ids는 우리가 참여할 그룹이 아니라 우리가 감시할 그룹들이다.**

    이 에이전트는 컨슈머 그룹에 절대 들어가지 않는다. 들어가면 브로커가
    리밸런스를 돌리고 `__consumer_offsets`에 커밋이 남으며, 같은 group_id를
    쓰는 실제 서비스의 파티션을 빼앗는다. 감시하러 들어가서 대상을 멈추는 셈이다.

    lag은 AdminClient로 밖에서 조회하고, 메시지는 `assign()`으로 그룹 밖에서
    직접 읽는다(`KafkaInspectorPort` 참고).

    **복수인 이유**: 한 법인의 같은 Kafka에 여러 서비스가 붙어 있고 각자 자기
    그룹을 쓴다(`dt-processor-mx-gumi`, `dt-sink-mx-gumi`, ...). 하나만 볼 수
    있게 만들면 나머지가 밀려도 모른다. 그리고 서비스는 **늘어난다.**

    그룹 이름에 법인이 박혀 있으므로(`-mx-gumi`) 이 값은 `gbm/` 층이 아니라
    `fct/{fct}/{gbm}.json`에 적는다. `"dt-processor-{gbm}-{fct}"` 같은 템플릿
    문법을 만들지 않는 이유: 오타 난 템플릿은 **존재하지 않는 그룹을 조용히
    감시한다**(lag 0으로 보이고, 그건 "정상"과 구별되지 않는다). 명시가 낫다.
    """

    bootstrap_server: list[str]
    group_ids: list[str] = []
    topic: dict[str, str] = {}    # 논리 이름 → 실제 토픽 이름 ("topic1": "GUMI_TOPIC")

    @field_validator("bootstrap_server")
    @classmethod
    def _at_least_one(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("bootstrap_server가 비어 있다")
        for entry in v:
            if ":" not in entry:
                raise ValueError(f"bootstrap_server는 host:port 형식이다 — {entry}")
        return v

    @field_validator("group_ids")
    @classmethod
    def _no_duplicate_groups(cls, v: list[str]) -> list[str]:
        # 같은 그룹이 두 번 있으면 lag 합계가 두 번 더해진다.
        duplicates = sorted({g for g in v if v.count(g) > 1})
        if duplicates:
            raise ValueError(f"group_ids에 중복이 있다 — {', '.join(duplicates)}")
        return v

    @model_validator(mode="after")
    def _something_to_watch(self):
        if not self.group_ids and not self.topic:
            raise ValueError("group_ids도 topic도 없다 — 감시할 것이 없으면 kafka 설정이 무의미하다")
        return self


class KafkaConfig(StrictModel):
    consumer: KafkaConsumerConfig


class RestParam(StrictModel):
    """등재 항목이 받는 파라미터 하나의 선언.

    `list`는 스칼라(str/int/float)들의 리스트다. 중첩 리스트나 dict는 받지 않는다 —
    그걸 허용하면 body 모양이 사실상 자유가 되고, 닫힌 스키마의 뜻이 사라진다.
    """
    type: Literal["str", "int", "float", "bool", "list"] = "str"
    required: bool = False


class RestEntry(StrictModel):
    """호출해도 되는 API 하나. **여기 없는 경로는 부를 수 없다.**"""
    method: Literal["GET", "POST"]
    path: str
    params: dict[str, RestParam] = {}

    @field_validator("path")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"path는 /로 시작해야 한다 — {v}")
        if "://" in v:
            raise ValueError(f"path에 호스트가 들어 있다 — {v}. 호스트는 base_url이 정한다")
        return v


class RestConfig(StrictModel):
    """대상 REST API. 등재된 항목만 호출할 수 있다(CLAUDE.md 규율 9와 같은 계열).

    `read_only: true` 같은 플래그를 두지 않는 이유: 목록에 없으면 문이 안 열리므로
    플래그가 중복이고, 끝점 수십 개에 플래그를 적으라고 하면 사람은 기동 검증을
    통과시키려고 전부 true로 적는다. 아무도 생각하지 않는 체크박스의 안전 가치는 0이다.
    """
    base_url: str
    timeout_s: float = 10.0
    headers: dict[str, str] = {}          # ${...}로 API 키를 참조할 수 있다
    entries: dict[str, RestEntry] = {}

    @field_validator("base_url")
    @classmethod
    def _scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"base_url은 http:// 또는 https://로 시작해야 한다 — {v}")
        return v.rstrip("/")

    @field_validator("timeout_s")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"timeout_s는 양수다 — {v}")
        return v


class InfraConfig(StrictModel):
    """전부 선택이다 — Kafka가 없는 사이트도 있을 수 있다."""
    redis: RedisConfig | None = None
    mongodb: MongoConfig | None = None
    kafka: KafkaConfig | None = None
    rest: RestConfig | None = None

    @model_validator(mode="after")
    def _at_least_one_system(self):
        if not (self.redis or self.mongodb or self.kafka or self.rest):
            raise ValueError("infra에 대상 시스템이 하나도 없다 — 무엇을 조사하라는 것인가")
        return self


class SiteRef(StrictModel):
    gbm: str        # 사업부 (mx)
    fct: str        # 법인/공장 (gumi)

    def __str__(self) -> str:
        return f"{self.gbm}/{self.fct}"


class SiteConfig(StrictModel):
    site: SiteRef
    infra: InfraConfig


class RegistryEntry(StrictModel):
    """registry.json의 한 줄 — 이 (gbm, fct) 조합이 실재하는가.

    디렉터리를 훑어서 유추하지 않는 이유: `gbm/mx.json`과 `fct/gumi/`가 있다고
    해서 mx가 구미에 있다는 뜻은 아니다. 조합은 **사람이 명시**한다.
    """
    gbm: str
    fct: str
    enabled: bool = True

    def __str__(self) -> str:
        return f"{self.gbm}/{self.fct}"


class Registry(StrictModel):
    sites: list[RegistryEntry] = []

    def active(self) -> list[RegistryEntry]:
        return [s for s in self.sites if s.enabled]
