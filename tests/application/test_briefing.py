from datetime import datetime

from src.application.briefing import (build_briefing, render_deployment,
                                      render_rules, upstream_slice)
from src.config.schema_site import CheckConfig
from src.domain.case import Case
from src.knowledge.deployment import Deployment
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


def _check(target, **kw):
    return CheckConfig.model_validate(
        {"judge": "rule", "schedule": {"interval": "10m"}, "target": target,
         "params": {"rule": "range", "min": 0, "max": 100}, **kw})


def test_슬라이스_밖의_점검은_브리핑에_안_실린다():
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=3)
    text = render_rules({"api.oee_range": _check("rest:/oee"),
                         "redis.other": _check("redis:other:*")},
                        slice_=sliced, target_locator="rest:/oee")
    assert "api.oee_range" in text
    assert "redis.other" not in text


def test_상류_입력_locator를_보는_점검도_실린다():
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=3)
    text = render_rules({"raw.freshness": _check("kafka:edge.raw")},
                        slice_=sliced, target_locator="rest:/oee")
    assert "raw.freshness" in text


def test_적용_룰은_점검마다_한_줄로_접힌다():
    check = CheckConfig.model_validate(
        {"judge": "rule", "schedule": {"interval": "10m"}, "target": "rest:/oee",
         "params": {"rule": "range", "note": "line1\nline2"}})
    text = render_rules({"c": check}, slice_=Topology(), target_locator="rest:/oee")
    assert len(text.splitlines()) == 1


def test_걸리는_점검이_없으면_빈_문자열이다():
    assert render_rules({}, slice_=Topology(), target_locator=None) == ""


def test_슬라이스_서비스의_배포_커밋만_싣는다():
    dep = Deployment.model_validate({"services": {
        "twin-api": {"repo": "twin", "commit": "abc123"},
        "unrelated": {"repo": "x", "commit": "def456"}}})
    sliced = upstream_slice(TOPO, "rest:/oee", max_depth=3)
    text = render_deployment(dep, slice_=sliced)
    assert "abc123" in text
    assert "def456" not in text


def test_배포_매핑이_없으면_없음이_아니라_미검증이다():
    text = render_deployment(None, slice_=Topology())
    assert "미검증" in text
