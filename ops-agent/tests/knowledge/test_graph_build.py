"""그래프 번들 — graphify 심볼 그래프 + 흐름 오버레이를 커밋에 박는다."""
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from src.knowledge import graph_build as gb

T0 = datetime(2026, 9, 22, 9, 0)


def test_git_grep_출력을_읽는다():
    text = ("# dt-core @ ab12cd34ef56\nab12cd34ef56:sink/writer.py:10:    mongo[x].insert_many(batch)\n\n"
            "config/gbm/mx.json:4:  \"alarm\": \"alarm_events\"\n")
    hits = gb.parse_grep("dt-core", "ab12cd34ef56", text)    # 커밋 접두가 있어도 없어도
    assert [(h.file, h.line) for h in hits] == [("sink/writer.py", 10), ("config/gbm/mx.json", 4)]
    assert hits[0].text.strip() == "mongo[x].insert_many(batch)" and hits[0].commit == "ab12cd34ef56"


def test_합칠_때_노드는_id로_엣지는_이어_붙인다():
    overlay = {"nodes": [{"id": "service_sink"}], "links": [{"source": "a", "target": "b"}]}
    symbols = {"nodes": [{"id": "dt_core_sink_writer_run"}, {"id": "service_sink", "label": "덮으면 안 됨"}],
               "links": [{"source": "c", "target": "d"}]}
    merged = gb.merge_graphs(overlay, [symbols])
    assert [n["id"] for n in merged["nodes"]] == ["service_sink", "dt_core_sink_writer_run"]
    assert "label" not in merged["nodes"][0] and len(merged["links"]) == 2


def test_번들을_쓰고_읽고_커밋을_대조한다(tmp_path):
    meta = gb.GraphMeta(gbm="mx", fct="gumi", commits={"dt-core": "a" * 40}, built_at="t", graphify="없음")
    gb.write_bundle(tmp_path, overlay={"nodes": [], "links": []}, merged={"nodes": [], "links": []}, meta=meta)
    got = gb.read_bundle(tmp_path)
    assert got is not None and got[1].commits == {"dt-core": "a" * 40}
    assert gb.check_bundle(got[1], {"dt-core": "a" * 40}) == []
    problems = gb.check_bundle(got[1], {"dt-core": "b" * 40, "dt-api": "c" * 40})
    assert len(problems) == 2
    assert any("code sync" in p for p in problems) and any("이 레포가 없다" in p for p in problems)


def test_깨진_번들은_None이다(tmp_path):
    (tmp_path / "graph.json").write_text("{", encoding="utf-8")
    assert gb.read_bundle(tmp_path) is None
    assert gb.read_bundle(tmp_path / "없음") is None


def test_graphify가_없으면_건너뛰고_이유를_남긴다(tmp_path):
    status, detail, path = gb.run_graphify(tmp_path, None)
    assert status == "skipped" and "GRAPHIFY_BIN" in detail and path is None


def test_graphify_바이너리는_env가_PATH보다_먼저다(monkeypatch, tmp_path):
    fake = tmp_path / "graphify"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("GRAPHIFY_BIN", str(fake))
    assert gb.find_graphify() == str(fake)
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는것"))
    assert gb.find_graphify() is None, "가리킨 것이 없으면 PATH로 조용히 넘어가지 않는다"


def test_graphify가_죽으면_failed로_흡수한다(tmp_path):
    bad = tmp_path / "graphify"
    bad.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
    bad.chmod(0o755)
    status, detail, _ = gb.run_graphify(tmp_path, str(bad))
    assert status == "failed" and "종료코드 3" in detail and "boom" in detail


@pytest.mark.skipif(not gb.find_graphify(), reason="graphify가 없다 (GRAPHIFY_BIN 또는 PATH)")
def test_진짜_graphify로_코드만_추출한다(tmp_path):
    """**실제 소비자로 확인한다.** 라벨링(LLM)을 안 부르고, graph.json이 우리 스키마와 합쳐진다."""
    (tmp_path / "a.py").write_text("def f():\n    return g()\n\n\ndef g():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True, encoding="utf-8")
    status, detail, path = gb.run_graphify(tmp_path, gb.find_graphify())
    assert status == "ok", detail
    symbols = json.loads(path.read_text(encoding="utf-8"))
    assert symbols["nodes"] and all("id" in n for n in symbols["nodes"])
    report = (tmp_path / "graphify-out" / "GRAPH_REPORT.md").read_text(encoding="utf-8")
    assert "Token cost: 0 input" in report, "라벨링 LLM 호출이 나갔다"
    merged = gb.merge_graphs({"nodes": [{"id": "service_x", "label": "x"}], "links": []}, [symbols])
    assert len(merged["nodes"]) == len(symbols["nodes"]) + 1
