"""심볼 인덱스 질의(11d 6c-1) — "누가 부르나", "A에서 B로 어떻게 가나", "이 자원을 누가 쓰고 읽나".

11b의 추적기는 끝점에서 앞으로만 걸어서 이 역질문들에 답이 없었다 — 11d를 시작한 이유다. 인덱스가 서고 나면
조회다. 순수 함수이고 던지지 않는다.

표시는 사람이 옮겨 적을 만큼 짧게 한다. 확실한 호출은 `→`, 추정은 `?→`, 베이스·포트 메서드를 부르면 구현으로
갈 수 있다는 디스패치는 `=>`. 호출은 레포를 건너지 않으므로(레포 사이는 HTTP·Kafka, 흐름 그래프의 몫) 레포는
경로 앞에 한 번만 적는다.
"""
from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, field

from src.knowledge.index import Index

MAX_HOPS = 12
# 펼칠 노드 상한 — 클래스 계층이 두꺼운 레포(사내 overrides 1859)에서 경로 수가 폭발해도 끝나야 한다.
BUDGET = 20_000
_ROUTE = re.compile(r"\.(get|post|put|patch|delete|head|options|api_route|route|websocket)\(")
MARKS = {"exact": "→", "candidate": "?→"}
DISPATCH = "=>"


@dataclass(frozen=True)
class Link:
    src: int
    dst: int
    mark: str          # "→" | "?→" | "=>"
    line: int          # 부르는 자리(src 파일의 줄). 디스패치는 0


@dataclass
class Callers:
    direct: list[Link] = field(default_factory=list)          # 바로 부르는 곳
    entries: list[list[Link]] = field(default_factory=list)   # 진입점 → … → 대상, 짧은 것부터
    dead_ends: list[list[Link]] = field(default_factory=list) # 디스패치로만 닿았고 부르는 쪽이 없는 베이스·포트 메서드에서
    cut: bool = False                                          # 상한에 걸려 다 못 봤다


@dataclass(frozen=True)
class Use:
    kind: str
    name: str
    direction: str     # "writes" | "reads"
    sid: int
    line: int


class Graph:
    """호출 그래프의 인접 목록 — 한 번 만든다. `overrides`·`implements`(구현 → 베이스·포트)를 뒤집어 "베이스 => 구현"
    디스패치로 넣는다: 베이스 메서드를 부르는 코드는 실행 시점에 그 구현들로 간다."""

    def __init__(self, index: Index):
        self.index = index
        self.modules = _modules(index)
        self.out: dict[int, list[Link]] = {}
        self.inc: dict[int, list[Link]] = {}
        seen: set[Link] = set()
        for e in index.edges:
            if e.type == "calls":
                link = Link(e.src, e.dst, MARKS.get(e.certainty, "?→"), e.line)
            elif e.type in ("overrides", "implements"):
                link = Link(e.dst, e.src, DISPATCH, 0)
            else:
                continue
            if link in seen:            # 같은 짝이 두 길(상속·이름 규칙)로 들어와도 한 번
                continue
            seen.add(link)
            self.out.setdefault(link.src, []).append(link)
            self.inc.setdefault(link.dst, []).append(link)


def find(index: Index, text: str) -> list[int]:
    """이름 → 심볼들. `qualname`, 끝부분(`Class.method`·`func`), 앞에 `파일:`·`레포:`를 붙여 좁힌다. 정확한 qualname이
    있으면 그것만, 없으면 끝부분이 맞는 것 전부 — 여럿이면 호출부가 후보를 보여 주고 멈춘다(사람이 고른다)."""
    parts = text.strip().replace("\\", "/").split(":")
    name, repo, file = parts[-1], None, None
    for p in parts[:-1]:
        if p.endswith(".py"):
            file = p
        elif p:
            repo = p
    pool = [s for s in index.symbols if s.kind in ("function", "method", "class")
            and (repo is None or s.repo == repo) and (file is None or s.file == file)]
    exact = [s.id for s in pool if s.qualname == name]
    return exact or [s.id for s in pool if s.qualname.endswith("." + name)]


def targets(index: Index, sid: int) -> list[int]:
    """클래스를 고르면 생성자 호출이 대상이다 — 엣지는 `__init__`(없으면 클래스)로 간다."""
    s = index.symbols[sid]
    if s.kind != "class":
        return [sid]
    init = index.lookup(s.repo, f"{s.qualname}.__init__")
    return [sid] + ([init] if init is not None else [])


