"""app config의 숫자들이 **서로 어긋나지 않는가.**

개별 값은 각자 타당해 보여도 조합이 틀릴 수 있다. 증거 예산이 그랬다 — 개별
상한과 총 예산이 각각은 멀쩡한데 곱해 보면 총 예산이 한 번도 안 쓰였다.
"""
import pytest
from pydantic import ValidationError

from src.config.schema_app import AppConfig, InvestigationConfig

# ── 증거 예산이 서로 어긋나지 않는가 ──────────────────────────────

def test_개별_상한이_총_예산을_놀리지_않는다():
    """**사내 측정에서 잡힌 것이다.**

    개별 상한이 1200, 총 예산이 12000이던 시절에는 증거가 9건일 때도
    9×1200 < 12000이라 **총 예산이 한 번도 병목이 아니었다.** 개별 상한 혼자
    자르고 있었고, 5건 중 4건이 잘렸다.

    불변식: **최근 5건은 온전히 들어간다.** 그 이상부터 총 예산이 정한다.
    """
    cfg = InvestigationConfig()
    assert 5 * cfg.evidence_chars >= cfg.evidence_total_chars, (
        f"증거 5건({5 * cfg.evidence_chars}자)이 총 예산"
        f"({cfg.evidence_total_chars}자)에 못 미친다 — 총 예산이 논다")
    assert cfg.evidence_chars <= cfg.evidence_total_chars, (
        "증거 하나가 전체 예산을 먹는다")


def test_라운드_상한_기본값은_6이다():
    """사내 모델은 라운드당 읽기 둘이라 4라운드면 여덟 번이고, 네 실행 모두 상한에서 끝났다.
    기본값을 바꾸면 이 테스트도 같이 바꾼다 — 조용히 바뀌지 않게."""
    from src.config.schema_app import InvestigationConfig

    assert InvestigationConfig().max_rounds == 6


def test_판정_프롬프트_경로도_config에_있다():
    """12a — 운영이 고치는 파일이라 코드에 박지 않는다(frame·integrate와 같은 자리)."""
    cfg = InvestigationConfig()
    assert cfg.conclude_prompt == "prompts/investigate-conclude.md"


# ── R2-1 — 역할별 LLM ──────────────────────────────────────────────

GATEWAY = {"model": "fast", "model_id": "1", "base_url": "https://g.example/v1",
           "pass_key": "pass-123", "client_key": "client-456", "headers": {"X-A": "1"}}


def test_역할별_LLM은_기본_위에_부분_덮어쓰기다():
    """사내 실측: 생각하는 모델을 모든 턴에 쓰니 한 턴이 180초 벽에 걸렸다. 액션 턴은 빠른 모델, 판정은 생각하는
    모델 — 역할마다 **다른 것만** 적고 나머지(인증·TLS·주소)는 기본 `llm`을 물려받는다."""
    app = AppConfig(llm=GATEWAY, llm_roles={"conclude": {"model": "think", "model_id": "9", "timeout_s": 600,
                                                       "max_tokens": 4000, "headers": {"X-B": "2"}}})
    conclude = app.llm_for("conclude")
    assert conclude.model == "think" and conclude.model_id == "9" and conclude.timeout_s == 600
    assert conclude.max_tokens == 4000 and conclude.pass_key.get_secret_value() == "pass-123"
    assert conclude.headers == {"X-A": "1", "X-B": "2"}                 # 중첩도 부분 덮어쓰기
    assert app.llm_for("lead") is app.llm and app.llm_for("report") is app.llm   # 안 적은 역할은 기본 그대로
    assert AppConfig().llm_for("conclude") is None


def test_역할_덮어쓰기의_모르는_키와_모르는_역할은_막는다():
    with pytest.raises(ValidationError) as caught:
        AppConfig(llm=GATEWAY, llm_roles={"conclude": {"foo": 1}})
    assert "llm_roles.conclude" in str(caught.value) and "foo" in str(caught.value)
    with pytest.raises(ValidationError):
        AppConfig(llm=GATEWAY, llm_roles={"nope": {"model": "x"}})
    with pytest.raises(ValidationError) as none:
        AppConfig(llm_roles={"conclude": {"model": "x"}})
    assert "llm" in str(none.value)
    with pytest.raises(ValueError):
        AppConfig(llm=GATEWAY).llm_for("nope")


def test_프롬프트_상한은_integrate_8000_conclude_10000이고_2000_아래는_막는다():
    from pydantic import ValidationError

    cfg = InvestigationConfig()
    assert (cfg.integrate_prompt_chars, cfg.conclude_prompt_chars) == (8000, 10000)
    with pytest.raises(ValidationError):
        InvestigationConfig(integrate_prompt_chars=1000)


def test_판정_역할은_따로_안_적으면_response_format이_none이다():
    """사내 측정 #3: 생각 모델(판정 턴)에 json_schema를 걸면 4/4 JSON이 깨졌고, 같은 프롬프트를 스키마 없이 보내면 정상이었다.
    판정 역할의 **기본값**이 none이다 — 적으면 그 값이 이긴다. 다른 역할은 기본 llm 그대로(같은 객체 — 어댑터 한 벌)."""
    app = AppConfig(llm=GATEWAY)
    assert app.llm.response_format == "json_schema"
    assert app.llm_for("conclude").response_format == "none" and app.llm_for("conclude") is not app.llm
    assert app.llm_for("lead") is app.llm and app.llm_for("report") is app.llm
    asked = AppConfig(llm=GATEWAY, llm_roles={"conclude": {"response_format": "json_object"}})
    assert asked.llm_for("conclude").response_format == "json_object"                 # 적으면 이긴다
    plain = AppConfig(llm={**GATEWAY, "response_format": "none"})
    assert plain.llm_for("conclude") is plain.llm                                      # 바뀌는 게 없으면 같은 객체
