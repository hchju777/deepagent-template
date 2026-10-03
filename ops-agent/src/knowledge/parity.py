"""추적기와 인덱스의 대조(11d 6c-2) — 엔진을 하나로 모으기 전에 둘이 어디서 다르게 말하나를 숫자로.

리드가 받는 `code.trace` 사슬은 11b 추적기가 끝점에서 앞으로 걸어 만든 것이다. 인덱스로도 같은 끝점의 핸들러(사슬
첫 걸음을 품는 함수)에서 **같은 깊이**로 호출·디스패치를 따라가 닿는 자원을 모은다. 깊이를 맞추지 않으면 "인덱스만"이
대부분 더 깊이 간 몫이 되어 대조가 뜻을 잃는다. 다르면 끝점 노드에 적어 `code.trace` 출력에 나오게 하고 `code
status`가 센다 — 패리티가 확인되면 추적기를 뺀다(decisions ⑱). 던지지 않는다.

다르면 자원마다 **왜**를 한 줄 붙인다(`why`) — 사내 대조가 "다름 7"을 냈을 때 사람이 끝점마다 `code trace`·`code uses`·
`code path`를 손으로 옮겨 적어야 원인을 가를 수 있었다. 인덱스만: 핸들러에서 그 자원을 건드리는 함수까지의 경로(확실한
호출로 닿는 길을 먼저 고른다 — 추정 `?→`를 거쳐서만 닿으면 인덱스가 넘어간 것이다). 추적기만: 추적기 사슬을 뿌리부터
내려가며 인덱스가 처음 못 닿은 걸음(= 인덱스에 없는 호출), 다 닿으면 그 함수에서 이름을 못 본 것.
"""
from __future__ import annotations

import re

from src.knowledge import query as qy
from src.knowledge.index import Index
from src.knowledge.trace import MAX_DEPTH

_FIRST = re.compile(r"^(.*):L(\d+) ")
_NOT_RESOURCE = ("endpoint", "service", "repo")
# 인덱스만 자원의 경로를 고르는 순서 — 확실한 호출만, 디스패치까지, 추정까지. 짧은 추정 길보다 긴 확실한 길이 낫다.
_TIERS = (frozenset({qy.MARKS["exact"]}), frozenset({qy.MARKS["exact"], qy.DISPATCH}), None)


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
        repo = node.get("trace_repo", "")
        handler = _innermost(spans, repo, m.group(1), int(m.group(2)))
        if handler is None:
            node["index_check"] = {"status": "no_handler"}
            continue
        reached = qy.reach(graph, [handler.id], max_hops=MAX_DEPTH)
        got = {(r.kind, r.name) for sid in reached for r in index.symbols[sid].resources} & known
        reads: dict[tuple, dict] = {}
        for e in overlay["links"]:
            if e.get("source") == node["id"] and e.get("origin") == "trace" and e.get("target") in by_id:
                t = by_id[e["target"]]
                reads.setdefault((t.get("type"), t.get("label")), e)
        want = set(reads)
        why: dict[str, str] = {}
        trees: list = []
        for k, n in sorted(got - want):
            why[f"{n} [{k}]"] = _index_why(index, graph, handler, (k, n), reached, trees)
        for k, n in sorted(want - got):
            why[f"{n} [{k}]"] = _tracer_why(index, graph, node, repo, spans, reached, reads[(k, n)])
        only_index = sorted(f"{n} [{k}]" for k, n in got - want)
        only_tracer = sorted(f"{n} [{k}]" for k, n in want - got)
        node["index_check"] = {"status": "diff" if only_index or only_tracer else "same",
                               "handler": handler.qualname, "only_index": only_index, "only_tracer": only_tracer,
                               "why": why}
    return {**overlay, "nodes": nodes}


def _innermost(spans: dict, repo: str, file: str, line: int):
    around = [s for s in spans.get((repo, file), []) if s.line <= line <= s.end_line]
    return min(around, key=lambda s: s.end_line - s.line) if around else None


def _index_why(index: Index, graph: qy.Graph, handler, key: tuple, reached: set[int], trees: list) -> str:
    holders = [sid for sid in sorted(reached) if key in {(r.kind, r.name) for r in index.symbols[sid].resources}]
    if not trees:
        trees.extend(qy.reach_tree(graph, [handler.id], max_hops=MAX_DEPTH, marks=m) for m in _TIERS)
    path: list = []
    for tree in trees:
        hits = [sid for sid in holders if sid in tree]
        if hits:
            path = min((qy.path_to(tree, sid) for sid in hits), key=len)
            break
    if not path:
        return f"[{handler.repo}] {qy.short(index, handler.id, graph.modules)} — 핸들러가 직접"
    text = qy.render_path(index, path)
    if any(link.mark == qy.MARKS["candidate"] for link in path):
        text += " — 이름만 같은 후보(?→)를 거친다"
    return text


def _tracer_why(index: Index, graph: qy.Graph, node: dict, repo: str, spans: dict, reached: set[int],
                read: dict) -> str:
    chain = list(node.get("chain") or [])
    parents = list(node.get("chain_parent") or [None] * len(chain))
    at = f"{read.get('source_file', '?')}:{read.get('source_location', '?')}"
    step = int(read.get("step", -1))
    if not 0 <= step < len(chain):
        return f"추적기: {at}"
    lineage, cur = [], step
    while cur is not None and len(lineage) < 64:
        lineage.append(cur)
        cur = parents[cur]
    prev = None
    for i in reversed(lineage):
        m = _FIRST.match(chain[i])
        fn = _innermost(spans, repo, m.group(1), int(m.group(2))) if m else None
        if fn is None:
            return f"추적기 걸음 {chain[i]}을 품는 함수가 인덱스에 없다"
        if fn.id not in reached:
            src = qy.short(index, prev.id, graph.modules) if prev is not None else "핸들러"
            return f"인덱스가 못 이은 호출 {src} → {qy.short(index, fn.id, graph.modules)} ({m.group(1)}:L{m.group(2)})"
        prev = fn
    return f"인덱스는 {qy.short(index, prev.id, graph.modules)}에 닿지만 거기서 이 이름을 못 본다 (추적기: {at})"
