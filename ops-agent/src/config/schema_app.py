"""사이트를 가로지르는 전역 설정.

사이트 config와 나누는 기준: **사이트마다 달라지는가.** 접속 정보는 사이트마다
다르므로 site config에, 시간대와 출력 경로는 프로세스 하나에 하나뿐이므로 여기에.
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator

from pydantic import Field

from src.config.schema_llm import LlmConfig
from src.config.schema_mail import MailConfig
from src.domain.base import StrictModel


class InvestigationConfig(StrictModel):
    """조사 엔진의 울타리. **전부 코드가 쥐는 값이고 LLM은 못 바꾼다**(규율 6).

    사이트를 가로질러 하나인 이유: 조사의 깊이는 법인이 아니라 우리가 감당할
    LLM 호출량과 시간이 정한다.
    """

    # 라운드 상한. 닿으면 "계속하자"는 결정을 무시하고 끝낸다 — 억지 결론 대신
    # "미확정"을 허용하는 것이 12a의 설계이고, 그 상한이 여기다.
    max_rounds: int = Field(default=4, ge=1)
    # 한 라운드에 동시에 돌릴 태스크 수. 대상 시스템에 가는 부하의 상한이기도 하다.
    parallel_width: int = Field(default=3, ge=1)
    # 케이스 하나가 가질 수 있는 태스크 총수. 이게 없으면 라운드마다 태스크를
    # 쌓기만 하는 계획이 상한 없이 자란다.
    max_tasks: int = Field(default=24, ge=1)


class AppConfig(StrictModel):
    # 순찰 주기·보고서 시각 표시의 기준. UTC로 저장하고 사람에게 보일 때만 이걸 쓴다.
    timezone: str = "Asia/Seoul"
    output_dir: str = "output"
    # LLM은 사이트를 가로질러 하나다 — 법인마다 다른 모델을 쓸 이유가 없고,
    # 사이트마다 두면 같은 게이트웨이를 향한 커넥션 풀이 사이트 수만큼 생긴다.
    llm: LlmConfig | None = None
    # 메일도 사이트를 가로질러 하나다 — 보고서 수신자는 법인이 아니라 조직이 정한다.
    mail: MailConfig = MailConfig()
    investigation: InvestigationConfig = InvestigationConfig()

    @field_validator("timezone")
    @classmethod
    def _resolvable(cls, v: str) -> str:
        # Windows에는 tz 데이터베이스가 없어서 tzdata 패키지가 없으면 여기서 걸린다.
        # 런타임에 보고서를 쓰다가 죽는 것보다 기동에서 막는 편이 낫다.
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"해석할 수 없는 timezone — {v}: {exc}") from exc
        return v
