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


def test_예시의_건수가_읽을_수_있는_크기다():
    """**읽을 수 없는 5건보다 읽을 수 있는 3건이 낫다.**

    예산은 증거 하나당 고정이라 건수를 늘리면 건당 글자가 그만큼 줄어든다.
    사내 측정에서 `limit=5`의 문서 다섯 건이 한 건도 온전히 안 들어갔다 —
    10b의 258자 제조 문서와 같은 일이다.
    """
    from src.application.briefing import _NAMED_READ

    budget = InvestigationConfig().evidence_chars
    for action, params in _NAMED_READ:
        limit = params.get("limit")
        if limit is None:
            continue
        assert budget // limit >= 400, (
            f"{action}의 limit={limit}이면 한 건에 {budget // limit}자다 — "
            f"제조 문서 한 건도 안 들어간다")
