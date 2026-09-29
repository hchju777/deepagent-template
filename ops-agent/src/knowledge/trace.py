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

from src.knowledge.flow import Hit, Name, Route, _is_noise, _quoted_whole

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
    via: str = "literal"    # 이름이 코드에 어떻게 있었나 — "literal"(문자열 그대로) | "key"(config 키 토큰) | "alias"(상수·Enum)


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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return await self.returns_class(mod, node, hops=hops + 1)      # provider → 반환 클래스
        if isinstance(node, ast.Await):
            return await self.klass(mod, node.value, hops=hops + 1)
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

    async def returns_class(self, mod: _Mod, func: ast.AST, *, hops: int = 0) -> tuple[_Mod, ast.ClassDef] | None:
        """함수가 돌려주는 클래스 — `return Cls(...)`, `return await make()`, `return _singleton`.
        FastAPI의 `Depends(provider)`가 Protocol 포트 뒤의 **실제 구현체**를 아는 유일한 자리다."""
        if hops > 6:
            return None
        for node in ast.walk(func):
            if isinstance(node, ast.Return) and node.value is not None:
                got = await self.klass(mod, node.value, hops=hops + 1)
                if got is not None:
                    return got
        return None

    async def find_method(self, mod: _Mod, cls: ast.ClassDef, name: str, *, hops: int = 0
                          ) -> tuple[_Mod, ast.AST, ast.ClassDef] | None:
        """클래스와 부모들에서 메서드 — `(정의된 모듈, 함수, 정의한 클래스)`. 사내 저장소는 공통
        메서드를 부모(`BaseRepo`)에 둔다."""
        m = _method(cls, name)
        if m is not None:
            return mod, m, cls
        if hops >= 4:
            return None
        for base in cls.bases:
            found = await self.klass(mod, base)
            if found is not None and found[1] is not cls:
                got = await self.find_method(found[0], found[1], name, hops=hops + 1)
                if got is not None:
                    return got
        return None

    async def implementations(self, mod: _Mod, proto: ast.ClassDef) -> list[tuple[_Mod, ast.ClassDef]]:
        """그 Protocol·ABC를 **상속한** 클래스들 — `class X(Proto)`·`class X(Base, Proto)`. 캐시."""
        key = ("impl:" + proto.name, False)
        if key in self._defs:
            for cmod, _ in self._defs[key]:
                self.touched.add(cmod.path)
            return self._defs[key]
        out: list[tuple[_Mod, ast.ClassDef]] = []
        patterns = [f"({proto.name})", f"({proto.name},", f", {proto.name})", f", {proto.name},"]
        seen: set[tuple[str, int]] = set()
        for hit in sorted(await self.source.grep(patterns), key=lambda h: (h.file, h.line)):
            if _is_noise(hit.file, hit.text) or not hit.file.endswith(".py"):
                continue
            hmod = await self.module(hit.file)
            if hmod is None:
                continue
            for cls in hmod.classes.values():
                if (hmod.path, cls.lineno) in seen or cls is proto:
                    continue
                names = {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in cls.bases}
                if proto.name in names:
                    seen.add((hmod.path, cls.lineno))
                    out.append((hmod, cls))
        self._defs[key] = out
        return out

    async def classes_named(self, name: str) -> list[tuple[_Mod, ast.ClassDef]]:
        """레포 전체에서 `class name(`·`class name:`. 사내 저장소는 포트(`XRepository(Protocol)`)와
        구현(`XRepository(부모 저장소)`)이 **같은 이름**이고 상속 관계가 없다 — 이름이 유일한 연결이다."""
        key = ("class:" + name, False)
        if key in self._defs:
            for cmod, _ in self._defs[key]:
                self.touched.add(cmod.path)
            return self._defs[key]
        out: list[tuple[_Mod, ast.ClassDef]] = []
        for hit in sorted(await self.source.grep([f"class {name}(", f"class {name}:"]), key=lambda h: (h.file, h.line)):
            if _is_noise(hit.file, hit.text) or not hit.file.endswith(".py"):
                continue
            hmod = await self.module(hit.file)
            if hmod is None or name not in hmod.classes:
                continue
            cls = hmod.classes[name]
            if cls.lineno == hit.line and not any(c is cls for _, c in out):
                out.append((hmod, cls))
        self._defs[key] = out
        return out

    async def structural_impls(self, pmod: _Mod, proto: ast.ClassDef) -> list[tuple[_Mod, ast.ClassDef]]:
        """Protocol이 선언한 메서드를 **전부 가진** 클래스들 — PEP 544의 정의 그대로, 상속도 이름도 필요 없다.
        후보 파일은 선언 중 가장 긴 이름의 `def m(`으로 grep한다(`get`보다 특이할 확률이 높다). 사내 세 번째
        추적에서 이름 규약으로 고른 추정이 그 아래 읽기 608개를 전부 추정으로 만들었다 — 구조로 맞으면 확실이다."""
        methods = [b.name for b in proto.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and not b.name.startswith("__")]
        if not methods:
            return []
        key = ("struct:" + proto.name, False)
        if key in self._defs:
            for cmod, _ in self._defs[key]:
                self.touched.add(cmod.path)
            return self._defs[key]
        out: list[tuple[_Mod, ast.ClassDef]] = []
        probe = max(methods, key=len)
        for hit in sorted(await self.source.grep([f"def {probe}("]), key=lambda h: (h.file, h.line)):
            if _is_noise(hit.file, hit.text) or not hit.file.endswith(".py"):
                continue
            hmod = await self.module(hit.file)
            if hmod is None:
                continue
            for cls in hmod.classes.values():
                if cls is proto or _is_protocol(cls) or any(c is cls for _, c in out):
                    continue
                found = [await self.find_method(hmod, cls, m) for m in methods]
                if all(f is not None and not _is_abstract(f[1]) for f in found):
                    out.append((hmod, cls))
        self._defs[key] = out
        return out

    async def field_class(self, mod: _Mod, cls: ast.ClassDef, attr: str, *, hops: int = 0
                          ) -> tuple[_Mod, ast.ClassDef] | None:
        """`self.<attr>`의 클래스 — `self.x = Cls(...)`, 생성자 인자 `self.x = param`(주석으로),
        클래스 본문의 `x: Cls`. 부모 클래스도 본다."""
        for node in ast.walk(cls):
            if not (isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                    and t.value.id == "self" and t.attr == attr for t in node.targets)):
                continue
            if isinstance(node.value, ast.Name):
                for fn in cls.body:
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        ann = _param_annotation(fn, node.value.id)
                        if ann is not None:
                            return await self.klass(mod, ann)
                continue
            got = await self.klass(mod, node.value)
            if got is not None:
                return got
        for st in cls.body:
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.target.id == attr:
                return await self.klass(mod, st.annotation)
        if hops < 4:
            for base in cls.bases:
                found = await self.klass(mod, base)
                if found is not None and found[1] is not cls:
                    got = await self.field_class(found[0], found[1], attr, hops=hops + 1)
                    if got is not None:
                        return got
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
    cls: ast.ClassDef | None            # 정의한 클래스
    depth: int
    grade_cap: str                      # 이 노드에 이르는 길이 확실했으면 "확실", 후보 중 하나였으면 "추정"
    self_cls: tuple[_Mod, ast.ClassDef] | None = None   # 실행 시점의 self 클래스(부모 메서드면 자식)

    @property
    def qualname(self) -> str:
        return f"{self.cls.name}.{self.func.name}" if self.cls is not None else self.func.name


