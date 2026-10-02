"""전역 심볼 인덱스(11d 6a) — 레포 **전체**를 배포 커밋에서 한 번 훑어 심볼과 함수→함수 엣지를 만든다.

## 왜 끝점 추적기(`trace.py`) 옆에 하나 더 있는가

추적기는 끝점 하나에서 **앞으로만** 간다. 그 사슬은 노드에 텍스트로 붙고, 함수→함수 엣지는 그래프에 없다.
그래서 "이 컬렉션은 **누가** 쓰나"(역방향)와 "이 함수를 고치면 **어디가** 깨지나"(영향도)를 물을 대상이 없었다.
사내의 다른 코드 지도 도구가 같은 질문을 2-pass 인덱스로 풀었고(심볼 6,858·호출 표본 400건 검증), 그 알고리즘을
이 리포의 규율(던지지 않는다, 등급을 붙인다, 추정을 확실처럼 말하지 않는다, 배포 커밋을 읽는다) 위에 옮겼다.
끝은 엔진 하나다 — 추적기의 좁히기 규칙은 여기로 옮겨 오고, 끝점 사슬은 이 인덱스 위의 BFS가 된다(6c).

## 2-pass인 이유

파일 A가 파일 B의 클래스를 부르는데 B를 아직 안 읽었으면 1-pass는 해석에 실패한다. **수집**(모든 `.py`를 파싱해
심볼과 원시 참조만)과 **해석**(전역 심볼 표가 다 선 뒤 원시 참조를 엣지로)을 가르면 전방 참조가 전부 풀린다.

## 추측의 비용은 비대칭이다

못 찾은 엣지는 리드가 `code.read`로 메우지만 틀린 엣지는 리드를 엉뚱한 서비스로 보낸다. 흔한 이름 stoplist·후보
상한 12·"인자로 넘긴 이름은 확정 해석만"은 전부 이 비대칭 때문이다 — 거짓을 줄이려고 참을 일부 버린다.

## 키

내부 id는 배열 위치(그래프 연산이 정수 색인), 바깥에 나가는 키는 `(repo, qualname)`이다. 재인덱싱해도 리드가
라운드 1에서 인용한 자리가 라운드 3에서 다른 것을 가리키면 안 된다.
"""
from __future__ import annotations

import ast
import builtins
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Protocol

from src.knowledge.flow import Name, direction
from src.knowledge.trace import _collect_reads

# 사내 도구가 측정으로 정한 값들. candidate 매칭에서 제외하는 흔한 이름 — 안 막으면 `.get(` 하나가 후보 수십 개로
# 퍼져 그래프가 쓰레기가 된다. 후보 12개는 정보가 아니라 소음이다.
STOPLIST = frozenset(
    "get set items keys values append update add remove join split strip format read write close open copy sort "
    "index count insert clear lower upper replace startswith endswith exists commit execute json dict list str int "
    "isoformat strftime now sleep info debug warning error exception critical log match search sub findall dumps "
    "loads put cancel result create_task gather run model_dump model_validate dispose".split())
MAX_CANDIDATES = 12
EDGE_TYPES = ("calls", "inherits", "imports", "overrides", "implements")
CERTAINTIES = ("exact", "candidate")
_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", "site-packages", "build", "dist"})
_UNWRAP = frozenset({"Optional", "Final", "Type", "ClassVar", "Annotated", "Required", "NotRequired"})
_PROTOCOL_BASES = frozenset({"Protocol", "ABC", "ABCMeta"})
_UPPER_ASSIGN = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*(?::\s*[^=]+)?=\s*['\"]([^'\"]+)['\"]")


# ── 자료 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resource:
    kind: str
    name: str
    direction: str      # "reads" | "writes" — 줄의 동사. 없거나 둘 다면 reads(보수적)
    line: int
    via: str            # literal | key | alias


@dataclass
class Symbol:
    id: int
    kind: str           # module | class | function | method
    name: str
    qualname: str       # 모듈 FQN까지 — `app.services.alarm.AlarmService.step`
    repo: str
    file: str
    line: int
    end_line: int
    class_id: int | None = None
    is_async: bool = False
    decorators: tuple[str, ...] = ()
    signature: str = ""
    parse_error: str = ""                       # module만. 파싱 실패도 모듈이다 — 커버리지는 전수여야 한다
    resources: tuple[Resource, ...] = ()        # function·method만. 함수별 자원 참조(방향 포함)


@dataclass
class Edge:
    src: int
    dst: int
    type: str
    certainty: str
    line: int
    count: int = 1
    via: str = ""       # 어떻게 풀었나 — self/field/param/local/import/ctor/restricted/name/callback/decorator


class IndexSource(Protocol):
    async def files(self) -> list[str]: ...
    async def read(self, path: str) -> str | None: ...


@dataclass
class Index:
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    commits: dict[str, str] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    unresolved: dict[str, int] = field(default_factory=dict)
    unresolved_shapes: dict[str, dict[str, int]] = field(default_factory=dict)   # 모양 → 이름 → 건수(6b-0 진단)
    shared_prefixes: dict[str, list[str]] = field(default_factory=dict)          # repo → `.gitmodules`의 path 접두사

    def __post_init__(self) -> None:
        self._by_key: dict[tuple[str, str], int] = {(s.repo, s.qualname): s.id for s in self.symbols}
        self._edge_pos: dict[tuple[int, int, str], int] = {(e.src, e.dst, e.type): i for i, e in enumerate(self.edges)}

    # ── 심볼 ──
    def add_symbol(self, **fields) -> Symbol:
        sym = Symbol(id=len(self.symbols), **fields)
        self.symbols.append(sym)
        self._by_key[(sym.repo, sym.qualname)] = sym.id
        return sym

    def lookup(self, repo: str, qualname: str) -> int | None:
        return self._by_key.get((repo, qualname))

    def key(self, sid: int) -> tuple[str, str]:
        s = self.symbols[sid]
        return s.repo, s.qualname

    # ── 엣지 ──
    def add_edge(self, src: int, dst: int, type: str, certainty: str, *, line: int, via: str = "") -> None:
        """같은 (src, dst, type)은 하나다. candidate로 들어왔다가 exact 근거가 생기면 **승격**한다 — 반대는 없다.
        근거는 누적되지 상쇄되지 않는다."""
        if src == dst:
            return
        pos = self._edge_pos.get((src, dst, type))
        if pos is None:
            self._edge_pos[(src, dst, type)] = len(self.edges)
            self.edges.append(Edge(src, dst, type, certainty, line, 1, via))
            return
        cur = self.edges[pos]
        cur.count += 1
        if cur.certainty == "candidate" and certainty == "exact":
            cur.certainty, cur.line, cur.via = "exact", line, via

    def edges_from(self, sid: int, type: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.src == sid and (type is None or e.type == type)]

    def edges_to(self, sid: int, type: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.dst == sid and (type is None or e.type == type)]

    # ── 자원 ──
    def writers(self, kind: str, name: str) -> list[int]:
        return [s.id for s in self.symbols
                if any(r.kind == kind and r.name == name and r.direction == "writes" for r in s.resources)]

    def readers(self, kind: str, name: str) -> list[int]:
        return [s.id for s in self.symbols
                if any(r.kind == kind and r.name == name and r.direction == "reads" for r in s.resources)]

    # ── 요약·직렬화 ──
    def summary(self) -> dict:
        kinds = Counter(s.kind for s in self.symbols)
        by_type: dict[str, dict[str, int]] = {t: {"exact": 0, "candidate": 0} for t in EDGE_TYPES}
        for e in self.edges:
            by_type.setdefault(e.type, {"exact": 0, "candidate": 0})[e.certainty] += 1
        return {"symbols": len(self.symbols), "modules": kinds.get("module", 0), "kinds": dict(kinds),
                "parse_errors": sum(1 for s in self.symbols if s.parse_error),
                "edges": by_type, "edges_total": len(self.edges),
                "resources": sum(len(s.resources) for s in self.symbols),
                "unresolved": dict(self.unresolved), "gaps": len(self.gaps)}

    def to_dict(self) -> dict:
        return {"commits": dict(self.commits),
                "symbols": [{**asdict(s), "resources": [list(r) for r in map(asdict_tuple, s.resources)],
                             "decorators": list(s.decorators)} for s in self.symbols],
                "edges": [asdict(e) for e in self.edges],
                "gaps": list(self.gaps), "unresolved": dict(self.unresolved),
                "unresolved_shapes": {k: dict(v) for k, v in self.unresolved_shapes.items()},
                "shared_prefixes": {k: list(v) for k, v in self.shared_prefixes.items()}}

    @classmethod
    def from_dict(cls, data: dict) -> "Index":
        symbols = []
        for d in data.get("symbols", []):
            d = dict(d)
            d["resources"] = tuple(Resource(*r) for r in d.get("resources", []))
            d["decorators"] = tuple(d.get("decorators", ()))
            symbols.append(Symbol(**d))
        edges = [Edge(**e) for e in data.get("edges", [])]
        return cls(symbols=symbols, edges=edges, commits=dict(data.get("commits", {})),
                   gaps=list(data.get("gaps", [])), unresolved=dict(data.get("unresolved", {})),
                   unresolved_shapes={k: dict(v) for k, v in data.get("unresolved_shapes", {}).items()},
                   shared_prefixes={k: list(v) for k, v in data.get("shared_prefixes", {}).items()})


