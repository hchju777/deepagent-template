"""두 엔진의 대조(11d 6c-2 → 6d-3) — 오버레이의 끝점 사슬은 **인덱스**가 만들고(`index_trace`), 11b 추적기는 대조용으로
한 번 더 돈다. 끝점마다 인덱스 읽기와 추적기 읽기를 (종류, 이름)으로 대조해 다르면 자원마다 **왜**를 적는다 — 사내
대조가 "다름 7"을 냈을 때 사람이 끝점마다 `code trace`·`code uses`·`code path`를 손으로 옮겨 적어야 원인을 가를 수 있었다.

인덱스만: 핸들러에서 그 자원을 건드리는 함수까지의 경로(확실한 호출로 닿는 길을 먼저 고른다 — 추정 `?→`를 거쳐서만
닿으면 인덱스가 넘어간 것이다). 추적기만: 추적기 사슬을 뿌리부터 내려가며 인덱스가 처음 못 닿은 걸음(= 인덱스에
없는 호출), 다 닿으면 그 함수에서 이름을 못 본 것. 사내 150/6이 재현되면 추적기와 이 모듈을 지운다(6d-4). 던지지 않는다.
"""
from __future__ import annotations

import re

from src.knowledge import query as qy
from src.knowledge.index import Index
from src.knowledge.trace import MAX_DEPTH, Trace

_FIRST = re.compile(r"^(.*):L(\d+) ")
_NOT_RESOURCE = ("endpoint", "service", "repo")
# 인덱스만 자원의 경로를 고르는 순서 — 확실한 호출만, 디스패치까지, 추정까지. 짧은 추정 길보다 긴 확실한 길이 낫다.
_TIERS = (frozenset({qy.MARKS["exact"]}), frozenset({qy.MARKS["exact"], qy.DISPATCH}), None)


def check(overlay: dict, index: Index, graph: qy.Graph, tracer: dict[str, Trace]) -> dict:
    """`tracer`는 끝점 id → 추적기 결과. 인덱스 사슬이 없는 끝점(라우트 줄 아래 함수를 인덱스에서 못 찾음)은
    `no_handler`로 센다 — 추적기는 봤던 끝점이 사슬을 잃는 것이라 숫자로 남겨야 한다."""
    nodes = [dict(n) for n in overlay["nodes"]]
    by_id = {n["id"]: n for n in nodes}
    # 양쪽 다 오버레이에 노드가 있는 자원만 센다 — `add_trace`가 그렇게 싣는다(이름 목록 밖은 노드가 없다).
    known = {(n.get("type"), n.get("label")) for n in nodes if n.get("type") not in _NOT_RESOURCE}
    spans: dict[tuple[str, str], list] = {}
    for s in index.symbols:
        if s.kind in ("function", "method"):
            spans.setdefault((s.repo, s.file), []).append(s)
    for node in nodes:
        theirs = tracer.get(node["id"]) if node.get("type") == "endpoint" else None
        if theirs is None or theirs.status != "ok":
            continue
        if node.get("traced") != "ok" or not node.get("chain"):
            node["index_check"] = {"status": "no_handler"}
            continue
        repo = node.get("trace_repo", "")
        chain, parents = node["chain"], node.get("chain_parent") or [None] * len(node["chain"])
        handlers: list = []
        for i, p in enumerate(parents):
            m = _FIRST.match(chain[i]) if p is None else None
            fn = _innermost(spans, repo, m.group(1), int(m.group(2))) if m else None
            if fn is not None and all(fn.id != h.id for h in handlers):
                handlers.append(fn)
        if not handlers:
            node["index_check"] = {"status": "no_handler"}
            continue
        starts = [h.id for h in handlers]
        reached = qy.reach(graph, starts, max_hops=MAX_DEPTH)
        got = set()
        for e in overlay["links"]:
            if e.get("source") == node["id"] and e.get("origin") == "trace" and e.get("target") in by_id:
                t = by_id[e["target"]]
                got.add((t.get("type"), t.get("label")))
        reads = {}
        for r in theirs.reads:
            reads.setdefault((r.kind, r.name), r)
        want = set(reads) & known
        why: dict[str, str] = {}
        trees: list = []
        for k, n in sorted(got - want):
            why[f"{n} [{k}]"] = _index_why(index, graph, starts, (k, n), reached, trees)
        for k, n in sorted(want - got):
            why[f"{n} [{k}]"] = _tracer_why(index, graph, theirs, repo, spans, reached, reads[(k, n)])
        only_index = sorted(f"{n} [{k}]" for k, n in got - want)
        only_tracer = sorted(f"{n} [{k}]" for k, n in want - got)
        node["index_check"] = {"status": "diff" if only_index or only_tracer else "same",
                               "handler": handlers[0].qualname, "only_index": only_index, "only_tracer": only_tracer,
                               "why": why}
    return {**overlay, "nodes": nodes}


def _innermost(spans: dict, repo: str, file: str, line: int):
    around = [s for s in spans.get((repo, file), []) if s.line <= line <= s.end_line]
    return min(around, key=lambda s: s.end_line - s.line) if around else None


def _index_why(index: Index, graph: qy.Graph, starts: list[int], key: tuple, reached: set[int],
               trees: list) -> str:
    holders = [sid for sid in sorted(reached) if key in {(r.kind, r.name) for r in index.symbols[sid].resources}]
    if not trees:
        trees.extend(qy.reach_tree(graph, starts, max_hops=MAX_DEPTH, marks=m) for m in _TIERS)
    best, path = None, []
    for tree in trees:
        hits = [sid for sid in holders if sid in tree]
        if hits:
            best = min(hits, key=lambda sid: len(qy.path_to(tree, sid)))
            path = qy.path_to(tree, best)
            break
    if best is None:
        return "인덱스 경로를 되짚지 못했다"
    if not path:
        return f"[{index.symbols[best].repo}] {qy.short(index, best, graph.modules)} — 핸들러가 직접"
    text = qy.render_path(index, path)
    if any(link.mark == qy.MARKS["candidate"] for link in path):
        text += " — 이름만 같은 후보(?→)를 거친다"
    return text


def _tracer_why(index: Index, graph: qy.Graph, theirs: Trace, repo: str, spans: dict, reached: set[int],
                read) -> str:
    chain = list(theirs.chain)
    at = f"{read.file}:L{read.line}"
    if not 0 <= read.step < len(chain):
        return f"추적기: {at}"
    lineage, cur = [], read.step
    while cur is not None and len(lineage) < 64:
        lineage.append(cur)
        cur = chain[cur].parent
    prev = None
    for i in reversed(lineage):
        st = chain[i]
        fn = _innermost(spans, repo, st.file, st.line)
        if fn is None:
            return f"추적기 걸음 {st.file}:L{st.line} {st.qualname}을 품는 함수가 인덱스에 없다"
        if fn.id not in reached:
            src = qy.short(index, prev.id, graph.modules) if prev is not None else "핸들러"
            return f"인덱스가 못 이은 호출 {src} → {qy.short(index, fn.id, graph.modules)} ({st.file}:L{st.line})"
        prev = fn
    return f"인덱스는 {qy.short(index, prev.id, graph.modules)}에 닿지만 거기서 이 이름을 못 본다 (추적기: {at})"
