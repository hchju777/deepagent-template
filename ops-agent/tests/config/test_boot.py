"""기동 검증 — 문제를 전부 모아서 돌려주고, 스스로는 절대 죽지 않는다."""
import json

from src.boot import validate_boot

GOOD_LAYERS = {
    "gbm/common.json": {"infra": {"redis": {"db": 0}}},
    "fct/gumi/mx.json": {"infra": {"redis": {"url": "redis://gumi:6379",
                                             "password": "${MX_GUMI_REDIS_PASSWORD}"}}},
}
ENV = {"MX_GUMI_REDIS_PASSWORD": "hunter2"}


def _tree(root, *, app=None, registry=None, layers=None):
    root.mkdir(parents=True, exist_ok=True)
    if app is not None:
        (root / "app.json").write_text(json.dumps(app), encoding="utf-8")
    if registry is not None:
        (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    for relative, data in (GOOD_LAYERS if layers is None else layers).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


def _default(root, **kw):
    return _tree(root, app=kw.pop("app", {"timezone": "Asia/Seoul"}),
                 registry=kw.pop("registry", {"sites": [{"gbm": "mx", "fct": "gumi"}]}), **kw)


def test_정상_트리는_문제가_없다(tmp_path):
    assert validate_boot(_default(tmp_path / "c"), env=ENV) == []


def test_문제가_셋이면_셋_다_보고한다(tmp_path):
    """하나씩 던지면 사람은 고치고-돌리기를 세 번 한다. 그게 이 함수의 존재 이유다."""
    root = _default(
        tmp_path / "c",
        app={"timezone": "없는/타임존"},
        registry={"sites": [{"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "suwon"}]},
        layers={"fct/gumi/mx.json": {"infra": {"redis": {"url": "http://나쁜스킴"}}},
                "fct/suwon/mx.json": {"infra": {"redis": {"url": "redis://s:6379",
                                                          "password": "${없는키}"}}}})
    wheres = sorted(e.where for e in validate_boot(root, env=ENV))
    assert wheres == ["app.json", "mx/gumi", "mx/suwon"]


def test_app이_깨져도_사이트_검증은_계속한다(tmp_path):
    root = _default(tmp_path / "c", app={"timezone": "없는/타임존"},
                    layers={"fct/gumi/mx.json": {"infra": {}}})
    assert sorted(e.where for e in validate_boot(root, env=ENV)) == ["app.json", "mx/gumi"]


def test_registry가_없으면_말한다(tmp_path):
    root = _tree(tmp_path / "c", app={"timezone": "Asia/Seoul"})
    assert [e.where for e in validate_boot(root, env=ENV)] == ["registry.json"]


def test_사이트가_하나도_없으면_말한다(tmp_path):
    root = _default(tmp_path / "c", registry={"sites": []})
    errors = validate_boot(root, env=ENV)
    assert errors and "사이트가 하나도 없다" in errors[0].message


def test_registry의_중복을_잡는다(tmp_path):
    # 같은 조합이 두 번 있으면 어느 쪽 enabled가 이기는지 순서에 달린다.
    root = _default(tmp_path / "c", registry={"sites": [{"gbm": "mx", "fct": "gumi"},
                                                        {"gbm": "mx", "fct": "gumi"}]})
    assert any("중복된 사이트" in e.message for e in validate_boot(root, env=ENV))


def test_비활성_사이트는_검증하지_않는다(tmp_path):
    # 아직 config를 안 만든 사이트를 enabled=false로 등록해 둘 수 있어야 한다.
    root = _default(tmp_path / "c",
                    registry={"sites": [{"gbm": "mx", "fct": "gumi"},
                                        {"gbm": "mx", "fct": "미개설", "enabled": False}]})
    assert validate_boot(root, env=ENV) == []


def test_기동_검증은_스스로_죽지_않는다(tmp_path):
    """검증기가 raise하면 '통과했는지 실패했는지'조차 알 수 없게 된다."""
    errors = validate_boot(tmp_path / "없는디렉터리", env=ENV)
    assert errors and all(e.message for e in errors)


def test_보고_문자열이_사람이_읽을_수_있다(tmp_path):
    root = _default(tmp_path / "c", layers={"fct/gumi/mx.json": {"infra": {}}})
    assert str(validate_boot(root, env=ENV)[0]).startswith("[mx/gumi]")


# ── 순찰 점검 ────────────────────────────────────────────────────────

REST_LAYER = {
    "gbm/common.json": {"infra": {"redis": {"db": 0}}},
    "gbm/mx.json": {"infra": {"rest": {"entries": {
        "summary_badge": {"method": "POST", "path": "/summary/badge",
                          "params": {"line_code": {"type": "list", "required": False}}}}}}},
    "fct/gumi/mx.json": {"infra": {
        "redis": {"url": "redis://gumi:6379", "password": "${MX_GUMI_REDIS_PASSWORD}"},
        "rest": {"base_url": "http://gumi:8080"}}},
}


def _with_patrol(root, probes):
    layers = dict(REST_LAYER)
    layers["gbm/common.json"] = {
        **REST_LAYER["gbm/common.json"],
        "patrol": {"checks": {"badge_all_zero": {
            "concern": "operation", "probes": probes,
            "rule": "items_all_zero",
            "params": {"items": {"probe": "badge", "path": "response"},
                       "identity": ["group", "title"],
                       "counts": ["alarm", "caution", "normal"]}}}}}
    return _default(root, layers=layers)


def test_점검이_가리키는_REST_등재_항목이_없으면_기동이_막는다(tmp_path):
    """**오타는 런타임에 고칠 수 없다.**

    `summary_badge`를 `summary_bagde`로 적으면 매 순찰마다 실패하는데, 그 실패는
    `unreachable`로 흡수되어 "대상이 안 붙는다"처럼 보인다. 진짜 장애와 구별이 안 되고,
    28사이트에서는 그런 줄 하나가 묻힌다.
    """
    root = _with_patrol(tmp_path / "c", {"badge": {
        "action": "rest.query", "params": {"entry": "summary_bagde", "params": {}}}})
    errors = validate_boot(root, env=ENV)
    assert [e for e in errors if "등재되지 않은 REST 항목" in e.message], errors


def test_점검의_params가_등재_스키마를_벗어나면_기동이_막는다(tmp_path):
    root = _with_patrol(tmp_path / "c", {"badge": {
        "action": "rest.query",
        "params": {"entry": "summary_badge", "params": {"line_codes": ["P222"]}}}})
    errors = validate_boot(root, env=ENV)
    assert [e for e in errors if "line_codes" in e.message], errors


def test_올바른_점검은_기동을_통과한다(tmp_path):
    root = _with_patrol(tmp_path / "c", {"badge": {
        "action": "rest.query",
        "params": {"entry": "summary_badge", "params": {"line_code": ["P222"]}}}})
    assert validate_boot(root, env=ENV) == []