def route(index: Index, sid: int) -> str | None:
    """라우트 핸들러면 그 데코레이터(`router.post('/badge')`) — 사람이 끝점을 알아본다."""
    return next((d for d in index.symbols[sid].decorators if _ROUTE.search(d)), None)


def is_route(index: Index, sid: int) -> bool:
    return route(index, sid) is not None


def callers(graph: Graph, starts: list[int], *, max_hops: int = MAX_HOPS) -> Callers:
    """대상을 부르는 쪽을 진입점까지 거슬러 오른다(넓이 우선 — 경로는 짧은 것부터). 진입점은 부르는 쪽이 없는 함수와
    라우트 핸들러다. 디스패치로만 닿은 베이스·포트 메서드는 부르는 쪽이 없어도 진입점이 아니다 — 실행의 시작이 아니라
    프레임워크가 부르는 자리일 수 있어서 `dead_ends`로 따로 말한다."""
    out = Callers()
    start_set = set(starts)
    for s in starts:
        out.direct.extend(graph.inc.get(s, []))
    parent: dict[int, Link] = {}
    depth = {s: 0 for s in starts}
    queue = deque(starts)
    roots: list[int] = []
    dead: list[int] = []
    while queue:
        node = queue.popleft()
        inc = graph.inc.get(node, [])
        if node not in start_set:
            if not inc:
                (dead if parent[node].mark == DISPATCH else roots).append(node)
                continue
            if is_route(graph.index, node):
                roots.append(node)
        if depth[node] >= max_hops:
            out.cut = True
            continue
        for link in inc:
            if link.src in depth:
                continue
            if len(depth) >= BUDGET:
                out.cut = True
                break
            depth[link.src] = depth[node] + 1
            parent[link.src] = link
            queue.append(link.src)
    out.entries = [_back(parent, start_set, r) for r in roots]
    out.dead_ends = [_back(parent, start_set, d) for d in dead]
    return out


def _back(parent: dict[int, Link], start_set: set[int], node: int) -> list[Link]:
    path = []
    while node not in start_set:
        path.append(parent[node])
        node = parent[node].dst
    return path


def paths(graph: Graph, starts: list[int], goals: list[int], *, k: int = 3,
          max_hops: int = MAX_HOPS) -> tuple[list[list[Link]], bool]:
    """A에서 B로 가는 호출 경로, 짧은 것부터 최대 k개. 한 노드를 k번 넘게 지나지 않는다(경로 수 폭발 방지) — 그래서
    "짧은 k개"는 근사다. 상한에 걸리면 `cut`."""
    goal_set, start_set = set(goals), set(starts)
    found: list[list[Link]] = []
    visits: Counter = Counter()
    queue = deque((s, []) for s in starts)
    expanded, cut = 0, False
    while queue and len(found) < k:
        node, path = queue.popleft()
        if path and node in goal_set:
            found.append(path)
            continue
        if len(path) >= max_hops:
            cut = True
            continue
        on_path = {l.src for l in path}
        for link in graph.out.get(node, []):
            if link.dst in on_path or link.dst in start_set or visits[link.dst] >= k:
                continue
            visits[link.dst] += 1
            expanded += 1
            if expanded > BUDGET:
                return found, True
            queue.append((link.dst, path + [link]))
    return found, cut


def uses(index: Index, name: str) -> list[Use]:
    """이 이름의 자원(컬렉션·토픽·키·그룹)을 쓰고 읽는 함수들 — 쓰기가 앞. 정확한 이름이 없으면 부분 일치(키 템플릿
    `alarm:stats:{line}`을 그대로 치기는 어렵다)."""
    def collect(match) -> list[Use]:
        found = [Use(r.kind, r.name, r.direction, s.id, r.line)
                 for s in index.symbols for r in s.resources if match(r.name)]
        return sorted(found, key=lambda u: (u.direction != "writes", u.name, u.sid, u.line))
    return collect(lambda n: n == name) or collect(lambda n: name in n)


def ancestors(index: Index, sid: int) -> list[int]:
    """클래스의 부모들 — `inherits` 엣지를 끝까지, 가까운 것부터, 순환은 한 번만(R3-5d ③). 인덱스는 처음부터 이 엣지를 저장한다."""
    out: list[int] = []
    seen = {sid}
    stack = [e.dst for e in index.edges_from(sid, "inherits")]
    while stack:
        base = stack.pop(0)
        if base in seen:
            continue
        seen.add(base)
        out.append(base)
        stack += [e.dst for e in index.edges_from(base, "inherits")]
    return out


