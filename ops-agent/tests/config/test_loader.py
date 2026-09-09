"""로더 — 네 층을 병합하고, env를 해석하고, 검증한다."""
import json

import pytest

from src.config.loader import (ConfigError, load_app_config, load_registry,
                               load_site_config)

GBM_COMMON = {"infra": {"redis": {"db": 0}, "mongodb": {"database": "data"}}}
GBM_MX = {"infra": {"mongodb": {"user": "dmfReadOnly"}}}
FCT_COMMON = {"infra": {"rest": {"timeout_s": 20}}}
FCT_MX = {"infra": {
    "redis": {"url": "redis://gumi:6379", "password": "${MX_GUMI_REDIS_PASSWORD}"},
    "mongodb": {"url": "mongodb://gumi:27017", "password": "${MX_GUMI_MONGO_PASSWORD}"},
    "rest": {"base_url": "http://gumi:8080"}}}
ENV = {"MX_GUMI_REDIS_PASSWORD": "hunter2", "MX_GUMI_MONGO_PASSWORD": "s3cret"}


def _tree(root, *, layers=None, app=None, registry=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps(app or {"timezone": "Asia/Seoul"}),
                                   encoding="utf-8")
    (root / "registry.json").write_text(
        json.dumps(registry or {"sites": [{"gbm": "mx", "fct": "gumi"}]}), encoding="utf-8")
    layers = layers if layers is not None else {
        "gbm/common.json": GBM_COMMON, "gbm/mx.json": GBM_MX,
        "fct/gumi/common.json": FCT_COMMON, "fct/gumi/mx.json": FCT_MX}
    for relative, data in layers.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


# ── 계층 병합 ────────────────────────────────────────────────────────

def test_네_층이_하나로_합쳐진다(tmp_path):
    site, _ = load_site_config(_tree(tmp_path / "c"), "mx", "gumi", env=ENV)
    assert site.infra.redis.db == 0                       # gbm/common
    assert site.infra.mongodb.user == "dmfReadOnly"       # gbm/mx
    assert site.infra.rest.timeout_s == 20                # fct/gumi/common
    assert site.infra.redis.url == "redis://gumi:6379"    # fct/gumi/mx


def test_출처를_함께_돌려준다(tmp_path):
    _, provenance = load_site_config(_tree(tmp_path / "c"), "mx", "gumi", env=ENV)
    assert provenance["infra.redis.db"] == "gbm/common"
    assert provenance["infra.redis.url"] == "fct/gumi/mx"


def test_없는_층은_그냥_건너뛴다(tmp_path):
    # 넷 다 있을 필요는 없다 — 법인 공통 설정이 없는 사이트가 흔하다.
    root = _tree(tmp_path / "c", layers={
        "gbm/common.json": {"infra": {"redis": {"db": 0}}},
        "fct/gumi/mx.json": {"infra": {"redis": {"url": "redis://gumi:6379"}}}})
    site, _ = load_site_config(root, "mx", "gumi", env=ENV)
    assert site.infra.redis.url == "redis://gumi:6379"
    assert site.infra.redis.db == 0


def test_검증은_층별이_아니라_병합_결과에_한다(tmp_path):
    """`gbm/mx.json`이 user를, `fct/gumi/mx.json`이 password를 말한다.

    층마다 검증하면 둘 다 "짝이 없다"고 실패한다 — 각 층은 원래 불완전하다.
    합친 뒤에 봐야 한다. 반대로 **합쳤는데도** 짝이 안 맞으면 그건 진짜 오류다.
    """
    complete = _tree(tmp_path / "완전", layers={
        "gbm/mx.json": {"infra": {"mongodb": {"database": "data", "user": "dmfReadOnly"}}},
        "fct/gumi/mx.json": {"infra": {"mongodb": {"url": "mongodb://gumi:27017",
                                                   "password": "${MX_GUMI_MONGO_PASSWORD}"}}}})
    site, _ = load_site_config(complete, "mx", "gumi", env=ENV)
    assert site.infra.mongodb.user == "dmfReadOnly"

    missing_user = _tree(tmp_path / "불완전", layers={
        "fct/gumi/mx.json": {"infra": {"mongodb": {"url": "mongodb://gumi:27017",
                                                   "database": "data",
                                                   "password": "${MX_GUMI_MONGO_PASSWORD}"}}}})
    with pytest.raises(ConfigError, match="user가 없다"):
        load_site_config(missing_user, "mx", "gumi", env=ENV)