def asdict_tuple(r: Resource) -> tuple:
    return (r.kind, r.name, r.direction, r.line, r.via)


# ── pass 1: 수집 ─────────────────────────────────────────────────────────

@dataclass
class _Func:
    sid: int
    node: ast.AST
    mod: "_Module"
    class_sid: int | None
    params: list[str]
    annotations: dict[str, ast.expr]
    defaults: dict[str, ast.expr]
    locals_: dict[str, ast.expr] = field(default_factory=dict)
    calls: list[tuple[tuple[str, ...], int]] = field(default_factory=list)
    refs: list[tuple[tuple[str, ...], int]] = field(default_factory=list)        # 인자로 넘긴 이름·속성
    decorator_calls: list[tuple[tuple[str, ...], int]] = field(default_factory=list)
    self_assigns: list[tuple[str, ast.expr]] = field(default_factory=list)      # self.attr = <식>
    free: set[str] = field(default_factory=set)          # 바깥 함수의 인자·지역 — 클로저가 부르는 이름


@dataclass
class _Class:
    sid: int
    node: ast.ClassDef
    mod: "_Module"
    bases: list[tuple[str, ...]]
    methods: dict[str, int] = field(default_factory=dict)
    attr_annotations: dict[str, ast.expr] = field(default_factory=dict)
    is_port: bool = False       # Protocol/ABC 또는 본문이 `...`·abstractmethod뿐인 메서드가 있다


class _Module:
    def __init__(self, repo: str, path: str, fqn: str, text: str, sid: int):
        self.repo, self.path, self.fqn, self.text, self.sid = repo, path, fqn, text, sid
        self.lines = text.splitlines()
        self.defs: dict[str, int] = {}                      # 최상위 클래스·함수 이름 → sid
        self.imports: dict[str, str] = {}                   # 묶인 이름 → FQN(`a.b.C` 또는 모듈)
        self.aliases: dict[str, ast.expr] = {}              # 모듈 수준 대입 — 타입 별칭·싱글턴
        self.string_consts: dict[str, str] = {}             # IDENT = "리터럴" (자원 별칭용)
        self.stars: list[str] = []                          # `from x import *`의 x(FQN) — 재수출을 따라간다
        self.funcs: list[_Func] = []
        self.classes: list[_Class] = []
        self.tree: ast.Module | None = None


def _chain(node: ast.AST) -> tuple[str, ...] | None:
    """`a.b.c` → ("a","b","c"); `Cls().m` → ("Cls","()","m"); `await x.m` → x.m. 못 펴면 None."""
    if isinstance(node, ast.Await):
        return _chain(node.value)
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        head = _chain(node.value)
        return None if head is None else head + (node.attr,)
    if isinstance(node, ast.Call):
        head = _chain(node.func)
        return None if head is None else head + ("()",)
    return None


def _module_fqn(path: str, prefix: str = "") -> str:
    parts = path[:-3].split("/") if path.endswith(".py") else path.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    fqn = ".".join(p for p in parts if p)
    return f"{prefix}.{fqn}" if prefix and fqn else (prefix or fqn)


def _relative(base_fqn: str, level: int, module: str | None, is_package: bool) -> str:
    parts = base_fqn.split(".") if base_fqn else []
    if not is_package:
        parts = parts[:-1]
    if level > 1:
        parts = parts[:len(parts) - (level - 1)] if level - 1 <= len(parts) else []
    return ".".join(p for p in parts + ([module] if module else []) if p)


def _is_port_class(node: ast.ClassDef) -> bool:
    for b in node.bases:
        tail = _chain(b)
        if tail and tail[-1] in _PROTOCOL_BASES:
            return True
    for st in node.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(d, ast.Name) and d.id == "abstractmethod" or
                   isinstance(d, ast.Attribute) and d.attr == "abstractmethod" for d in st.decorator_list):
                return True
            body = [s for s in st.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                               and isinstance(s.value.value, str))]
            if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and body[0].value.value is Ellipsis:
                return True
    return False