def reachable_members(index: Index, sid: int) -> tuple[list[int], list[int], int]:
    """클래스의 **진입에서 호출 그래프로 닿는** 메서드들(R3-5e ②) → (자기 메서드들, 닿은 부모 메서드들, 안 닿은 부모 메서드 수). 진입 = 자기 메서드 전부 +
    부모 메서드 중 `calls` 엣지가 하나도 안 들어오는 것(프레임워크가 부르는 `_run` 같은 것 — 사람 결정 10-19, 토폴로지에 진입 이름을 적게 하지 않는다).
    거기서 `calls`(exact·candidate — 템플릿 메서드의 `self.handle()`은 하위 클래스들로 candidate)로 닿는 부모 메서드만, 이 클래스와 부모들의 메서드 안에서.
    측정 #15 P0-2: 부모 믹스인의 메서드 전부를 붙이니 다른 자식만 부르는 `<근무일 키>` 읽기가 홉 29/33에 실렸다(그 메서드를 부르는 파일은 75개 중 6개).
    함수면 자기만."""
    s = index.symbols[sid]
    if s.kind != "class":
        return [sid], [], 0
    bases = ancestors(index, sid)
    own = [m.id for m in index.symbols if m.class_id == sid]
    parents = [m.id for b in bases for m in index.symbols if m.class_id == b]
    family = set(own) | set(parents)
    roots = own + [m for m in parents if not index.edges_to(m, "calls")]
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        stack += [e.dst for e in index.edges_from(m, "calls") if e.dst in family and e.dst not in seen]
    reached = [m for m in parents if m in seen]
    return own, reached, len(parents) - len(reached)


def resources_lines(index: Index, sid: int, *, where, cap: int = 20) -> list[str]:
    """`code.resources` 한 벌 — 이 클래스의 메서드들(함수면 자기)과 **진입에서 닿는 부모 클래스 메서드들**(`reachable_members`)이 읽고 쓰는 자원, 읽기가 앞.
    자원 하나는 한 번(첫 메서드·줄 — 자기 것이 부모 것보다 앞). 측정 #13 추가분 3: 읽기가 부모(믹스인)의 `_run`에 있는 클래스가 "자원이 자기 출력뿐"으로
    보였다; 측정 #15 P0-2: 부모 메서드 전부를 붙이니 안 부르는 메서드의 키가 실렸다 — 둘째 줄에 뺀 수를 적는다. 줄의 메서드 자리가 부모의 것이면 어느
    부모인지 거기 보인다. 자원이 없으면 그 사실 — 키를 조립식으로 만들면 텍스트로는 못 찾는다(없는 것이 아니라 못 본 것)."""
    s = index.symbols[sid]
    bases = ancestors(index, sid) if s.kind == "class" else []
    own, reached, skipped = reachable_members(index, sid)
    members = own + reached
    lines = [f"{display(index, sid)} {where(sid)}".rstrip()]
    if bases:
        lines.append(f"  부모 클래스의 자원 포함: {', '.join(index.symbols[b].qualname for b in bases)}"
                     + (f" · 진입에서 안 닿은 부모 메서드 {skipped}개 뺌" if skipped else ""))
    # `self.<속성>`으로 고른 자원(`attr:<속성>:<클래스>`, R3-5f ③)은 **이 클래스의 MRO에서 가장 가까운 정의**의 것만 — 측정 #16 P0-c: 형제 클래스의 키가 입력으로
    # 실렸다. 정의는 `Symbol.consts`(자원 아닌 리터럴도)로 본다(R3-5g ①, 측정 #17: 자기 속성이 `"material"`인 클래스가 못 이음으로 보였다) — 가장 가까운
    # 정의가 자원이 아니면 풀린 것이라 줄이 없고, MRO에 정의가 없을 때만 못 이음(값을 가진 다른 클래스만 적는다). `consts`가 없는 옛 번들은 후보 중 가장 가까운 것.
    root = sid if s.kind == "class" else s.class_id
    chain = [root] + (bases if s.kind == "class" else ancestors(index, root)) if root is not None else []
    order = [index.symbols[c].qualname for c in chain]
    consts = {index.symbols[c].qualname: dict(index.symbols[c].consts) for c in chain}
    legacy = not any(consts.values())
    seen: dict[tuple[str, str, str], tuple[int, int]] = {}
    unresolved: dict[str, set[str]] = {}
    for mid in members:
        by_attr: dict[str, list] = {}
        for r in index.symbols[mid].resources:
            parts = r.via.split(":", 2) if r.via.startswith("attr:") else []
            if len(parts) == 3:
                by_attr.setdefault(parts[1], []).append((parts[2], r))
                continue
            seen.setdefault((r.direction, r.kind, r.name), (mid, r.line))
        for attr, cands in by_attr.items():
            owners = {o for o, _ in cands}
            nearest = next((q for q in order if ((q in owners) if legacy else (attr in consts[q]))), None)
            if nearest is None:
                unresolved.setdefault(attr, set()).update(owners)
                continue
            for o, r in cands:
                if o == nearest:
                    seen.setdefault((r.direction, r.kind, r.name), (mid, r.line))
    for direction, label in (("reads", "읽기"), ("writes", "쓰기")):
        rows = sorted((k, v) for k, v in seen.items() if k[0] == direction)
        if not rows:
            continue
        lines.append(f"  {label} {len(rows)}:")
        lines += [f"    {name} [{kind}] — {display(index, mid, line)}" for (_, kind, name), (mid, line) in rows[:cap]]
    for attr, owners in sorted(unresolved.items()):
        lines.append(f"  self.{attr}: 이 클래스와 부모에 그 속성의 리터럴이 없다 — 못 이음(자원 값을 가진 다른 클래스: {', '.join(sorted(owners))})")
    if not seen:
        lines.append(f"  읽거나 쓰는 자원이 인덱스에 없다 — 메서드 {len(members)}개 (키를 조립식으로 만들면 텍스트로는 못 찾는다)")
    return lines


