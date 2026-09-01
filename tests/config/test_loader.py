import json

import pytest
from src.config.loader import ConfigError, load_registry, load_site_config


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
