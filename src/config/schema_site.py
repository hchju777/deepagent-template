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


class KafkaTarget(StrictModel):
    bootstrap: str


class RestTarget(StrictModel):
    base_url: str


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
        if self.interval is not None and not _INTERVAL.match(self.interval):
            raise ValueError(f"interval 형식 오류: {self.interval!r} (예: '30s', '5m', '1h')")
        if self.cron is not None and len(self.cron.split()) != 5:
            raise ValueError(f"cron은 5필드여야 한다: {self.cron!r}")
        return self


class CheckConfig(StrictModel):
    judge: Literal["rule", "llm", "rule+llm"]
    schedule: Schedule
    target: str | None = None          # 토폴로지 locator — 해석 검증은 boot에서
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
