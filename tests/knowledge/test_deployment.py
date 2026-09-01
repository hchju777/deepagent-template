from src.knowledge.deployment import load_deployment
from src.knowledge.digest import canonical_digest


def test_digest는_키순서와_공백에_불변():
    a = {"b": 1, "a": [1, 2]}
    b = {"a": [1, 2], "b": 1}
    assert canonical_digest(a) == canonical_digest(b)
    assert canonical_digest(a) != canonical_digest({"a": [2, 1], "b": 1})


def test_deployment_로드와_부재(tmp_path):
    d = tmp_path / "knowledge" / "deployment" / "mx"
    d.mkdir(parents=True)
    (d / "gumi.yaml").write_text(
        "services:\n  twin-aggregator: { repo: twin-services, commit: a3f9c2 }\n",
        encoding="utf-8")
    dep = load_deployment(tmp_path / "knowledge", "mx", "gumi")
    assert dep.services["twin-aggregator"].commit == "a3f9c2"
    assert load_deployment(tmp_path / "knowledge", "mx", "suwon") is None
