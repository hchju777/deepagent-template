"""흐름 그래프 사람용 html — 외부 참조 0, 레포색 고정, 데이터 안전."""
import json
import re

from src.presentation import flow_html

OVERLAY = {
    "nodes": [
        {"id": "service_api", "label": "api", "type": "service", "repo": "dt-api", "role": "읽는다"},
        {"id": "service_sink", "label": "sink", "type": "service", "repo": "dt-core", "role": "저장한다"},
        {"id": "repo_dt_core", "label": "dt-core", "type": "repo"},
        {"id": "collection_alarm_events", "label": "alarm_events", "type": "collection",
         "key_path": "mongodb_collection.alarm"},
    ],
    "links": [
        {"source": "service_sink", "target": "repo_dt_core", "relation": "runs", "origin": "topology"},
        {"source": "service_sink", "target": "collection_alarm_events", "relation": "writes",
         "confidence": "INFERRED", "origin": "code", "source_file": "sink/w.py", "source_location": "L10",
         "text": 'mongo["alarm_events"].insert_many(b)  # </script><b>'},
        {"source": "service_api", "target": "collection_alarm_events", "relation": "reads",
         "confidence": "EXTRACTED", "origin": "code", "source_file": "api/q.py", "source_location": "L3"},
    ],
}


def _page():
    return flow_html.render(OVERLAY, title='mx/gumi <"x">', built_at="2026-09-23T10:00",
                            commits={"dt-core": "abc"})


def test_외부_참조가_없다():
    """graphify의 graph.html은 vis-network를 unpkg.com에서 받아 사내망에서 빈 화면이다.
    팀원에게 파일 하나만 건네려면 모든 것이 안에 있어야 한다."""
    page = _page()
    assert not re.search(r'(src|href)="https?://', page)
    assert "<script" in page and "<style>" in page


def test_레포색은_이름순으로_고정이고_runs는_안_그린다():
    page = _page()
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
                      .replace("<\\/", "</"))
    assert data["colors"] == {"dt-api": flow_html.REPO_COLORS[0], "dt-core": flow_html.REPO_COLORS[1]}
    assert {e["relation"] for e in data["links"]} == {"writes", "reads"}
    assert data["links"][0]["file"] == "sink/w.py" and data["links"][0]["line"] == "L10"


def test_데이터_속_닫는_태그와_제목이_문서를_못_끊는다():
    """근거 줄 원문에 `</script>`가 있으면 그대로 넣을 때 문서가 거기서 끝난다."""
    page = _page()
    body = page.split('<script id="data"', 1)[1]
    assert "</script><b>" not in body.split("</script>", 1)[0]     # 데이터 블록 안에는 닫는 태그가 없다
    assert "<\\/script><b>" in page
    assert '<title>mx/gumi &lt;&quot;x&quot;&gt; · 데이터 흐름</title>' in page


def test_아홉_번째_레포부터는_회색이다():
    """색을 돌려 쓰면 두 레포가 같은 색이 된다 — 그보다 회색이 낫다."""
    nodes = [{"id": f"service_s{i}", "label": f"s{i}", "type": "service", "repo": f"r{i:02d}"} for i in range(10)]
    page = flow_html.render({"nodes": nodes, "links": []}, title="t", built_at="", commits={})
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert data["colors"]["r08"] == flow_html.OTHER_COLOR and data["colors"]["r09"] == flow_html.OTHER_COLOR
    assert len({data["colors"][f"r{i:02d}"] for i in range(8)}) == 8


def test_끝점_종류와_serves_엣지를_그린다():
    overlay = {"nodes": [{"id": "service_api", "label": "api", "type": "service", "repo": "dt-api"},
                         {"id": "endpoint_x", "label": "/summary/badge", "type": "endpoint", "method": "POST"}],
               "links": [{"source": "service_api", "target": "endpoint_x", "relation": "serves", "origin": "code",
                          "confidence": "EXTRACTED", "source_file": "api/r.py", "source_location": "L5"}]}
    page = flow_html.render(overlay, title="t", built_at="b", commits={})
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
                      .replace("<\\/", "</"))
    assert "endpoint" in data["kind_order"] and data["kind_label"]["endpoint"]
    assert [e["relation"] for e in data["links"]] == ["serves"]
    assert "'serves'" in page, "그리는 관계 목록에 serves가 있어야 선이 보인다"