def short(index: Index, sid: int, modules: dict[tuple[str, str], str] | None = None) -> str:
    """경로 한 칸 — 모듈 끝 이름 + 모듈 안의 이름(`impl.MongoStore.save`, `svc.handle`)."""
    s = index.symbols[sid]
    mods = modules if modules is not None else _modules(index)
    mod = mods.get((s.repo, s.file), "")
    inner = s.qualname[len(mod) + 1:] if mod and s.qualname.startswith(mod + ".") else s.qualname
    tail = mod.rsplit(".", 1)[-1] if mod else ""
    return f"{tail}.{inner}" if tail else inner


def display(index: Index, sid: int, line: int | None = None) -> str:
    s = index.symbols[sid]
    return f"{s.qualname} ({s.file}:L{line if line is not None else s.line})"


def render_path(index: Index, path: list[Link]) -> str:
    if not path:
        return ""
    mods = _modules(index)
    parts = [f"[{index.symbols[path[0].src].repo}] {short(index, path[0].src, mods)}"]
    parts += [f"{l.mark} {short(index, l.dst, mods)}" for l in path]
    return " ".join(parts)


def _modules(index: Index) -> dict[tuple[str, str], str]:
    return {(s.repo, s.file): s.qualname for s in index.symbols if s.kind == "module"}


# ── 표시 조립(6d-1) — CLI `code callers`·`code uses`와 리드의 `code.callers`·`code.uses`가 **같은 함수**를 부른다.
# 둘로 베끼면 한쪽이 먼저 어긋나고(규율 8과 같은 이유), 사내에서 사람이 CLI로 맞춰 본 답과 리드가 받는 답이 달라진다.
# 줄은 들여쓰기 없이 돌려주고 호출부가 앞을 붙인다. `where(sid)`는 `[레포 · 서비스]` — 토폴로지는 호출부가 안다.

def ambiguous_lines(index: Index, text: str, found: list[int], *, cap: int = 5) -> list[str]:
    lines = [f"{text}: 여럿이다({len(found)}) — qualname이나 `파일:qualname`으로 하나를 골라 다시:"]
    lines += [f"  {display(index, sid)} [{index.symbols[sid].repo}]" for sid in found[:cap]]
    if len(found) > cap:
        lines.append(f"  … 외 {len(found) - cap}")
    return lines


def repo_hint(index: Index, found: list[int], repo_services: dict[str, tuple[str, ...]], *, how: str) -> list[str]:
    """찾은 심볼이 **레포 둘 이상**에 걸치면 레포와 그 서비스들을 한 줄로 — `파일:qualname`으로는 못 가른다(측정 #9 P1 4: 파일도 같았다). 레포
    단위로 센다(측정 #10 P1 6: 한 레포를 여러 서비스가 `path` 없이 공유하면 서비스 단위 셈이 그 파일을 못 세어 안내가 안 떴다). 어떻게 고르는지는
    호출부가 댄다(어댑터는 인자, CLI는 옵션). 한 레포 안의 여럿은 `ambiguous_lines`의 몫."""
    repos = sorted({index.symbols[sid].repo for sid in found})
    if len(repos) < 2:
        return []
    shown = ", ".join(f"{repo}({', '.join(repo_services.get(repo, ())) or '서비스 없음'})" for repo in repos)
    return [f"  같은 이름이 레포 {len(repos)}곳에 있다 — {how}로 고른다: {shown}"]