def _param_annotation(func: ast.AST, name: str) -> ast.expr | None:
    args = func.args
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        if a.arg == name:
            return a.annotation
    return None


def _param_names(func: ast.AST) -> set[str]:
    args = func.args
    return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}


def _line_of(segment: str, needle: str, base: int) -> int:
    for i, line in enumerate(segment.splitlines()):
        if needle in line:
            return base + i
    return base


def _body_nodes(func: ast.AST):
    """함수 **본문**의 노드만 — 데코레이터(`@router.get("/x")`)는 프레임워크 등록이지 호출 경로가
    아니고, 훑으면 `router`(외부 클래스의 싱글턴)의 `.get`이 "받는 쪽 미상" gap으로 새 끝점마다 남는다."""
    for st in func.body:
        yield from ast.walk(st)


def _quoted_at(segment: str, needle: str) -> int | None:
    """`needle`이 **문자열 리터럴 안에서** 처음 나오는 줄 오프셋. 템플릿의 리터럴(`line:{id}` → `line:`)은
    타입 주석 `line: str`과도 겹치므로, 같은 줄에서 따옴표가 먼저 열려 있어야 읽기로 친다."""
    m = re.search(r"""["'][^"'\n]*""" + re.escape(needle), segment)
    return None if m is None else segment.count("\n", 0, m.start())


_PORT_SUFFIXES = ("Protocol", "Port", "Interface", "ABC")
_PORT_PREFIXES = ("Abstract", "I")