def _data(page):
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
                      .replace("<\\/", "</"))


UPSTREAM = {
    "nodes": [{"id": "service_api", "label": "api", "type": "service", "repo": "dt-api"},
              {"id": "service_sink", "label": "sink", "type": "service", "repo": "dt-core"},
              {"id": "service_other", "label": "other", "type": "service", "repo": "dt-core"},
              {"id": "endpoint_x", "label": "/x", "type": "endpoint"},
              {"id": "rediskey_k", "label": "SITE:k:{line}", "type": "rediskey"},
              {"id": "topic_t", "label": "mx.t", "type": "topic"},
              {"id": "collection_c", "label": "c", "type": "collection"}],
    "links": [{"source": "service_api", "target": "endpoint_x", "relation": "serves", "origin": "code",
               "confidence": "EXTRACTED", "source_file": "a.py", "source_location": "L1"},
              {"source": "endpoint_x", "target": "rediskey_k", "relation": "reads", "origin": "trace",
               "confidence": "INFERRED", "via": "key", "source_file": "a.py", "source_location": "L2"},
              {"source": "service_sink", "target": "rediskey_k", "relation": "writes", "origin": "code",
               "confidence": "INFERRED", "source_file": "s.py", "source_location": "L9"},
              {"source": "service_sink", "target": "topic_t", "relation": "consumes", "origin": "config",
               "confidence": "EXTRACTED", "source_file": "config/gbm/mx.json", "source_location": "L3"},
              {"source": "service_other", "target": "collection_c", "relation": "writes", "origin": "code",
               "confidence": "INFERRED", "source_file": "o.py", "source_location": "L4"},
              {"source": "service_api", "target": "collection_c", "relation": "reads", "origin": "code",
               "confidence": "EXTRACTED", "source_file": "a.py", "source_location": "L7"}],
}


def test_추적_엣지는_데이터에_싣되_기본은_숨기고_초점에서만_켠다():
    """끝점 → 자원 읽기는 자원 열 안의 선이라 늘 그리면 지도를 흐린다 — 데이터에는 싣고 CSS로 숨겼다가
    초점(클릭)일 때만 켠다. 끝점을 누르면 "무엇을 읽나"가 보여야 한다는 것이 사람의 첫 요구였다."""
    page = flow_html.render(UPSTREAM, title="t", built_at="b", commits={})
    data = _data(page)
    trace = [e for e in data["links"] if e["origin"] == "trace"]
    assert [(e["source"], e["target"], e["via"]) for e in trace] == [("endpoint_x", "rediskey_k", "key")]
    assert ".edge.trace{display:none}" in page and "svg.focused .edge.trace.on{display:inline}" in page


def test_끝점의_상류_3홉을_계산해_싣는다():
    """끝점 ← 읽는 자원(1) ← 그 자원을 쓰는 서비스(2) ← 그 서비스가 읽는 것(3). 서빙 서비스는 1홉의 맥락.
    관계없는 것(other → c, api → c)은 안 든다. 그림과 오른쪽 패널이 이 표로 홉을 가른다."""
    hops = {(e["source"], e["relation"], e["target"]): e["hop"] for e in flow_html.upstream(UPSTREAM, "endpoint_x")}
    assert hops == {("service_api", "serves", "endpoint_x"): 1,
                    ("endpoint_x", "reads", "rediskey_k"): 1,
                    ("service_sink", "writes", "rediskey_k"): 2,
                    ("service_sink", "consumes", "topic_t"): 3}
    data = _data(flow_html.render(UPSTREAM, title="t", built_at="b", commits={}))
    assert set(data["upstream"]) == {"endpoint_x"}          # 추적 읽기가 있는 끝점만
    assert {e["hop"] for e in data["upstream"]["endpoint_x"]} == {1, 2, 3}
    assert flow_html.upstream(UPSTREAM, "collection_c") == []
