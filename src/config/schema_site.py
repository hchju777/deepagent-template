"""사이트 계층 config 스키마 — 스펙 §4.5.

- 인증 필드는 선택: 없는 법인은 url만, 있는 법인은 username/password 추가.
- 비밀값은 SecretStr — 로그·config show에서 자동 마스킹된다.
- extra="forbid"가 전역 키의 사이트 계층 침입(§4.5-①)도 함께 거부한다.
"""
import re
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, model_validator

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
    query_schema allowlist를 우회하며, `..`는 httpx가 정규화해 다른 끝점이 된다.
    호출 시점이 아니라 **config 검증 시점**에 막는 이유: 등재 목록은 사람이 읽고
    승인하는 것이므로, 읽는 사람이 보는 값과 나가는 값이 같아야 한다.
    """
    if not path.startswith("/") or path.startswith("//"):
        return "경로는 '/'로 시작하는 상대 경로여야 한다(절대 URL·프로토콜 상대 금지)"
    if any(ch in path for ch in "?#%") or any(ch in path for ch in "\r\n\t"):
        return "경로에 '?'·'#'·'%'·제어문자를 쓸 수 없다 — 쿼리는 query_schema로 선언한다"
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
    query_schema: dict[str, BodyFieldType] = {}   # GET 항목의 쿼리 파라미터(키+타입)

    @model_validator(mode="after")
    def _shape_is_sound(self):
        # GET에 body를 실으면 프록시·서버마다 동작이 갈린다 — 쿼리 키로 표현한다.
        if self.method == "GET" and self.body_schema:
            raise ValueError("GET 항목에는 body_schema를 둘 수 없다 — query_schema를 쓰라")
        # POST에 query_schema를 적으면 조용히 무시된다 — 사람이 쓴 제약이 아무 효과
        # 없이 통과하는 것은 등재제가 배격하는 형태다.
        if self.method == "POST" and self.query_schema:
            raise ValueError("POST 항목에는 query_schema를 둘 수 없다 — body_schema를 쓰라")
        problem = entry_path_problem(self.path)
        if problem is not None:
            raise ValueError(problem)
        return self


_ENTRY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RestTarget(StrictModel):
    base_url: str
    auth: RestAuth | None = None
    entries: dict[str, RestEntry] = {}

    @model_validator(mode="after")
    def _entry_names_cannot_mimic_locators(self):
        """항목 이름이 경로처럼 보이면 두 이름공간이 섞인다.

        `target: "rest:<이름>"`에서 이름이 `/oee`면 resolve_probe의 슬래시 휴리스틱과
        boot의 이름공간 분기가 둘 다 이것을 토폴로지 locator로 읽는다. 그런데
        `check.probe`를 명시하면 휴리스틱을 우회해 실제로는 POST가 나간다 —
        리뷰어가 읽는 것(v1식 읽기 전용 GET)과 나가는 것이 달라진다.

        등재제의 안전 근거는 사람이 목록을 읽는 것이므로, 읽고 오해할 수 있는
        이름을 애초에 만들지 못하게 한다.
        """
        for name in self.entries:
            if not _ENTRY_NAME.match(name):
                raise ValueError(
                    f"등재 항목 이름 {name!r}은 영숫자로 시작하는 식별자여야 한다 "
                    f"— '/'·':'·공백은 토폴로지 locator와 혼동된다")
        return self


class RepoRef(StrictModel):
    name: str
    path: str


class CodeTarget(StrictModel):
    repos: list[RepoRef]


class Guards(StrictModel):
    timeout_s: float = 10
    max_rows: int = 1000
    max_concurrent: int = 4


class StubSeedsConfig(StrictModel):
    """스텁 어댑터가 돌려줄 응답. `adapters="stub"`일 때만 쓰인다.

    이게 없으면 config.example의 점검이 전부 "404: 스텁에 등록되지 않은 끝점"으로
    끝난다 — 예시가 배선만 보여주고 한 번도 성공하지 못하는 상태였다. README의
    "5분 빠른 시작"이 실제로 돌려면 대상 시스템 없이도 결과가 나와야 한다.
    """
    rest_responses: dict[str, Any] = {}      # "/oee" 또는 "POST /summary/prod"
    redis_data: dict[str, Any] = {}
    redis_ttls: dict[str, int] = {}
    mongo_collections: dict[str, list[dict]] = {}
    kafka_messages: dict[str, list[dict]] = {}
    kafka_offsets: dict[str, dict] = {}


class TargetConfig(StrictModel):
    adapters: Literal["stub", "real"] = "stub"  # 스텁 ↔ 실구현 전환 (전작 패턴)
    redis: RedisTarget | None = None
    mongo: MongoTarget | None = None
    kafka: KafkaTarget | None = None
    rest: RestTarget | None = None
    code: CodeTarget | None = None
    guards: Guards = Guards()
    stub_seeds: StubSeedsConfig | None = None


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


_CARDINALITY = re.compile(r"^(all|first:[1-9][0-9]*|sample:[1-9][0-9]*)$")


class _ResolverBase(StrictModel):
    cardinality: str = "all"

    @model_validator(mode="after")
    def _cardinality_is_known(self):
        if not _CARDINALITY.match(self.cardinality):
            raise ValueError(
                f"cardinality {self.cardinality!r}는 all·first:N·sample:N 중 하나여야 한다")
        return self


class RestResolver(_ResolverBase):
    """형제 조회 항목을 불러 값을 얻는다 — 대상 시스템 자신이 인정한 목록이라 가장 강하다."""
    from_: Literal["rest"] = Field(alias="from")
    entry: str
    field: str


class MongoResolver(_ResolverBase):
    from_: Literal["mongo"] = Field(alias="from")
    collection: str
    field: str
    filter: dict[str, Any] = {}


class RedisResolver(_ResolverBase):
    from_: Literal["redis"] = Field(alias="from")
    pattern: str


class ClockResolver(StrictModel):
    """주입된 시계로 값을 만든다 — datetime.now()를 직접 부르지 않는다(규율 2)."""
    from_: Literal["clock"] = Field(alias="from")
    expr: Literal["today", "yesterday", "now_iso"]


class UnfilteredResolver(StrictModel):
    """**의도한 전체 조회**를 명시한다(스펙 §2-N3).

    해석 실패로 우연히 전체 조회에 도달하는 경로와 처음부터 전체를 보려는 의도를
    코드가 구별할 수 있어야 한다 — 빈 필터는 endpoint에 따라 0/0/0(거짓 경보)이
    되기도 하고 전체 조회(거짓 안심, 조용해서 더 위험)가 되기도 한다.
    """
    from_: Literal["unfiltered"] = Field(alias="from")


ResolverSpec = Annotated[
    RestResolver | MongoResolver | RedisResolver | ClockResolver | UnfilteredResolver,
    Field(discriminator="from_")]


class CheckConfig(StrictModel):
    judge: Literal["rule", "llm", "rule+llm"]
    schedule: Schedule
    target: str | None = None          # 토폴로지 locator 또는 등재 항목 이름(rest:<이름>) — 해석 검증은 boot에서
    probe: str | None = None           # 프로브 레지스트리 이름. None이면 target의 kind로 기본 프로브 선택
    params: dict[str, Any] = {}
    sample: int | None = None
    on_budget_exhausted: Literal["skip", "escalate"] = "skip"
    resolve: dict[str, ResolverSpec] = {}   # 값이 아니라 값이 어디서 오는지를 선언한다

    @model_validator(mode="after")
    def _static_and_resolved_keys_are_disjoint(self):
        static = self.params.get("body") if isinstance(self.params, dict) else None
        overlap = sorted(set(static or {}) & set(self.resolve))
        if overlap:
            raise ValueError(
                f"params.body와 resolve에 같은 키가 있다: {overlap} — 어느 쪽이 이기는지 "
                f"사람이 헷갈리면 안 되므로 한 곳에만 둔다")
        return self


class SitePatrol(StrictModel):
    checks: dict[str, CheckConfig] = {}


class KnowledgeConfig(StrictModel):
    root: str = "knowledge"


class SiteConfig(StrictModel):
    target: TargetConfig
    patrol: SitePatrol = SitePatrol()
    knowledge: KnowledgeConfig = KnowledgeConfig()
