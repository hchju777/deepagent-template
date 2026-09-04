from datetime import datetime

from src.application.briefing import build_briefing, upstream_slice
from src.domain.case import Case
from src.knowledge.topology import Topology

TOPO = Topology.model_validate({
    "services": {
        "edge-gateway":    {"writes": [{"kind": "kafka", "topic": "edge.raw"}]},
        "twin-aggregator": {"reads": [{"kind": "kafka", "topic": "edge.raw"}],
                            "writes": [{"kind": "mongo", "collection": "twin_state"}]},
        "twin-api":        {"reads": [{"kind": "mongo", "collection": "twin_state"}],
                            "writes": [{"kind": "rest", "endpoint": "/oee"}]},
        "unrelated":       {"writes": [{"kind": "redis", "key": "other:*"}]},
    },
    "derivations": {
        "rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                      "via": "twin-api", "key": "line"},
        "mongo:twin_state": {"inputs": [{"kind": "kafka", "topic": "edge.raw"}],
                             "via": "twin-aggregator"},
    }})


def test_상류_슬라이스는_사슬만_담고_무관_서비스는_뺀다():
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=3)
    assert set(sliced.services) == {"twin-api", "twin-aggregator", "edge-gateway"}
    assert set(sliced.derivations) == {"rest:/oee", "mongo:twin_state"}


def test_깊이_제한이_사슬을_자른다():
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=1)
    assert "twin-api" in sliced.services
    assert "edge-gateway" not in sliced.services


def test_브리핑은_빈_섹션을_명시한다():
    case = Case(id="c", gbm="mx", fct="gumi", origin="patrol",
                symptom="OEE 512%", t0=datetime(2026, 9, 3, 8, 0))
    text = build_briefing(case, upstream_slice(TOPO, "rest:/oee"))
    assert "OEE 512%" in text and "rest:/oee" in text and "twin-aggregator" in text
    assert "없음" in text            # rules/history/docs 미제공 → 명시


def _case(concern):
    return Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", concern=concern,
                symptom="OEE 512%", t0=datetime(2026, 9, 3, 8, 0),
                target_locator="rest:/oee")


def test_브리핑이_concern별로_다른_방향을_준다():
    # 어디를 먼저 볼지를 말할 뿐 판정을 대신하지 않는다 — 힌트가 결론을 지시하면
    # 그건 우리가 판정을 코드에 박은 것이다(규율 6).
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=3)
    system = build_briefing(_case("system"), sliced)
    operation = build_briefing(_case("operation"), sliced)
    assert system != operation
    assert "현장" in operation and "현장" not in system
