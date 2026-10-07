"""사이트를 가로지르는 전역 설정.

사이트 config와 나누는 기준: **사이트마다 달라지는가.** 접속 정보는 사이트마다
다르므로 site config에, 시간대와 출력 경로는 프로세스 하나에 하나뿐이므로 여기에.
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from typing import Any

from pydantic import ValidationError, field_validator, model_validator

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
    # 6인 이유: 사내 모델은 라운드당 읽기 둘이라 4라운드면 여덟 번이고, 네 실행 모두
    # `conclude` 없이 상한에서 끝났다. haiku급도 결론까지 4라운드를 다 썼다. 12a의
    # conclude 게이트가 생기면 근거가 서는 순간 더 일찍 끝난다 — 상한은 천장이지 목표가 아니다.
    max_rounds: int = Field(default=6, ge=1)
    # 한 라운드에 동시에 돌릴 태스크 수. 대상 시스템에 가는 부하의 상한이기도 하다.
    parallel_width: int = Field(default=3, ge=1)
    # 케이스 하나가 가질 수 있는 태스크 총수. 이게 없으면 라운드마다 태스크를
    # 쌓기만 하는 계획이 상한 없이 자란다.
    max_tasks: int = Field(default=24, ge=1)
    # 증거 하나를 리드에게 보여 줄 때 쓸 글자 예산. **줄이면 조사가 눈이 먼다** —
    # 문서 한 건이 안 들어가면 리드는 필드 이름만 보고 값은 못 봐서, 같은 질의를
    # 말만 바꿔 다시 낸다(실제로 4라운드 중 셋이 그랬다). 늘리면 프롬프트가 커진다.
    #
    # **총 예산의 1/5로 잡는다** = 최근 5건은 온전히 보인다. 1200이던 시절에는
    # 증거가 9건일 때도 9×1200 < 12000이라 **총 예산이 한 번도 병목이 아니었다** —
    # 개별 상한 혼자 자르고 있었고, 사내 측정에서 5건 중 4건이 잘렸다.
    evidence_chars: int = Field(default=2400, ge=200)
    # 증거 블록 전체 예산. 넘으면 **오래된 것부터 한 줄 요약만** 남긴다 — id와
    # 출처는 끝까지 남으므로 인용은 계속 유효하다.
    evidence_total_chars: int = Field(default=12000, ge=1000)
    # 리드 프롬프트. config 안의 상대 경로다 — 운영이 직접 고치는 파일이라
    # 코드에 박아 두면 고치려고 배포를 해야 한다.
    frame_prompt: str = Field(default="prompts/investigate-frame.md", min_length=1)
    integrate_prompt: str = Field(default="prompts/investigate-integrate.md",
                                  min_length=1)
    conclude_prompt: str = Field(default="prompts/investigate-conclude.md", min_length=1)


# LLM을 쓰는 자리. 액션 턴(lead)은 빠른 모델, 판정(conclude)은 생각하는 모델, 보고서 서술(report) — 사내 실측
# (12a 리뷰 4번)에서 생각하는 모델을 모든 턴에 쓰니 한 턴이 게이트웨이 180초 벽에 걸렸다. triage의 입구 매핑(R3)이
# 생기면 그 역할이 여기 는다 — 읽는 곳이 없는 칸은 미리 두지 않는다(`test_dead_settings`).
LLM_ROLES = ("lead", "conclude", "report")


class LlmRoles(StrictModel):
    """역할마다 기본 `llm` 위에 **덮어쓸 것만** 적는다(부분, 중첩도 부분). 안 적은 역할은 기본 그대로다."""
    lead: dict[str, Any] = {}
    conclude: dict[str, Any] = {}
    report: dict[str, Any] = {}


def _merged(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merged(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


class AppConfig(StrictModel):
    # 순찰 주기·보고서 시각 표시의 기준. UTC로 저장하고 사람에게 보일 때만 이걸 쓴다.
    timezone: str = "Asia/Seoul"
    output_dir: str = "output"
    # 순찰이 연 케이스가 사는 곳. 파일인 이유와 한계는
    # `infrastructure/case_store_file.py` 맨 위에 있다.
    case_store: str = "output/cases.json"
    # LLM은 사이트를 가로질러 하나다 — 법인마다 다른 모델을 쓸 이유가 없고,
    # 사이트마다 두면 같은 게이트웨이를 향한 커넥션 풀이 사이트 수만큼 생긴다.
    llm: LlmConfig | None = None
    llm_roles: LlmRoles = LlmRoles()
    # 메일도 사이트를 가로질러 하나다 — 보고서 수신자는 법인이 아니라 조직이 정한다.
    mail: MailConfig = MailConfig()
    investigation: InvestigationConfig = InvestigationConfig()

    def llm_for(self, role: str) -> LlmConfig | None:
        """그 역할의 실효 LLM 설정 — 기본 `llm`에 역할의 덮어쓰기를 얹어 **다시 검증한** 것. 덮어쓰기가 없으면 기본
        객체 그대로(`is`로 같다 — 호출부가 어댑터를 두 벌 만들지 않는 근거)."""
        if role not in LLM_ROLES:
            raise ValueError(f"모르는 LLM 역할 — {role}. 있는 것: {', '.join(LLM_ROLES)}")
        if self.llm is None:
            return None
        over = getattr(self.llm_roles, role)
        if not over:
            return self.llm
        return LlmConfig.model_validate(_merged(self.llm.model_dump(mode="python"), over))

    @model_validator(mode="after")
    def _roles_resolve(self):
        # 역할 덮어쓰기의 오류는 기동에서 잡는다 — 판정 턴에 가서야 "모르는 키"로 죽으면 조사 하나가 날아간다.
        used = [role for role in LLM_ROLES if getattr(self.llm_roles, role)]
        if used and self.llm is None:
            raise ValueError(f"llm_roles({', '.join(used)})는 llm이 있을 때만 — 역할은 기본 llm 위에 덮어쓴다")
        for role in used:
            try:
                self.llm_for(role)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(x) for x in first.get("loc", ())) or "(전체)"
                raise ValueError(f"llm_roles.{role}: {where} — {first.get('msg')}") from exc
        return self

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
