"""코드 추적기 — 끝점(또는 심볼)에서 출발해 **함수 사슬**을 따라가며 읽는 자원을 모은다. (11b 커밋 1)

리드에게 주는 것은 "어느 함수 몇 줄을 읽을지"다. 200개 파일을 함수 서너 개로 좁히는 것이 코드의
몫이고, 그 함수가 값을 어떻게 조합하는지 해석하는 것은 리드의 몫이다(step-11b-trace.md).

순수하다: 파일 읽기와 grep을 주입받고(`Source`), 시계도 없고, 던지지 않는다. 테스트는 메모리
사전으로, 운영은 배포 커밋의 git 리더로 감싼다. grep 패턴은 **리터럴만** 낸다 — 운영이 `-F`다.

해석 규칙(전부 같은 레포 안):
- 이름 호출 `foo()`: 같은 모듈의 정의 → import한 모듈 → 레포 `def foo(` grep(모듈 함수만).
- 메서드 호출 `x.m()`: `self`면 그 클래스, `self.f`면 `self.f = Cls()`로 필드의 클래스, 인자면 주석
  (`Annotated[Cls, Depends(g)]`까지 벗겨서)으로 클래스, 모듈 별칭이면 그 모듈의 함수. 못 좁히면
  레포의 `def m(` 중 **메서드**(클래스 안 정의) 전부를 후보로 따라가되 그 아래 읽기는 추정이다.
- `Depends(g)`의 g는 본문 호출 뒤에 따라간다(데이터 경로가 사슬 앞에 오게).
- 리터럴이 그대로 있으면 확실, config 키 토큰이나 Enum 별칭 경유면 추정. `getattr`은 gap.
"""
from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from src.knowledge.flow import Hit, Name, Route, _is_noise

MAX_DEPTH = 6
MAX_NODES = 200
_UPPER_ASSIGN = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*['\"]([^'\"]+)['\"]")


@dataclass(frozen=True)
class Step:
    file: str
    line: int
    qualname: str


@dataclass(frozen=True)
class Read:
    kind: str
    name: str
    grade: str          # "확실" | "추정"
    file: str
    line: int


@dataclass(frozen=True)
class Gap:
    file: str
    line: int
    why: str


@dataclass(frozen=True)
class Trace:
    target: str
    repo: str
    status: str         # "ok" | "not_found"
    reason: str = ""
    chain: tuple[Step, ...] = ()
    reads: tuple[Read, ...] = ()
    gaps: tuple[Gap, ...] = ()


class Source(Protocol):
    async def read(self, path: str) -> str | None: ...
    async def grep(self, patterns: list[str]) -> list[Hit]: ...


async def alias_index(names: Iterable[Name], source: Source) -> dict[str, Name]:
    """`LINE_STATUS = "line_status"` 같은 대문자 상수·Enum 멤버 정의 줄에서 **식별자 → 이름**.
    사내 코드는 config 키를 Enum 뒤에 두어 텍스트로는 키가 함수 본문에 안 보인다(11c 사내 실행 2).
    이 색인이 있어야 `Keys.LINE_STATUS`가 그 redis 키를 읽는 것으로 읽힌다(추정)."""
    names = list(names)
    wanted: dict[str, Name] = {}
    for n in names:
        wanted.setdefault(n.value, n)
        wanted.setdefault(n.key_token, n)
    patterns = sorted({f'"{k}"' for k in wanted} | {f"'{k}'" for k in wanted})
    out: dict[str, Name] = {}
    for hit in sorted(await source.grep(patterns), key=lambda h: (h.file, h.line)):
        if _is_noise(hit.file, hit.text):
            continue
        m = _UPPER_ASSIGN.match(hit.text)
        if m and m.group(2) in wanted and m.group(1) not in out:
            out[m.group(1)] = wanted[m.group(2)]
    return out


# ── 모듈 색인 ────────────────────────────────────────────────────────