def _port_aliases(name: str) -> list[str]:
    """포트 이름에서 구현체 이름을 짐작한다 — `XProtocol`·`XPort`·`XInterface`·`AbstractX`·`IX` → `X`.
    코드가 보증하는 연결이 아니므로 이걸로 고른 구현체는 추정이고 gap에 남긴다."""
    out: list[str] = []
    for suf in _PORT_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            out.append(name[: -len(suf)])
    for pre in _PORT_PREFIXES:
        rest = name[len(pre):]
        if name.startswith(pre) and rest[:1].isupper():
            out.append(rest)
    return [a for i, a in enumerate(out) if a not in out[:i]]


def _implementers(cands) -> list:
    """같은 이름 메서드 후보에서 포트의 선언(`...`뿐인 것)을 뺀다 — 선언은 구현체 후보가 아니다."""
    return [c for c in cands if not _is_abstract(c[1])]


def _key_at(segment: str, n: Name) -> int | None:
    """config 키 토큰이 따옴표로 있는 줄의 오프셋 — 조상 키(`required_tokens`)가 같은 줄에 다 있거나, 여러
    조각짜리 토큰이 통째로 따옴표 안이어야 한다. 11c가 grep 판정에서 세운 기준 그대로다: DAO 부모의
    `{"history": 0}` 같은 필드명이 한 단어 키 토큰과 겹쳐 사내 읽기 611개 중 510개가 이 가짜였다."""
    tok = n.key_token
    for i, line in enumerate(segment.splitlines()):
        if f'"{tok}"' not in line and f"'{tok}'" not in line:
            continue
        if all(t in line for t in n.required_tokens) or _quoted_whole(tok, line):
            return i
    return None


def _is_protocol(cls: ast.ClassDef) -> bool:
    for b in cls.bases:
        name = b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
        if name in ("Protocol", "ABC"):
            return True
    return False


def _is_abstract(func: ast.AST) -> bool:
    """본문이 `...`·`pass`·`raise NotImplementedError`뿐인 메서드 — 포트의 선언이지 구현이 아니다."""
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return True
    if len(body) != 1:
        return False
    st = body[0]
    if isinstance(st, ast.Pass):
        return True
    if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and st.value.value is Ellipsis:
        return True
    if isinstance(st, ast.Raise):
        exc = st.exc.func if isinstance(st.exc, ast.Call) else st.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


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
                cls = _enclosing_class(mod, func)
                roots.append(_Node(mod, func, cls, 0, "확실", (mod, cls) if cls else None))
        if not roots:
            return Trace(target, repo, "not_found",
                         f"{target}: 레포 {repo}의 라우트 선언에 없다 — code graph의 끝점 목록을 보라",
                         gaps=tuple(r.parse_gaps_touched()))
    else:
        found = await r.defs_named(target, methods=False) + await r.defs_named(target, methods=True)
        for mod, func, cls in found:
            roots.append(_Node(mod, func, cls, 0, "확실" if len(found) == 1 else "추정",
                               (mod, cls) if cls else None))
        if not roots:
            return Trace(target, repo, "not_found",
                         f"{target}: 레포 {repo}에 `def {target}(`가 없다", gaps=tuple(r.parse_gaps_touched()))
        if len(found) > 1:
            gaps.append(Gap(found[0][0].path, found[0][1].lineno,
                            f"{target}: 정의 {len(found)}개 — 전부 출발점으로 삼았고 읽기는 추정이다"))

    chain: list[Step] = []
    reads: dict[tuple[str, str, str, int], Read] = {}
    visited: set[tuple[str, int]] = set()
    scanned: set[tuple[str, int]] = set()          # 읽기를 이미 훑은 클래스 본문·모듈 상수
    queue: list[_Node] = list(roots)
    later: list[_Node] = []          # Depends(g)의 g — 데이터 경로가 사슬 앞에 오게 뒤로 미룬다

    def add_read(kind: str, name: str, grade: str, file: str, line: int, via: str) -> None:
        key = (kind, name, file, line)
        cur = reads.get(key)
        if cur is None or (cur.grade == "추정" and grade == "확실"):
            reads[key] = Read(kind, name, grade, file, line, via)

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
        for text, base, path, cap in await _read_chunks(r, node, scanned):
            _collect_reads(text, base, path, cap, names, aliases, add_read)
        segment, base = node.mod.segment(node.func)
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


