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
    with pytest.raises(ValidationError):
        Schedule.model_validate({"interval": "0m"})              # 0은 간격이 아니다


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


def test_등재_항목은_메서드와_닫힌_body_스키마를_요구한다():
    from src.config.schema_site import RestTarget
    target = RestTarget.model_validate({
        "base_url": "http://x",
        "entries": {"summary_prod": {"method": "POST", "path": "/summary/prod",
                                     "body_schema": {"part_code": "list[str]",
                                                     "line_code": "str"}}}})
    entry = target.entries["summary_prod"]
    assert entry.method == "POST" and entry.query_keys == []


def test_쓰기_메서드는_등재할_수_없다():
    # 메서드를 등재 항목이 정하므로, 여기서 막지 않으면 config 한 줄로
    # 대상 시스템에 쓰기를 할 수 있게 된다.
    from src.config.schema_site import RestTarget
    for method in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(ValidationError):
            RestTarget.model_validate({"base_url": "http://x",
                                       "entries": {"e": {"method": method, "path": "/x"}}})


def test_body_타입_어휘_밖은_거부된다():
    from src.config.schema_site import RestTarget
    with pytest.raises(ValidationError):
        RestTarget.model_validate({
            "base_url": "http://x",
            "entries": {"e": {"method": "POST", "path": "/x",
                              "body_schema": {"f": "dict"}}}})


def test_GET_항목은_body를_가질_수_없다():
    # GET에 body를 실으면 프록시·서버마다 동작이 갈린다. 쿼리 키로 표현해야 한다.
    from src.config.schema_site import RestTarget
    with pytest.raises(ValidationError):
        RestTarget.model_validate({
            "base_url": "http://x",
            "entries": {"e": {"method": "GET", "path": "/x",
                              "body_schema": {"f": "str"}}}})


def test_인증_토큰은_SecretStr로_마스킹된다():
    from src.config.schema_site import RestTarget
    target = RestTarget.model_validate({
        "base_url": "http://x",
        "auth": {"header": "x-dep-ticket", "value": "비밀토큰"}})
    assert "비밀토큰" not in repr(target)
    assert target.auth.value.get_secret_value() == "비밀토큰"


def test_등재_경로는_base_url을_벗어날_수_없다():
    # path에 검증이 없으면 Task 1이 세운 방어(절대 URL·임베디드 쿼리·순회)가
    # 통째로 비껴간다 — 실제로 http://evil/wipe가 base_url을 벗어나 나갔다.
    from src.config.schema_site import RestTarget
    for bad in ("http://evil.internal/wipe", "//evil.internal/wipe",
                "/mes/plan?admin=1", "/a/../../admin", "/a/./b", "mes/plan",
                "/a%2e%2e/b", "/a/b#frag", "/a;x=y/b", "/a\nb"):
        with pytest.raises(ValidationError):
            RestTarget.model_validate({"base_url": "http://x",
                                       "entries": {"e": {"method": "POST", "path": bad}}})


def test_정상_경로는_자리표시자를_포함해_통과한다():
    from src.config.schema_site import RestTarget
    for ok in ("/summary/prod", "/api/v1/lines/{line}/oee", "/mes/plan"):
        target = RestTarget.model_validate({"base_url": "http://x",
                                            "entries": {"e": {"method": "POST", "path": ok}}})
        assert target.entries["e"].path == ok
