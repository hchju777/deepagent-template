"""사이트 계층 config 스키마 — 스펙 §4.5.

- 인증 필드는 선택: 없는 법인은 url만, 있는 법인은 username/password 추가.
- 비밀값은 SecretStr — 로그·config show에서 자동 마스킹된다.
- extra="forbid"가 전역 키의 사이트 계층 침입(§4.5-①)도 함께 거부한다.
"""
import re
from typing import Any, Literal

from pydantic import SecretStr, model_validator

from src.config.schema_app import StrictModel

_INTERVAL = re.compile(r"^\d+[smh]$")


class RedisTarget(StrictModel):
    url: str
    password: SecretStr | None = None


class MongoTarget(StrictModel):
    url: str
    username: str | None = None
    password: SecretStr | None = None
    db: str = "twin"  # RealMongo 필수 인자


class KafkaTarget(StrictModel):
    bootstrap: str


# body 필드 타입 어휘. 좁게 닫는다 — 넓히면 검증이 느슨해지고, 느슨한 검증은
# 없는 검증과 같다. 실제로 필요해지면 그때 하나씩 연다.
BodyFieldType = Literal["str", "int", "float", "bool", "list[str]", "list[int]"]


def entry_path_problem(path: str) -> str | None:
    """등재 항목 경로가 base_url을 벗어나지 못하게 한다.

    infrastructure의 query_rules가 아니라 여기 있는 이유: 경로 검증은 config
    로딩 시점의 일회성 판정이라 어댑터와 공유할 필요가 없고, config가
    infrastructure를 import하면 config → infrastructure → knowledge → config
    순환이 된다(의존은 항상 안쪽을 향한다).

    path에 검증이 없으면 endpoint_allowed가 세운 방어가 통째로 비껴간다 —
    절대 URL은 base_url을 벗어나고(실증: http://evil/wipe), 임베디드 쿼리는
    query_keys allowlist를 우회하며, `..`는 httpx가 정규화해 다른 끝점이 된다.
    호출 시점이 아니라 **config 검증 시점**에 막는 이유: 등재 목록은 사람이 읽고
    승인하는 것이므로, 읽는 사람이 보는 값과 나가는 값이 같아야 한다.
    """
    if not path.startswith("/") or path.startswith("//"):
        return "경로는 '/'로 시작하는 상대 경로여야 한다(절대 URL·프로토콜 상대 금지)"
    if any(ch in path for ch in "?#%") or any(ch in path for ch in "\r\n\t"):
        return "경로에 '?'·'#'·'%'·제어문자를 쓸 수 없다 — 쿼리는 query_keys로 선언한다"
    if any(seg in (".", "..") or ";" in seg for seg in path.split("/")):
        return "경로에 '.'·'..' 세그먼트나 매트릭스 파라미터(';')를 쓸 수 없다"
    return None


class RestAuth(StrictModel):
    """대상 API가 요구하는 인증 헤더. 값은 반드시 ${ENV} 참조로 준다."""
    header: str
    value: SecretStr


class RestEntry(StrictModel):
    """호출을 허가받은 끝점 하나.

    **메서드가 여기 있는 것이 이 설계의 핵심이다** — 호출자는 항목 이름만 대고,
    어떤 HTTP 메서드로 나갈지는 어댑터가 이 값을 보고 정한다. 그래서 "임의의
    POST를 수행하라"는 호출이 코드에 표현될 수 없다.

    쓰기 메서드를 등재 어휘에서 빼는 이유: 메서드 결정권을 config로 옮긴 이상,
    여기서 막지 않으면 config 한 줄로 대상 시스템에 쓰기를 할 수 있다.
    """
    method: Literal["GET", "POST"] = "GET"
    path: str
    body_schema: dict[str, BodyFieldType] = {}
    query_keys: list[str] = []

    @model_validator(mode="after")
    def _shape_is_sound(self):
        # GET에 body를 실으면 프록시·서버마다 동작이 갈린다 — 쿼리 키로 표현한다.
        if self.method == "GET" and self.body_schema:
            raise ValueError("GET 항목에는 body_schema를 둘 수 없다 — query_keys를 쓰라")
        # POST에 query_keys를 적으면 조용히 무시된다 — 사람이 쓴 제약이 아무 효과
        # 없이 통과하는 것은 등재제가 배격하는 형태다.
        if self.method == "POST" and self.query_keys:
            raise ValueError("POST 항목에는 query_keys를 둘 수 없다 — body_schema를 쓰라")
        problem = entry_path_problem(self.path)
        if problem is not None:
            raise ValueError(problem)
        return self


class RestTarget(StrictModel):
    base_url: str
    auth: RestAuth | None = None
    entries: dict[str, RestEntry] = {}


class RepoRef(StrictModel):
    name: str
    path: str


class CodeTarget(StrictModel):
    repos: list[RepoRef]


class Guards(StrictModel):
    timeout_s: float = 10
    max_rows: int = 1000
    max_concurrent: int = 4


class TargetConfig(StrictModel):
    adapters: Literal["stub", "real"] = "stub"  # 스텁 ↔ 실구현 전환 (전작 패턴)
    redis: RedisTarget | None = None
    mongo: MongoTarget | None = None
    kafka: KafkaTarget | None = None
    rest: RestTarget | None = None
    code: CodeTarget | None = None
    guards: Guards = Guards()


class Schedule(StrictModel):
    interval: str | None = None
    cron: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.interval is None) == (self.cron is None):
            raise ValueError("schedule은 interval과 cron 중 정확히 하나만 선언한다")
        if self.interval is not None:
            if not _INTERVAL.match(self.interval):
                raise ValueError(f"interval 형식 오류: {self.interval!r} (예: '30s', '5m', '1h')")
            if int(self.interval[:-1]) == 0:
                raise ValueError("interval은 0보다 커야 한다")
        if self.cron is not None and len(self.cron.split()) != 5:
            raise ValueError(f"cron은 5필드여야 한다: {self.cron!r}")
        return self


class CheckConfig(StrictModel):
    judge: Literal["rule", "llm", "rule+llm"]
    schedule: Schedule
    target: str | None = None          # 토폴로지 locator — 해석 검증은 boot에서
    probe: str | None = None           # 프로브 레지스트리 이름. None이면 target의 kind로 기본 프로브 선택
    params: dict[str, Any] = {}
    sample: int | None = None
    on_budget_exhausted: Literal["skip", "escalate"] = "skip"


class SitePatrol(StrictModel):
    checks: dict[str, CheckConfig] = {}


class KnowledgeConfig(StrictModel):
    root: str = "knowledge"


class SiteConfig(StrictModel):
    target: TargetConfig
    patrol: SitePatrol = SitePatrol()
    knowledge: KnowledgeConfig = KnowledgeConfig()