class _Mod:
    def __init__(self, path: str, text: str, tree: ast.Module):
        self.path, self.text, self.tree = path, text, tree
        self.lines = text.splitlines()
        self.funcs: dict[str, ast.AST] = {}
        self.classes: dict[str, ast.ClassDef] = {}
        self.assigns: dict[str, ast.expr] = {}
        self.imports: dict[str, tuple[str, str | None]] = {}     # 별칭 → (모듈, 속성|None)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs.setdefault(node.name, node)
            elif isinstance(node, ast.ClassDef):
                self.classes.setdefault(node.name, node)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self.assigns.setdefault(node.targets[0].id, node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                self.assigns.setdefault(node.target.id, node.value)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for a in node.names:
                    self.imports[a.asname or a.name] = (node.module, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.imports[a.asname or a.name.split(".")[0]] = (a.name, None)

    def segment(self, node: ast.AST) -> tuple[str, int]:
        start, end = node.lineno, getattr(node, "end_lineno", node.lineno)
        return "\n".join(self.lines[start - 1:end]), start


def _method(cls: ast.ClassDef, name: str) -> ast.AST | None:
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _enclosing_class(mod: _Mod, func: ast.AST) -> ast.ClassDef | None:
    for cls in mod.classes.values():
        if func in cls.body:
            return cls
    return None


class _Repo:
    """파싱·grep 캐시 + 해석. **끝점들 사이에서 공유한다**(`Tracer`) — 사내 끝점 156개가 같은 파일과
    같은 `def 이름(` grep을 반복하면 git 호출이 수천 번이다. 못 읽은 파일은 gap 하나로 남기고 계속
    간다. 파싱 gap은 파일별로 두고, 각 trace는 자기가 **건드린** 파일의 것만 가져간다 — 다른 끝점의
    문법 오류가 이 끝점의 결과로 새면 안 된다."""

    def __init__(self, repo: str, source: Source):
        self.repo, self.source = repo, source
        self.mods: dict[str, _Mod | None] = {}
        self.parse_gaps: dict[str, Gap] = {}
        self.touched: set[str] = set()
        self._defs: dict[tuple[str, bool], list] = {}

    def parse_gaps_touched(self) -> list[Gap]:
        return [self.parse_gaps[p] for p in sorted(self.touched) if p in self.parse_gaps]

    async def module(self, path: str) -> _Mod | None:
        self.touched.add(path)
        if path in self.mods:
            return self.mods[path]
        text = await self.source.read(path)
        mod = None
        if text is not None:
            try:
                mod = _Mod(path, text, ast.parse(text))
            except SyntaxError as exc:
                self.parse_gaps[path] = Gap(path, exc.lineno or 0, f"문법 오류로 못 읽었다 — {exc.msg}")
            except (ValueError, RecursionError) as exc:                  # noqa: PERF203
                self.parse_gaps[path] = Gap(path, 0, f"파싱 실패 — {type(exc).__name__}")
        self.mods[path] = mod
        return mod

    async def module_of(self, dotted: str) -> _Mod | None:
        base = dotted.replace(".", "/")
        for path in (f"{base}.py", f"{base}/__init__.py"):
            mod = await self.module(path)
            if mod is not None:
                return mod
        return None

    async def lookup(self, mod: _Mod, name: str, *, hops: int = 0) -> tuple[_Mod, ast.AST] | None:
        """모듈 범위의 이름 하나 — 함수·클래스·대입(별칭). import를 따라간다(순환 방지 4홉)."""
        if name in mod.funcs:
            return mod, mod.funcs[name]
        if name in mod.classes:
            return mod, mod.classes[name]
        if name in mod.assigns:
            return mod, mod.assigns[name]
        if name in mod.imports and hops < 4:
            dotted, attr = mod.imports[name]
            if attr is None:
                target = await self.module_of(dotted)
                return (target, target.tree) if target is not None else None
            target = await self.module_of(dotted)
            if target is not None:
                found = await self.lookup(target, attr, hops=hops + 1)
                if found:
                    return found
            # `from pkg import mod` 꼴 — 속성이 모듈일 수 있다
            sub = await self.module_of(f"{dotted}.{attr}")
            if sub is not None:
                return sub, sub.tree
        return None

    async def klass(self, mod: _Mod, node: ast.AST, *, hops: int = 0) -> tuple[_Mod, ast.ClassDef] | None:
        """식(주석·별칭·호출)이 가리키는 클래스. `Annotated[Cls, …]`·`Optional[Cls]`는 첫 인자,
        별칭(`X = Annotated[...]`)은 그 값으로 다시."""
        if hops > 6:
            return None
        if isinstance(node, ast.ClassDef):
            return mod, node
        if isinstance(node, ast.Name):
            found = await self.lookup(mod, node.id)
            if found and found[1] is not node:
                return await self.klass(found[0], found[1], hops=hops + 1)
            return None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            found = await self.lookup(mod, node.value.id)
            if found and isinstance(found[1], ast.Module):
                inner = await self.lookup(found[0], node.attr)
                if inner:
                    return await self.klass(inner[0], inner[1], hops=hops + 1)
            return None
        if isinstance(node, ast.Subscript):
            inner = node.slice
            if isinstance(inner, ast.Tuple) and inner.elts:
                inner = inner.elts[0]
            return await self.klass(mod, inner, hops=hops + 1)
        if isinstance(node, ast.Call):
            return await self.klass(mod, node.func, hops=hops + 1)
        return None

    async def depends_of(self, mod: _Mod, node: ast.AST, *, hops: int = 0) -> list[tuple[_Mod, ast.AST]]:
        """주석 안의 `Depends(g)`들 — g의 정의."""
        if hops > 6:
            return []
        out: list[tuple[_Mod, ast.AST]] = []
        if isinstance(node, ast.Name):
            found = await self.lookup(mod, node.id)
            if found and isinstance(found[1], ast.expr) and found[1] is not node:
                out += await self.depends_of(found[0], found[1], hops=hops + 1)
            return out
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "Depends" and sub.args:
                arg = sub.args[0]
                if isinstance(arg, ast.Name):
                    found = await self.lookup(mod, arg.id)
                    if found and isinstance(found[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append(found)
        return out

    async def field_class(self, mod: _Mod, cls: ast.ClassDef, attr: str) -> tuple[_Mod, ast.ClassDef] | None:
        """`self.<attr> = Cls(...)`를 클래스 안 어디서든 찾아 Cls로."""
        for node in ast.walk(cls):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                            and t.value.id == "self" and t.attr == attr for t in node.targets)):
                return await self.klass(mod, node.value.func)
        return None

    async def defs_named(self, name: str, *, methods: bool) -> list[tuple[_Mod, ast.AST, ast.ClassDef | None]]:
        """레포 전체에서 `def name(` — 모듈 함수만 또는 메서드만. 문서·테스트는 뺀다. 이름별로 캐시."""
        key = (name, methods)
        if key in self._defs:
            for mod, _, _ in self._defs[key]:
                self.touched.add(mod.path)
            return self._defs[key]
        out = []
        hits = sorted(await self.source.grep([f"def {name}("]), key=lambda h: (h.file, h.line))
        for hit in hits:
            if _is_noise(hit.file, hit.text) or not hit.file.endswith(".py"):
                continue
            mod = await self.module(hit.file)
            if mod is None:
                continue
            for node in ast.walk(mod.tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name \
                        and node.lineno == hit.line:
                    cls = _enclosing_class(mod, node)
                    if (cls is not None) == methods:
                        out.append((mod, node, cls))
        self._defs[key] = out
        return out


# ── 추적 ─────────────────────────────────────────────────────────────

@dataclass
class _Node:
    mod: _Mod
    func: ast.AST
    cls: ast.ClassDef | None
    depth: int
    grade_cap: str          # 이 노드에 이르는 길이 확실했으면 "확실", 후보 중 하나였으면 "추정"

    @property
    def qualname(self) -> str:
        return f"{self.cls.name}.{self.func.name}" if self.cls is not None else self.func.name


def _param_annotation(func: ast.AST, name: str) -> ast.expr | None:
    args = func.args
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        if a.arg == name:
            return a.annotation
    return None


def _line_of(segment: str, needle: str, base: int) -> int:
    for i, line in enumerate(segment.splitlines()):
        if needle in line:
            return base + i
    return base


class Tracer:
    """레포 하나의 추적기 — 파싱·grep 캐시와 별칭 색인을 끝점들 사이에서 공유한다."""

    def __init__(self, repo: str, source: Source, *, names: Iterable[Name], routes: Iterable[Route] = (),
                 max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES):
        self.repo, self.source = repo, source
        self.names, self.routes = list(names), list(routes)
        self.max_depth, self.max_nodes = max_depth, max_nodes
        self.aliases: dict[str, Name] | None = None
        self._cache = _Repo(repo, source)

    async def prepare(self) -> None:
        self.aliases = await alias_index(self.names, self.source)

    async def trace(self, target: str) -> Trace:
        if self.aliases is None:
            await self.prepare()
        return await trace(target, repo=self.repo, source=self.source, names=self.names, routes=self.routes,
                           aliases=self.aliases, max_depth=self.max_depth, max_nodes=self.max_nodes,
                           cache=self._cache)


async def trace(target: str, *, repo: str, source: Source, names: Iterable[Name],
                routes: Iterable[Route] = (), aliases: dict[str, Name] | None = None,
                max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES, cache: _Repo | None = None) -> Trace:
    """끝점 path(`/…`)나 심볼 이름에서 출발한 `Trace`. 던지지 않는다."""
    names = list(names)
    aliases = dict(aliases or {})
    r = cache if cache is not None else _Repo(repo, source)
    r.touched = set()
    gaps: list[Gap] = []
    roots: list[_Node] = []
    if target.startswith("/"):
        for route in sorted((rt for rt in routes if rt.repo == repo and rt.path == target),
                            key=lambda rt: (rt.file, rt.line)):
            mod = await r.module(route.file)
            if mod is None:
                continue
            func = _handler_at(mod, route.line)
            if func is not None:
                roots.append(_Node(mod, func, _enclosing_class(mod, func), 0, "확실"))
        if not roots:
            return Trace(target, repo, "not_found",
                         f"{target}: 레포 {repo}의 라우트 선언에 없다 — code graph의 끝점 목록을 보라",
                         gaps=tuple(r.parse_gaps_touched()))
    else:
        found = await r.defs_named(target, methods=False) + await r.defs_named(target, methods=True)
        for mod, func, cls in found:
            roots.append(_Node(mod, func, cls, 0, "확실" if len(found) == 1 else "추정"))
        if not roots:
            return Trace(target, repo, "not_found",
                         f"{target}: 레포 {repo}에 `def {target}(`가 없다", gaps=tuple(r.parse_gaps_touched()))
        if len(found) > 1:
            gaps.append(Gap(found[0][0].path, found[0][1].lineno,
                            f"{target}: 정의 {len(found)}개 — 전부 출발점으로 삼았고 읽기는 추정이다"))

    chain: list[Step] = []
    reads: dict[tuple[str, str, str, int], Read] = {}
    visited: set[tuple[str, int]] = set()
    queue: list[_Node] = list(roots)
    later: list[_Node] = []          # Depends(g)의 g — 데이터 경로가 사슬 앞에 오게 뒤로 미룬다

    def add_read(kind: str, name: str, grade: str, file: str, line: int) -> None:
        key = (kind, name, file, line)
        cur = reads.get(key)
        if cur is None or (cur.grade == "추정" and grade == "확실"):
            reads[key] = Read(kind, name, grade, file, line)

    while queue or later:
        if not queue:
            queue, later = later, []
        node = queue.pop(0)
        key = (node.mod.path, node.func.lineno)
        if key in visited:
            continue
        if len(visited) >= max_nodes:
            gaps.append(Gap(node.mod.path, node.func.lineno, f"노드 상한 {max_nodes}에서 멈춤: {node.qualname}"))
            break
        visited.add(key)
        chain.append(Step(node.mod.path, node.func.lineno, node.qualname))
        segment, base = node.mod.segment(node.func)
        _collect_reads(segment, base, node, names, aliases, add_read)
        if "getattr(" in segment:
            gaps.append(Gap(node.mod.path, _line_of(segment, "getattr(", base),
                            "getattr로 고른 대상은 못 따라간다 — 리드가 code.read로 본다"))
        callees, deps = await _callees(r, node, gaps)
        if not callees and not deps:
            continue
        if node.depth >= max_depth:
            names_ = ", ".join(sorted({c.qualname for c in callees + deps}))
            gaps.append(Gap(node.mod.path, node.func.lineno,
                            f"깊이 상한 {max_depth}에서 멈춤: {node.qualname} → {names_}"))
            continue
        queue += callees
        later += deps

    ordered = sorted(reads.values(), key=lambda x: (x.file, x.line, x.kind, x.name))
    return Trace(target, repo, "ok", chain=tuple(chain), reads=tuple(ordered),
                 gaps=tuple(r.parse_gaps_touched() + gaps))


def _handler_at(mod: _Mod, decorator_line: int) -> ast.AST | None:
    """데코레이터 줄 바로 아래의 함수 — 데코레이터의 줄 번호가 라우트 히트의 줄이다."""
    best = None
    for node in ast.walk(mod.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(getattr(d, "lineno", -1) == decorator_line for d in node.decorator_list):
                return node
            if node.lineno > decorator_line and (best is None or node.lineno < best.lineno):
                best = node
    return best


def _collect_reads(segment: str, base: int, node: _Node, names: list[Name],
                   aliases: dict[str, Name], add_read) -> None:
    cap = node.grade_cap
    for n in names:
        if n.literal and n.literal in segment:
            add_read(n.kind, n.value, "추정" if cap == "추정" else "확실",
                     node.mod.path, _line_of(segment, n.literal, base))
        elif f'"{n.key_token}"' in segment or f"'{n.key_token}'" in segment:
            add_read(n.kind, n.value, "추정", node.mod.path, _line_of(segment, n.key_token, base))
    for ident, n in aliases.items():
        if re.search(rf"\b{re.escape(ident)}\b", segment):
            add_read(n.kind, n.value, "추정", node.mod.path, _line_of(segment, ident, base))


async def _callees(r: _Repo, node: _Node, gaps: list[Gap]) -> tuple[list[_Node], list[_Node]]:
    """이 함수 본문의 호출들이 가리키는 정의. `(본문 호출, Depends 제공자)`."""
    mod, func, cls = node.mod, node.func, node.cls
    out: list[_Node] = []
    seen: set[tuple[str, int]] = set()
    depth, cap = node.depth + 1, node.grade_cap

    def push(target_mod: _Mod, target: ast.AST, target_cls: ast.ClassDef | None, grade: str) -> None:
        key = (target_mod.path, target.lineno)
        if key not in seen and target is not func:
            seen.add(key)
            out.append(_Node(target_mod, target, target_cls, depth, "추정" if "추정" in (grade, cap) else "확실"))

    async def push_class_ctor(found: tuple[_Mod, ast.ClassDef] | None) -> None:
        if found is not None:
            init = _method(found[1], "__init__")
            if init is not None:
                push(found[0], init, found[1], "확실")

    for call in ast.walk(func):
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        if isinstance(f, ast.Name):
            found = await r.lookup(mod, f.id)
            if found and isinstance(found[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
                push(found[0], found[1], _enclosing_class(found[0], found[1]), "확실")
            elif found and isinstance(found[1], ast.ClassDef):
                await push_class_ctor((found[0], found[1]))
            elif found is None and not hasattr(builtins, f.id):
                cands = await r.defs_named(f.id, methods=False)
                for cmod, cfunc, ccls in cands:
                    push(cmod, cfunc, ccls, "확실" if len(cands) == 1 else "추정")
                if len(cands) > 1:
                    gaps.append(Gap(mod.path, call.lineno, f"{f.id}: 후보 {len(cands)}개 — 전부 따라가되 읽기는 추정"))
            continue
        if not isinstance(f, ast.Attribute):
            continue
        recv, meth = f.value, f.attr
        owner: tuple[_Mod, ast.ClassDef] | None = None
        if isinstance(recv, ast.Name) and recv.id == "self" and cls is not None:
            owner = (mod, cls)
        elif (isinstance(recv, ast.Attribute) and isinstance(recv.value, ast.Name)
              and recv.value.id == "self" and cls is not None):
            owner = await r.field_class(mod, cls, recv.attr)
        elif isinstance(recv, ast.Name):
            ann = _param_annotation(func, recv.id)
            if ann is not None:
                owner = await r.klass(mod, ann)
            else:
                found = await r.lookup(mod, recv.id)
                if found and isinstance(found[1], ast.Module):
                    inner = await r.lookup(found[0], meth)
                    if inner and isinstance(inner[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
                        push(inner[0], inner[1], None, "확실")
                    continue
                if found and isinstance(found[1], ast.ClassDef):
                    owner = (found[0], found[1])            # 클래스메서드·정적 호출
        elif isinstance(recv, ast.Call):
            owner = await r.klass(mod, recv.func)
        if owner is not None:
            m = _method(owner[1], meth)
            if m is not None:
                push(owner[0], m, owner[1], "확실")
                continue
        if isinstance(recv, ast.Name) and (recv.id == "self" or owner is not None):
            continue
        # 받는 쪽을 못 좁혔다 — 레포의 메서드 정의 전부가 후보다. 없으면 외부 라이브러리로 보고 지나간다.
        cands = await r.defs_named(meth, methods=True)
        cands = [c for c in cands if c[1] is not func]
        for cmod, cfunc, ccls in cands:
            push(cmod, cfunc, ccls, "확실" if len(cands) == 1 else "추정")
        if len(cands) > 1:
            gaps.append(Gap(mod.path, call.lineno,
                            f"{meth}: 받는 쪽을 못 좁혀 후보 {len(cands)}개 — 전부 따라가되 읽기는 추정"))

    deps: list[_Node] = []
    for a in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]:
        if a.annotation is None:
            continue
        for dmod, dfunc in await r.depends_of(mod, a.annotation):
            key = (dmod.path, dfunc.lineno)
            if key not in seen:
                seen.add(key)
                deps.append(_Node(dmod, dfunc, _enclosing_class(dmod, dfunc), depth, cap))
    return out, deps
