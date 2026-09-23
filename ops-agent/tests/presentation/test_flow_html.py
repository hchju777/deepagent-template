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
