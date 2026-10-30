"""요청 본문 — **명령이 나가기 전에** 거른다.

argv는 셸을 안 거치므로 주입은 없지만, 이름 자리에 `--today` 같은 옵션을 실으면 CLI 파서가 그것을 옵션으로
읽는다. 그래서 이름은 영숫자로 시작하는 좁은 모양만 받고, 자유 글(제목·질문)은 `--subject=값`·`--` 뒤로만
넘긴다(`routes_*`). 본문의 모르는 키는 422다(StrictModel) — 오타가 기본값으로 조용히 도는 것을 막는다.
"""
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator

from src.config.schema_mail import MailConfig
from src.domain.base import StrictModel

# Ingress가 60초에 끊는다 — 그 안에 응답이 나가야 한다.
WAIT_MAX_S = 55.0

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Role = Literal["report", "lead", "conclude"]


class Wait(StrictModel):
    wait_s: float = Field(default=WAIT_MAX_S, ge=0, le=WAIT_MAX_S,
                          description="이만큼 기다렸다 끝났으면 결과(200), 아니면 작업 id(202)")


class ScheduleListRequest(Wait):
    scenario: Name | None = None


class ReportAggregateRequest(Wait):
    scenario: Name | None = None
    today: date | None = Field(default=None, description="이 날 돌았다고 치고(YYYY-MM-DD)")


class ReportRunRequest(ReportAggregateRequest):
    dry_run: bool = Field(default=True, description="기본 true — 나갈 메일만 보여 준다(파일은 쓴다)")
    no_mail: bool = False


class MailSendRequest(Wait):
    subject: str = Field(default="연결 테스트", min_length=1, max_length=200)
    dry_run: bool = Field(default=True, description="기본 true — 나갈 요청만 보여 준다")
    to: list[str] = Field(default_factory=list, max_length=50,
                          description="주면 config 수신자는 무시하고 이 주소들에만")

    @field_validator("to")
    @classmethod
    def _same_rule_as_config(cls, v: list[str]) -> list[str]:
        # config와 같은 규칙(`MailConfig`)이다 — 여기서 따로 정하면 API는 통과시키고 CLI는 거르는 주소가 생긴다.
        try:
            MailConfig(recipients=v)
        except ValidationError as exc:
            raise ValueError(exc.errors()[0]["msg"]) from None
        return v


class DoctorRequest(Wait):
    gbm: Name | None = None
    fct: Name | None = None


class LlmCheckRequest(Wait):
    role: Role | None = Field(default=None, description="그 역할의 실효 설정으로(llm_roles). 없으면 기본 llm")


class LlmAskRequest(LlmCheckRequest):
    prompt: str = Field(min_length=1, max_length=2000)


class LlmRawRequest(StrictModel):
    prompt: str = Field(default="ping", min_length=1, max_length=2000)
    role: Role | None = None


class SitesRequest(StrictModel):
    gbm: Name | None = None
    concurrency: int = Field(default=4, ge=1, le=16, description="동시에 볼 사이트 수")


class EgressRequest(SitesRequest):
    fct: Name | None = None


class TraceRequest(StrictModel):
    # 닫힌 목록이다 — 임의의 host:port를 받으면 이 API가 포트 스캐너가 된다.
    target: Literal["mail", "llm", "redis", "mongo"] = "llm"
    gbm: Name | None = Field(default=None, description="redis·mongo일 때 사이트")
    fct: Name | None = None
    max_hops: int = Field(default=15, ge=1, le=30)
    hop_timeout_s: float = Field(default=1.0, ge=0.2, le=3.0)
