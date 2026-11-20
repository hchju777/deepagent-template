"""`tools/trace_stats.py` — 사내에서 한 줄로 돌리는 끝점 추적 요약. overlay의 모양(11c·11b가 쓰는
끝점 노드의 traced/chain/gaps, origin `trace`인 reads 엣지)에서 일곱 줄을 만든다. 이름은 지어낸 것이다."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "trace_stats", Path(__file__).resolve().parents[1] / "tools" / "trace_stats.py")
trace_stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trace_stats)

OVERLAY = {
    "nodes": [
        {"id": "endpoint_a", "type": "endpoint", "traced": "ok",
         "chain": ["api/r.py:L3 view_a", "api/s.py:L8 Svc.total", "api/d.py:L4 Dao.count"],
         "gaps": ["api/s.py:L9 get: 받는 쪽 미상, 후보 3개 — 안 따라간다"]},
        {"id": "endpoint_b", "type": "endpoint", "traced": "ok",
         "chain": ["api/r.py:L9 view_b"],
         "gaps": ["api/r.py:L10 getattr로 고른 대상은 못 따라간다 — 리드가 code.read로 본다",
                  "api/r.py:L11 깊이 상한 6에서 멈춤: Dao.deep → x"]},
        {"id": "endpoint_c", "type": "endpoint"},
        {"id": "collection_x", "type": "collection"},
    ],
    "links": [
        {"source": "endpoint_a", "target": "collection_x", "relation": "reads", "origin": "trace", "confidence": "EXTRACTED", "via": "literal"},
        {"source": "endpoint_a", "target": "collection_x", "relation": "reads", "origin": "trace", "confidence": "INFERRED", "via": "alias"},
        {"source": "svc", "target": "collection_x", "relation": "reads", "origin": "code", "confidence": "EXTRACTED"},
    ],
}


def test_일곱_줄에_끝점_수와_막힌_끝점과_gap_종류가_있다():
    lines = trace_stats.stats(OVERLAY)
    assert len(lines) == 8 and [l[0] for l in lines] == list("12345678")
    assert lines[0].startswith("1 끝점 3 ") and "자원까지 1" in lines[0] and "막힘 1" in lines[0]
    assert "'EXTRACTED/literal': 1" in lines[1] and "'INFERRED/alias': 1" in lines[1]
    assert "최소 1 · 중간 3 · 최대 3" in lines[2]
    assert "받는 쪽 미상, 후보 N개" in lines[3] and "'get'" in lines[4]
    assert "'Dao'" in lines[5] and "(모듈 함수)" in lines[5]
    assert lines[6].startswith("7 막힌 끝점 gap") and "getattr" in lines[6] and "최소 1 · 중간 1 · 최대 1" in lines[6]
    assert lines[7].startswith("8 gap 예시 ") and "get: 받는 쪽 미상, 후보 3개 — 안 따라간다" in lines[7]


def test_추적_결과가_없어도_안_죽는다():
    lines = trace_stats.stats({"nodes": [{"id": "e", "type": "endpoint"}], "links": []})
    assert lines[0].startswith("1 끝점 1 ") and "막힘 0" in lines[0] and "사슬 길이 -" in lines[2]
    assert lines[7] == "8 gap 예시 -"


def test_파일이_없으면_말하고_1을_돌려준다(capsys, tmp_path):
    assert trace_stats.main(["x", str(tmp_path / "none.json")]) == 1
    assert "code graph" in capsys.readouterr().out