class _Collector:
    """모듈 하나의 심볼과 원시 참조. **해석하지 않는다.**"""

    def __init__(self, index: Index, mod: _Module):
        self.index, self.mod = index, mod

    def run(self, tree: ast.Module) -> None:
        mod = self.mod
        mod.tree = tree
        for st in tree.body:
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                self._imports(st, overwrite=True)
            elif isinstance(st, (ast.If, ast.Try)):
                # `if TYPE_CHECKING:`·`try: import x` 아래의 import — 모듈 수준 import가 이긴다.
                blocks = [st.body, st.orelse, getattr(st, "finalbody", [])] + [h.body for h in getattr(st, "handlers", [])]
                for inner in (n for blk in blocks for n in blk if isinstance(n, (ast.Import, ast.ImportFrom))):
                    self._imports(inner, overwrite=False)
            elif isinstance(st, (ast.Assign, ast.AnnAssign)):
                targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                value = st.value
                for t in targets:
                    if isinstance(t, ast.Name) and value is not None:
                        mod.aliases[t.id] = value
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            mod.string_consts[t.id] = value.value
        self._walk_body(tree.body, owner_qual=mod.fqn, class_=None)

    def _imports(self, st: ast.Import | ast.ImportFrom, *, overwrite: bool) -> None:
        """함수 안의 지연 import(`from a import b as gb` 뒤 `gb.f()`)도 같은 표에 넣는다 — 순환 import 우회로
        흔한데 안 보면 그 호출이 전부 미해석이다. 모듈 수준 이름과 겹치면 모듈 수준이 이긴다."""
        mod = self.mod
        put = mod.imports.__setitem__ if overwrite else mod.imports.setdefault
        if isinstance(st, ast.Import):
            for a in st.names:
                bound = a.asname or a.name.split(".")[0]
                put(bound, a.name if a.asname else a.name.split(".")[0])
                mod.imports.setdefault(a.name, a.name)          # `import a.b` → 체인 접두사 `a.b`도 모듈이다
            return
        is_pkg = mod.path.endswith("__init__.py")
        base = _relative(mod.fqn, st.level, st.module, is_pkg) if st.level else (st.module or "")
        for a in st.names:
            if a.name == "*":
                if base and base not in mod.stars:
                    mod.stars.append(base)
            else:
                put(a.asname or a.name, f"{base}.{a.name}" if base else a.name)

    def _walk_body(self, body: list[ast.stmt], *, owner_qual: str, class_: _Class | None) -> None:
        for st in body:
            if isinstance(st, ast.ClassDef):
                self._class(st, owner_qual, class_)
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._func(st, owner_qual, class_)
            elif class_ is not None and isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                class_.attr_annotations[st.target.id] = st.annotation
            elif class_ is not None and isinstance(st, ast.Assign):
                for t in st.targets:
                    if isinstance(t, ast.Name) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
                        self.mod.string_consts[t.id] = st.value.value
            elif isinstance(st, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                # `if TYPE_CHECKING:`·`try: import` 아래의 정의도 심볼이다.
                for inner in getattr(st, "body", []) + getattr(st, "orelse", []) + getattr(st, "finalbody", []):
                    if isinstance(inner, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._walk_body([inner], owner_qual=owner_qual, class_=class_)

    def _class(self, node: ast.ClassDef, owner_qual: str, outer: _Class | None) -> None:
        qual = f"{owner_qual}.{node.name}"
        sym = self.index.add_symbol(kind="class", name=node.name, qualname=qual, repo=self.mod.repo,
                                    file=self.mod.path, line=node.lineno, end_line=node.end_lineno or node.lineno,
                                    decorators=tuple(ast.unparse(d) for d in node.decorator_list))
        cls = _Class(sid=sym.id, node=node, mod=self.mod,
                     bases=[c for c in (_chain(b) for b in node.bases) if c], is_port=_is_port_class(node))
        self.mod.classes.append(cls)
        if outer is None and owner_qual == self.mod.fqn:
            self.mod.defs[node.name] = sym.id
        self._walk_body(node.body, owner_qual=qual, class_=cls)

    def _func(self, node: ast.AST, owner_qual: str, class_: _Class | None, *, outer: "_Func | None" = None) -> None:
        qual = f"{owner_qual}.{node.name}"
        args = node.args
        params = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        if args.vararg:
            params.append(args.vararg.arg)
        if args.kwarg:
            params.append(args.kwarg.arg)
        annotations = {a.arg: a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs if a.annotation}
        positional = args.posonlyargs + args.args
        defaults = {}
        for a, d in zip(positional[len(positional) - len(args.defaults):], args.defaults):
            defaults[a.arg] = d
        for a, d in zip(args.kwonlyargs, args.kw_defaults):
            if d is not None:
                defaults[a.arg] = d
        sym = self.index.add_symbol(
            kind="method" if class_ is not None else "function", name=node.name, qualname=qual,
            repo=self.mod.repo, file=self.mod.path, line=node.lineno, end_line=node.end_lineno or node.lineno,
            class_id=class_.sid if class_ is not None else None,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            decorators=tuple(ast.unparse(d) for d in node.decorator_list),
            signature=f"({', '.join(params)})")
        fn = _Func(sid=sym.id, node=node, mod=self.mod, class_sid=class_.sid if class_ else None,
                   params=params, annotations=annotations, defaults=defaults)
        if outer is not None:
            fn.free = set(outer.params) | set(outer.locals_) | outer.free
        self.mod.funcs.append(fn)
        if class_ is not None:
            class_.methods.setdefault(node.name, sym.id)
        elif owner_qual == self.mod.fqn:
            self.mod.defs.setdefault(node.name, sym.id)
        for d in node.decorator_list:
            c = _chain(d.func if isinstance(d, ast.Call) else d)
            if c:
                fn.decorator_calls.append((c, d.lineno))
        self._scan(fn, node.body, class_)
        # 중첩 정의는 자기 심볼을 갖는다 — 본문 훑기에선 뺐으니 여기서 내려간다.
        for st in _nested_defs(node):
            if isinstance(st, ast.ClassDef):
                self._class(st, qual, class_)
            else:
                self._func(st, qual, None, outer=fn)

    def _scan(self, fn: _Func, body: list[ast.stmt], class_: _Class | None) -> None:
        for node in _body_walk(body):
            if isinstance(node, ast.Call):
                c = _chain(node.func)
                if c:
                    fn.calls.append((c, node.lineno))
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if isinstance(arg, (ast.Name, ast.Attribute)):
                        rc = _chain(arg)
                        if rc:
                            fn.refs.append((rc, node.lineno))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self._imports(node, overwrite=False)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Name):
                    fn.locals_.setdefault(t.id, node.value)
                elif (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"):
                    fn.self_assigns.append((t.attr, node.value))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                # `x: Cls = make()` — 어노테이션이 파라미터 힌트와 같은 자격이다.
                fn.annotations.setdefault(node.target.id, node.annotation)
                if node.value is not None:
                    fn.locals_.setdefault(node.target.id, node.value)


def _nested_defs(func: ast.AST) -> list[ast.AST]:
    """바로 아래 단계의 중첩 정의만 — 더 깊은 것은 그 정의가 자기 차례에 내려간다."""
    out, queue = [], list(func.body)
    while queue:
        node = queue.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node)
            continue
        queue.extend(ast.iter_child_nodes(node))
    return sorted(out, key=lambda n: n.lineno)


def _body_walk(body: list[ast.stmt]):
    """함수 본문의 노드를 **문장 순서대로** — 중첩 def·class 안은 빼고(자기 심볼이 있다), 데코레이터는 빼고.
    순서가 있어야 "첫 대입"이 첫 대입이다."""
    queue = list(body)
    while queue:
        node = queue.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        queue.extend(ast.iter_child_nodes(node))


# ── pass 2: 해석 ─────────────────────────────────────────────────────────

class _Resolver:
    def __init__(self, index: Index, modules: dict[str, dict[str, _Module]], names: list[Name],
                 shared: dict[str, list[str]] | None = None):
        self.index = index
        self.modules = modules                     # repo → fqn → 모듈
        self.names = names
        self.shared = dict(shared or {})           # repo → 서브모듈 접두사 — 아직 인덱스에 없는 공유 라이브러리
        self.shapes: dict[str, Counter] = defaultdict(Counter)        # 못 푼 호출의 모양 → 이름 → 건수
        self.attr_source: dict[tuple[int, str], tuple] = {}           # (class, attr) → ("param", 이름, fn) | ("call", 머리, fn)
        self.override_of: dict[int, int] = {}                          # 메서드 → 가장 가까운 재정의 대상(베이스 메서드)
        self._shared_present: dict[tuple[str, str], bool] = {}         # (레포, 공유 접두사) → 그 레포 인덱스에 있나
        self.classes: dict[int, _Class] = {}
        self.funcs: dict[int, _Func] = {}
        self.methods_by_name: dict[str, list[int]] = defaultdict(list)
        self.funcs_by_name: dict[str, list[int]] = defaultdict(list)
        self.classes_by_name: dict[str, list[int]] = defaultdict(list)
        self.unresolved: Counter = Counter()
        self.param_types: dict[tuple[int, str], int] = {}          # (func sid, 파라미터) → class sid
        self.attr_types: dict[tuple[int, str], list[int]] = {}      # (class sid, 속성) → class sid들(하나면 확실)
        self.sub_of: dict[int, list[int]] = defaultdict(list)       # class → 하위 클래스들
        self.base_of: dict[int, list[int]] = defaultdict(list)      # class → 부모 클래스들
        self.ctor_sites: dict[int, list[tuple[_Func, ast.Call]]] = defaultdict(list)   # class → 생성 호출들
        self._quiet = False                                          # 인자 참조 해석 중엔 미해석을 안 센다
        self.attrs_of: dict[int, set[str]] = defaultdict(set)        # class → 어노테이션·`self.x =`로 아는 속성 이름
        for repo_mods in modules.values():
            for mod in repo_mods.values():
                for c in mod.classes:
                    self.classes[c.sid] = c
                    self.classes_by_name[c.node.name].append(c.sid)
                for f in mod.funcs:
                    self.funcs[f.sid] = f
                    sym = index.symbols[f.sid]
                    (self.methods_by_name if f.class_sid is not None else self.funcs_by_name)[sym.name].append(f.sid)

    # ── 이름 → 대상 ──
    def module(self, repo: str, fqn: str) -> _Module | None:
        return self.modules.get(repo, {}).get(fqn)

    def resolve_fqn(self, repo: str, fqn: str) -> tuple[str, Any] | None:
        """`a.b.C.m` → ("symbol", sid) | ("module", 모듈) | None. 모듈 접두사를 긴 것부터 대조한다."""
        parts = fqn.split(".")
        for cut in range(len(parts), 0, -1):
            mod = self.module(repo, ".".join(parts[:cut]))
            if mod is None:
                continue
            rest = parts[cut:]
            if not rest:
                return "module", mod
            return self._descend(mod, rest)
        return None

    def _module_of_fqn(self, repo: str, fqn: str) -> _Module | None:
        """`a.b.C` → 모듈 `a.b`(심볼이면 그것을 정의한 모듈, 모듈이면 그 자신)."""
        parts = fqn.split(".")
        for cut in range(len(parts), 0, -1):
            mod = self.module(repo, ".".join(parts[:cut]))
            if mod is not None:
                return mod
        return None

    def is_external(self, fn: _Func, chain: tuple[str, ...]) -> bool:
        """머리가 import된 이름인데 어느 레포에도 없다 — 서드파티다. 동명 메서드 추정(e)으로 떨어뜨리면
        `httpx.get()`이 우리 `get`들의 후보가 된다. 파라미터·지역이 그 이름을 가리면 import가 아니다."""
        head, mod = chain[0], fn.mod
        if head in fn.params or head in fn.locals_ or head in fn.free or head in mod.defs:
            return False
        return head in mod.imports and self.resolve_fqn(mod.repo, mod.imports[head]) is None

    def _descend(self, mod: _Module, rest: list[str], _seen: set | None = None) -> tuple[str, Any] | None:
        sid = mod.defs.get(rest[0])
        if sid is None:
            # 모듈 수준 이름이 import된 것일 수 있다(`from x import C` 뒤 `mod.C`)
            target = mod.imports.get(rest[0])
            if target is None:
                if rest[0] in mod.aliases:
                    return self._value(mod, mod.aliases[rest[0]], rest[1:])
                return self._via_star(mod, rest, _seen)
            got = self.resolve_fqn(mod.repo, target)
            if got is None or not rest[1:]:
                return got
            if got[0] == "symbol":
                return self._attr_of_symbol(got[1], rest[1:])
            if got[0] == "module":
                return self._descend(got[1], rest[1:])
            return self._value(got[2], got[1], rest[1:])
        if len(rest) == 1:
            return "symbol", sid
        return self._attr_of_symbol(sid, rest[1:])

    def _via_star(self, mod: _Module, rest: list[str], _seen: set | None = None) -> tuple[str, Any] | None:
        """`from .core import *`로 들여온 이름. 공유 라이브러리의 `__init__.py`가 이렇게 재수출하는 일이 흔하고,
        버리면 `from <공유> import Store`가 공유 코드가 인덱스에 있어도 안 풀린다(사내: 서브모듈 다섯 개가 다
        채워졌는데 공유 라이브러리 470). `__all__`이 있으면 그 이름만, 없으면 밑줄 없는 이름만 나간다 —
        파이썬과 같다. 서로 `*`하는 순환은 본 모듈을 다시 안 연다."""
        if not mod.stars:
            return None
        seen = _seen if _seen is not None else set()
        if (mod.repo, mod.fqn) in seen:
            return None
        seen.add((mod.repo, mod.fqn))
        for base in mod.stars:
            src = self.module(mod.repo, base)
            if src is None or not _exports(src, rest[0]):
                continue
            got = self._descend(src, rest, seen)
            if got is not None:
                return got
        return None

    def _value(self, owner: _Module, expr: ast.expr, attrs: list[str]) -> tuple[str, Any, Any] | None:
        """모듈 수준 값(싱글턴). 다른 모듈에서 `from m import logger`로 들여 `logger.info()`로 부르면 그 값의 클래스는
        **정의한 모듈**에서 정해야 한다 — 부르는 쪽 모듈에는 그 클래스 이름이 없다(사내 공유 라이브러리 470이 전부
        공유 쪽 로깅 모듈의 `logger` 하나였다)."""
        if not attrs:
            return "expr", expr, owner
        c = self._class_of_value(owner, expr)
        return self._attr_of_symbol(c, attrs) if c is not None else None

    def _class_of_value(self, owner: _Module, expr: ast.expr, hops: int = 0) -> int | None:
        """모듈 수준 값이 어느 클래스의 인스턴스인가 — `AppLog()`, `make_client()`의 반환, 다른 값의 별칭."""
        if hops > 8:
            return None
        if isinstance(expr, ast.Await):
            expr = expr.value
        if isinstance(expr, ast.Call):
            c = self.class_of_annotation(owner, expr.func, hops=hops + 1)
            if c is not None:
                return c
            chain = _chain(expr.func)
            if chain and "()" not in chain:
                got, used = self.resolve_prefix(owner, chain)
                if got and got[0] == "symbol" and used == len(chain) and got[1] in self.funcs:
                    return self.returns_class(self.funcs[got[1]], hops=hops + 1)
            return None
        chain = _chain(expr)
        if chain and "()" not in chain:
            got, used = self.resolve_prefix(owner, chain)
            if got and got[0] == "expr" and used == len(chain):
                return self._class_of_value(got[2], got[1], hops + 1)
        return None

    def _value_origin(self, owner: _Module, expr: ast.expr) -> str | None:
        """값이 인덱스 밖 import에서 만들어졌으면 그 FQN — `client = httpx.Client()`, `logger = _base.bind(...)`."""
        v = expr.value if isinstance(expr, ast.Await) else expr
        vc = _chain(v.func) if isinstance(v, ast.Call) else _chain(v)
        if vc and vc[0] in owner.imports and self.resolve_fqn(owner.repo, owner.imports[vc[0]]) is None:
            return owner.imports[vc[0]]
        return None

    def _origin(self, repo: str, target: str, hops: int = 0) -> str:
        """이름이 실제로 온 곳. 우리 모듈이 서드파티 이름을 다시 내보내면(`from loguru import logger`) 그 서드파티다 —
        공유 라이브러리로 세면 "인덱서가 못 찾은 우리 코드"로 읽힌다. 따라갈 수 없으면 그대로."""
        if hops > 8:
            return target
        mods = self.modules.get(repo, {})
        parts = target.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            owner = mods.get(".".join(parts[:cut]))
            if owner is None:
                continue
            name, rest = parts[cut], parts[cut + 1:]
            if name in owner.imports:
                return self._origin(repo, ".".join([owner.imports[name], *rest]), hops + 1)
            if name in owner.aliases:
                got = self._value_origin(owner, owner.aliases[name])
                return self._origin(repo, got, hops + 1) if got else target
            return target
        return target

    def _attr_of_symbol(self, sid: int, rest: list[str]) -> tuple[str, Any] | None:
        if sid in self.classes and len(rest) == 1:
            m = self.find_method(sid, rest[0])
            return ("symbol", m) if m is not None else None
        return None

    def resolve_name(self, mod: _Module, name: str) -> tuple[str, Any] | None:
        """모듈 안의 이름 하나 — 모듈 정의 → import 별칭 → 모듈 수준 대입(별칭 식)."""
        if name in mod.defs:
            return "symbol", mod.defs[name]
        if name in mod.imports:
            return self.resolve_fqn(mod.repo, mod.imports[name])
        if name in mod.aliases:
            return "expr", mod.aliases[name], mod
        return self._via_star(mod, [name])

    def resolve_prefix(self, mod: _Module, chain: tuple[str, ...]) -> tuple[tuple[str, Any] | None, int]:
        """체인 접두사를 긴 것부터 import·정의와 대조 — `import a.b` 뒤 `a.b.c()`는 머리 한 글자로 안 풀린다."""
        for cut in range(len(chain), 0, -1):
            head = ".".join(chain[:cut])
            if head in mod.imports:
                return self.resolve_fqn(mod.repo, mod.imports[head]), cut
            if cut == 1:
                got = self.resolve_name(mod, head)
                if got is not None:
                    return got, 1
        return None, 0

    # ── 클래스 ──
    def class_of_annotation(self, mod: _Module, expr: ast.expr | None, *, hops: int = 0) -> int | None:
        """어노테이션이 가리키는 클래스. `Optional[X]`·`Final[X]`·`Annotated[X, …]`·`Type[X]`·`X | None`·문자열은
        벗기고 `list[X]` 같은 컨테이너는 **안 벗긴다** — 원소에 대한 호출이 아니다."""
        if expr is None or hops > 8:
            return None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                return self.class_of_annotation(mod, ast.parse(expr.value, mode="eval").body, hops=hops + 1)
            except SyntaxError:
                return None
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            for side in (expr.left, expr.right):
                if isinstance(side, ast.Constant) and side.value is None:
                    continue
                got = self.class_of_annotation(mod, side, hops=hops + 1)
                if got is not None:
                    return got
            return None
        if isinstance(expr, ast.Subscript):
            head = _chain(expr.value)
            if head and head[-1] in _UNWRAP:
                inner = expr.slice
                first = inner.elts[0] if isinstance(inner, ast.Tuple) and inner.elts else inner
                if head[-1] == "Annotated" and isinstance(inner, ast.Tuple):
                    provided = self._depends_provider_class(mod, inner.elts[1:])
                    if provided is not None:
                        return provided
                return self.class_of_annotation(mod, first, hops=hops + 1)
            return None
        chain = _chain(expr)
        if chain is None or chain[-1] == "()":
            return None
        got, used = self.resolve_prefix(mod, chain)
        if got is None:
            return None
        if got[0] == "symbol" and got[1] in self.classes and used == len(chain):
            return got[1]
        if got[0] == "expr":
            return self.class_of_annotation(got[2], got[1], hops=hops + 1)
        if got[0] == "module" and used < len(chain):
            inner = self._descend(got[1], list(chain[used:]))
            if inner and inner[0] == "symbol" and inner[1] in self.classes:
                return inner[1]
        return None

    def _depends_provider_class(self, mod: _Module, extras: list[ast.expr]) -> int | None:
        """`Annotated[T, Depends(g)]` — g가 돌려주는 클래스가 진짜 타입이다(포트 뒤의 구현체)."""
        for e in extras:
            if isinstance(e, ast.Call) and _chain(e.func) and _chain(e.func)[-1] == "Depends" and e.args:
                got = self.resolve_name(mod, _chain(e.args[0])[0]) if _chain(e.args[0]) else None
                if got and got[0] == "symbol" and got[1] in self.funcs:
                    return self.returns_class(self.funcs[got[1]])
        return None

    def returns_class(self, fn: _Func, *, hops: int = 0) -> int | None:
        if hops > 4:
            return None
        for node in ast.walk(fn.node):
            if isinstance(node, ast.Return) and node.value is not None:
                got = self.class_of_expr(fn, node.value, hops=hops + 1)
                if got is not None:
                    return got
        return None

    def class_of_expr(self, fn: _Func, expr: ast.expr, *, hops: int = 0) -> int | None:
        """함수 안의 식이 어떤 클래스의 인스턴스인가 — 생성자 호출, 타입을 아는 이름, `self.attr`."""
        if hops > 8:
            return None
        if isinstance(expr, ast.Await):
            return self.class_of_expr(fn, expr.value, hops=hops + 1)
        if isinstance(expr, ast.Call):
            chain = _chain(expr.func)
            if chain:
                got = self.class_of_annotation(fn.mod, expr.func, hops=hops + 1)
                if got is not None:
                    return got
                target = self._call_target(fn, chain, hops=hops + 1)
                if target is not None and target in self.funcs:
                    return self.returns_class(self.funcs[target], hops=hops + 1)
            return None
        if isinstance(expr, ast.Name):
            return self.type_of_var(fn, expr.id, hops=hops + 1)
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "self" \
                and fn.class_sid is not None:
            types = self.attr_types.get((fn.class_sid, expr.attr)) or []
            return types[0] if len(types) == 1 else None
        return None

    def type_of_var(self, fn: _Func, var: str, *, hops: int = 0) -> int | None:
        if (fn.sid, var) in self.param_types:
            return self.param_types[(fn.sid, var)]
        # `a = a.next()` — a의 타입을 알려면 a.next()의 반환 타입이, 그걸 알려면 a의 타입이 필요하다. 이 재귀가
        # `_call_target`→`resolve_call`을 지나며 hops를 0으로 되돌려 실제 코드(`src/`)에서 RecursionError가 났다.
        # hops는 재귀 전체가 한 계수를 나눠 쓴다.
        if hops > 8:
            return None
        if var in fn.locals_:
            return self.class_of_expr(fn, fn.locals_[var], hops=hops + 1)
        got = self.resolve_name(fn.mod, var)
        if got and got[0] == "expr":
            return self._class_of_value(got[2], got[1], hops + 1) \
                or self.class_of_annotation(got[2], got[1], hops=hops + 1)
        return None

    def ancestors(self, csid: int) -> list[int]:
        out, seen, stack = [], {csid}, list(self.base_of.get(csid, []))
        while stack:
            b = stack.pop(0)
            if b in seen:
                continue
            seen.add(b)
            out.append(b)
            stack.extend(self.base_of.get(b, []))
        return out

    def descendants(self, csid: int) -> list[int]:
        out, seen, stack = [], {csid}, list(self.sub_of.get(csid, []))
        while stack:
            s = stack.pop(0)
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
            stack.extend(self.sub_of.get(s, []))
        return out

    def find_method(self, csid: int, name: str) -> int | None:
        for c in [csid] + self.ancestors(csid):
            m = self.classes[c].methods.get(name) if c in self.classes else None
            if m is not None:
                return m
        return None

    # ── 호출의 대상 ──
    def _call_target(self, fn: _Func, chain: tuple[str, ...], *, hops: int = 0) -> int | None:
        """확정으로 풀리는 호출 대상 하나 — 생성자면 `__init__`(없으면 클래스). 타입 추론용이라 미해석을 안 센다 —
        같은 호출을 connect()가 다시 풀 때 한 번 더 세면 두 배가 된다."""
        prev, self._quiet = self._quiet, True
        try:
            res = self.resolve_call(fn, chain, hops=hops)
        finally:
            self._quiet = prev
        if res and res[0] == "exact" and len(res[1]) == 1:
            return res[1][0]
        return None

    def resolve_call(self, fn: _Func, chain: tuple[str, ...], *, hops: int = 0
                     ) -> tuple[str, list[int], str] | None:
        """`(확신, 대상들, via)` 또는 None. 순서가 전부다 — 위에서 걸리면 거기서 끝, 아래로 갈수록 약하다.
        `hops`는 타입 추론(c)이 다른 호출의 반환 타입을 묻는 깊이 — 재귀 전체가 한 계수를 나눠 쓴다."""
        mod = fn.mod
        if chain[-1] == "()" or hops > 8:
            return None
        # (a) 단일 이름 — 파라미터·지역이 모듈 이름을 가린다. `def f(clock): clock()`을 import된 `clock`으로
        # 풀면 확실 표시가 붙은 거짓 엣지다(실제 코드에서 났다).
        if len(chain) == 1:
            name = chain[0]
            if name in fn.params or (name in fn.free and name not in fn.locals_):
                # 클로저: 데코레이터의 `wrapper`가 바깥 인자 `func`를 부른다 — 실행 시점에 정해진다(사내 맨 이름 func 28).
                self._count("variable_call")
                return None
            if name in fn.locals_:
                alias = _chain(fn.locals_[name])
                if alias and "()" not in alias and alias != chain:
                    res = self.resolve_call(fn, alias, hops=hops + 1)
                    if res and res[0] == "exact":
                        return res
                self._count("variable_call")
                return None
            got = self.resolve_name(mod, name)
            if got is None and hasattr(builtins, name):
                self._count("builtin")
                return None
            if got and got[0] == "symbol":
                return self._as_target(got[1], "name")
            if got and got[0] == "expr":
                c = self.class_of_annotation(got[2], got[1], hops=hops + 1)
                return self._as_target(c, "alias") if c is not None else None
            if got is not None:
                return None
            if self.is_external(fn, chain):
                self._count("external")
                self._note_external(fn, chain)
                return None
            cands = self._same_repo(fn, self.funcs_by_name.get(name, []))
            if not cands:
                self._count("unknown")
                self._note("bare_name", name)
                return None
            if len(cands) > MAX_CANDIDATES:
                self._count("too_many")
                self._note("too_many_method", name)
                return None
            return "candidate", cands, "name"
        head, rest = chain[0], chain[1:]
        # (b) self.m() / cls.m()
        if head in ("self", "cls") and fn.class_sid is not None:
            if len(rest) == 1:
                m = self.find_method(fn.class_sid, rest[0])
                if m is not None:
                    return "exact", [m], "self"
                subs = [self.classes[s].methods[rest[0]] for s in self.descendants(fn.class_sid)
                        if rest[0] in self.classes[s].methods]
                if subs:
                    return "candidate", subs, "restricted"          # 템플릿 메서드 — 실행 시점 수신 타입은 자기 하위
                # `self._clock()` — 메서드가 아니라 호출 가능한 필드다. method_missing으로 세면 해석 실패처럼 보인다.
                known = any(rest[0] in self.attrs_of.get(c, ()) for c in [fn.class_sid] + self.ancestors(fn.class_sid))
                self._count("field_call" if known else "method_missing")
                return None
            if rest[-1] == "()":
                return None
            types = self.attr_types.get((fn.class_sid, rest[0])) or []
            if len(rest) == 2 and types:
                found = [self.find_method(t, rest[1]) for t in types]
                found = [m for m in found if m is not None]
                if found:
                    return ("exact" if len(types) == 1 else "candidate"), found, "field"
                self._count("method_missing")
                return None
            return self._fallback(fn, chain, "field_unknown")
        if self.is_external(fn, chain):
            self._count("external")
            self._note_external(fn, chain)
            return None
        # (c′) 생성자 호출을 받는 쪽 — `Cls().m()`
        if "()" in rest or chain[-1] == "()":
            cut = list(chain).index("()")
            head_chain, tail = chain[:cut], chain[cut + 1:]
            # `super().m()` — `()`가 낀 체인이라 여기로 떨어져 동명 후보가 되고 있었다(사내 super 257 · __init__ 85).
            if head_chain == ("super",) and fn.class_sid is not None and len(tail) == 1:
                return self._super_call(fn, tail[0])
            plain = all(p.isidentifier() for p in head_chain)
            c = self.class_of_annotation(mod, ast.parse(".".join(head_chain), mode="eval").body, hops=hops + 1) \
                if plain else None
            via = "ctor"
            if c is None and plain:
                # `f(...).m()` — f의 반환 클래스에서. 생성자가 아니라 팩토리 함수를 거친 수신자.
                target = self._call_target(fn, head_chain, hops=hops + 1)
                if target is not None and target in self.funcs:
                    c, via = self.returns_class(self.funcs[target], hops=hops + 1), "returns"
            if c is not None and len(tail) == 1:
                m = self.find_method(c, tail[0])
                return ("exact", [m], via) if m is not None else None
            return self._fallback(fn, chain, "call_recv")
        # (c) 타입을 아는 이름 경유 — 파라미터·지역·모듈 싱글턴
        if len(rest) == 1:
            t = self.type_of_var(fn, head, hops=hops + 1) if head not in mod.imports and head not in mod.defs else None
            if t is not None:
                m = self.find_method(t, rest[0])
                if m is not None:
                    return "exact", [m], "param" if (fn.sid, head) in self.param_types else "local"
                self._count("method_missing")
                return None
        # (d) import·모듈·클래스 경유 — 긴 접두사부터
        got, used = self.resolve_prefix(mod, chain)
        if got is not None:
            tail = list(chain[used:])
            if got[0] == "module":
                inner = self._descend(got[1], tail) if tail else None
                return self._as_target(inner[1], "import") if inner and inner[0] == "symbol" else None
            if got[0] == "symbol":
                if not tail:
                    return self._as_target(got[1], "import")
                if got[1] in self.classes and len(tail) == 1:
                    m = self.find_method(got[1], tail[0])
                    return ("exact", [m], "class") if m is not None else None
                return None
            if got[0] == "expr":
                owner = got[2]
                c = self.class_of_annotation(owner, got[1], hops=hops + 1) \
                    or self._class_of_value(owner, got[1], hops + 1)
                if c is None:
                    # 값의 클래스를 모른다 — 전엔 아무것도 안 세고 버렸다. 서드파티에서 만든 값이면
                    # (`client = httpx.Client()`) 그쪽으로 센다.
                    origin = self._value_origin(owner, got[1])
                    if origin is not None:
                        self._count("external")
                        self._note_target(owner.repo, origin)
                    else:
                        self._count("unknown")
                        self._note_receiver(fn, chain)
                    return None
                if len(tail) == 1:
                    m = self.find_method(c, tail[0])
                    return ("exact", [m], "singleton") if m is not None else None
                return None
        # (e) 전부 실패 — 같은 이름 메서드 전부에 candidate (stoplist·상한)
        return self._fallback(fn, chain, "name")

    def _as_target(self, sid: int, via: str) -> tuple[str, list[int], str] | None:
        if sid in self.classes:
            init = self.find_method(sid, "__init__")
            return "exact", [init if init is not None else sid], "ctor"
        if sid in self.funcs:
            return "exact", [sid], via
        return None

    def _count(self, why: str) -> None:
        if not self._quiet:
            self.unresolved[why] += 1

    def _same_repo(self, fn: _Func, pool: list[int]) -> list[int]:
        """추정 후보는 부르는 쪽 레포 안에서만 — 다른 레포의 함수는 이 프로세스에 없다(레포 사이는 HTTP·Kafka, 흐름
        그래프의 몫). 공유 라이브러리는 레포마다 자기 핀으로 들어오므로(6b-2) 레포를 건너면 같은 이름이 레포 수만큼
        겹친다."""
        repo = fn.mod.repo
        return [s for s in pool if s != fn.sid and self.index.symbols[s].repo == repo]

    def _super_call(self, fn: _Func, name: str) -> tuple[str, list[int], str] | None:
        """직접 베이스들의 MRO에서 — 하나면 확실, 베이스마다 다르면 그 조상들만 후보(협력적 super는 여럿을 돈다).
        베이스가 인덱스 밖(공유 라이브러리·서드파티)이면 그쪽 미해석으로 센다 — 동명 후보로 떨어뜨리지 않는다."""
        found: list[int] = []
        for b in self.base_of.get(fn.class_sid, []):
            m = self.find_method(b, name)
            if m is not None and m not in found:
                found.append(m)
        if found:
            return ("exact" if len(found) == 1 else "candidate"), found, "super"
        c = self.classes[fn.class_sid]
        for b in c.bases:
            target = c.mod.imports.get(b[0])
            if target and self.resolve_fqn(c.mod.repo, target) is None:
                self._count("external")
                self._note_target(c.mod.repo, target)
                return None
        self._count("method_missing")
        return None

    def _fallback(self, fn: _Func, chain: tuple[str, ...], why: str) -> tuple[str, list[int], str] | None:
        meth = chain[-1]
        if meth in STOPLIST:
            self._count("stoplist")
            return None
        cands = self._same_repo(fn, self.methods_by_name.get(meth, []))
        if not cands:
            self._count("unknown")
            self._note_receiver(fn, chain)
            return None
        if len(cands) > MAX_CANDIDATES:
            # 재정의 뿌리로 접는다 — 구현체 30개가 전부 `Base.save`를 재정의하면 후보 30개 대신 뿌리 하나다.
            # `overrides`가 구현체로 이어 주므로 impact 질의는 그대로 된다. 뿌리가 인덱스 밖(공유 라이브러리)이면
            # 안 접히고 지금처럼 버린다(사내 process 265 · save 174 · collect 116).
            roots: list[int] = []
            for m in cands:
                for _ in range(64):
                    if m not in self.override_of:
                        break
                    m = self.override_of[m]
                if m not in roots:
                    roots.append(m)
            if len(roots) <= MAX_CANDIDATES:
                return "candidate", roots, "root"
            self._count("too_many")
            self._note("too_many_method", meth)
            self._note_receiver(fn, chain)
            return None
        return "candidate", cands, "name"

    # ── 미해석 진단(6b-0) — 못 푼 것의 **모양**을 센다. 숫자가 다음 커밋을 정한다 ──
    def _note(self, shape: str, name: str) -> None:
        if not self._quiet:
            self.shapes[shape][name] += 1

    def _is_shared(self, repo: str, fqn: str) -> bool:
        return any(fqn == p or fqn.startswith(p + ".") for p in self.shared.get(repo, ()))

    def _import_shared(self, fn: _Func, head: str | None) -> bool | None:
        """머리가 import면 공유 라이브러리인지(True/False), import가 아니면 None."""
        if head is None or head in fn.params or head in fn.locals_ or head not in fn.mod.imports:
            return None
        return self._is_shared(fn.mod.repo, fn.mod.imports[head])

    def _annotation_shared(self, fn: _Func, param: str) -> bool:
        ann = fn.annotations.get(param)
        if isinstance(ann, ast.Subscript):
            ann = ann.slice.elts[0] if isinstance(ann.slice, ast.Tuple) and ann.slice.elts else ann.slice
        chain = _chain(ann) if ann is not None else None
        return bool(chain) and self._import_shared(fn, chain[0]) is True

    def _note_external(self, fn: _Func, chain: tuple[str, ...]) -> None:
        self._note_target(fn.mod.repo, fn.mod.imports[chain[0]])

    def _note_target(self, repo: str, target: str) -> None:
        origin = self._origin(repo, target)
        if not self._is_shared(repo, origin):
            return self._note("external_third", origin.split(".")[0])
        self._note("external_shared", target.split(".")[0])
        # 이유를 가른다 — 레포에 공유 모듈이 하나도 없으면 서브모듈이 안 들어온 것(`code status`의 일), 모듈은
        # 있는데 이름을 못 찾으면 인덱서의 일(재수출·동적 이름). 한 숫자로 세면 사내에 한 번 더 물어야 한다.
        mods = self.modules.get(repo, {})
        prefix = next(p for p in self.shared.get(repo, ()) if target == p or target.startswith(p + "."))
        key = (repo, prefix)
        if key not in self._shared_present:
            self._shared_present[key] = any(f == prefix or f.startswith(prefix + ".") for f in mods)
        if not self._shared_present[key]:
            return self._note("shared_absent", repo)
        parts = target.split(".")
        cut = next((c for c in range(len(parts) - 1, 0, -1) if ".".join(parts[:c]) in mods), len(parts) - 1)
        self._note("shared_unnamed", ".".join(parts[:cut + 1]))

    def _note_receiver(self, fn: _Func, chain: tuple[str, ...]) -> None:
        """`x.m()`을 못 풀었을 때 x가 무엇인가 — 주입된 필드·힌트 없는 파라미터·외부 호출 결과의 지역·공유
        라이브러리 타입. 사내 두 번째 숫자(too_many 823 · unknown 1009)를 이 묶음으로 갈라야 6b-1이 정해진다."""
        head = chain[0]
        if head in ("self", "cls") and fn.class_sid is not None and len(chain) >= 2:
            attr = chain[1]
            src = next((self.attr_source[(c, attr)] for c in [fn.class_sid] + self.ancestors(fn.class_sid)
                        if (c, attr) in self.attr_source), None)
            if src is None:
                return self._note("self_attr", attr)
            kind, origin, ofn = src
            if kind == "param":
                return self._note("self_attr_shared" if self._annotation_shared(ofn, origin) else "self_attr_param", attr)
            if kind == "call":
                return self._note("self_attr_shared" if self._import_shared(ofn, origin) else "self_attr_call", attr)
            return self._note("self_attr", attr)
        if head in fn.params:
            return self._note("param_shared" if self._annotation_shared(fn, head) else "param", head)
        if head in fn.locals_:
            v = fn.locals_[head]
            v = v.value if isinstance(v, ast.Await) else v
            vc = _chain(v.func) if isinstance(v, ast.Call) else _chain(v)
            shared = self._import_shared(fn, vc[0]) if vc else None
            if shared is True:
                return self._note("local_shared", head)
            if shared is False and self.resolve_fqn(fn.mod.repo, fn.mod.imports[vc[0]]) is None:
                return self._note("local_external", head)
            return self._note("local", head)
        return self._note("other", head)

    def resolve_ref(self, fn: _Func, chain: tuple[str, ...]) -> int | None:
        """인자로 넘긴 이름이 **확정으로** 가리키는 함수·메서드 — 등록처에서 실행된다. candidate는 안 만든다:
        인자로 넘어가는 이름은 함수가 아닌 것이 훨씬 많아 추측하면 전부 거짓이 된다."""
        if "()" in chain or (len(chain) == 1 and chain[0] in fn.params):
            return None
        prev, self._quiet = self._quiet, True
        try:
            res = self.resolve_call(fn, chain)
        finally:
            self._quiet = prev
        if res is None or res[0] != "exact" or len(res[1]) != 1 or res[2] == "ctor":
            return None
        target = res[1][0]
        return target if target in self.funcs else None

    # ── 타입 테이블 ──
    def build_types(self) -> None:
        for csid, c in self.classes.items():
            self.attrs_of[csid].update(c.attr_annotations)
        for fn in self.funcs.values():
            if fn.class_sid is not None:
                self.attrs_of[fn.class_sid].update(a for a, _ in fn.self_assigns)
                for attr, value in fn.self_assigns:
                    if (fn.class_sid, attr) in self.attr_source:
                        continue
                    if isinstance(value, ast.Name) and value.id in fn.params:
                        self.attr_source[(fn.class_sid, attr)] = ("param", value.id, fn)
                    else:
                        v = value.value if isinstance(value, ast.Await) else value
                        vc = _chain(v.func) if isinstance(v, ast.Call) else None
                        self.attr_source[(fn.class_sid, attr)] = ("call", vc[0], fn) if vc else ("other", "", fn)
        # 상속 먼저 — find_method가 조상을 봐야 한다.
        for csid, c in self.classes.items():
            for b in c.bases:
                got = self.class_of_annotation(c.mod, ast.parse(".".join(p for p in b if p != "()"), mode="eval").body) \
                    if all(p.isidentifier() or p == "()" for p in b) else None
                if got is not None and got != csid:
                    self.base_of[csid].append(got)
                    self.sub_of[got].append(csid)
        # 파라미터 힌트 → 그 함수의 변수 타입
        for fsid, fn in self.funcs.items():
            for p, ann in fn.annotations.items():
                c = self.class_of_annotation(fn.mod, ann)
                if c is None and p in fn.defaults:
                    c = self._depends_provider_class(fn.mod, [fn.defaults[p]])
                if c is not None:
                    self.param_types[(fsid, p)] = c
            for p, d in fn.defaults.items():
                if (fsid, p) not in self.param_types:
                    c = self._depends_provider_class(fn.mod, [d])
                    if c is not None:
                        self.param_types[(fsid, p)] = c
        # 생성 호출 지점 — 역전파가 쓴다. 한 번만 훑는다.
        for fn in self.funcs.values():
            for node in _body_walk(fn.node.body):
                if isinstance(node, ast.Call):
                    c = self.class_of_annotation(fn.mod, node.func)
                    if c is not None:
                        self.ctor_sites[c].append((fn, node))
        # 속성 타입: 클래스 수준 어노테이션, `self.attr = Cls()`, `self.attr = param`(힌트 있으면)
        pending: list[tuple[int, str, _Func, str]] = []          # 힌트 없는 주입 — 생성 지점 역전파 후보
        for csid, c in self.classes.items():
            for attr, ann in c.attr_annotations.items():
                t = self.class_of_annotation(c.mod, ann)
                if t is not None:
                    self.attr_types[(csid, attr)] = [t]
            for msid in c.methods.values():
                fn = self.funcs[msid]
                for attr, value in fn.self_assigns:
                    if (csid, attr) in self.attr_types:
                        continue
                    if isinstance(value, ast.Name) and value.id in fn.params:
                        t = self.param_types.get((fn.sid, value.id))
                        if t is not None:
                            self.attr_types[(csid, attr)] = [t]
                        else:
                            pending.append((csid, attr, fn, value.id))
                        continue
                    t = self.class_of_expr(fn, value)
                    if t is not None:
                        self.attr_types[(csid, attr)] = [t]
        # 생성 지점 역전파 — `self.repo = repo`에 힌트가 없으면 `Cls(...)` 호출들의 그 인자를 본다. 생성 지점이
        # 하나거나 전부 같으면 확실, 갈리면 추정(여러 타입), 없으면 모른다.
        for csid, attr, init_fn, param in pending:
            types = self._types_at_construction(csid, init_fn, param)
            if types:
                self.attr_types.setdefault((csid, attr), types)

    def _types_at_construction(self, csid: int, init_fn: _Func, param: str) -> list[int]:
        positional = [p for p in init_fn.params if p != "self"]
        pos = positional.index(param) if param in positional else None
        found: list[int] = []
        for fn, node in self.ctor_sites.get(csid, []):
            arg = None
            for kw in node.keywords:
                if kw.arg == param:
                    arg = kw.value
            if arg is None and pos is not None and pos < len(node.args):
                arg = node.args[pos]
            if arg is None:
                continue
            t = self.class_of_expr(fn, arg)
            if t is not None and t not in found:
                found.append(t)
        return found

    # ── 엣지 ──
    def connect(self) -> None:
        idx = self.index
        for csid, c in self.classes.items():
            for b in self.base_of.get(csid, []):
                idx.add_edge(csid, b, "inherits", "exact", line=c.node.lineno, via="base")
            for name, msid in c.methods.items():
                if name.startswith("__"):
                    continue
                for a in self.ancestors(csid):
                    base_m = self.classes[a].methods.get(name) if a in self.classes else None
                    if base_m is not None:
                        idx.add_edge(msid, base_m, "overrides", "exact", line=self.funcs[msid].node.lineno, via="mro")
                        self.override_of[msid] = base_m
                        break           # 가장 가까운 조상 하나 — 전부에 걸면 베이스 40 × 구현 30이 1200 엣지다(사내 3741)
        for fn in self.funcs.values():
            for chain, line in fn.calls:
                res = self.resolve_call(fn, chain)
                if res is None:
                    continue
                certainty, targets, via = res
                for t in targets:
                    idx.add_edge(fn.sid, t, "calls", certainty, line=line, via=via)
            for chain, line in fn.decorator_calls:
                res = self.resolve_call(fn, chain)
                if res and res[0] == "exact":
                    for t in res[1]:
                        idx.add_edge(fn.sid, t, "calls", "exact", line=line, via="decorator")
            for chain, line in fn.refs:
                t = self.resolve_ref(fn, chain)
                if t is not None:
                    idx.add_edge(fn.sid, t, "calls", "exact", line=line, via="callback")
        for repo_mods in self.modules.values():
            for mod in repo_mods.values():
                for target in set(mod.imports.values()):
                    # `from a.b import C`의 target은 `a.b.C`다 — 심볼이면 그 심볼을 **품은 모듈**로 잇는다.
                    owner = self._module_of_fqn(mod.repo, target)
                    if owner is not None and owner.sid != mod.sid:
                        idx.add_edge(mod.sid, owner.sid, "imports", "exact", line=1, via="import")
        self._link_same_name_ports()

    def _link_same_name_ports(self) -> None:
        """포트(Protocol/ABC)와 **이름이 같은** 클래스는 그 구현체다 — 포트/어댑터 관례. 메서드 단위로 잇고 없는
        메서드는 엣지 없이 gap에 적는다. 동명이 여럿이면 포트와 모듈 경로를 가장 길게 공유하는 쪽, 그다음 메서드를
        더 많이 갖춘 쪽, 그래도 같으면 둘 다 추정이다 — 한 레포에 도메인이 여럿이면 다른 도메인의 동명 클래스가
        걸리는 것이 사내 도구가 적어 둔 약점이었다."""
        idx = self.index
        for psid, port in self.classes.items():
            if not port.is_port:
                continue
            same = [c for c in self.classes_by_name.get(port.node.name, [])
                    if c != psid and not self.classes[c].is_port and self.classes[c].mod.repo == port.mod.repo]
            if not same:
                continue
            declared = [n for n in port.methods if not n.startswith("__")]
            pq = idx.symbols[psid].qualname.split(".")

            def score(c: int) -> tuple[int, int]:
                cq = idx.symbols[c].qualname.split(".")
                shared = 0
                for a, b in zip(pq, cq):
                    if a != b:
                        break
                    shared += 1
                covered = sum(1 for n in declared if self.find_method(c, n) is not None)
                return shared, covered

            ranked = sorted(same, key=score, reverse=True)
            top = score(ranked[0])
            chosen = [c for c in ranked if score(c) == top]
            certainty = "exact" if len(chosen) == 1 else "candidate"
            for c in chosen:
                missing = []
                for n in declared:
                    m = self.find_method(c, n)
                    if m is None:
                        missing.append(n)
                        continue
                    idx.add_edge(m, port.methods[n], "implements", certainty, line=self.funcs[m].node.lineno, via="same_name")
                if missing:
                    idx.gaps.append(f"{idx.symbols[psid].qualname}: 동명 클래스 {idx.symbols[c].qualname}에 없는 메서드: "
                                    f"{', '.join(missing[:3])}")

    # ── 자원 참조 ──
    def attach_resources(self) -> None:
        if not self.names:
            return
        aliases: dict[str, Name] = {}
        wanted: dict[str, Name] = {}
        for n in self.names:
            wanted.setdefault(n.code_value or n.value, n)
            wanted.setdefault(n.key_token, n)
        for repo_mods in self.modules.values():
            for mod in repo_mods.values():
                for ident, value in mod.string_consts.items():
                    if value in wanted and ident not in aliases:
                        aliases[ident] = wanted[value]
        for fn in self.funcs.values():
            node = fn.node
            start, end = node.lineno, node.end_lineno or node.lineno
            # 데코레이터는 빼고 본문부터 — `_collect_reads`는 줄 오프셋으로 답한다.
            body_start = node.body[0].lineno if node.body else start
            segment = "\n".join(fn.mod.lines[body_start - 1:end])
            found: list[Resource] = []

            def add(kind: str, name: str, grade: str, file: str, line: int, via: str) -> None:
                text = fn.mod.lines[line - 1] if 0 < line <= len(fn.mod.lines) else ""
                d = "writes" if direction(text) == "writes" else "reads"
                r = Resource(kind, name, d, line, via)
                if r not in found:
                    found.append(r)

            _collect_reads(segment, body_start, fn.mod.path, "확실", self.names, aliases, add)
            if found:
                sym = self.index.symbols[fn.sid]
                self.index.symbols[fn.sid] = Symbol(**{**asdict(sym), "resources": tuple(found),
                                                     "decorators": sym.decorators})


# ── 조립 ─────────────────────────────────────────────────────────────────

async def build_index(sources: dict[str, IndexSource], *, names: Iterable[Name] = (),
                      commits: dict[str, str] | None = None, prefixes: dict[str, str] | None = None) -> Index:
    """레포들의 인덱스 하나. 던지지 않는다 — 못 읽는 파일은 `parse_error`가 붙은 모듈로 남는다."""
    index = Index(commits=dict(commits or {}))
    prefixes = dict(prefixes or {})
    modules: dict[str, dict[str, _Module]] = {}
    for repo, source in sorted(sources.items()):
        try:
            files = await source.files()
        except Exception as exc:                                        # noqa: BLE001
            index.gaps.append(f"{repo}: 파일 목록을 못 읽었다 — {type(exc).__name__}: {exc}")
            files = []
        # 채워진 서브모듈의 파일은 이미 목록에 있다(리더의 `ls`, 6b-2). 안 채워진 것으로 가는 import가 "못 본 우리
        # 코드"인지 알아야 서드파티와 가른다(진단).
        try:
            gitmodules = await source.read(".gitmodules")
        except Exception:                                               # noqa: BLE001
            gitmodules = None
        if gitmodules:
            index.shared_prefixes[repo] = _gitmodules_prefixes(gitmodules)
        for path in sorted(f for f in files if is_indexed(f)):
            fqn = _module_fqn(path, prefixes.get(repo, ""))
            try:
                text = await source.read(path)
            except Exception as exc:                                    # noqa: BLE001
                text = None
                err = f"{type(exc).__name__}: {exc}"
            else:
                err = "" if text is not None else "읽기 실패"
            sym = index.add_symbol(kind="module", name=fqn.rsplit(".", 1)[-1] or path, qualname=fqn, repo=repo,
                                   file=path, line=1, end_line=max(1, len((text or "").splitlines())),
                                   parse_error=err)
            mod = _Module(repo, path, fqn, text or "", sym.id)
            modules.setdefault(repo, {})[fqn] = mod
            if text is None:
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError) as exc:
                index.symbols[sym.id] = Symbol(**{**asdict(sym), "parse_error": f"{type(exc).__name__}: {exc}"})
                continue
            _Collector(index, mod).run(tree)
    resolver = _Resolver(index, modules, list(names), shared=index.shared_prefixes)
    resolver.build_types()
    resolver.connect()
    resolver.attach_resources()
    index.unresolved = dict(resolver.unresolved)
    index.unresolved_shapes = {k: dict(v) for k, v in resolver.shapes.items() if v}
    return index