def entry_brief(index: Index, graph: Graph, sid: int) -> str:
    """이 함수가 어느 진입점에서 오나 — `uses` 줄의 꼬리."""
    res = callers(graph, [sid])
    if not res.direct:
        tag = route(index, sid)
        return f"진입점: 이 함수(라우트 {tag})" if tag else "진입점: 이 함수(부르는 곳 없음)"
    names = []
    for p in res.entries[:3]:
        tag = route(index, p[0].src)
        names.append(short(index, p[0].src, graph.modules) + (f"(라우트 {tag})" if tag else ""))
    if not names:
        return ("진입점을 못 찾았다 — 부르는 쪽 없는 베이스·포트 메서드로만 닿는다(프레임워크가 부를 수 있다)"
                if res.dead_ends else "진입점을 못 찾았다(순환)")
    extra = len(res.entries) - len(names)
    return "진입점: " + ", ".join(names) + (f" 외 {extra}" if extra > 0 else "")


def _more(total: int, cap: int | None, more: str) -> list[str]:
    return [f"  … 외 {total - cap}{more}"] if cap is not None and total > cap else []


def callers_lines(index: Index, graph: Graph, sid: int, *, where, cap: int | None = 10,
                  more: str = "") -> list[str]:
    """`code callers` 한 벌 — 대상, 바로 부르는 곳, 진입점 경로, 막다른 베이스, 상한."""
    res = callers(graph, targets(index, sid))
    lines = [f"대상 {display(index, sid)} {where(sid)}"]
    if not res.direct:
        tag = route(index, sid)
        lines.append("부르는 곳이 없다 — 이 함수가 진입점이다"
                     + (f"(라우트 {tag})" if tag else "(스크립트·스케줄·동적 호출일 수 있다)"))
        return lines
    lines.append(f"바로 부르는 곳 {len(res.direct)} (줄은 부르는 자리):")
    for link in res.direct[:cap]:
        how = {DISPATCH: " — 베이스·포트 메서드를 거쳐(디스패치)", MARKS["candidate"]: " — 추정"}.get(link.mark, "")
        lines.append(f"  {display(index, link.src, link.line or None)} {where(link.src)}{how}")
    lines += _more(len(res.direct), cap, more)
    if res.entries:
        lines.append(f"진입점 {len(res.entries)}:")
        for p in res.entries[:cap]:
            tag = route(index, p[0].src)
            lines.append(f"  {render_path(index, p)}" + (f"   (라우트 {tag})" if tag else ""))
        lines += _more(len(res.entries), cap, more)
    else:
        lines.append("진입점을 못 찾았다" + ("" if res.dead_ends else " — 부르는 쪽이 순환뿐이다"))
    if res.dead_ends:
        lines.append(f"부르는 쪽 없는 베이스·포트 메서드 {len(res.dead_ends)} — 프레임워크가 부를 수 있다:")
        lines += [f"  {render_path(index, p)}" for p in res.dead_ends[:cap]]
        lines += _more(len(res.dead_ends), cap, more)
    if res.cut:
        lines.append(f"⚠ 상한({MAX_HOPS}단계·노드 {BUDGET})에 걸려 다 못 봤다")
    return lines


def uses_lines(index: Index, graph: Graph, found: list[Use], *, where, cap: int | None = 10,
               more: str = "") -> list[str]:
    """`code uses` 한 벌 — 자원마다 쓰기·읽기 함수와 그 진입점 한 줄."""
    groups: dict[tuple[str, str], list[Use]] = {}
    for u in found:
        groups.setdefault((u.name, u.kind), []).append(u)
    lines: list[str] = []
    for (name, kind), items in groups.items():
        lines.append(f"{name} [{kind}]")
        for direction, label in (("writes", "쓰기"), ("reads", "읽기")):
            rows = [u for u in items if u.direction == direction]
            if not rows:
                continue
            lines.append(f"  {label} {len(rows)}:")
            lines += [f"    {display(index, u.sid, u.line)} {where(u.sid)} — {entry_brief(index, graph, u.sid)}"
                      for u in rows[:cap]]
            lines += [("  " + l) for l in _more(len(rows), cap, more)]
    return lines
