import pytest
from pydantic import ValidationError
from src.knowledge.topology import DataRef, load_topology, topology_problems

COMMON = """
services:
  twin-aggregator:
    code: { repo: twin-services, path: services/aggregator }
    reads:  [ { kind: kafka, topic: "edge.raw.{line}" } ]
    writes: [ { kind: mongo, collection: twin_state } ]
  twin-api:
    reads:  [ { kind: mongo, collection: twin_state } ]
derivations:
  "rest:/api/v1/lines/{line}/oee":
    inputs: [ { kind: mongo, collection: twin_state } ]
    via: twin-api
    key: line
"""


def _tree(tmp_path, common=COMMON, site=""):
    d = tmp_path / "knowledge" / "topology"
    (d / "mx").mkdir(parents=True)
    (d / "common.yaml").write_text(common, encoding="utf-8")
    (d / "mx" / "gumi.yaml").write_text(site, encoding="utf-8")
    return tmp_path / "knowledge"


def test_kind별_필수_필드():
    DataRef.model_validate({"kind": "kafka", "topic": "t"})
    with pytest.raises(ValidationError):
        DataRef.model_validate({"kind": "kafka", "collection": "c"})  # 잘못된 필드


def test_로드와_사이트_오버라이드(tmp_path):
    root = _tree(tmp_path, site="""
services:
  twin-aggregator:
    reads: [ { kind: kafka, topic: "edge.raw.gumi.{line}" } ]
""")
    topo = load_topology(root, "mx", "gumi")
    assert topo.services["twin-aggregator"].reads[0].locator == "kafka:edge.raw.gumi.{line}"
    assert topo.derivations["rest:/api/v1/lines/{line}/oee"].key == "line"
    assert "mongo:twin_state" in topo.locators()


def test_끊긴_via는_정합성_오류(tmp_path):
    root = _tree(tmp_path, site="""
derivations:
  "rest:/api/v1/lines/{line}/oee": { inputs: [ { kind: mongo, collection: twin_state } ],
                                     via: ghost-service }
""")
    topo = load_topology(root, "mx", "gumi")
    problems = topology_problems(topo)
    assert any("ghost-service" in p for p in problems)
