import pytest
from pydantic import ValidationError
from src.config.schema_site import Schedule, SiteConfig


def _site(**patrol_checks):
    return {
        "target": {"redis": {"url": "redis://g:6379"},
                   "mongo": {"url": "mongodb://g:27017",
                             "username": "reader", "password": "pw"}},
        "patrol": {"checks": patrol_checks},
    }


def test_인증은_선택이고_password는_마스킹된다():
    cfg = SiteConfig.model_validate(_site())
    assert cfg.target.redis.password is None                    # 없는 법인
    dumped = cfg.model_dump(mode="json")
    assert dumped["target"]["mongo"]["password"] == "**********"  # 있는 법인, 마스킹


def test_schedule은_interval_xor_cron():
    Schedule.model_validate({"interval": "5m"})
    Schedule.model_validate({"cron": "0 8,20 * * *"})
    with pytest.raises(ValidationError):
        Schedule.model_validate({"interval": "5m", "cron": "0 8 * * *"})
    with pytest.raises(ValidationError):
        Schedule.model_validate({})
    with pytest.raises(ValidationError):
        Schedule.model_validate({"interval": "5 minutes"})      # 형식 위반


def test_전역_키가_사이트_계층에_오면_거부():
    data = _site()
    data["engine"] = {"max_rounds": 99}
    with pytest.raises(ValidationError, match="engine"):
        SiteConfig.model_validate(data)


def test_점검_정의():
    cfg = SiteConfig.model_validate(_site(**{
        "api.oee_range": {"judge": "rule", "schedule": {"interval": "10m"},
                          "target": "rest:/api/v1/lines/{line}/oee",
                          "params": {"min": 0, "max": 100}},
    }))
    check = cfg.patrol.checks["api.oee_range"]
    assert check.on_budget_exhausted == "skip"                  # 기본값
    assert check.params["max"] == 100


def test_adapters_모드는_stub이_기본이고_오타는_거부():
    cfg = SiteConfig.model_validate(_site())
    assert cfg.target.adapters == "stub"
    with pytest.raises(ValidationError):
        SiteConfig.model_validate({**_site(), "target": {**_site()["target"], "adapters": "rael"}})
