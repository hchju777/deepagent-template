from src.config.merge import deep_merge, record_provenance


def test_중첩_dict는_재귀_병합되고_스칼라는_덮어쓴다():
    base = {"target": {"redis": {"url": "a"}, "guards": {"timeout_s": 10}}}
    prov: dict[str, str] = {}
    record_provenance(base, source="gbm/mx", provenance=prov)
    merged = deep_merge(
        base, {"target": {"redis": {"url": "b"}}},
        source="factories/gumi/mx", provenance=prov,
    )
    assert merged["target"]["redis"]["url"] == "b"
    assert merged["target"]["guards"]["timeout_s"] == 10      # 보존
    assert prov["target.redis.url"] == "factories/gumi/mx"
    assert prov["target.guards.timeout_s"] == "gbm/mx"
    assert base["target"]["redis"]["url"] == "a"              # base 불변


def test_null은_키를_삭제하고_하위_출처도_지운다():
    base = {"patrol": {"checks": {"kafka.lag": {"judge": "rule"}}}}
    prov: dict[str, str] = {}
    record_provenance(base, source="gbm/mx", provenance=prov)
    merged = deep_merge(
        base, {"patrol": {"checks": {"kafka.lag": None}}},
        source="factories/gumi/mx", provenance=prov,
    )
    assert "kafka.lag" not in merged["patrol"]["checks"]
    assert not any(p.startswith("patrol.checks.kafka.lag") for p in prov)