def _exports(mod: _Module, name: str) -> bool:
    """`from mod import *`가 `name`을 내보내나 — `__all__`(문자열 리스트·튜플)이 있으면 그것, 없으면 밑줄 없는 이름."""
    declared = mod.aliases.get("__all__")
    if isinstance(declared, (ast.List, ast.Tuple)):
        return any(isinstance(e, ast.Constant) and e.value == name for e in declared.elts)
    return not name.startswith("_")


_GITMODULES_PATH = re.compile(r"^\s*path\s*=\s*(\S+)", re.M)


def _gitmodules_prefixes(text: str) -> list[str]:
    """`path = dir/sub` → import 접두사 `dir.sub`. 서브모듈 디렉터리가 곧 패키지라는 가정이다(사내가 그 모양) —
    채워진 서브모듈의 모듈 이름도 같은 가정으로 경로에서 나온다. 안 맞으면 공유 비중이 0으로 보이고 import가 안
    풀린다 — 그때 고친다."""
    return sorted({m.group(1).strip("/").replace("/", ".") for m in _GITMODULES_PATH.finditer(text)})


def _skipped(path: str) -> bool:
    return any(part in _SKIP_DIRS for part in path.split("/")[:-1])


def _is_test_file(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    base = parts[-1]
    return any(p in ("tests", "test") for p in parts[:-1]) or base == "conftest.py" \
        or base.startswith("test_") or base.endswith(("_test.py", "_tests.py"))


def is_indexed(path: str) -> bool:
    """인덱스에 들어가는 파일인가 — 하네스의 커버리지 불변식도 **이 술어**로 센다. 둘이 갈리면 `.py == module`이
    영원히 안 맞는다. 테스트 파일은 입구에서 건너뛴다: 테스트가 어떤 함수를 부르는지는 "누가 부르나"의 답이
    아니고, 사내 첫 실행에서 정밀도 표본의 틀린 넷이 전부 테스트였다."""
    return path.endswith(".py") and not _skipped(path) and not _is_test_file(path)
