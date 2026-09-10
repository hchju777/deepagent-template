"""CLI 경계 — 어느 사이트를 볼 것인가를 어떻게 정하는가.

여기서 틀리면 **다른 법인의 Redis를 들여다본다.** 그건 에러가 아니라 조용히
잘못된 답이라서, 사람이 알아채기까지 오래 걸린다.
"""
import json

import pytest

from src.__main__ import _resolve_site, build_parser

ENV = {"REDIS_PASSWORD": "hunter2"}


@pytest.fixture
def config_root(tmp_path):
    root = tmp_path / "config"
    (root / "gbm").mkdir(parents=True)
    (root / "fct" / "gumi").mkdir(parents=True)
    (root / "fct" / "sevt").mkdir(parents=True)
    (root / "app.json").write_text('{"timezone": "Asia/Seoul"}', encoding="utf-8")
    (root / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"}]}), encoding="utf-8")
    (root / "gbm" / "common.json").write_text(
        json.dumps({"infra": {"redis": {"db": 0, "password": "${REDIS_PASSWORD}"}}}),
        encoding="utf-8")
    for fct in ("gumi", "sevt"):
        (root / "fct" / fct / "mx.json").write_text(
            json.dumps({"infra": {"redis": {"url": f"redis://{fct}:6379"}}}), encoding="utf-8")
    return root


def _parse(*argv):
    return build_parser().parse_args(list(argv))


# ── --gbm/--fct의 위치 ────────────────────────────────────────────────

def test_하위_명령_앞에_써도_된다(config_root):
    args = _parse("--gbm", "mx", "--fct", "sevt", "peek", "redis", "--key", "k")
    site, _ = _resolve_site(config_root, args, ENV)
    assert str(site.site) == "mx/sevt"


def test_하위_명령_뒤에_써도_된다(config_root):
    """argparse의 전역 옵션은 원래 앞에만 온다. 사람은 뒤에 쓰는 쪽이 자연스럽다."""
    args = _parse("peek", "redis", "--key", "k", "--gbm", "mx", "--fct", "sevt")
    site, _ = _resolve_site(config_root, args, ENV)
    assert str(site.site) == "mx/sevt"


def test_뒤에_안_쓰면_앞의_값이_살아_있다(config_root):
    # 하위 파서의 기본값이 None이면 전역에서 받은 값을 덮어써 버린다(SUPPRESS로 막는다).
    args = _parse("--gbm", "mx", "--fct", "gumi", "peek", "redis", "--key", "k")
    assert (args.gbm, args.fct) == ("mx", "gumi")


def test_doctor와_config_show에도_붙는다(config_root):
    for argv in (("doctor", "--fct", "sevt"), ("config", "show", "--fct", "sevt")):
        site, _ = _resolve_site(config_root, _parse(*argv), ENV)
        assert str(site.site) == "mx/sevt"


# ── 좁히기 ───────────────────────────────────────────────────────────

def test_한쪽만_줘도_유일하면_통한다(config_root):
    # 사업부가 mx뿐이면 --fct만으로 충분하다.
    site, _ = _resolve_site(config_root, _parse("peek", "redis", "--fct", "sevt"), ENV)
    assert str(site.site) == "mx/sevt"


def test_활성_사이트가_하나면_생략해도_된다(tmp_path, config_root):
    (config_root / "registry.json").write_text(
        json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"},
                              {"gbm": "mx", "fct": "sevt", "enabled": False}]}),
        encoding="utf-8")
    site, _ = _resolve_site(config_root, _parse("peek", "redis"), ENV)
    assert str(site.site) == "mx/gumi"


def test_좁혀지지_않으면_임의로_고르지_않고_묻는다(config_root):
    with pytest.raises(SystemExit) as caught:
        _resolve_site(config_root, _parse("peek", "redis"), ENV)
    message = str(caught.value)
    assert "mx/gumi, mx/sevt" in message
    assert "--gbm mx --fct gumi" in message, "따라 칠 수 있는 예가 있어야 한다"


def test_없는_사이트는_등록된_것을_알려준다(config_root):
    with pytest.raises(SystemExit) as caught:
        _resolve_site(config_root, _parse("peek", "redis", "--fct", "없는법인"), ENV)
    assert "registry에 없는 사이트" in str(caught.value)
    assert "mx/gumi" in str(caught.value)


def test_고른_사이트의_설정이_실제로_다르다(config_root):
    gumi, _ = _resolve_site(config_root, _parse("peek", "redis", "--fct", "gumi"), ENV)
    sevt, _ = _resolve_site(config_root, _parse("peek", "redis", "--fct", "sevt"), ENV)
    assert gumi.infra.redis.url != sevt.infra.redis.url
    assert gumi.infra.redis.db == sevt.infra.redis.db == 0   # 공통 층은 같다
