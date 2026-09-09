"""로더 — 파일이 없거나 JSON이 깨졌거나 env가 비었을 때 무엇을 말하는가."""
import json

import pytest

from src.config.loader import ConfigError, load_app_config, load_site_config, site_files

GOOD_SITE = {
    "site": {"gbm": "mx", "fct": "gumi"},
    "infra": {"redis": {"url": "redis://h:6379", "password": "${MX_GUMI_REDIS_PASSWORD}"}},
}


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_env를_치환해_읽는다(tmp_path):
    path = _write(tmp_path / "mx-gumi.json", GOOD_SITE)
    site = load_site_config(path, env={"MX_GUMI_REDIS_PASSWORD": "hunter2"})
    assert site.infra.redis.password.get_secret_value() == "hunter2"


def test_env가_비면_어느_키인지_말한다(tmp_path):
    path = _write(tmp_path / "mx-gumi.json", GOOD_SITE)
    with pytest.raises(ConfigError, match="MX_GUMI_REDIS_PASSWORD"):
        load_site_config(path, env={})


def test_파일이_없으면_경로를_말한다(tmp_path):
    with pytest.raises(ConfigError, match="config 파일이 없다"):
        load_site_config(tmp_path / "없는파일.json", env={})


def test_JSON이_깨지면_줄과_열을_말한다(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{\n  "site": {,\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"JSON이 깨졌다.*:2:"):
        load_site_config(path, env={})


def test_스키마_오류는_어느_필드인지_말한다(tmp_path):
    path = _write(tmp_path / "bad.json",
                  {"site": {"gbm": "mx", "fct": "gumi"},
                   "infra": {"redis": {"url": "http://h:6379"}}})
    with pytest.raises(ConfigError, match=r"infra.redis.url"):
        load_site_config(path, env={})


def test_한글이_든_config를_읽는다(tmp_path):
    # Windows(cp949)에서만 깨지는 경로 — encoding="utf-8"이 빠지면 여기서 죽는다.
    path = _write(tmp_path / "app.json", {"timezone": "Asia/Seoul", "output_dir": "보고서"})
    assert load_app_config(path, env={}).output_dir == "보고서"


def test_해석_못하는_타임존을_거부한다(tmp_path):
    path = _write(tmp_path / "app.json", {"timezone": "Asia/Seoul_오타"})
    with pytest.raises(ConfigError, match="해석할 수 없는 timezone"):
        load_app_config(path, env={})


def test_사이트_파일_목록은_정렬돼_있다(tmp_path):
    (tmp_path / "sites").mkdir()
    for name in ("z.json", "a.json", "m.json"):
        _write(tmp_path / "sites" / name, GOOD_SITE)
    assert [p.name for p in site_files(tmp_path)] == ["a.json", "m.json", "z.json"]


def test_sites_디렉터리가_없으면_빈_목록이다(tmp_path):
    assert site_files(tmp_path) == []


def test_env는_기본값이_없어_반드시_넘겨야_한다(tmp_path):
    # 기본값이 있으면 누군가 안 넘기고, 그 경로만 치환이 안 된 채로 돈다.
    path = _write(tmp_path / "mx-gumi.json", GOOD_SITE)
    with pytest.raises(TypeError, match="env"):
        load_site_config(path)          # type: ignore[call-arg]
