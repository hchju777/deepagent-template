import json

from src.__main__ import main
from tests.test_boot import ENV, _tree   # 트리 픽스처 재사용


def test_registry_출력(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["registry", "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out
    assert code == 0 and "mx/gumi" in out and "mx/off" in out


def test_config_show는_비밀을_마스킹한다(tmp_path, capsys, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr("os.environ",
                        {**ENV, "MX_REDIS_PW": "hunter2"})
    # redis에 password 참조 추가
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["redis"]["password"] = "${MX_REDIS_PW}"
    gbm.write_text(json.dumps(data), encoding="utf-8")

    code = main(["config", "show", "--gbm", "mx", "--fct", "gumi",
                 "--config-root", str(tmp_path / "config")])
    out = capsys.readouterr().out
    assert code == 0
    assert "hunter2" not in out and "**********" in out
    assert "gbm/mx" in out                       # 출처 표시


def test_knowledge_validate_실패는_exit_1(tmp_path, capsys, monkeypatch):
    _tree(tmp_path, check_target="rest:/ghost")
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["knowledge", "validate",
                 "--config-root", str(tmp_path / "config"),
                 "--repo-root", str(tmp_path)])
    assert code == 1
    assert "rest:/ghost" in capsys.readouterr().err


def test_깨진_registry는_stderr와_exit_1(tmp_path, capsys, monkeypatch):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "registry.json").write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr("os.environ", dict(ENV))
    code = main(["registry", "--config-root", str(tmp_path / "config")])
    assert code == 1
    assert "JSON 파싱 실패" in capsys.readouterr().err
