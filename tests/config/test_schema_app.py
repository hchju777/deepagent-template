import pytest
from pydantic import ValidationError
from src.config.schema_app import AppConfig

MINIMAL = {"llm": {"profiles": {"judge": "m-s", "subagent": "m-m", "lead": "m-l"}}}


def test_최소_config로_기본값이_채워진다():
    cfg = AppConfig.model_validate(MINIMAL)
    assert cfg.engine.max_rounds == 6
    assert cfg.investigations.max_concurrent == 2
    assert cfg.patrol.llm_budget.max_calls_per_hour == 30
    assert cfg.store.retention.ledger_d == 30
    assert cfg.timezone == "Asia/Seoul"


def test_unknown_key는_거부된다():
    with pytest.raises(ValidationError, match="scheduel"):
        AppConfig.model_validate({**MINIMAL, "scheduel": {}})   # 오타 키


def test_llm_프로파일은_필수다():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({})
