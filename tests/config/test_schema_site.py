import pytest
from pydantic import ValidationError
from src.config.schema_site import CheckConfig, Schedule, SiteConfig


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
    assert entry.method == "POST" and entry.query_schema == {}


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


def test_등재_항목_이름은_locator를_흉내낼_수_없다():
    # 이름을 "/oee"로 두고 check.probe를 명시하면 resolve_probe의 슬래시 휴리스틱과
    # boot의 이름공간 분기를 동시에 우회한다 — 리뷰어가 target: "rest:/oee"를
    # v1식 읽기 전용 GET으로 읽는데 실제로는 POST가 나간다.
    from src.config.schema_site import RestTarget
    for bad in ("/oee", "a/b", "rest:x", "a:b", "", " x", "x ", "/"):
        with pytest.raises(ValidationError):
            RestTarget.model_validate({
                "base_url": "http://x",
                "entries": {bad: {"method": "POST", "path": "/x"}}})


def test_정상_항목_이름은_통과한다():
    from src.config.schema_site import RestTarget
    for ok in ("summary_prod", "mes-plan", "badge2"):
        t = RestTarget.model_validate({"base_url": "http://x",
                                       "entries": {ok: {"method": "POST", "path": "/x"}}})
        assert ok in t.entries


def test_해석기_스펙은_종류별로_필요한_필드를_요구한다():
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
        "params": {"rule": "exists", "field": "body.badge"},
        "resolve": {
            "part_code": {"from": "rest", "entry": "list_parts", "field": "part_code",
                          "cardinality": "all"},
            "line_code": {"from": "mongo", "collection": "lines", "field": "line_code",
                          "filter": {"active": True}, "cardinality": "first:10"},
            "date": {"from": "clock", "expr": "today"},
            "graph_type": {"from": "unfiltered"}}})
    assert check.resolve["part_code"].entry == "list_parts"
    assert check.resolve["line_code"].cardinality == "first:10"


def test_알_수_없는_해석기_종류는_거부된다():
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({
            "judge": "rule", "schedule": {"interval": "5m"},
            "resolve": {"x": {"from": "s3", "bucket": "b"}}})


def test_해석기_종류마다_필요한_필드가_강제된다():
    for bad in ({"from": "rest", "field": "x"},              # entry 없음
                {"from": "mongo", "field": "x"},             # collection 없음
                {"from": "clock"},                           # expr 없음
                {"from": "clock", "expr": "언젠가"}):         # 어휘 밖 expr
        with pytest.raises(ValidationError):
            CheckConfig.model_validate({
                "judge": "rule", "schedule": {"interval": "5m"}, "resolve": {"x": bad}})


def test_카디널리티_어휘_밖은_거부된다():
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({
            "judge": "rule", "schedule": {"interval": "5m"},
            "resolve": {"x": {"from": "rest", "entry": "e", "field": "f",
                              "cardinality": "무제한"}}})


def test_정적_값과_해석_키가_겹치면_거부된다():
    # 어느 쪽이 이기는지 사람이 헷갈리면 안 된다.
    with pytest.raises(ValidationError):
        CheckConfig.model_validate({
            "judge": "rule", "schedule": {"interval": "5m"},
            "params": {"body": {"part_code": ["P001"]}},
            "resolve": {"part_code": {"from": "unfiltered"}}})


def test_운영_축_전용_rule은_concern을_명시해야_한다():
    # 기본값 "system"을 둔 대가다. 원래 근거였던 "기존 rule은 전부 파이프라인
    # 신호"는 거짓이었다 — max/defect_count는 기존 rule로 쓴 현장 이상이다.
    # rule 이름으로 concern을 **추측하지는 않는다**: 큐 깊이가 전부 0인 것은
    # 파이프라인 신호이므로 "system"이라고 적으면 통과한다. 요구하는 것은
    # 사람이 한 번 답하는 것뿐이다.
    import pytest
    from pydantic import ValidationError
    from src.config.schema_site import CheckConfig
    base = {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e"}
    for rule_params in ({"rule": "all_zero", "field": "body.badge"},
                        {"rule": "expected_state", "field": "body.s", "expect": ["x"]}):
        with pytest.raises(ValidationError, match="concern"):
            CheckConfig.model_validate({**base, "params": rule_params})
        # 명시하면 어느 값이든 통과한다 — 우리가 대신 정하지 않는다.
        for concern in ("system", "operation"):
            assert CheckConfig.model_validate(
                {**base, "concern": concern, "params": rule_params}).concern == concern


def test_기존_rule은_concern_없이도_통과한다():
    # ~100곳을 고치게 만들지 않는다는 결정은 그대로 유지한다.
    from src.config.schema_site import CheckConfig
    assert CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/x",
        "params": {"rule": "max", "field": "body.oee", "max": 5}}).concern == "system"


def test_축_전용_rule_집합이_실재하는_rule만_담는다():
    # _AXIS_SPECIFIC_RULES와 _RULES를 잇는 것이 없다. 오타나 이름 변경으로
    # 집합이 헛돌면 그 rule은 concern 명시 없이 통과하고, 그 망각이 조용하다.
    from src.config.schema_site import _AXIS_SPECIFIC_RULES
    from src.patrol.rules import _RULES
    assert _AXIS_SPECIFIC_RULES <= set(_RULES), sorted(_AXIS_SPECIFIC_RULES - set(_RULES))
