"""사이트를 가로지르는 전역 설정.

사이트 config와 나누는 기준: **사이트마다 달라지는가.** 접속 정보는 사이트마다
다르므로 site config에, 시간대와 출력 경로는 프로세스 하나에 하나뿐이므로 여기에.
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator

from src.config.schema_llm import LlmConfig
from src.domain.base import StrictModel


class AppConfig(StrictModel):
    # 순찰 주기·보고서 시각 표시의 기준. UTC로 저장하고 사람에게 보일 때만 이걸 쓴다.
    timezone: str = "Asia/Seoul"
    output_dir: str = "output"
    # LLM은 사이트를 가로질러 하나다 — 법인마다 다른 모델을 쓸 이유가 없고,
    # 사이트마다 두면 같은 게이트웨이를 향한 커넥션 풀이 사이트 수만큼 생긴다.
    llm: LlmConfig | None = None

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
