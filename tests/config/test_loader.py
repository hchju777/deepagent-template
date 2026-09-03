import json

import pytest
from src.config.loader import ConfigError, load_app_config, load_registry, load_site_config


def _write(tmp_path, rel, data):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def _config_tree(tmp_path):
    _write(tmp_path, "config/registry.json",
           {"sites": [{"gbm": "mx", "fct": "gumi"},
                      {"gbm": "mx", "fct": "suwon", "enabled": False}]})
    _write(tmp_path, "config/gbm/mx.json",
           {"target": {"redis": {"url": "${MX_REDIS_URL}"}},
            "patrol": {"checks": {"api.freshness": {
                "judge": "rule", "schedule": {"interval": "5m"}}}}})
    _write(tmp_path, "config/factories/gumi/common.json",
           {"target": {"guards": {"max_rows": 500}}})
    _write(tmp_path, "config/factories/gumi/mx.json",
           {"patrol": {"checks": {"api.freshness": None}}})   # 사이트에서 점검 끔


def test_merge_순서와_null_삭제와_출처(tmp_path):
    _config_tree(tmp_path)
    cfg, prov = load_site_config(tmp_path / "config", "mx", "gumi",
                                 env={"MX_REDIS_URL": "redis://g:6379"})
    assert cfg.target.redis.url == "redis://g:6379"           # env 해석됨
    assert cfg.target.guards.max_rows == 500                  # common이 덮음
    assert "api.freshness" not in cfg.patrol.checks           # null로 꺼짐
    assert prov["target.guards.max_rows"] == "factories/gumi/common"


def test_env_누락은_키_이름을_전부_모아_거부(tmp_path):
    _config_tree(tmp_path)
    with pytest.raises(ConfigError) as exc:
        load_site_config(tmp_path / "config", "mx", "gumi", env={})
    assert any("MX_REDIS_URL" in p for p in exc.value.problems)


def test_registry_enabled_기본값(tmp_path):
    _config_tree(tmp_path)
    reg = load_registry(tmp_path / "config")
    assert [(s.gbm, s.fct, s.enabled) for s in reg.sites] == [
        ("mx", "gumi", True), ("mx", "suwon", False)]


def test_앞_계층이_없어도_null_마커는_삭제로_동작한다(tmp_path):
    # gbm/common 계층 없이 마지막 계층만 존재 — 스펙상 허용되는 배치
    _write(tmp_path, "config/factories/gumi/mx.json",
           {"target": {"redis": {"url": "redis://g:6379"}},
            "patrol": {"checks": {"api.freshness": None}}})
    cfg, prov = load_site_config(tmp_path / "config", "mx", "gumi", env={})
    assert "api.freshness" not in cfg.patrol.checks
    assert not any(p.startswith("patrol.checks.api.freshness") for p in prov)


def test_app_json의_env_참조는_env가_주어지면_치환된다(tmp_path):
    _write(tmp_path, "config/app.json",
           {"store": {"backend": "mongo", "mongo_url": "${AGENT_MONGO_URL}"},
            "llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}}})
    cfg = load_app_config(tmp_path / "config", env={"AGENT_MONGO_URL": "mongodb://x/y"})
    assert cfg.store.mongo_url == "mongodb://x/y"


def test_app_json의_env_미주입시엔_치환을_건너뛴다(tmp_path):
    # env를 아예 넘기지 않으면(기본 None) 해석을 건너뛴다 — literal "${...}"가
    # 문자열 타입 검증은 통과하므로 여기서는 실패하지 않는다(호출부가 env를
    # 반드시 넘기도록 고치는 게 C1의 실제 방어선이다).
    _write(tmp_path, "config/app.json",
           {"llm": {"profiles": {"judge": "${MISSING}", "subagent": "b", "lead": "c"}}})
    cfg = load_app_config(tmp_path / "config")
    assert cfg.llm.profiles.judge == "${MISSING}"


def test_깨진_JSON은_트레이스백이_아니라_ConfigError다(tmp_path):
    p = tmp_path / "config" / "gbm"
    p.mkdir(parents=True)
    (p / "mx.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_site_config(tmp_path / "config", "mx", "gumi", env={})
    assert any("JSON 파싱 실패" in prob for prob in exc.value.problems)


def test_점검_body의_env_참조는_거부된다(tmp_path):
    # params.body는 증거에 평문으로 영속되고 보고서에 렌더되며 서브에이전트의
    # get_evidence 도구로 LLM 프롬프트에도 실린다 — 비밀값이 그 경로로 새면
    # SecretStr 마스킹이 아무 소용이 없다. 인증은 rest.auth의 몫이다.
    import json

    from src.config.loader import ConfigError, load_site_config
    (tmp_path / "gbm").mkdir()
    (tmp_path / "gbm" / "mx.json").write_text(json.dumps({
        "target": {"adapters": "stub", "rest": {
            "base_url": "http://x",
            "entries": {"e": {"method": "POST", "path": "/x",
                              "body_schema": {"api_key": "str"}}}}},
        "patrol": {"checks": {"c": {"judge": "rule", "schedule": {"interval": "5m"},
                                    "target": "rest:e",
                                    "params": {"rule": "exists", "field": "body",
                                               "body": {"api_key": "${MES_TOKEN}"}}}}}}))
    with pytest.raises(ConfigError) as exc:
        load_site_config(tmp_path, "mx", "gumi", env={"MES_TOKEN": "tok-SECRET"})
    assert any("params.body" in p for p in exc.value.problems)


def test_해석기_스펙의_env_참조도_거부된다(tmp_path):
    # resolve.filter 값은 대상 쿼리로 나가고 config show에 평문으로 찍힌다
    # (SecretStr이 아니라 마스킹 대상이 아니다).
    import json

    from src.config.loader import ConfigError, load_site_config
    (tmp_path / "gbm").mkdir()
    (tmp_path / "gbm" / "mx.json").write_text(json.dumps({
        "target": {"adapters": "stub", "mongo": {"url": "mongodb://x"},
                   "rest": {"base_url": "http://x", "entries": {
                       "e": {"method": "POST", "path": "/x",
                             "body_schema": {"line_code": "list[str]"}}}}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
            "params": {"rule": "exists", "field": "body"},
            "resolve": {"line_code": {"from": "mongo", "collection": "lines",
                                      "field": "line_code",
                                      "filter": {"token": "${MX_SECRET}"}}}}}}}))
    with pytest.raises(ConfigError) as exc:
        load_site_config(tmp_path, "mx", "gumi", env={"MX_SECRET": "super-secret"})
    assert any("resolve" in p for p in exc.value.problems)
