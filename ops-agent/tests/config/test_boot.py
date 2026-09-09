"""기동 검증 — 문제를 전부 모아서 돌려주고, 스스로는 절대 죽지 않는다."""
import json
from pathlib import Path

from src.boot import validate_boot

APP = {"timezone": "Asia/Seoul", "output_dir": "output"}
SITE = {"site": {"gbm": "mx", "fct": "gumi"},
        "infra": {"redis": {"url": "redis://h:6379", "password": "${MX_GUMI_REDIS_PASSWORD}"}}}
ENV = {"MX_GUMI_REDIS_PASSWORD": "hunter2"}


def _tree(root: Path, *, app=APP, sites: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if app is not None:
        (root / "app.json").write_text(json.dumps(app), encoding="utf-8")
    (root / "sites").mkdir(exist_ok=True)
    for name, data in (sites if sites is not None else {"mx-gumi.json": SITE}).items():
        (root / "sites" / name).write_text(json.dumps(data), encoding="utf-8")
    return root


def test_정상_트리는_문제가_없다(tmp_path):
    assert validate_boot(_tree(tmp_path / "config"), env=ENV) == []


def test_문제가_셋이면_셋_다_보고한다(tmp_path):
    """하나씩 던지면 사람은 고치고-돌리기를 세 번 한다. 그게 이 함수의 존재 이유다."""
    root = _tree(
        tmp_path / "config",
        app={"timezone": "없는/타임존"},
        sites={
            "a.json": {"site": {"gbm": "mx", "fct": "gumi"},
                       "infra": {"redis": {"url": "http://h:6379"}}},          # 스킴 오류
            "b.json": {"site": {"gbm": "mx", "fct": "suwon"},
                       "infra": {"redis": {"url": "redis://h:6379",
                                           "password": "${없는키}"}}},          # env 누락
        })
    errors = validate_boot(root, env=ENV)
    wheres = sorted(e.where for e in errors)
    assert wheres == ["app.json", "sites/a.json", "sites/b.json"], errors


def test_사이트가_하나도_없으면_말한다(tmp_path):
    root = _tree(tmp_path / "config", sites={})
    assert [e.where for e in validate_boot(root, env=ENV)] == ["sites/"]


def test_app_json이_없어도_사이트_검증은_계속한다(tmp_path):
    root = _tree(tmp_path / "config", app=None,
                 sites={"a.json": {"site": {"gbm": "mx", "fct": "gumi"}, "infra": {}}})
    wheres = sorted(e.where for e in validate_boot(root, env=ENV))
    assert wheres == ["app.json", "sites/a.json"]     # 앞이 실패해도 뒤를 계속 본다


def test_같은_사이트가_두_파일에_있으면_잡는다(tmp_path):
    # 어느 쪽이 이기는지가 파일 이름 순서에 달리면
    # 나중에 "설정을 바꿨는데 안 먹는다"로 나타난다.
    root = _tree(tmp_path / "config", sites={"a.json": SITE, "b.json": SITE})
    errors = validate_boot(root, env=ENV)
    assert len(errors) == 1
    assert "중복" in errors[0].message and errors[0].where == "sites/b.json"


def test_기동_검증은_스스로_죽지_않는다(tmp_path):
    """검증기가 raise하면 '통과했는지 실패했는지'조차 알 수 없게 된다."""
    root = tmp_path / "없는디렉터리"
    errors = validate_boot(root, env=ENV)
    assert errors and all(e.message for e in errors)


def test_보고_문자열이_사람이_읽을_수_있다(tmp_path):
    root = _tree(tmp_path / "config", sites={"a.json": {"site": {"gbm": "mx", "fct": "gumi"},
                                                        "infra": {}}})
    line = str(validate_boot(root, env=ENV)[0])
    assert line.startswith("[sites/a.json]")
