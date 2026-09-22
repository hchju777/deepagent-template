"""app config의 숫자들이 **서로 어긋나지 않는가.**

개별 값은 각자 타당해 보여도 조합이 틀릴 수 있다. 증거 예산이 그랬다 — 개별
상한과 총 예산이 각각은 멀쩡한데 곱해 보면 총 예산이 한 번도 안 쓰였다.
"""
from src.config.schema_app import InvestigationConfig

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

