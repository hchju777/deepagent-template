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


def test_문맥은_줄_번호로_앞뒤_한_줄만_붙는다():
    """`git grep -n -C1 … HEAD`의 실제 출력(측정, git 2.43)을 그대로 먹인다. 경로에 `-`가
    있어도(`al-arms.py`·`mx-1.json`) 문맥 줄의 경로를 매치 줄에서 얻으므로 안 헷갈린다.
    묶음 안에 세 줄이 있어도 붙는 것은 앞뒤 한 줄뿐이다."""
    text = ("HEAD:api/al-arms.py:1:def recent_alarms(cfg, mongo, since):\n"
            "HEAD:api/al-arms.py:2:    coll = cfg[\"mongodb_collection\"][\"alarm\"][\"collection\"]\n"
            "HEAD:api/al-arms.py-3-    return list(mongo[coll].find({}))\n"
            "--\n"
            "HEAD:api/al-arms.py-7-    x = 1\n"
            "HEAD:api/al-arms.py:8:    y = \"alarm\"\n"
            "HEAD:api/al-arms.py-9-    z = 2\n"
            "--\n"
            "HEAD:cfg-dir/mx-1.json-1-{\n"
            "HEAD:cfg-dir/mx-1.json:2:  \"mongodb_collection\": {\"alarm\": {\"collection\": \"alarm_events\"}}\n"
            "HEAD:cfg-dir/mx-1.json-3-}\n"
            "--\n"
            "HEAD:top.txt:1:alarm\n")
    hits = gb.parse_grep("dt-api", "HEAD", text)
    assert [(h.file, h.line) for h in hits] == [("api/al-arms.py", 1), ("api/al-arms.py", 2),
                                                ("api/al-arms.py", 8), ("cfg-dir/mx-1.json", 2),
                                                ("top.txt", 1)]
    ctx = {(h.file, h.line): h.context for h in hits}
    assert ctx[("api/al-arms.py", 2)] == ("def recent_alarms(cfg, mongo, since):\n"
                                          "    return list(mongo[coll].find({}))")
    assert ctx[("api/al-arms.py", 1)] == '    coll = cfg["mongodb_collection"]["alarm"]["collection"]'
    assert ctx[("api/al-arms.py", 8)] == "    x = 1\n    z = 2"
    assert ctx[("cfg-dir/mx-1.json", 2)] == "{\n}"
    assert ctx[("top.txt", 1)] == ""


def test_문맥_없이_이웃한_줄_번호는_파일이_다르면_안_섞인다():
    """`-C` 없는 출력에는 `--`가 없어 파일 여럿이 한 묶음이다. 줄 번호만 보면 a.py 10과
    b.py 11이 이웃이 된다."""
    hits = gb.parse_grep("r", "c", "a.py:10:x = alarm\nb.py:11:y = alarm\n")
    assert [h.context for h in hits] == ["", ""]


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


def _dying_graphify(dir_: Path) -> Path:
    """종료코드 3으로 죽는 가짜 graphify. Windows는 셸뱅을 모르고 확장자 없는 파일을 실행
    자체를 거부한다(WinError 193, 사내 측정) — 거기서는 `.cmd`다. 프로덕션은 pip가 만든
    `graphify.exe` 런처를 쓰므로 이 분기는 테스트에만 있다."""
    if os.name == "nt":
        bad = dir_ / "graphify.cmd"
        bad.write_text("@echo boom 1>&2\r\n@exit /b 3\r\n", encoding="utf-8", newline="")
    else:
        bad = dir_ / "graphify"
        bad.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
        bad.chmod(0o755)
    return bad


def test_graphify가_죽으면_failed로_흡수한다(tmp_path):
    status, detail, _ = gb.run_graphify(tmp_path, str(_dying_graphify(tmp_path)))
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
