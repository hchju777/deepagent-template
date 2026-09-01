import json

from src.boot import validate_boot

ENV = {"MX_REDIS_URL": "redis://g:6379"}


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(tmp_path, *, check_target="rest:/oee", repo_name="twin-services"):
    _write(tmp_path, "config/app.json", json.dumps(
        {"llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}}}))
    _write(tmp_path, "config/registry.json", json.dumps(
        {"sites": [{"gbm": "mx", "fct": "gumi"},
                   {"gbm": "mx", "fct": "off", "enabled": False}]}))
    _write(tmp_path, "config/gbm/mx.json", json.dumps(
        {"target": {"redis": {"url": "${MX_REDIS_URL}"},
                    "code": {"repos": [{"name": "twin-services", "path": "/r"}]}},
         "patrol": {"checks": {"c1": {"judge": "rule",
                                      "schedule": {"interval": "5m"},
                                      "target": check_target}}}}))
    _write(tmp_path, "knowledge/topology/common.yaml", f"""
services:
  twin-api:
    code: {{ repo: {repo_name}, path: api }}
    writes: [ {{ kind: rest, endpoint: /oee }} ]
derivations:
  "rest:/oee": {{ inputs: [ {{ kind: mongo, collection: twin_state }} ], via: twin-api }}
""")
    return tmp_path


def test_정상_트리는_통과하고_disabled_사이트는_건너뛴다(tmp_path):
    _tree(tmp_path)   # mx/off 사이트는 config 파일이 없지만 disabled라 검사 안 함
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []


def test_해석_안되는_룰_타깃은_거부(tmp_path):
    _tree(tmp_path, check_target="rest:/ghost")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("rest:/ghost" in e.problem for e in errors)


def test_토폴로지의_미등록_repo는_거부(tmp_path):
    _tree(tmp_path, repo_name="ghost-repo")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("ghost-repo" in e.problem for e in errors)


def test_오류는_전부_모인다(tmp_path):
    _tree(tmp_path, check_target="rest:/ghost", repo_name="ghost-repo")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert len(errors) >= 2


def test_깨진_토폴로지는_크래시가_아니라_오류로_모인다(tmp_path):
    _tree(tmp_path)
    # 토폴로지를 스키마 위반(kafka인데 collection 선언)으로 덮어쓴다
    _write(tmp_path, "knowledge/topology/common.yaml", """
services:
  twin-api:
    writes: [ { kind: kafka, collection: wrong_field } ]
""")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("토폴로지 로드 실패" in e.problem for e in errors)