async def _read_chunks(r: _Repo, node: _Node, scanned: set[tuple[str, int]]
                       ) -> list[tuple[str, int, str, str]]:
    """읽기를 훑을 본문들 — `(텍스트, 첫 줄, 파일, 등급 상한)`. 함수 본문에 더해 ① 실행 시점 클래스와
    부모들의 본문 상수(`collection = "…"` — 사내 저장소가 컬렉션을 이렇게 둔다) ② 함수가 참조하는
    모듈 상수표(`MAPPING = [(Enum.A, Keys.A), …]` — 식별자가 함수 본문엔 없다). ①은 실행 시점 클래스를
    좁힌 결과라 사슬의 등급을 따르고, ②는 표의 어느 줄을 쓰는지 모르므로 추정이 상한이다. 같은 것은 한
    번만 훑는다."""
    mod, func = node.mod, node.func
    segment, base = mod.segment(func)
    out = [(segment, base, mod.path, node.grade_cap)]
    cur = node.self_cls or ((mod, node.cls) if node.cls is not None else None)
    hops = 0
    while cur is not None and hops < 5:
        cmod, cls = cur
        if (cmod.path, cls.lineno) not in scanned:
            scanned.add((cmod.path, cls.lineno))
            for st in cls.body:
                if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    text, line = cmod.segment(st)
                    out.append((text, line, cmod.path, node.grade_cap))
        nxt = None
        for b in cls.bases:
            nxt = await r.klass(cmod, b)
            if nxt is not None and nxt[1] is not cls:
                break
            nxt = None
        cur, hops = nxt, hops + 1
    params = _param_names(func)
    for name in sorted({n.id for n in _body_nodes(func) if isinstance(n, ast.Name)
                        and isinstance(n.ctx, ast.Load) and n.id not in params}):
        found = await r.lookup(mod, name)
        if found is None or not isinstance(found[1], ast.expr):
            continue
        fmod, value = found
        if (fmod.path, value.lineno) in scanned:
            continue
        scanned.add((fmod.path, value.lineno))
        text, line = fmod.segment(value)
        out.append((text, line, fmod.path, "추정"))
    return out


def _collect_reads(segment: str, base: int, path: str, cap: str, names: list[Name],
                   aliases: dict[str, Name], add_read) -> None:
    for n in names:
        at = _quoted_at(segment, n.literal) if n.literal else None
        if at is not None:
            add_read(n.kind, n.value, "추정" if cap == "추정" else "확실", path, base + at, "literal")
        elif (at := _key_at(segment, n)) is not None:
            add_read(n.kind, n.value, "추정", path, base + at, "key")
    for ident, n in aliases.items():
        if re.search(rf"\b{re.escape(ident)}\b", segment):
            add_read(n.kind, n.value, "추정", path, _line_of(segment, ident, base), "alias")


