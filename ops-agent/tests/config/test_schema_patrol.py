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


# ── rule과 params ────────────────────────────────────────────────────

RULE = {"rule": "items_all_zero",
        "params": {"items": {"probe": "badge", "path": "response"},
                   "identity": ["group", "title"],
                   "counts": ["alarm", "caution", "normal"]}}


def full(**overrides) -> dict:
    body = {"concern": "operation", "probes": PROBES, **RULE}
    body.update(overrides)
    return body


def test_모르는_rule_이름은_거부된다():
    with pytest.raises(ValidationError):
        CheckConfig.model_validate(full(rule="all_zero"))


def test_rule이_선언되지_않은_프로브를_가리키면_거부된다():
    """`"items": {"probe": "badges"}`처럼 이름이 틀리면 매 순찰마다 실패하는데,
    그 실패는 `unreachable`로 흡수되어 "대상이 안 붙는다"처럼 보인다."""
    with pytest.raises(ValidationError) as caught:
        CheckConfig.model_validate(full(params={**RULE["params"],
                                                "items": {"probe": "badges"}}))
    assert "선언되지 않은 프로브" in str(caught.value)


def test_가드도_선언되지_않은_프로브를_가리키면_거부된다():
    with pytest.raises(ValidationError) as caught:
        CheckConfig.model_validate(full(params={
            **RULE["params"],
            "only_when": {"probe": "prod", "path": "response.status",
                          "equals": "In Production"}}))
    assert "선언되지 않은 프로브" in str(caught.value)


def test_identity와_counts는_비어_있을_수_없다():
    """식별자가 없으면 finding이 어느 항목인지 못 말하고, counts가 없으면
    "전부 0"이 공집합에 대해 항상 참이 된다."""
    for field in ("identity", "counts"):
        with pytest.raises(ValidationError):
            CheckConfig.model_validate(full(params={**RULE["params"], field: []}))


def test_가드의_기대값은_문자열만_받는다():
    """숫자·bool을 열면 `False == 0` 같은 비교가 조용히 통과하는 길이 생긴다."""
    with pytest.raises(ValidationError):
        CheckConfig.model_validate(full(params={
            **RULE["params"],
            "only_when": {"probe": "badge", "path": "x", "equals": {"a": 1}}}))


def test_rule_params의_모르는_키도_거부된다():
    with pytest.raises(ValidationError):
        CheckConfig.model_validate(full(params={**RULE["params"], "min_count": 3}))
