"""점검 선언의 스키마 — 오타가 런타임까지 못 가게 막는다."""
import pytest
from pydantic import ValidationError

from src.config.schema_patrol import CheckConfig, ProbeSpec

PROBES = {"badge": {"action": "rest.query",
                    "params": {"entry": "summary_badge", "params": {}}}}


def test_concern은_필수다():
    """기본값이 있으면 **우리 첫 rule부터 오분류다** — 0/0/0은 operation인데
    안 적으면 system으로 인프라팀에 간다. 원본이 기본값을 둔 것은 픽스처 90곳을
    고치기 싫어서였고, 우리는 그 비용이 0이다."""
    with pytest.raises(ValidationError) as caught:
        CheckConfig.model_validate({"probes": PROBES})
    assert "concern" in str(caught.value)


def test_모르는_concern은_거부된다():
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({"concern": "infra", "probes": PROBES})


def test_프로브가_하나도_없으면_거부된다():
    """읽는 것이 없는 점검은 매 순찰마다 아무 일도 안 하면서 통과한다."""
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({"concern": "operation", "probes": {}})


def test_미등재_action은_로드_시점에_거부된다():
    """기동이 아니라 **로드 시점**에 막는다. 오타난 action은 런타임에 매 순찰마다
    실패하는데, 그 실패는 "대상이 안 붙는다"처럼 보여 원인을 가린다."""
    with pytest.raises(ValidationError) as caught:
        ProbeSpec.model_validate({"action": "mongo.aggregate", "params": {}})
    assert "미등재 action" in str(caught.value)


def test_action의_인자도_로드_시점에_본다():
    with pytest.raises(ValidationError) as caught:
        ProbeSpec.model_validate({"action": "redis.get", "params": {"pattern": "x"}})
    assert "모르는 인자" in str(caught.value) or "필요한 인자가 없다" in str(caught.value)


def test_모르는_키는_거부된다():
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({"concern": "operation", "probes": PROBES,
                                    "rule": "items_all_zero"})
