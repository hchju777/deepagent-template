from src.config.envresolve import resolve_env_refs


def test_전체일치_참조만_치환하고_부분_문자열은_그대로():
    data = {"redis": {"url": "${MX_GUMI_REDIS_URL}", "note": "url is ${NOT_A_REF} ok"}}
    resolved, missing = resolve_env_refs(data, env={"MX_GUMI_REDIS_URL": "redis://g:6379"})
    assert resolved["redis"]["url"] == "redis://g:6379"
    assert resolved["redis"]["note"] == "url is ${NOT_A_REF} ok"   # 보간 미지원(의도)
    assert missing == []


def test_부재_또는_빈값_키는_missing에_모인다():
    data = {"mongo": {"url": "${A_URL}", "password": "${A_PW}"}}
    resolved, missing = resolve_env_refs(data, env={"A_URL": ""})
    assert sorted(missing) == ["A_PW", "A_URL"]