async def _callees(r: _Repo, node: _Node, gaps: list[Gap]) -> tuple[list[_Node], list[_Node]]:
    """이 함수 본문의 호출들이 가리키는 정의. `(본문 호출, Depends 제공자)`.

    받는 쪽을 좁히는 순서: `self`·`self.f`(필드의 클래스) → 인자 주석 → 같은 함수의 지역 변수(대입한
    호출의 반환 클래스) → 모듈 별칭·클래스 → 호출 결과. 좁힌 클래스의 메서드가 Protocol·추상이면
    구현체로 간다(Depends provider의 반환 클래스 → 상속한 클래스 → 같은 이름의 메서드들). 받는 쪽을 **못
    좁혔고** 후보가 여럿이면 안 따라간다 — 사내 첫 추적에서 `.get(`이 후보 넷으로 퍼져 깊이 예산을
    다 먹었다. 인자에 주석이 없을 때만 후보 전부를 추정으로 따라간다.
    """
    mod, func, cls = node.mod, node.func, node.cls
    self_cls = node.self_cls or ((mod, cls) if cls is not None else None)
    out: list[_Node] = []
    seen: set[tuple[str, int]] = set()
    reported: set[str] = set()
    depth, cap = node.depth + 1, node.grade_cap
    params = _param_names(func)
    locals_: dict[str, ast.expr] = {}
    for st in _body_nodes(func):
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            locals_.setdefault(st.targets[0].id, st.value)

    def push(tmod: _Mod, target: ast.AST, tcls: ast.ClassDef | None, grade: str,
             self_of: tuple[_Mod, ast.ClassDef] | None = None) -> None:
        key = (tmod.path, target.lineno)
        if key not in seen and target is not func:
            seen.add(key)
            out.append(_Node(tmod, target, tcls, depth, "추정" if "추정" in (grade, cap) else "확실",
                             self_of if self_of is not None else ((tmod, tcls) if tcls is not None else None)))

    async def push_ctor(found: tuple[_Mod, ast.ClassDef] | None) -> None:
        if found is not None:
            got = await r.find_method(found[0], found[1], "__init__")
            if got is not None:
                push(got[0], got[1], got[2], "확실", self_of=found)

    async def follow(owner: tuple[_Mod, ast.ClassDef], meth: str, *, ann: ast.expr | None, line: int,
                     self_of: tuple[_Mod, ast.ClassDef] | None = None) -> bool:
        """좁힌 클래스에서 메서드를 따라간다. 찾았으면 True(구현체가 없어도). `self_of`는 실행 시점
        클래스를 덮어쓴다 — `super().m()`은 부모의 m이지만 self는 자식 그대로다."""
        got = await r.find_method(owner[0], owner[1], meth)
        if got is None:
            return False
        fmod, m, defining = got
        if not (_is_protocol(owner[1]) or _is_abstract(m)):
            push(fmod, m, defining, "확실", self_of=self_of or owner)
            return True
        # 포트다 — 구현체를 찾는다.
        impls: list[tuple[_Mod, ast.ClassDef]] = []
        if ann is not None:
            for pmod, pfunc in await r.depends_of(mod, ann):
                impl = await r.returns_class(pmod, pfunc)
                if impl is not None:
                    impls.append(impl)
        grade = "확실"
        if not impls:
            impls = await r.implementations(owner[0], owner[1])
            if len(impls) > 1:
                grade = "추정"
                gaps.append(Gap(mod.path, line, f"{meth}: {owner[1].name} 구현체 {len(impls)}개 — 전부 따라가되 읽기는 추정"))
        if not impls:
            # 상속하지 않는 구현체 — 선언한 메서드를 다 가진 클래스(언어의 정의). 하나면 확실, 둘셋이면 추정,
            # 더 많으면 `def get(` 하나짜리 포트다 — 안 따라간다.
            structural = await r.structural_impls(owner[0], owner[1])
            if len(structural) == 1:
                impls = structural
            elif 1 < len(structural) <= 3:
                impls, grade = structural, "추정"
                gaps.append(Gap(mod.path, line, f"{meth}: {owner[1].name} 구조가 맞는 클래스 {len(structural)}개 — 전부 따라가되 읽기는 추정"))
            elif len(structural) > 3:
                gaps.append(Gap(mod.path, line, f"{meth}: {owner[1].name} 구조가 맞는 클래스 {len(structural)}개 — 안 따라간다"))
                return True
        if not impls:
            # 구조로는 안 맞는다(선언한 메서드 일부가 외부 부모에 있거나 아직 없다) — 이름으로 짐작한다: 같은 이름
            # 클래스(사내 저장소 모양), 그 다음 이름 규약. 둘 다 코드가 보증하지 않으니 추정이고 gap에 남긴다.
            # **무엇이 없는지**를 적는다 — 사내에서 이 gap이 175개였는데 이유를 못 읽어 다음 판단을 못 했다.
            declared = [b.name for b in owner[1].body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not b.name.startswith("__")]

            async def lacking(imod: _Mod, icls: ast.ClassDef) -> str:
                missing = []
                for name in declared:
                    got = await r.find_method(imod, icls, name)
                    if got is None or _is_abstract(got[1]):
                        missing.append(name)
                return f"포트 메서드 {len(declared)}개 중 없는 것: {', '.join(missing[:3]) or '-'}"

            same = [(smod, scls) for smod, scls in await r.classes_named(owner[1].name)
                    if scls is not owner[1] and not _is_protocol(scls)]
            if same:
                impls, grade = same, "추정"
                gaps.append(Gap(mod.path, line, f"{meth}: {owner[1].name} 같은 이름 클래스 {len(same)}개로 갔다 — {await lacking(*same[0])}, 읽기는 추정"))
            else:
                for alias in _port_aliases(owner[1].name):
                    same = [(smod, scls) for smod, scls in await r.classes_named(alias) if not _is_protocol(scls)]
                    if same:
                        impls, grade = same, "추정"
                        gaps.append(Gap(mod.path, line, f"{meth}: {owner[1].name} 구현체를 이름 규약으로 골랐다 — {alias}({await lacking(*same[0])}), 읽기는 추정"))
                        break
        if not impls:
            cands = _implementers(c for c in await r.defs_named(meth, methods=True) if c[1] is not m and c[1] is not func)
            for cmod, cfunc, ccls in cands:
                push(cmod, cfunc, ccls, "추정", self_of=(cmod, ccls) if ccls else None)
            if cands:
                gaps.append(Gap(mod.path, line, f"{meth}: {owner[1].name} 구현체를 못 찾아 같은 이름 {len(cands)}개 — 읽기는 추정"))
            return True
        for imod, icls in impls:
            got = await r.find_method(imod, icls, meth)
            if got is not None:
                push(got[0], got[1], got[2], grade, self_of=(imod, icls))
        return True

    for call in _body_nodes(func):
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        if isinstance(f, ast.Name):
            found = await r.lookup(mod, f.id)
            if found and isinstance(found[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
                push(found[0], found[1], _enclosing_class(found[0], found[1]), "확실")
            elif found and isinstance(found[1], ast.ClassDef):
                await push_ctor((found[0], found[1]))
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
        kind, ann = "unknown", None
        if (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name) and recv.func.id == "super"
                and self_cls is not None):
            # 부모 중 m을 가진 첫 클래스. 없으면(외부 부모) 조용히 — `def __init__(` 전부가 후보였다(사내 61개).
            for b in self_cls[1].bases:
                base = await r.klass(self_cls[0], b)
                if base is not None and base[1] is not self_cls[1] and await r.find_method(base[0], base[1], meth):
                    owner = base
                    break
            if owner is not None:
                await follow(owner, meth, ann=None, line=call.lineno, self_of=self_cls)
            continue
        if isinstance(recv, ast.Name) and recv.id == "self" and self_cls is not None:
            owner, kind = self_cls, "self"
        elif (isinstance(recv, ast.Attribute) and isinstance(recv.value, ast.Name)
              and recv.value.id == "self" and self_cls is not None):
            owner, kind = await r.field_class(self_cls[0], self_cls[1], recv.attr), "field"
        elif isinstance(recv, ast.Name) and recv.id in params:
            ann = _param_annotation(func, recv.id)
            kind = "param" if ann is not None else "param_free"
            if ann is not None:
                owner = await r.klass(mod, ann)
        elif isinstance(recv, ast.Name) and recv.id in locals_:
            owner, kind = await r.klass(mod, locals_[recv.id]), "local"
        elif isinstance(recv, ast.Name):
            found = await r.lookup(mod, recv.id)
            if found and isinstance(found[1], ast.Module):
                inner = await r.lookup(found[0], meth)
                if inner and isinstance(inner[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
                    push(inner[0], inner[1], None, "확실")
                continue                                   # 모듈 별칭 — 함수가 없으면 외부 것이다
            if found and isinstance(found[1], ast.ClassDef):
                owner, kind = (found[0], found[1]), "class"
            elif found and isinstance(found[1], ast.expr):
                owner, kind = await r.klass(found[0], found[1]), "local"     # 모듈 수준 싱글턴
        elif isinstance(recv, (ast.Call, ast.Await)):
            owner, kind = await r.klass(mod, recv), "call"
        if owner is not None and await follow(owner, meth, ann=ann, line=call.lineno):
            continue
        if kind in ("self", "field", "class") and owner is not None:
            continue                                       # 좁혔는데 메서드가 없다 — 외부 부모거나 동적
        cands = _implementers(c for c in await r.defs_named(meth, methods=True) if c[1] is not func)
        if kind == "param_free":
            for cmod, cfunc, ccls in cands:
                push(cmod, cfunc, ccls, "확실" if len(cands) == 1 else "추정",
                     self_of=(cmod, ccls) if ccls else None)
            if len(cands) > 1:
                gaps.append(Gap(mod.path, call.lineno,
                                f"{meth}: 받는 쪽을 못 좁혀 후보 {len(cands)}개 — 전부 따라가되 읽기는 추정"))
            continue
        if len(cands) == 1 and kind != "unknown":
            # 이름조차 못 찾은 받는 쪽(`mongo[...]`·외부 객체)은 후보가 하나여도 안 간다 — 커서의 `.count()`가
            # 레포의 유일한 `def count(`로 잘못 이어졌다.
            cmod, cfunc, ccls = cands[0]
            push(cmod, cfunc, ccls, "추정", self_of=(cmod, ccls) if ccls else None)
        elif len(cands) > 1 and kind != "unknown" and meth not in reported:
            reported.add(meth)
            gaps.append(Gap(mod.path, call.lineno, f"{meth}: 받는 쪽 미상, 후보 {len(cands)}개 — 안 따라간다"))

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