def test_층이_하나도_없으면_어디를_찾았는지_말한다(tmp_path):
    root = _tree(tmp_path / "c", layers={})
    with pytest.raises(ConfigError, match="fct/gumi/mx.json"):
        load_site_config(root, "mx", "gumi", env=ENV)


def test_아래_층이_위_층을_지울_수_있다(tmp_path):
    # null 마커 — 사업부 공통으로 켠 것을 특정 법인에서 끈다.
    root = _tree(tmp_path / "c", layers={
        "gbm/mx.json": {"infra": {"redis": {"url": "redis://공통:6379"},
                                  "kafka": {"consumer": {"bootstrap_server": ["b:9092"],
                                                         "group_id": "g",
                                                         "topic": {"t": "T"}}}}},
        "fct/gumi/mx.json": {"infra": {"kafka": None}}})
    site, _ = load_site_config(root, "mx", "gumi", env=ENV)
    assert site.infra.kafka is None, "이 법인에는 Kafka가 없다고 말했는데 남아 있다"


def test_층_파일이_site를_선언하면_거부한다(tmp_path):
    # 사이트 정체성이 두 곳에서 오면 어긋났을 때 어느 쪽이 맞는지 아무도 모른다.
    root = _tree(tmp_path / "c", layers={
        "fct/gumi/mx.json": dict(FCT_MX, site={"gbm": "mx", "fct": "suwon"})})
    with pytest.raises(ConfigError, match="site가 선언돼 있다"):
        load_site_config(root, "mx", "gumi", env=ENV)


def test_사이트_정체성은_인자가_정한다(tmp_path):
    site, _ = load_site_config(_tree(tmp_path / "c"), "mx", "gumi", env=ENV)
    assert str(site.site) == "mx/gumi"


# ── env·형식 ─────────────────────────────────────────────────────────

def test_env가_비면_어느_키인지_말한다(tmp_path):
    with pytest.raises(ConfigError, match="MX_GUMI_REDIS_PASSWORD"):
        load_site_config(_tree(tmp_path / "c"), "mx", "gumi", env={})


def test_JSON이_깨지면_줄과_열을_말한다(tmp_path):
    root = _tree(tmp_path / "c")
    (root / "gbm" / "mx.json").write_text('{\n  "infra": {,\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"JSON이 깨졌다.*:2:"):
        load_site_config(root, "mx", "gumi", env=ENV)


def test_스키마_오류는_어느_필드인지_말한다(tmp_path):
    root = _tree(tmp_path / "c", layers={"fct/gumi/mx.json":
                                         {"infra": {"redis": {"url": "http://나쁨"}}}})
    with pytest.raises(ConfigError, match=r"infra.redis.url"):
        load_site_config(root, "mx", "gumi", env=ENV)


def test_한글이_든_config를_읽는다(tmp_path):
    # Windows(cp949)에서만 깨지는 경로 — encoding="utf-8"이 빠지면 여기서 죽는다.
    root = _tree(tmp_path / "c", app={"timezone": "Asia/Seoul", "output_dir": "보고서"})
    assert load_app_config(root, env={}).output_dir == "보고서"


def test_env는_기본값이_없어_반드시_넘겨야_한다(tmp_path):
    # 기본값이 있으면 누군가 반드시 안 넘긴다 — 그 경로만 치환이 안 된 채로 돈다.
    with pytest.raises(TypeError, match="env"):
        load_site_config(_tree(tmp_path / "c"), "mx", "gumi")   # type: ignore[call-arg]


# ── registry ─────────────────────────────────────────────────────────

def test_registry가_활성_사이트를_말한다(tmp_path):
    root = _tree(tmp_path / "c", registry={"sites": [
        {"gbm": "mx", "fct": "gumi"},
        {"gbm": "mx", "fct": "suwon", "enabled": False}]})
    registry = load_registry(root)
    assert len(registry.sites) == 2
    assert [str(s) for s in registry.active()] == ["mx/gumi"]


def test_registry가_없으면_말한다(tmp_path):
    root = tmp_path / "빈트리"
    root.mkdir()
    with pytest.raises(ConfigError, match="config 파일이 없다"):
        load_registry(root)
