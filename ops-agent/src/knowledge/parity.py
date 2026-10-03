"""추적기와 인덱스의 대조(11d 6c-2) — 엔진을 하나로 모으기 전에 둘이 어디서 다르게 말하나를 숫자로.

리드가 받는 `code.trace` 사슬은 11b 추적기가 끝점에서 앞으로 걸어 만든 것이다. 인덱스로도 같은 끝점의 핸들러(사슬
첫 걸음을 품는 함수)에서 **같은 깊이**로 호출·디스패치를 따라가 닿는 자원을 모은다. 깊이를 맞추지 않으면 "인덱스만"이
대부분 더 깊이 간 몫이 되어 대조가 뜻을 잃는다. 다르면 끝점 노드에 적어 `code.trace` 출력에 나오게 하고 `code
status`가 센다 — 패리티가 확인되면 추적기를 뺀다(decisions ⑱). 던지지 않는다.
"""
from __future__ import annotations

import re

from src.knowledge import query as qy
from src.knowledge.index import Index
from src.knowledge.trace import MAX_DEPTH

_FIRST = re.compile(r"^(.*):L(\d+) ")
_NOT_RESOURCE = ("endpoint", "service", "repo")


def check(overlay: dict, index: Index, graph: qy.Graph) -> dict:
    nodes = [dict(n) for n in overlay["nodes"]]
    by_id = {n["id"]: n for n in nodes}
    # 인덱스 쪽도 오버레이에 노드가 있는 자원만 센다 — `add_trace`가 그렇게 싣는다(이름 목록 밖은 노드가 없다).
    known = {(n.get("type"), n.get("label")) for n in nodes if n.get("type") not in _NOT_RESOURCE}
    spans: dict[tuple[str, str], list] = {}
    for s in index.symbols:
        if s.kind in ("function", "method"):
            spans.setdefault((s.repo, s.file), []).append(s)
    for node in nodes:
        if node.get("type") != "endpoint" or node.get("traced") != "ok" or not node.get("chain"):
            continue
        m = _FIRST.match(node["chain"][0])
        if m is None:
            continue
        file, line = m.group(1), int(m.group(2))
        around = [s for s in spans.get((node.get("trace_repo", ""), file), []) if s.line <= line <= s.end_line]
        if not around:
            node["index_check"] = {"status": "no_handler"}
            continue
        handler = min(around, key=lambda s: s.end_line - s.line)          # 가장 안쪽 함수
        reached = qy.reach(graph, [handler.id], max_hops=MAX_DEPTH)
        got = {(r.kind, r.name) for sid in reached for r in index.symbols[sid].resources} & known
        want = set()
        for e in overlay["links"]:
            if e.get("source") == node["id"] and e.get("origin") == "trace" and e.get("target") in by_id:
                t = by_id[e["target"]]
                want.add((t.get("type"), t.get("label")))
        only_index = sorted(f"{n} [{k}]" for k, n in got - want)
        only_tracer = sorted(f"{n} [{k}]" for k, n in want - got)
        node["index_check"] = {"status": "diff" if only_index or only_tracer else "same",
                               "handler": handler.qualname, "only_index": only_index, "only_tracer": only_tracer}
    return {**overlay, "nodes": nodes}
