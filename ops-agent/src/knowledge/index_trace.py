"""끝점 사슬을 **심볼 인덱스**에서 만든다(11d 6d-3) — 11b 추적기와 같은 `Trace` 모양으로.

decisions ⑱의 "엔진 하나". 추적기는 끝점마다 앞으로 걸으며 파싱·grep을 다시 했고, 인덱스는 레포 전체를 한 번 해석해
뒀다. 사내 대조(같음 150 · 다름 6)로 인덱스가 추적기보다 못한 지점이 없다는 것이 섰으니 사슬을 여기서 만들고,
추적기는 대조용으로 한 번 더 돈 뒤 지운다. 던지지 않는다.

추적기와 맞춘 것: 같은 path의 라우트 선언 **전부**가 뿌리, 깊이 6·노드 200, 리터럴은 확실·config 키·별칭 경유는 추정,
이름만 같은 후보(`?→`)를 거친 아래는 전부 추정이고 gap에 후보 수를 적는다, `getattr`은 gap. 디스패치(`=>`, 베이스·포트
→ 구현)는 깊이를 안 먹는다 — 추적기는 호출자에서 구현으로 바로 갔다.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from src.knowledge import query as qy
from src.knowledge.flow import Route
from src.knowledge.index import Index
from src.knowledge.trace import MAX_DEPTH, MAX_NODES, Gap, Read, Step, Trace

_GETATTR = "getattr로 고른 대상은 못 따라간다 — 리드가 code.read로 본다"


@dataclass
class _Node:
    sid: int
    depth: int
    parent: int | None
    cap: str            # "확실" | "추정" — 여기까지 오는 길에 추정 호출이 있었나


def trace(index: Index, graph: qy.Graph, *, repo: str, target: str, routes: list[Route],
          max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES) -> Trace:
    roots, missing = _roots(index, repo, target, routes)
    if not roots:
        if missing:
            return Trace(target, repo, "not_found",
                         f"{target}: 라우트 줄 아래 함수가 인덱스에 없다 — {' · '.join(missing)}")
        return Trace(target, repo, "not_found",
                     f"{target}: 레포 {repo}의 라우트 선언에 없다 — code graph의 끝점 목록을 보라")
    chain: list[Step] = []
    reads: dict[tuple[str, str, str, int], Read] = {}
    gaps: list[Gap] = []
    visited: set[int] = set()
    queue: deque[_Node] = deque(_Node(sid, 0, None, "확실") for sid in roots)
    while queue:
        node = queue.popleft()
        if node.sid in visited:
            continue
        s = index.symbols[node.sid]
        if len(visited) >= max_nodes:
            gaps.append(Gap(s.file, s.line, f"노드 상한 {max_nodes}에서 멈춤: {_short(index, graph, node.sid)}"))
            break
        visited.add(node.sid)
        chain.append(Step(s.file, s.line, _short(index, graph, node.sid), node.parent))
        step = len(chain) - 1
        for r in s.resources:
            grade = "추정" if node.cap == "추정" or r.via != "literal" else "확실"
            key = (r.kind, r.name, s.file, r.line)
            cur = reads.get(key)
            if cur is None or (cur.grade == "추정" and grade == "확실"):
                reads[key] = Read(r.kind, r.name, grade, s.file, r.line, r.via, step)
        for line, _, why in s.unresolved:
            if why == "getattr":
                gaps.append(Gap(s.file, line, _GETATTR))
        children = _children(index, graph, node, gaps, step)
        if not children:
            continue
        if node.depth >= max_depth and any(c.depth > node.depth for c in children):
            names = ", ".join(sorted({_short(index, graph, c.sid) for c in children if c.depth > node.depth}))
            gaps.append(Gap(s.file, s.line, f"깊이 상한 {max_depth}에서 멈춤: {_short(index, graph, node.sid)} → {names}"))
            children = [c for c in children if c.depth <= node.depth]
        queue.extend(children)
    ordered = sorted(reads.values(), key=lambda x: (x.file, x.line, x.kind, x.name))
    return Trace(target, repo, "ok", chain=tuple(chain), reads=tuple(ordered), gaps=tuple(gaps))


def _roots(index: Index, repo: str, target: str, routes: list[Route]) -> tuple[list[int], list[str]]:
    """라우트 선언 줄 **바로 아래 첫 함수** — 추적기의 `_handler_at`과 같다. 심볼의 줄은 `def` 줄이라 데코레이터 줄보다
    뒤에 있다. 같은 path의 선언 전부가 뿌리다(GET·PUT …)."""
    by_file: dict[str, list] = {}
    for s in index.symbols:
        if s.repo == repo and s.kind in ("function", "method"):
            by_file.setdefault(s.file, []).append(s)
    roots: list[int] = []
    missing: list[str] = []
    for rt in sorted((r for r in routes if r.repo == repo and r.path == target), key=lambda r: (r.file, r.line)):
        below = [s for s in by_file.get(rt.file, []) if s.line >= rt.line]
        if not below:
            missing.append(f"{rt.file}:L{rt.line}")
            continue
        sid = min(below, key=lambda s: s.line).id
        if sid not in roots:
            roots.append(sid)
    return roots, missing


def _children(index: Index, graph: qy.Graph, node: _Node, gaps: list[Gap], step: int) -> list[_Node]:
    """이 함수에서 다음 걸음들. 확실 호출은 그대로, 후보(`?→`)는 아래를 추정으로 — 호출 자리마다 후보 수를 gap에.
    디스패치(`=>`)는 구현이 여럿이면 추정이고 gap에 적는다(추적기의 "구현체 N개"와 같다)."""
    s = index.symbols[node.sid]
    links = graph.out.get(node.sid, [])
    out: list[_Node] = []
    by_line: dict[int, list] = {}
    for link in links:
        if link.mark == qy.MARKS["candidate"]:
            by_line.setdefault(link.line, []).append(link)
    for line, group in sorted(by_line.items()):
        name = index.symbols[group[0].dst].name
        gaps.append(Gap(s.file, line, f"{name}: 받는 쪽을 못 좁혀 후보 {len(group)}개 — 전부 따라가되 읽기는 추정"))
    dispatch = [link for link in links if link.mark == qy.DISPATCH]
    dispatch_cap = node.cap
    if len(dispatch) > 1:
        dispatch_cap = "추정"
        owner = index.symbols[s.class_id].name if s.class_id is not None else s.name
        gaps.append(Gap(s.file, s.line, f"{s.name}: {owner} 구현체 {len(dispatch)}개 — 전부 따라가되 읽기는 추정"))
    for link in sorted(links, key=lambda l: (l.line, l.dst)):
        if link.mark == qy.DISPATCH:
            out.append(_Node(link.dst, node.depth, step, dispatch_cap))
        elif link.mark == qy.MARKS["candidate"]:
            out.append(_Node(link.dst, node.depth + 1, step, "추정"))
        else:
            out.append(_Node(link.dst, node.depth + 1, step, node.cap))
    return out


def _short(index: Index, graph: qy.Graph, sid: int) -> str:
    """사슬에 적는 이름 — 모듈 안의 이름(`AlarmRepo.recent`). 추적기의 qualname과 같은 꼴."""
    s = index.symbols[sid]
    mod = graph.modules.get((s.repo, s.file), "")
    return s.qualname[len(mod) + 1:] if mod and s.qualname.startswith(mod + ".") else s.qualname
