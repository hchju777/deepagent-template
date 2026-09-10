"""CLI 경계 — 어느 사이트를 볼 것인가를 어떻게 정하는가.

여기서 틀리면 **다른 법인의 Redis를 들여다본다.** 그건 에러가 아니라 조용히
잘못된 답이라서, 사람이 알아채기까지 오래 걸린다.
"""
import json

import pytest

from src.__main__ import _resolve_site, parse_args

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
    """프로덕션과 **같은 입구**를 쓴다 — build_parser를 직접 부르면 병합 단계를
    건너뛰어, 실제로 깨지는 조합이 테스트에서는 통과한다."""
    return parse_args(list(argv))


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
    # 같은 dest를 양쪽에 달면 하위 파서의 "안 줬음"이 전역 값을 덮어쓰는 파이썬
    # 버전이 있다 — Linux는 통과하고 Windows에서 깨졌던 지점이다.
    args = _parse("--gbm", "mx", "--fct", "gumi", "peek", "redis", "--key", "k")
    assert (args.gbm, args.fct) == ("mx", "gumi")


def test_양쪽에_다_쓰면_뒤가_이긴다(config_root):
    args = _parse("--fct", "gumi", "peek", "redis", "--key", "k", "--fct", "sevt")
    assert args.fct == "sevt"
    site, _ = _resolve_site(config_root, args, ENV)
    assert str(site.site) == "mx/sevt"


def test_어느_위치든_같은_결과다(config_root):
    """argparse 내부 동작에 기대지 않는다는 것을 양쪽으로 확인한다."""
    before, _ = _resolve_site(config_root,
                              _parse("--fct", "sevt", "peek", "redis", "--key", "k"), ENV)
    after, _ = _resolve_site(config_root,
                             _parse("peek", "redis", "--key", "k", "--fct", "sevt"), ENV)
    assert str(before.site) == str(after.site) == "mx/sevt"


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


# ── 구조 불변식: argparse 내부 동작에 기대지 않는다 ─────────────────────

def _subparser_actions():
    from src.__main__ import build_parser
    parser = build_parser()
    choices = {}
    for action in parser._actions:                              # noqa: SLF001
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            choices.update(action.choices)
    return choices


def test_하위_명령의_사이트_옵션은_전역과_dest를_공유하지_않는다():
    """dest를 공유하면 값을 합치는 일이 **argparse 내부 동작**에 달린다.

    하위 파서의 결과를 부모 namespace에 합치는 방식이 파이썬 버전 사이에서
    바뀌었고, 그래서 같은 dest를 쓰면 어떤 버전에서는 하위 파서의 "안 줬음"이
    전역에서 받은 값을 덮어쓴다. 실제로 Linux는 통과하고 Windows(다른 파이썬)에서
    다섯 개가 깨졌다.

    "나중에 누가 단순화하려고 dest를 합치는 것"을 막기 위해 구조로 못 박는다.
    """
    offenders = []
    for name, sub in _subparser_actions().items():
        for action in sub._actions:                              # noqa: SLF001
            if action.dest in ("gbm", "fct"):
                offenders.append(f"{name}.{action.dest}")
    assert not offenders, (
        f"하위 명령이 전역과 같은 dest를 쓴다 — {offenders}. "
        "gbm_sub/fct_sub로 분리하고 _merge_site_options가 합쳐야 한다")


def test_사이트_옵션은_앞뒤_양쪽에서_받아들여진다():
    """파싱만 본다 — config가 없어도 도는 테스트라 원인 구분에 쓸 수 있다."""
    assert _parse("--fct", "gumi", "doctor").fct == "gumi"
    assert _parse("doctor", "--fct", "gumi").fct == "gumi"
    assert _parse("--gbm", "mx", "peek", "redis", "--key", "k").gbm == "mx"
    assert _parse("peek", "redis", "--key", "k", "--gbm", "mx").gbm == "mx"


# ── 명령이 실제로 돌아가는가 (import 누락류를 잡는다) ────────────────────

@pytest.fixture
def echo_config(tmp_path):
    """네트워크를 안 타는 llm 설정 — 명령 배선만 본다."""
    root = tmp_path / "config"
    (root / "gbm").mkdir(parents=True)
    (root / "fct" / "gumi").mkdir(parents=True)
    (root / "app.json").write_text(json.dumps({
        "timezone": "Asia/Seoul",
        "llm": {"adapter": "echo", "model": "dev"}}), encoding="utf-8")
    (root / "registry.json").write_text(
        json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"}]}), encoding="utf-8")
    (root / "fct" / "gumi" / "mx.json").write_text(
        json.dumps({"infra": {"redis": {"url": "redis://h:6379"}}}), encoding="utf-8")
    return root


@pytest.mark.parametrize("argv", [
    ["boot"], ["sites"], ["config", "show"], ["config", "show", "--no-provenance"],
    ["llm", "describe"], ["llm", "ask", "안녕"], ["llm", "check"],
    ["peek", "rest", "--list"],
])
def test_명령이_끝까지_돌아간다(echo_config, argv, capsys):
    """`llm describe`가 import 누락으로 죽은 적이 있다 — 스키마 테스트로는 안 잡힌다.

    `peek rest --list`는 rest 설정이 없으므로 SystemExit이 정상이다. 그 경우에도
    **NameError나 AttributeError가 아니어야** 한다.
    """
    from src.__main__ import main

    try:
        code = main(["--config-root", str(echo_config), "--env-file", "/dev/null", *argv])
        assert code in (0, 1)
    except SystemExit as exit_:
        assert isinstance(exit_.code, (int, str)), f"예상 밖 종료 — {exit_.code!r}"
    out = capsys.readouterr().out
    assert out or argv[0] in ("peek",)


def test_llm_check가_echo로도_결과를_낸다(echo_config, capsys):
    from src.__main__ import main

    main(["--config-root", str(echo_config), "--env-file", "/dev/null", "llm", "check"])
    out = capsys.readouterr().out
    assert "붙는가" in out and "JSON" in out
