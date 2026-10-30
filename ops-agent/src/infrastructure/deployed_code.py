"""**배포된 커밋의 코드를 서비스 이름으로** 읽는다. 조사(2차)가 쓰는 표면이다.

## 왜 포트를 그대로 안 쓰는가

`CodeReaderPort`는 `show(repo, commit, path)`다. 이걸 그대로 등재표에 올리면 **리드가
레포 이름과 커밋을 고르게 된다.** 리드는 SHA를 모르고, 모르면 지어낸다 — 그러면
우리는 떠 있지도 않은 코드를 읽고 확신에 찬 오답을 낸다. 규율 3(LLM이 인용한 id를
신뢰하지 않는다)이 막으려던 것과 같은 구멍이다.

그래서 **리드가 대는 것은 서비스 이름뿐**이고, 레포·커밋·법인·config 경로는 전부
여기서 정해진다(규율 6: 재현 가능·상한·감사 가능한 것은 코드가 쥔다).

| 리드가 정한다 | 코드가 정한다 |
|---|---|
| 어느 서비스를 볼지 · 무엇을 찾을지 · 어느 파일을 읽을지 | 레포 · 커밋 · 법인 · config 층 경로 · 병합 규칙 |

## "파일 하나 읽기"와 "이름이 무엇인가"는 다른 물음이다

이름은 대상의 config에 사는데 그 config는 **층으로 갈린다**(gumi면 셋). 층 하나만
읽으면 위 층이 덮어쓴 값을 사실로 단정한다. 그래서 `config()`는 층 전부를 읽어
`merge_target`으로 합친다 — `read()`와 아예 다른 메서드인 이유다.

`read()`의 `path`는 리드가 지어내는 것이 아니라 **`grep()`이 돌려준 경로**다.

## 실패는 값이다

전부 `ProbeResult`로 흡수한다. 없는 서비스, 안 읽히는 층, 잘린 파일 — 운영 중에
정상적으로 일어나는 일이고, 여기서 던지면 조사 그래프가 통째로 죽는다(규율 1).
"""
import ast
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Callable

from src.domain.base import Clock
from src.domain.case import FilterHit, FilterScan, MergeHit
from src.domain.envelope import ProbeResult
from src.domain.jsonpath import select
from src.domain.ports import CodeReaderPort, DeployedCodePort
from src.knowledge import flow as flowgraph
from src.knowledge.flow import Hit, Name, names_from_config
from src.knowledge.graph_build import grep_snapshot, parse_grep
from src.knowledge.schema import Deployment, Topology
from src.knowledge.target_config import merge_target, parse_layer

_MAX_CHARS = 20000
# 리드에게 주는 grep·read의 줄 상한 — 리더의 기본값(`git_reader._MAX_LINES`)과 같은 수. grep은 리더에서 더 크게 받아
# 코드 줄을 앞세운 뒤 **여기서** 자른다 — 리더가 먼저 400줄에서 자르면 트리 순서상 앞에 오는 README가 전부를 먹는다
# (사내 실측: 끝점 path로 grep하니 문서 줄만 왔다).
_LINES = 400
_GREP_RAW_LINES, _GREP_RAW_CHARS = 4000, 400_000
# 코드가 아니라 **문서**인 줄. 버리지 않고 뒤로 보낸다 — 설명서의 한 줄이 단서일 때도 있다.
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
# 합친 설정을 그대로 실을 상한. 증거 한 건의 기본 예산(`evidence_chars` 2400)과 같은 수다 — 그 안에 안 들어가는
# 덤프는 어차피 첫 키에서 잘리므로(사내 실측), 그때는 **키 지도**를 주고 `key=`로 골라 읽게 한다.
_CONFIG_INLINE_CHARS = 2400
_MAP_DEPTH, _MAP_FANOUT, _MAP_VALUE_CHARS = 2, 24, 60

# 흐름 재료용 상한. 리더의 400줄·2만 자는 리드에게 주는 증거 봉투의 상한이지 그래프 재료의
# 상한이 아니다 — `alarm` 같은 키 토큰은 큰 레포에서 수백 줄이 정상이다.
FLOW_MAX_LINES = 50_000
FLOW_MAX_CHARS = 5_000_000
# 한 번의 `git grep`에 넘기는 패턴 수. 명령줄 길이(Windows 32K)와 출력 한 덩어리 크기의 균형.
FLOW_CHUNK = 20


# ── R3-4 ③ → R3-5 ③ — 단일값 검사의 범위와 짝(사람 결정 10-14·10-15) ──
_CODE_SUFFIX = ".py"
# 컴포넌트를 부르는 자리 `<수신자>.get("X")`. 변수 인자는 **같은 수신자**에 리터럴 get이 있는 파일에서만 "못 이음 — 변수"로 센다 — 사전의 `cfg.get(key)`까지
# 세면 어느 파일이나 못 이음이 된다.
_GET_LITERAL = re.compile(r"""(?P<recv>[\w.\]\)]+)\.get\(\s*(?P<q>["'])(?P<name>[^"'\n]+)(?P=q)\s*[,)]""")
_GET_VARIABLE = re.compile(r"""(?P<recv>[\w.\]\)]+)\.get\(\s*(?P<var>[A-Za-z_][\w.]*)\s*[,)]""")
_ACCESS = r"""(?:\[\s*(?P<q1>["'])(?P<f1>[^"'\n]+)(?P=q1)\s*\]|\.get\(\s*(?P<q2>["'])(?P<f2>[^"'\n]+)(?P=q2)[^()]*\)|getattr\([^(),]+,\s*(?P<q3>["'])(?P<f3>[^"'\n]+)(?P=q3)[^()]*\))"""
_LITERAL = r"""(?P<q4>["'])(?P<v>[^"'\n]*)(?P=q4)"""
_FORWARD = re.compile(_ACCESS + r"\s*(?P<op>==|!=)\s*" + _LITERAL)
# 뒤집힌 순서는 연산자와 접근 사이에 수신자(`rows[0]`·`self.row`)가 온다 — 공백·따옴표·연산자가 없는 한 토막까지 허용한다.
_REVERSED = re.compile(_LITERAL + r"""\s*(?P<op>==|!=)\s*[^\s=!"']*""" + _ACCESS)
# 등록의 사이트 인자 — 명시한 값이 전부 이 사이트와 같은 등록만 후보다(사람 결정 10-15: 같은 X가 사이트마다 다른 클래스).
_SITE_KEYS = ("gbm", "factory", "fct")


def component_names(text: str) -> tuple[list[str], list[str]]:
    """`<수신자>.get("X")` 리터럴들과, 같은 수신자의 `get(<변수>)` 변수 이름들(차례, 중복 없이). 리터럴만 잇는다 — 사내 `get("…")`은 리터럴이 대부분이고
    변수는 여섯뿐이라 새 데이터 링크 없이 그 둘로 잇고, 변수면 "못 이음 — 변수"다(사람 결정 10-14·10-15)."""
    literals: list[str] = []
    receivers: set[str] = set()
    for m in _GET_LITERAL.finditer(text):
        receivers.add(m.group("recv"))
        if m.group("name") not in literals:
            literals.append(m.group("name"))
    variables = [m.group("var") for m in _GET_VARIABLE.finditer(text) if m.group("recv") in receivers]
    return literals, list(dict.fromkeys(variables))


def _param_defaults(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """함수 인자 → 문자열 리터럴 기본값(있는 것만). 위치 인자는 뒤에서부터 기본값이 붙는다."""
    out: dict[str, str] = {}
    args = fn.args
    for arg, default in zip(args.args[len(args.args) - len(args.defaults):], args.defaults):
        if isinstance(default, ast.Constant) and isinstance(default.value, str):
            out[arg.arg] = default.value
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if isinstance(default, ast.Constant) and isinstance(default.value, str):
            out[arg.arg] = default.value
    return out


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    a = fn.args
    return [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]


def _positional_index(fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> int | None:
    """호출이 위치로 넘길 때의 자리 — 첫 인자가 `self`·`cls`면 뺀다(메서드 호출 `self.m(a, b)`의 인자 자리)."""
    names = [a.arg for a in fn.args.args]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return names.index(name) if name in names else None


def default_links(sources: dict[str, str]) -> dict[str, str]:
    """`<수신자>.get(<변수>)`의 변수 → 이을 리터럴(R3-5e ③ → R3-5f ②, 사람 결정 10-19·10-20). 그 변수가 그 호출을 품은 함수의 **인자**일 때, **같은 이름**의
    정의들과 호출들을 한 묶음으로 본다 — 함수 하나가 아니라 이름 단위. 측정 #16 P0-3: 실코드는 `get(<변수>)`를 품은 부모에 기본값이 없고 기본값은 재정의에
    있으며 재정의가 `super()`로 그 인자를 넘긴다 — 함수 단위로는 부모에서 "기본값 없음"으로 바로 못 잇고, `super()` 호출을 재정의 자신의 호출로 세어 자기
    인자로 되돌았다. 범위 안 호출이 넘긴 값(리터럴, 호출한 쪽의 인자면 그 이름에서 한 단계 더 — 깊이 3), 안 넘긴 호출이 쓸 기본값(같은 이름 정의들의 문자열
    기본값 — 하나일 때만), 호출이 하나도 없으면 그 기본값 — 전부 같은 하나여야 잇는다. 재정의가 자기 인자를 `super()`로 넘기는 것은 호출이 아니라 전달이다.
    값이 갈리거나 변수·식을 넘기면 못 잇는다(지금처럼 "못 이음 — 변수"). AST 사실이지 실행이 아니다. 깨진 파일은 없는 것."""
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    calls: list[tuple[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef | None]] = []
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append(node)
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        calls.append((inner, node))
    by_name: dict[str, list] = {}
    for fn in funcs:
        by_name.setdefault(fn.name, []).append(fn)

    def called_name(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return call.func.id if isinstance(call.func, ast.Name) else None

    def resolve(name: str, param: str, depth: int) -> str | None:
        """이 이름(정의 전부)의 이 인자가 실행 때 가질 **하나의** 리터럴 — 호출들이 넘긴 값과 안 넘긴 호출의 기본값이 전부 같은 하나여야 한다."""
        defs = [d for d in by_name.get(name, []) if param in _param_names(d)]
        if not defs or depth > 3:
            return None
        default_values = {_param_defaults(d).get(param) for d in defs} - {None}
        positions = {_positional_index(d, param) for d in defs}
        if len(positions) != 1:
            return None                                                 # 정의마다 자리가 다르면 위치 인자를 못 읽는다
        pos = next(iter(positions))
        values: set[str] = set()
        for call, caller in calls:
            if called_name(call) != name:
                continue
            passed = next((kw.value for kw in call.keywords if kw.arg == param), None)
            if passed is None and pos is not None and pos < len(call.args):
                passed = call.args[pos]
            if isinstance(passed, ast.Name) and caller in defs and passed.id == param:
                continue                                                # 재정의가 자기 인자를 super()로 넘긴다 — 호출이 아니라 전달
            if passed is None:
                if len(default_values) != 1:
                    return None                                         # 안 넘겼는데 쓸 기본값이 없거나 갈린다
                values.add(next(iter(default_values)))
            elif isinstance(passed, ast.Constant) and isinstance(passed.value, str):
                values.add(passed.value)
            elif isinstance(passed, ast.Name) and caller is not None and passed.id in _param_names(caller):
                got = resolve(caller.name, passed.id, depth + 1)        # 넘긴 것이 호출한 쪽의 인자 — 그 이름에서 한 단계 더
                if got is None:
                    return None
                values.add(got)
            else:
                return None                                             # 변수·식을 넘겼다 — 실행 때 값은 모른다
        if not values:
            values = default_values
        return next(iter(values)) if len(values) == 1 else None

    out: dict[str, str] = {}
    for fn in funcs:
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Name) and node.args[0].id in _param_names(fn)):
                var = node.args[0].id
                got = resolve(fn.name, var, 0)
                if got is not None and var not in out:
                    out[var] = got
    return out


def _literal_items(node) -> tuple[str, ...]:
    """반복이 도는 묶음이 **문자열 리터럴만 든 튜플·목록·집합**이면 그 원소들, 아니면 빈 튜플(못 읽는다). 문자열 하나는 묶음이 아니다 — 글자를 돈다."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and node.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return tuple(e.value for e in node.elts)
    return ()


def _loop_items(call: ast.Call, var: str, parents: dict[int, ast.AST]) -> tuple[str, ...] | None:
    """호출을 품은 가장 가까운 for·컴프리헨션 중 `var`를 도는 것의 묶음 — 리터럴만이면 그 원소들, 못 읽으면 빈 튜플, 그런 반복이 없으면 None(반복 변수가
    아니다). 호출이 그 반복의 묶음 식 자체에 있으면(`for key in c.get(key)`) 변수는 아직 안 묶였다 — 그 반복은 아니다."""
    node, child = parents.get(id(call)), call
    while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.Module)):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                if isinstance(gen.target, ast.Name) and gen.target.id == var and child is not gen.iter:
                    return _literal_items(gen.iter)
        elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name) and node.target.id == var and child is not node.iter:
            return _literal_items(node.iter)
        node, child = parents.get(id(node)), node
    return None


def loop_links(sources: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """`<수신자>.get(<변수>)`의 변수가 **같은 함수의 for·컴프리헨션에서 문자열 리터럴만 든 묶음을 도는 대상**이면 → 그 리터럴들 전부(R3-5m ①, 측정 #23
    `<지그 클래스>`의 유력한 줄 `<인덱스>.get(key) for key in ("<a>", "<b>")`). AST 사실이지 실행이 아니다 — 값은 그 묶음의 원소뿐이다. 이름 단위·전부-또는-전무
    (`default_links`와 같은 규칙): 같은 이름의 묶음이 여럿이면 합치고, 하나라도 못 읽으면(변수 묶음·리터럴 아닌 원소) 그 이름은 못 잇는다. 호출이 그 반복 **안**에
    있어야 한다 — 반복 뒤의 변수는 마지막 값이다. 런타임 객체의 속성(`get(handler.kind)`)은 `ast.Name`이 아니라 여기 오지 않는다(못 이음 그대로 — 측정 #23의
    `<이벤트 처리 클래스>`, P1-8). 깨진 파일은 없는 것."""
    found: dict[str, list[str]] = {}
    broken: set[str] = set()
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Name)):
                continue
            var = node.args[0].id
            items = _loop_items(node, var, parents)
            if items is None:
                continue
            if items:
                found.setdefault(var, [])
                found[var] += [x for x in items if x not in found[var]]
            else:
                broken.add(var)
    return {var: tuple(lits) for var, lits in found.items() if var not in broken}


def comparison_pairs(text: str) -> list[tuple[int, str, str, str, str]]:
    """코드 줄의 비교식에서 **필드와 리터럴이 양쪽**인 짝 `(줄, 필드, 값, 연산자, 본문)` 전부 — `x["f"] != "v"`·`r.get("f") == "v"`·`getattr(r, "f") == "v"`,
    뒤집힌 순서도. 사전 리터럴(`{"f": "v"}`)·주석·변수(`r[f]`, `!= v`)는 아니다. 먼저 전부 뽑아 두고 단일값 전부와 맞춘다(R3-5 ③ (a) — 필드 상한 없음).
    연산자(`==`·`!=`)는 사실로만 넘긴다 — 거르는 쪽인지는 걷기가 정한다(R3-5l ②, 측정 #22: `==`로 남기는 줄을 거름으로 셌다)."""
    out: list[tuple[int, str, str, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in (*_FORWARD.finditer(line), *_REVERSED.finditer(line)):
            out.append((i, m.group("f1") or m.group("f2") or m.group("f3"), m.group("v"), m.group("op"), line.strip()))
    return out


def _str_list(node) -> tuple[str, ...]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return tuple(e.value for e in node.elts)
    return ()


def merge_pairs(text: str) -> list[tuple[int, tuple[str, ...], tuple[str, ...], str, str, tuple[str, str] | None]]:
    """코드의 pandas 병합식 — `(줄, 왼쪽 키 필드들, 오른쪽 키 필드들, how, 본문, 이어진 호출)`. `x.merge(y, on=…)`·`pd.merge(x, y, on=…)`, `left_on`/`right_on`,
    `how`(없으면 inner). 키가 리터럴(문자열·문자열 목록)일 때만 — 변수면 모른다(실행이 아니라 사실만). R3-5g ④(사람 결정 10-20: P1-5 조인의 동결을 푼다,
    사내 병합식은 pandas merge). 이어진 호출은 병합식 결과에 바로 붙은 `.<이름>(…)` 중 문자열 인자가 꼭 하나인 것 — `(이름, 그 문자열)`, 없으면 None
    (R3-5h ②: 채움값 후보. 호출 이름은 정하지 않는다). 깨진 파일은 빈 목록."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    lines = text.splitlines()
    chained: dict[int, tuple[str, str]] = {}
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call):
            literals = [a.value for a in (*call.args, *(k.value for k in call.keywords)) if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if len(literals) == 1:
                chained[id(call.func.value)] = (call.func.attr, literals[0])
    out: list[tuple[int, tuple[str, ...], tuple[str, ...], str, str, tuple[str, str] | None]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "merge"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        on = _str_list(kw.get("on"))
        left, right = _str_list(kw.get("left_on")) or on, _str_list(kw.get("right_on")) or on
        if not left or not right or len(left) != len(right):
            continue
        how = kw["how"].value if isinstance(kw.get("how"), ast.Constant) and isinstance(kw["how"].value, str) else "inner"
        body = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else ""
        out.append((node.lineno, left, right, how, body, chained.get(id(node))))
    return sorted(out)


def comparison_lines(text: str, field: str, value: str) -> list[tuple[int, str]]:
    return [(i, body) for i, f, v, _op, body in comparison_pairs(text) if f == field and v == value]


@dataclass(frozen=True)
class Registration:
    """등록 모음 파일의 `register(component="X", <사이트 인자>)(<클래스>)` 한 건 — 데코레이터 형도 같은 모양으로 둔다."""
    component: str
    cls: str
    module: str | None            # `alias.Cls`면 import 별칭이 가리키는 모듈, 아니면 None
    site: dict
    path: str
    line: int


def _is_register(func) -> bool:
    return (isinstance(func, ast.Name) and func.id == "register") or (isinstance(func, ast.Attribute) and func.attr == "register")


def _register_args(call) -> tuple[str | None, dict]:
    component, site = None, {}
    for kw in call.keywords:
        if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, str):
            continue
        if kw.arg == "component":
            component = kw.value.value
        elif kw.arg in _SITE_KEYS:
            site[kw.arg] = kw.value.value
    return component, site


def registrations(path: str, text: str) -> list[Registration]:
    """한 파일의 등록들 — **AST**로(사람 결정 10-15): 정규식은 여러 줄 등록과 점 표기를 놓치고 문서 문자열 속 `@register` 예시를 등록으로 센다. 모양은
    (a) `register(component="X")(Cls)`, (b) 사이트 인자 `register(component="X", gbm=…, factory=…)(Cls)`, (c) 점 표기 `alias.Cls`(별칭은 import한 모듈),
    (d) 여러 줄, 그리고 데코레이터 `@register(component="X")` 아래의 클래스. 못 읽는 파일은 등록 없음 — 던지지 않는다."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split(".")[0]] = name.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for name in node.names:
                aliases[name.asname or name.name] = f"{node.module}.{name.name}"
    out: list[Registration] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and _is_register(node.func.func) and len(node.args) == 1:
            component, site = _register_args(node.func)
            target = node.args[0]
            if component is None:
                continue
            if isinstance(target, ast.Name):
                out.append(Registration(component, target.id, None, site, path, node.lineno))
            elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                out.append(Registration(component, target.attr, aliases.get(target.value.id, target.value.id), site, path, node.lineno))
        elif isinstance(node, ast.ClassDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and _is_register(deco.func):
                    component, site = _register_args(deco)
                    if component is not None:
                        out.append(Registration(component, node.name, None, site, path, node.lineno))
    return sorted(out, key=lambda r: r.line)


def _evidence(layers: list[tuple[str, str, dict]], value: str) -> tuple[str, int, str] | None:
    """값이 적힌 **실제로 합친 층**의 줄 — `(경로, 줄, 본문)`. 마지막에 이긴 층부터 본다.

    레포의 config 파일을 grep해서 첫 파일을 붙이면 `config/factories/_dev/…`처럼 이 사이트에
    안 쓰이는 층이 근거로 찍힌다(사내 첫 실행). 근거는 그 서비스가 실제로 읽은 층이어야 한다.
    """
    quoted = (f'"{value}"', f"'{value}'")
    for path, text, _ in reversed(layers):
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(q in line for q in quoted):
                return path, lineno, line.strip()
    for path, text, _ in reversed(layers):        # YAML·TOML은 따옴표 없이 적기도 한다
        for lineno, line in enumerate(text.splitlines(), 1):
            if value in line:
                return path, lineno, line.strip()
    return None


def _entries_naming(node, key: str, name: str, where: tuple = ()):
    """합친 config에서 `key` 잎이 그 클래스 이름인 객체 — `(경로, 객체)`. 값이 점 표기(`<모듈>.<클래스>`)여도 끝 이름으로 본다."""
    if isinstance(node, dict):
        value = node.get(key)
        if isinstance(value, str) and value.rsplit(".", 1)[-1] == name:
            yield list(where), node
        for k, v in node.items():
            yield from _entries_naming(v, key, name, (*where, str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _entries_naming(v, key, name, (*where, str(i)))


def _has_path(node, path: list[str]) -> bool:
    """그 층이 그 잎을 직접 적었나 — 병합 결과가 아니라 층 하나의 값에서."""
    for part in path:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return True


def _truthy(value) -> bool:
    """켜짐 잎 — JSON 참, 1, 문자열 "true"·"on"·"yes"·"1"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on")


class DeployedCode(DeployedCodePort):
    """`Adapters.code`에 꽂히는 어댑터. 사이트 하나(=`(gbm, fct)`) 기준이다."""

    def __init__(self, reader: CodeReaderPort, topology: Topology,
                 deployment: Deployment, *, gbm: str, fct: str, clock: Clock):
        self._reader = reader
        self._topology = topology
        self._deployment = deployment
        self._gbm, self._fct = gbm, fct
        self._clock = clock
        # 흐름 그래프(오버레이). 생성자가 아니라 뒤에 붙이는 이유: 낡았는지 보려면 배포 커밋을
        # 실제 SHA로 풀어야 하고, 그 일에 이 어댑터 자신이 필요하다.
        self._flow_graph: dict | None = None
        # 심볼 인덱스(11d)와 그 질의 그래프 — 같은 번들에서 같은 신선도로 붙는다. 질의 그래프는 한 번만 만든다.
        self._index = None
        self._qgraph = None
        # 레포 스냅샷(11e) — `(repo, commit)` → `경로 → 본문`. 커밋으로 주소가 매겨져 안 변하므로 한 번만 받는다.
        # `code graph`가 사이트 28개의 config 층을 병합할 때 여기서 읽는다 — git은 레포당 한 번이다.
        # (파일들 또는 None, 못 본 것의 사유) — 사유는 흐름 히트의 "잘렸다" 메모가 된다(안 채워진 submodule 등).
        self._snapshots: dict[tuple[str, str], tuple[dict[str, str] | None, str]] = {}
        # 레포 스냅샷의 등록들(AST) — `(repo, commit)`마다 한 번(R3-5 ③). 걷기가 클래스마다 묻는다.
        self._registrations: dict[tuple[str, str], list[Registration]] = {}

    def attach_flow_graph(self, graph: dict | None) -> None:
        self._flow_graph = graph

    def attach_index(self, index) -> None:
        from src.knowledge import query as qy

        self._index = index
        self._qgraph = qy.Graph(index) if index is not None else None

    def has_index(self) -> bool:
        return self._index is not None

    def class_names(self) -> frozenset[str]:
        """인덱스의 클래스 이름들 — 입구 노드가 키 값의 문자열 잎과 완전 일치로 "쓴 쪽"을 고르는 닫힌 집합(R3-1c ①-a)."""
        if self._index is None:
            return frozenset()
        return frozenset(s.name for s in self._index.symbols if s.kind == "class")

    def _repo_hint(self, found: list[int]) -> list[str]:
        """같은 이름이 **두 레포**에 있으면 `파일:qualname`으로는 못 가른다(측정 #9 P1 4 — 파일도 같았다). 레포 단위로 센다 — 서비스 단위 셈은 한
        레포를 여러 서비스가 `path` 없이 공유할 때 그 파일을 못 세어 안내가 안 떴다(측정 #10 P1 6)."""
        from src.knowledge import query as qy

        return qy.repo_hint(self._index, found, flowgraph.repo_services(self._topology), how="repo= 인자(또는 그 레포의 서비스로 service=)")

    def class_services(self) -> dict[str, tuple[str, ...]]:
        """클래스 이름 → 그 클래스가 있는 파일의 서비스들(`flow.owner`). 같은 이름이 두 레포에 있으면 둘 다(측정 #9 P1 4). `owner`가 못 가른
        파일(한 레포를 여러 서비스가 `path` 없이 공유)은 **그 레포의 서비스 전부** — 버리면 걷기가 그 클래스를 "한 곳"으로 보고 레포를 못 가른다
        (측정 #10 P0 2: 원천 키 셋이 수집기로 붙고 공용 키에서 걷기가 멈췄다). 홉 표의 `[서비스]`와 verify의 클래스→서비스 번역(⑤)이 쓴다."""
        if self._index is None:
            return {}
        shared = flowgraph.repo_services(self._topology)
        out: dict[str, set[str]] = {}
        for s in self._index.symbols:
            if s.kind == "class":
                svc = flowgraph.owner(s.file, s.repo, self._topology)[0]
                out.setdefault(s.name, set()).update([svc] if svc else shared.get(s.repo, ()))
        return {name: tuple(sorted(services)) for name, services in out.items()}

    def service_repos(self) -> dict[str, str]:
        """서비스 → 레포(토폴로지). 걷기가 클래스의 서비스들을 레포로 묶어 레포 단위로 가르는 데 쓴다(R3-4 ①)."""
        return {name: svc.repo for name, svc in self._topology.services.items()}

    def _where(self, sid: int) -> str:
        s = self._index.symbols[sid]
        svc = flowgraph.owner(s.file, s.repo, self._topology)[0]
        return f"[{s.repo}{' · ' + svc if svc else ''}]"

    async def callers(self, name: str) -> ProbeResult:
        from src.knowledge import query as qy

        source = f"code.callers {name}"
        if self._index is None:
            return ProbeResult.failed("심볼 인덱스가 없다 — `python -m src code graph`로 만든다",
                                      source=source, clock=self._clock)
        found = qy.find(self._index, name)
        if not found:
            return ProbeResult.failed(f"{name}: 인덱스에 없다 — code.trace·code.uses 출력에 나온 이름 끝부분"
                                      f"(`Class.method`)이나 `파일:qualname`으로", source=source, clock=self._clock)
        if len(found) > 1:
            return ProbeResult.failed("\n".join(qy.ambiguous_lines(self._index, name, found)),
                                      source=source, clock=self._clock)
        lines = qy.callers_lines(self._index, self._qgraph, found[0], where=self._where)
        return ProbeResult.succeeded("\n".join(lines), source=source, clock=self._clock)

    async def resources(self, name: str, service: str = "", repo: str = "") -> ProbeResult:
        from src.knowledge import query as qy

        source = f"code.resources {name}" + (f" service={service}" if service else f" repo={repo}" if repo else "")
        if self._index is None:
            return ProbeResult.failed("심볼 인덱스가 없다 — `python -m src code graph`로 만든다",
                                      source=source, clock=self._clock)
        text = name
        if service:
            known = self._topology.services.get(service)
            if known is None:
                return ProbeResult.failed(f"없는 서비스 — {service}. 아는 것: {', '.join(sorted(self._topology.services)) or '없음'}",
                                          source=source, clock=self._clock)
            text = f"{known.repo}:{name}"
        elif repo:
            # 한 레포를 여러 서비스가 공유하면 걷기는 레포로 가른다(R3-4 ①) — 서비스 하나를 대표로 대면 표와 기록이 거짓이 된다.
            known_repos = flowgraph.repo_services(self._topology)
            if repo not in known_repos:
                return ProbeResult.failed(f"없는 레포 — {repo}. 아는 것: {', '.join(known_repos) or '없음'}", source=source, clock=self._clock)
            text = f"{repo}:{name}"
        found = qy.find(self._index, text)
        if not found:
            return ProbeResult.failed(f"{name}: 인덱스에 없다 — 케이스 블록의 쓴 쪽 이름 그대로나 `파일:qualname`으로",
                                      source=source, clock=self._clock)
        if len(found) > 1:
            lines = qy.ambiguous_lines(self._index, name, found)
            lines += self._repo_hint(found)
            return ProbeResult.failed("\n".join(lines), source=source, clock=self._clock)
        return ProbeResult.succeeded("\n".join(qy.resources_lines(self._index, found[0], where=self._where)),
                                     source=source, clock=self._clock)

    def _registered(self, repo: str, snap: dict[str, str]) -> list[Registration]:
        key = (repo, self.pinned().get(repo, ""))
        if key not in self._registrations:
            self._registrations[key] = [r for path in sorted(snap) if path.endswith(_CODE_SUFFIX) for r in registrations(path, snap[path])]
        return self._registrations[key]

    def _site_picks(self, regs: list[Registration], component: str) -> list[Registration]:
        """이 사이트의 등록 — 명시한 사이트 인자가 전부 이 사이트 값과 같은 것만 후보, 그중 명시 인자가 많은 것, 수가 같으면 여럿(사람 결정 10-15)."""
        mine = {"gbm": self._gbm, "factory": self._fct, "fct": self._fct}
        ok = [r for r in regs if r.component == component and all(mine.get(k) == v for k, v in r.site.items())]
        if not ok:
            return []
        best = max(len(r.site) for r in ok)
        return [r for r in ok if len(r.site) == best]

    def _class_of(self, repo: str, reg: Registration):
        """등록이 가리키는 클래스 심볼 — 별칭이 있으면 그 모듈의 것. `(심볼 또는 None, 못 푼 사유)`."""
        from src.knowledge import query as qy

        found = [sid for sid in qy.find(self._index, f"{repo}:{reg.cls}") if self._index.symbols[sid].kind == "class"]
        if reg.module:
            found = [sid for sid in found if self._index.symbols[sid].qualname == f"{reg.module}.{reg.cls}"] or found
        if not found:
            return None, f"{reg.cls}(인덱스에 없음)"
        if len(found) > 1:
            return None, f"{reg.cls}(여럿)"
        return self._index.symbols[found[0]], ""

    async def filter_lines(self, name: str, *, fields: dict[str, str] | None = None, service: str = "", repo: str = "") -> FilterScan:
        """걷기의 단일값 검사(R3-4 ③ → R3-5 ③) — **범위**는 그 입력을 읽는 클래스의 파일 + 거기서 `get("X")` 리터럴로 부르는 컴포넌트의 **클래스 파일**
        (등록 모음 파일의 `register(component="X", …)(<클래스>)`를 AST로 읽고 클래스를 인덱스로 푼다; 등록 줄의 파일이 아니다 — 측정 #11 P0 3), **짝**은
        필드와 값이 한 비교식의 양쪽(연산자는 `op`에 사실로 — 거르는 쪽인지는 걷기가 정한다, R3-5l ②). 비교식을 먼저 뽑아 `fields` 전부와 맞춘다(상한
        없음). 못 이은 것(변수·이 사이트의 등록 없음·인덱스에 없음·여럿)은
        `unlinked` — 하나라도 있으면 걷기가 "일부만 검사"로 적는다. runner를 안 거친다 — 읽기 상한·증거 밖. 던지지 않는다 — 조회가 안 되면 `problem`."""
        from src.knowledge import query as qy

        fields = dict(fields or {})
        if self._index is None:
            return FilterScan(problem="심볼 인덱스가 없다")
        text = name
        if service:
            known = self._topology.services.get(service)
            if known is None:
                return FilterScan(problem=f"없는 서비스 — {service}")
            text = f"{known.repo}:{name}"
        elif repo:
            if repo not in flowgraph.repo_services(self._topology):
                return FilterScan(problem=f"없는 레포 — {repo}")
            text = f"{repo}:{name}"
        found = [sid for sid in qy.find(self._index, text) if self._index.symbols[sid].kind == "class"]
        if not found:
            return FilterScan(problem=f"{name}: 인덱스에 없다")
        if len(found) > 1:
            return FilterScan(problem=f"{name}: 여럿이다({len(found)}) — 레포 {', '.join(sorted({self._index.symbols[sid].repo for sid in found}))}")
        sym = self._index.symbols[found[0]]
        try:
            snap = await self.snapshot_for(sym.repo)
        except Exception as exc:                                    # noqa: BLE001
            return FilterScan(problem=f"{sym.repo}의 스냅샷을 못 받았다 — {type(exc).__name__}: {exc}")
        if snap is None:
            return FilterScan(problem=f"{sym.repo}의 스냅샷을 못 받았다")
        if sym.file not in snap:
            return FilterScan(problem=f"{sym.repo}의 스냅샷에 {sym.file}이 없다")
        # 범위 = 클래스 파일 + **진입에서 닿는** 부모 메서드들의 파일(R3-5d ③ → R3-5e ②: 부모 파일 전부를 넣으니 안 부르는 믹스인의 `get(<변수>)`가
        # "못 이음"으로 세어졌다 — 측정 #15 P0-3의 절반) + 이은 컴포넌트 클래스 파일
        files = [sym.file]
        _, reached, _ = qy.reachable_members(self._index, found[0])
        for mid in reached:
            path = self._index.symbols[mid].file
            if path in snap and path not in files:
                files.append(path)
        literals, variables = component_names("\n".join(snap.get(path, "") for path in files))
        regs = self._registered(sym.repo, snap)
        # 변수 인자의 기본값 리터럴로 잇는다(R3-5e ③) — 범위 파일 안에서 그 인자를 안 넘기면 실행 때 값은 그 기본값이다. 못 이으면 전처럼 "못 이음".
        resolved = default_links({path: snap.get(path, "") for path in files})
        # 반복 변수(R3-5m ①) — 같은 함수의 for·컴프리헨션이 리터럴 묶음을 돌면 그 리터럴 전부. 기본값 리터럴과 합쳐 "변수 → 리터럴들"로 본다.
        linked_vars: dict[str, tuple[str, ...]] = {v: (lit,) for v, lit in resolved.items()}
        for v, lits in loop_links({path: snap.get(path, "") for path in files}).items():
            linked_vars[v] = tuple(dict.fromkeys((*linked_vars.get(v, ()), *lits)))
        # 수신자에 리터럴 get이 하나도 없는 범위(뼈대의 `get(<변수>)`뿐 — 측정 #16 P0-3의 실코드 모양)에서는 `component_names`가 그 변수를 안 센다(사전 get과 못
        # 가른다). 이은 리터럴이 등록된 컴포넌트면 그 변수도 컴포넌트 변수다(R3-5f ②).
        variables += [v for v, lits in linked_vars.items() if v not in variables and any(r.component == lit for r in regs for lit in lits)]
        literals += [lit for v in variables for lit in linked_vars.get(v, ()) if lit not in literals]
        linked: list[str] = []
        linked_at: list[FilterHit] = []
        unlinked = [f"변수 {v}" for v in variables if v not in linked_vars]
        for comp in literals:
            picked = self._site_picks(regs, comp)
            if not picked:
                # 등록이 아예 없는 이름(`cfg.get("kind")` 같은 사전 get)은 컴포넌트가 아니다 — 못 이음도 아니다. 있는데 이 사이트 것이 없으면 못 이음.
                if any(r.component == comp for r in regs):
                    unlinked.append(f"{comp}(이 사이트의 등록 없음)")
                continue
            for reg in picked:
                csym, why = self._class_of(sym.repo, reg)
                if csym is None:
                    unlinked.append(f"{comp}: {why}")
                    continue
                if csym.file not in files:
                    files.append(csym.file)
                if comp not in linked:
                    linked.append(comp)
                linked_at.append(FilterHit(path=csym.file, line=csym.line, repo=sym.repo,
                                           text=f"class {csym.name} — register(component={comp!r}) {reg.path}:L{reg.line}"))
        if fields:
            # 뒤 칸의 차례 — 나쁜 필드 이름이 리터럴로 든 클래스 파일 먼저(사람 결정 10-15 (c))
            quoted = [q + f + q for f in fields for q in ("'", '"')]
            linked_at.sort(key=lambda h: 0 if any(q in snap.get(h.path, "") for q in quoted) else 1)
        hits = [FilterHit(path=path, line=i, text=body, repo=sym.repo, field=f, value=v, op=op)
                for path in files for i, f, v, op, body in comparison_pairs(snap.get(path, "")) if f in fields and fields[f] == v]
        # 범위 파일의 pandas 병합식(R3-5g ④) — 걷기가 두 입력의 그 필드 값이 겹치는지 센다. 비교식과 같은 범위, 같은 사실의 종류(코드가 무엇으로 잇나).
        merges = [MergeHit(path=path, line=i, text=body, left=list(left), right=list(right), how=how, chained=follow)
                  for path in files for i, left, right, how, body, follow in merge_pairs(snap.get(path, ""))]
        return FilterScan(hits=hits, checked=list(fields), files=files, linked=linked, linked_at=linked_at, unlinked=unlinked, merges=merges)

    async def uses(self, name: str, service: str = "") -> ProbeResult:
        from src.knowledge import query as qy

        source = f"code.uses {name}" + (f" service={service}" if service else "")
        if self._index is None:
            return ProbeResult.failed("심볼 인덱스가 없다 — `python -m src code graph`로 만든다",
                                      source=source, clock=self._clock)
        known = None
        if service:
            # 측정 #10 P1 7: 리드가 `code.resources`의 service=를 여기에도 냈다가 거부당했다 — 받아서 그 서비스의 레포 안으로 좁힌다.
            known = self._topology.services.get(service)
            if known is None:
                return ProbeResult.failed(f"없는 서비스 — {service}. 아는 것: {', '.join(sorted(self._topology.services)) or '없음'}",
                                          source=source, clock=self._clock)
        if name in self._topology.services:
            # 사내 측정 #5: 서비스 이름으로 내고 "인덱스에 없다"를 받자 같은 이름으로 또 냈다 — 어디로 가야 하는지까지 말한다.
            return ProbeResult.failed(f"{name}: 서비스 이름이다 — 서비스의 배선은 code.flow name={name!r}, 자원을 쓰는 함수는 "
                                      f"그 자원 이름(컬렉션·토픽·키)으로 code.uses", source=source, clock=self._clock)
        found = qy.uses(self._index, name)
        if known is not None:
            found = [u for u in found if self._index.symbols[u.sid].repo == known.repo]
        if not found:
            # 측정 #9: 케이스 블록의 `쓴 쪽: <클래스>`를 보고 `code.uses <클래스>`를 냈다(r2·r3) — 클래스는 자원이 아니다. 서비스 이름 때처럼
            # 어디로 가야 하는지까지 말한다.
            if any(self._index.symbols[sid].kind == "class" for sid in qy.find(self._index, name)):
                return ProbeResult.failed(f"{name}: 클래스 이름이다 — 그 클래스가 읽고 쓰는 자원은 code.resources name={name!r}, "
                                          f"자원을 쓰는 함수는 그 자원 이름(컬렉션·토픽·키)으로 code.uses", source=source, clock=self._clock)
            inside = f"({service}의 레포 {known.repo} 안에서) " if known is not None else ""
            return ProbeResult.failed(f"{name}: 이 이름의 자원을 쓰거나 읽는 함수가 인덱스에 없다{inside} — "
                                      f"<데이터 흐름>이나 증거에 나온 이름 그대로 써라", source=source, clock=self._clock)
        lines = qy.uses_lines(self._index, self._qgraph, found, where=self._where)
        return ProbeResult.succeeded("\n".join(lines), source=source, clock=self._clock)

    async def flow(self, name: str) -> ProbeResult:
        source = f"code.flow {name}"
        if self._flow_graph is None:
            return ProbeResult.failed("흐름 그래프가 없다 — `python -m src code graph`로 만든다",
                                      source=source, clock=self._clock)
        near = flowgraph.neighbors(self._flow_graph, name, depth=1)
        if not near:
            return ProbeResult.failed(
                f"{name}: 그래프에 없다 — <데이터 흐름>이나 증거에 나온 이름 그대로 써라",
                source=source, clock=self._clock)
        services = [n["id"] for n in self._flow_graph["nodes"] if n.get("type") == "service" and name in (n["id"], n["label"])]
        if services:
            # 서비스는 엣지 나열이 아니라 관계별 요약(사내 측정 #5: 684줄이 40줄에서 잘렸다). 자원은 전처럼 엣지.
            return ProbeResult.succeeded("\n".join(flowgraph.service_summary(self._flow_graph, services[0])),
                                         source=source, clock=self._clock)
        lines, left = flowgraph.neighbor_lines(self._flow_graph, near)
        return ProbeResult.succeeded(
            "\n".join(lines), source=source, clock=self._clock,
            truncated_reason=f"{left}줄 더 있다 — 관계가 많은 이름이다" if left else None)

    async def trace(self, endpoint: str) -> ProbeResult:
        source = f"code.trace {endpoint}"
        if self._flow_graph is None:
            return ProbeResult.failed("흐름 그래프가 없다 — `python -m src code graph`로 만든다",
                                      source=source, clock=self._clock)
        endpoint = flowgraph.endpoint_path(endpoint)
        node_id = flowgraph.endpoint_id(endpoint)
        if not any(n["id"] == node_id for n in self._flow_graph.get("nodes", [])):
            return ProbeResult.failed(
                f"{endpoint}: 그래프의 끝점에 없다 — 등재 항목의 path나 증거에 나온 path 그대로 써라",
                source=source, clock=self._clock)
        lines = flowgraph.trace_lines(self._flow_graph, node_id)
        if lines is None:
            return ProbeResult.failed(
                f"{endpoint}: 추적이 안 된 끝점이다 — 라우트 선언을 못 찾았거나 서빙 서비스를 모른다. "
                f"code.grep으로 핸들러를 찾아 code.read로 본다", source=source, clock=self._clock)
        return ProbeResult.succeeded("\n".join(lines), source=source, clock=self._clock)

    def service_names(self) -> tuple[str, ...]:
        """브리핑이 목록과 예시에 박을 이름들. **호출부가 토폴로지를 뒤지지 않게** 한다."""
        return tuple(sorted(self._topology.services))

    def service_roles(self) -> dict[str, str]:
        """이름 → 역할. 역할이 빈 서비스는 뺀다 — 빈 괄호는 정보가 아니라 소음이다."""
        return {name: svc.role for name, svc in sorted(self._topology.services.items())
                if svc.role}

    def describe(self) -> str:
        return f"code({self._gbm}/{self._fct}, 서비스 {len(self._topology.services)}개)"

    # ── 흐름 그래프(11c) 재료 ────────────────────────────────────

    def pinned(self) -> dict[str, str]:
        """레포 → 이 사이트의 배포 커밋(참조 그대로). 같은 레포는 커밋도 같다."""
        out: dict[str, str] = {}
        for name, svc in sorted(self._topology.services.items()):
            out.setdefault(svc.repo, self._deployment.pin_for(name, fct=self._fct).commit)
        return out

    async def flow_names(self) -> tuple[list[Name], list[str]]:
        """모든 서비스의 합친 config에서 뽑은 이름들(어느 서비스가 가졌는지 포함)과, 못 읽은
        서비스의 사유. 같은 레포의 서비스라도 환경변수로 고른 층이 다르면 합친 config가
        다르다 — 그래서 이름마다 서비스를 기억한다."""
        found: dict[tuple, Name] = {}
        holders: dict[tuple, set[str]] = {}
        evidence: dict[tuple, list] = {}
        problems = []
        for service in sorted(self._topology.services):
            got = await self._layers(service, f"code.config {service}")
            if isinstance(got, ProbeResult):
                problems.append(f"{service}: {got.error}")
                continue
            _, layers, _, _ = got
            merged = merge_target([(path, value) for path, _, value in layers])
            for n in names_from_config(merged, self._topology.flow.sources):
                key = (n.kind, n.value, n.key_path)
                found.setdefault(key, n)
                holders.setdefault(key, set()).add(service)
                where = _evidence(layers, n.value)
                if where:
                    evidence.setdefault(key, []).append((service, *where))
        return ([replace(n, services=tuple(sorted(holders[k])),
                         evidence=tuple(sorted(evidence.get(k, []))))
                 for k, n in found.items()], problems)

    async def flow_hits(self, patterns: list[str], *,
                        progress: Callable[[str], None] | None = None
                        ) -> tuple[dict[str, list[Hit]], list[str]]:
        """배포 커밋의 **스냅샷에서**(11e-3) `git grep -n -F -C1`과 같은 히트를 — `(패턴 → 히트, 잘림 사유)`.
        스냅샷을 못 받으면 `git grep`을 레포마다 몇 번(패턴 `FLOW_CHUNK`개씩)으로.

        패턴마다 한 번씩 띄우면 이름 100개·레포 3개에 600번이고, 20개씩 묶어도 사내는 레포당 19번 × 5레포 =
        231초였다 — 본문은 이미 스냅샷에 있다. 히트는 줄 본문에 패턴이 들어 있는지로 패턴별로 나눈다
        — `-F`라 git이 맞춘 것도 정확히 그 부분 문자열이다. 잘린 결과는 버리지 않고 사유로
        돌려준다(그래프에 엣지가 빠졌을 수 있다) — 스냅샷 길에서는 못 본 submodule이 그 사유다. 실패한 레포는
        조용히 빈다 — `code status`가 레포 상태를 따로 말한다. 앞뒤 한 줄은 `Hit.context`로 간다.
        """
        table: dict[str, list[Hit]] = {p: [] for p in patterns}
        notes: list[str] = []
        chunks = [patterns[i:i + FLOW_CHUNK] for i in range(0, len(patterns), FLOW_CHUNK)]
        for repo, commit in self.pinned().items():
            snap, unseen = await self._snapshot_result(repo)
            if snap is not None:
                if progress:
                    progress(f"{repo}: 코드에서 이름 찾는 중 (패턴 {len(patterns)}개, 스냅샷에서)")
                if unseen:
                    notes.append(f"{repo}: 코드 찾기가 잘렸다({unseen}) — 그래프에 엣지가 빠졌을 수 있다")
                for hit in grep_snapshot(repo, commit, snap, patterns, fixed=True, context=1):
                    for p in patterns:
                        if p in hit.text:
                            table[p].append(hit)
                continue
            if progress:
                progress(f"{repo}: 코드에서 이름 찾는 중 (패턴 {len(patterns)}개, git grep {len(chunks)}번)")
            for chunk in chunks:
                got = await self._reader.grep(repo, commit, chunk, context=1, fixed=True,
                                              max_lines=FLOW_MAX_LINES, max_chars=FLOW_MAX_CHARS)
                if got.status == "error" or not isinstance(got.data, str):
                    continue
                if not got.envelope.complete:
                    notes.append(f"{repo}: 코드 찾기가 잘렸다({got.envelope.truncated_reason}) — "
                                 f"그래프에 엣지가 빠졌을 수 있다")
                for hit in parse_grep(repo, commit, got.data):
                    for p in chunk:
                        if p in hit.text:
                            table[p].append(hit)
        return table, notes

    async def route_hits(self, *, progress: Callable[[str], None] | None = None
                         ) -> tuple[list[Hit], list[str]]:
        """배포 커밋의 스냅샷에서 라우트 선언 줄(FastAPI 모양)을 — 스냅샷이 없으면 레포마다 `git grep -n`(정규식) 한 번.
        `(히트, 잘림 사유)`. 조립은 `flow.routes_from_hits`가 한다 — 여기는 줄을 모을 뿐이다."""
        hits: list[Hit] = []
        notes: list[str] = []
        for repo, commit in self.pinned().items():
            if progress:
                progress(f"{repo}: 라우트 선언 찾는 중")
            snap, unseen = await self._snapshot_result(repo)
            if snap is not None:
                if unseen:
                    notes.append(f"{repo}: 라우트 찾기가 잘렸다({unseen}) — 끝점이 빠졌을 수 있다")
                hits += grep_snapshot(repo, commit, snap, list(flowgraph.ROUTE_REGEXES), fixed=False)
                continue
            got = await self._reader.grep(repo, commit, list(flowgraph.ROUTE_PATTERNS),
                                          max_lines=FLOW_MAX_LINES, max_chars=FLOW_MAX_CHARS)
            if got.status == "error" or not isinstance(got.data, str):
                continue
            if not got.envelope.complete:
                notes.append(f"{repo}: 라우트 찾기가 잘렸다({got.envelope.truncated_reason}) — "
                             f"끝점이 빠졌을 수 있다")
            hits += parse_grep(repo, commit, got.data)
        return hits, notes

    async def snapshot_for(self, repo: str) -> dict[str, str] | None:
        """그 레포의 배포 커밋 스냅샷. 못 받으면 None — 호출자가 `show`로 간다."""
        return (await self._snapshot_result(repo))[0]

    async def _snapshot_result(self, repo: str) -> tuple[dict[str, str] | None, str]:
        """`(파일들 또는 None, 못 본 것의 사유)` — 한 번만 받는다."""
        commit = self.pinned()[repo]
        key = (repo, commit)
        if key not in self._snapshots:
            got = await self._reader.snapshot(repo, commit)
            ok = got.status == "ok" and isinstance(got.data, dict)
            self._snapshots[key] = (got.data if ok else None, (got.envelope.truncated_reason or "") if ok else "")
        return self._snapshots[key]

    def _layer_paths(self, fct: str) -> list[str]:
        """그 사이트가 볼 층. `fct=""`는 **GBM 층만**(`{fct}`가 든 경로를 뺀다) — 그래프의 기준값이다."""
        if fct:
            return self._topology.resolved_config_paths(self._gbm, fct)
        return [p.replace("{gbm}", self._gbm) for p in self._topology.config_paths if "{fct}" not in p]

    async def _layers_in(self, service: str, fct: str):
        """`_layers`와 같은 `[(경로, 원문, 값)]`을 **스냅샷에서** — 사이트 28개 × 서비스 × 층을 git에 다시 묻지
        않기 위해서다. 스냅샷이 없으면 그 경로들만 `show`로 읽는다(같은 결과, 느릴 뿐). 못 읽으면 실패 `ProbeResult`."""
        source = f"code.config {service}" + (f" @{fct}" if fct else " @gbm")
        resolved = self._resolve(service, source)
        if isinstance(resolved, ProbeResult):
            return resolved
        known, commit, _ = resolved
        wanted = self._layer_paths(fct)
        if not wanted:
            return ProbeResult.failed("config_paths가 선언돼 있지 않다", source=source, clock=self._clock)
        snap = await self.snapshot_for(known.repo)
        layers = []
        for path in wanted:
            if snap is not None:
                text = snap.get(path)
            else:
                got = await self._reader.show(known.repo, commit, path, whole=True)
                text = got.data if got.status == "ok" and got.envelope.complete else None
            if text is None:
                continue                    # 없는 층은 정상이다(층은 선택)
            value, _ = parse_layer(path, text)
            if value is not None:
                layers.append((path, text, value))
        if not layers:
            return ProbeResult.failed(f"쓸 수 있는 config 층이 없다 — 찾은 자리: {', '.join(wanted)}",
                                      source=source, clock=self._clock)
        return layers

    async def names_for(self, fct: str) -> tuple[list[Name], list[str]]:
        """`flow_names`와 같은 이름들을 **그 사이트의 층**(스냅샷)에서. `fct=""`면 GBM 층만."""
        found: dict[tuple, Name] = {}
        holders: dict[tuple, set[str]] = {}
        evidence: dict[tuple, list] = {}
        problems = []
        for service in sorted(self._topology.services):
            got = await self._layers_in(service, fct)
            if isinstance(got, ProbeResult):
                problems.append(f"{service}: {got.error}")
                continue
            merged = merge_target([(path, value) for path, _, value in got])
            for n in names_from_config(merged, self._topology.flow.sources):
                key = (n.kind, n.value, n.key_path)
                found.setdefault(key, n)
                holders.setdefault(key, set()).add(service)
                where = _evidence(got, n.value)
                if where:
                    evidence.setdefault(key, []).append((service, *where))
        return ([replace(n, services=tuple(sorted(holders[k])), evidence=tuple(sorted(evidence.get(k, []))))
                 for k, n in found.items()], problems)

    async def site_overrides(self, fct: str, base: list[Name]) -> list[dict]:
        """그 사이트 층이 GBM 기준(`base`)과 **다르게** 정한 값만 — `sites/<fct>.json`의 내용(`flow.site_rows`: 서비스마다 그 서비스의 GBM 값과
        견준다, R3-5f ①)."""
        mine, _ = await self.names_for(fct)
        return flowgraph.site_rows(mine, base)

    def source_for(self, repo: str) -> "_GitSource":
        """추적기(`knowledge.trace`)의 `Source` — 그 레포의 배포 커밋에서 파일과 리터럴 grep."""
        return _GitSource(self._reader, repo, self.pinned()[repo], shared=self.snapshot_for)

    # ── 서비스 해석 ──────────────────────────────────────────────

    def _resolve(self, service: str, source: str):
        """서비스 이름 → `(레포, 커밋, 선언인가)`. 못 찾으면 `ProbeResult`."""
        known = self._topology.services.get(service)
        if known is None:
            return ProbeResult.failed(
                f"없는 서비스 — {service}. 아는 것: "
                f"{', '.join(sorted(self._topology.services)) or '없음'}",
                source=source, clock=self._clock)
        pin = self._deployment.pin_for(service, fct=self._fct)
        return known, pin.commit, pin.how

    def _pinned(self, source: str, commit: str, how: str) -> str:
        """증거에 **어느 커밋을 읽었는지**가 남아야 판정을 되짚을 수 있다."""
        return f"{source} @ {commit[:12]}" + ("" if how == "declared" else " (가정)")

    async def pipeline_here(self, class_name: str) -> str:
        """이 사이트에서 그 클래스의 파이프라인이 도는가 — 돌면 빈 문자열, 아니면 그 사실 한 줄(R3-5v ② — 검토 회신 11-03 3: 다른 사이트 값이 댄 쓴 쪽
        `<포장 계획 클래스>`는 두 사이트에만 켜져 있었는데 이 사이트 키의 쓴 쪽으로 채워 토픽까지 이었다). 이 사이트의 병합 config에서 클래스 키 값이 그 클래스인
        항목(토폴로지 `pipelines`)의 켜짐 값을 본다. 켜짐이 GBM 층에서만 오면(사이트 층이 안 덮는다) 이 사이트 배포 커밋의 그 클래스 파일에 정의가 있는지
        git으로 — GBM 층의 항목은 모든 사이트에 같지만 배포 커밋은 사이트마다 다르다. 사이트 층이 켰으면 그 사이트가 일부러 켠 것이다. 선언이 없으면 확인할
        수 없다는 사실을 돌려준다(걷기가 채우지 않게). 던지지 않는다."""
        try:
            return await self._pipeline_here(class_name)
        except Exception as exc:                                    # noqa: BLE001
            return f"이 사이트에서 도는지 확인하지 못했다({type(exc).__name__})"

    async def _pipeline_here(self, class_name: str) -> str:
        spec = self._topology.pipelines
        if spec is None:
            return "이 사이트에서 도는지 확인할 선언이 없다(토폴로지 pipelines)"
        services = sorted(self.class_services().get(class_name, ()))
        if not services:
            return f"인덱스에 {class_name}의 서비스가 없다"
        base = set(self._layer_paths(""))
        found, unread = [], []
        for service in services:
            got = await self._layers_in(service, self._fct)
            if isinstance(got, ProbeResult):
                unread.append(f"{service}: {got.error}")
                continue
            merged = merge_target([(path, value) for path, _, value in got])
            site_values = [value for path, _, value in got if path not in base]
            for where, entry in _entries_naming(merged, spec.class_key, class_name):
                by_site = any(_has_path(value, [*where, spec.enabled_key]) for value in site_values)
                found.append((service, where, entry.get(spec.enabled_key), by_site))
        if not found:
            if unread and len(unread) == len(services):
                return f"이 사이트 config를 못 읽었다 — {unread[0]}"
            return f"이 사이트 config에 {class_name} 파이프라인이 없다"
        on = [f for f in found if _truthy(f[2])]
        if not on:
            _, where, enabled, _ = found[0]
            return f"이 사이트 config에서 {class_name} 파이프라인이 꺼져 있다({'.'.join([*where, spec.enabled_key])}={json.dumps(enabled, ensure_ascii=False)})"
        if any(by_site for *_, by_site in on):
            return ""
        return await self._defined_at_pin(class_name, on[0][0])

    async def _defined_at_pin(self, class_name: str, service: str) -> str:
        """이 사이트 배포 커밋의 그 클래스 파일에 정의가 있나 — 있으면 빈 문자열."""
        known = self._topology.services[service]
        files = sorted({s.file for s in self._index.symbols if s.kind == "class" and s.name == class_name and s.repo == known.repo})
        if not files:
            return f"인덱스에 {class_name}의 파일이 없다"
        commit = self._deployment.pin_for(service, fct=self._fct).commit or self._deployment.default_ref
        got = await self._reader.show(known.repo, commit, files[0], whole=True)
        if got.status != "ok" or not isinstance(got.data, str):
            return f"이 사이트 배포 커밋 {commit[:12]}에 {files[0]} 파일이 없다 — {class_name} 정의가 아직 배포되지 않았을 수 있다"
        try:
            tree = ast.parse(got.data)
        except SyntaxError:
            return f"이 사이트 배포 커밋 {commit[:12]}의 {files[0]}를 파싱하지 못했다"
        if not any(isinstance(node, ast.ClassDef) and node.name == class_name for node in ast.walk(tree)):
            return f"이 사이트 배포 커밋 {commit[:12]}의 {files[0]}에 {class_name} 정의가 없다 — 아직 배포되지 않았을 수 있다"
        return ""

    def _elsewhere(self, path: str, repo: str) -> str:
        """`code.read`가 git에서 실패했을 때의 꼬리 — 인덱스가 이 경로를 **다른 레포**의 모듈로 알면 그 서비스를 말한다(측정 #8 P1 5: 리드가
        `code.uses` 증거의 경로를 다른 서비스 이름으로 읽어 git의 `fatal: path`만 받았다). 어느 레포에도 없는 경로면 인덱스에서 **같은 파일 이름**의
        모듈을 댄다(R3-5u ③ — 측정 #30 재측정: 리드가 3/3회 같은 틀린 디렉터리로 읽어 `fatal: path`만 받았다). 경로가 없다고 단정하지는 않는다 —
        인덱스와 배포 커밋이 다를 수 있다; 인덱스가 이 레포의 그 경로를 알면 꾸미지 않고, 아니면 인덱스가 아는 같은 이름을 말할 뿐이다."""
        if self._index is None:
            return ""
        modules = [s for s in self._index.symbols if s.kind == "module"]
        repos = sorted({s.repo for s in modules if s.file == path and s.repo != repo})
        if repos:
            other = repos[0]
            svc = flowgraph.owner(path, other, self._topology)[0]
            svcs = [svc] if svc else sorted(n for n, s in self._topology.services.items() if s.repo == other)
            if not svcs:
                return f" — 이 경로는 {other} 레포에 있다(토폴로지에 그 레포의 서비스가 없다)"
            return f" — 이 경로는 {'·'.join(svcs)}({other})에 있다 — service={svcs[0]!r}로 다시 내라"
        if any(s.file == path and s.repo == repo for s in modules):
            return ""
        base = path.rsplit("/", 1)[-1]
        same = sorted({(s.repo, s.file) for s in modules if s.file != path and s.file.rsplit("/", 1)[-1] == base})
        if not same:
            return ""

        def shown(r: str, f: str) -> str:
            svc = flowgraph.owner(f, r, self._topology)[0]
            return f"{r}:{f}" + (f"(service={svc!r})" if svc else "")

        more = f" 외 {len(same) - 3}" if len(same) > 3 else ""
        return f" — 인덱스에 같은 이름의 파일: {', '.join(shown(r, f) for r, f in same[:3])}{more} — 그 path로 다시 내라"

    # ── action 넷 ────────────────────────────────────────────────

    async def services(self) -> ProbeResult:
        """무엇을 조사할 수 있나. **인자가 없는 것이 정상이다** — 발견용이다."""
        source = f"code.services {self._gbm}"
        rows = []
        for name, service in sorted(self._topology.services.items()):
            pin = self._deployment.pin_for(name, fct=self._fct)
            rows.append({"service": name, "repo": service.repo, "role": service.role,
                         "commit": pin.commit[:12],
                         **({"selects": service.selects} if service.selects else {})})
        return ProbeResult.succeeded(rows, source=source, clock=self._clock)

    async def config(self, service: str, *, key: str | None = None) -> ProbeResult:
        """그 서비스가 배포 시점에 **실제로 보는 설정.** 층을 전부 합친 결과다.

        `key`면 그 자리만 통째로. 없이 불렀는데 상한을 넘으면 키 지도만 — 사내 실측에서 통째 덤프는 첫 키에서
        잘렸고 리드는 그 뒤에 무엇이 있는지 몰라 좁혀 물을 수도 없었다.
        """
        source = f"code.config {service}" + (f" key={key}" if key else "")
        got = await self._layers(service, source)
        if isinstance(got, ProbeResult):
            return got
        _, layers, broken, source = got
        read = " → ".join(path for path, _, _ in layers)
        merged = merge_target([(path, value) for path, _, value in layers])
        # 깨진 층이 있으면 **합친 결과가 틀렸다.** 그걸 완전하다고 적으면
        # 리드가 "이 설정은 이렇다"를 단정한다.
        problems = [" · ".join(broken) + " — 합친 값이 실제와 다를 수 있다"] if broken else []
        if key:
            ok, picked = select(merged, key)
            if not ok:
                return ProbeResult.failed(picked, source=f"{source} [{read}]", clock=self._clock)
            merged = picked
        elif len(json.dumps(merged, ensure_ascii=False)) > _CONFIG_INLINE_CHARS:
            # 지도의 경로를 컬렉션 이름으로 쓴 사내 측정 #5 — 지도가 스스로 말하고(`_주의`가 첫 키), 실행기도 막는다(`runner_probe`).
            merged = {"_주의": "아래 경로는 설정 안의 위치이지 컬렉션·키 이름이 아니다 — 그 자리의 값은 code.config key=<경로>로 읽는다",
                      "_키_지도": key_map(merged)}
            problems.append(f"합친 설정이 {_CONFIG_INLINE_CHARS}자를 넘어 키 지도만 실었다 — "
                            f"key=<지도의 경로>로 그 부분을 통째로 읽어라")
        return ProbeResult.succeeded(
            merged, source=f"{source} [{read}]", clock=self._clock,
            truncated_reason=" · ".join(problems) if problems else None)

    async def _layers(self, service: str, source: str):
        """그 서비스의 config 층들 — `(커밋, [(경로, 원문, 값)], 깨진 층, source)`. 못 읽으면
        실패 `ProbeResult`. `config()`와 `flow_names()`가 같이 쓴다 — 근거 줄을 찾으려면
        합친 값만이 아니라 **어느 층의 몇 번째 줄**인지가 필요하다."""
        resolved = self._resolve(service, source)
        if isinstance(resolved, ProbeResult):
            return resolved
        known, commit, how = resolved
        source = self._pinned(source, commit, how)

        wanted = self._topology.resolved_config_paths(self._gbm, self._fct)
        if not wanted:
            return ProbeResult.failed(
                f"config_paths가 선언돼 있지 않다 — knowledge/topology/{self._gbm}.json에 "
                f"이름이 사는 파일들을 적어야 한다",
                source=source, clock=self._clock)

        layers, broken = [], []
        for path in wanted:
            # **통째로 읽는다.** 잘린 JSON은 못 쓴다 — 400줄 상한에 걸린 층을
            # 그냥 파싱하면 "JSON이 아니다"가 나오고, 그 말은 **대상 파일이
            # 깨졌다**는 뜻으로 읽힌다. 사내에서 실제로 그랬다.
            got = await self._reader.show(known.repo, commit, path, whole=True)
            if got.status == "error":
                continue                    # 없는 층은 **정상이다**(층은 선택)
            if not got.envelope.complete:
                # **파싱하기 전에** 본다. 우리가 자른 것을 대상 탓으로 돌리지 않는다.
                broken.append(f"{path}: 우리가 잘라서 읽었다 — "
                              f"{got.envelope.truncated_reason}. 이 층은 안 썼다")
                continue
            value, why = parse_layer(path, got.data)
            if value is None:
                broken.append(why)
                continue
            layers.append((path, got.data, value))

        if not layers:
            # **왜 없는지까지 말한다.** 읽다가 실패한 층이 있으면 그게 원인이고,
            # 그걸 빼면 "경로가 틀렸다"로 읽혀서 사람이 맞는 경로를 고치러 간다.
            why = (" · ".join(broken) if broken
                   else f"찾은 자리: {', '.join(wanted)}. `code status`를 보라")
            return ProbeResult.failed(
                f"쓸 수 있는 config 층이 하나도 없다 — {why}",
                source=source, clock=self._clock)
        return commit, layers, broken, source

    async def grep(self, patterns: list[str], service: str = "") -> ProbeResult:
        """**이 이름을 누가 쓰나.** 11a가 존재하는 이유다.

        `service`를 안 주면 모든 레포를 본다 — 이름이 어느 서비스 것인지 모르는
        상태가 정상이기 때문이다(decisions ⑮).
        """
        source = f"code.grep {patterns}" + (f" in {service}" if service else "")
        if not patterns:
            return ProbeResult.failed("찾을 문자열이 없다", source=source, clock=self._clock)

        if service:
            resolved = self._resolve(service, source)
            if isinstance(resolved, ProbeResult):
                return resolved
            known, commit, how = resolved
            targets = [(service, known.repo, commit, how, known.path)]
        else:
            # 레포가 같으면 커밋도 같다(코드는 GBM 단위로 같다) — 한 번만 본다.
            seen, targets = set(), []
            for name, known in sorted(self._topology.services.items()):
                pin = self._deployment.pin_for(name, fct=self._fct)
                key = (known.repo, pin.commit)
                if key in seen:
                    continue
                seen.add(key)
                targets.append((name, known.repo, pin.commit, pin.how, ""))

        chunks, docs, problems, complete = [], [], [], True
        for _, repo, commit, how, path in targets:
            # 리더의 기본 상한(400줄)보다 크게 받는다 — 자르는 것은 코드 줄을 앞세운 뒤 우리가 한다(`_LINES`).
            got = await self._reader.grep(repo, commit, patterns, path=path,
                                          max_lines=_GREP_RAW_LINES, max_chars=_GREP_RAW_CHARS)
            if got.status == "error":
                problems.append(f"{repo}: {got.error}")
                continue
            if not got.envelope.complete:
                complete = False
                problems.append(f"{repo}: {got.envelope.truncated_reason}")
            code_lines, doc_lines = split_doc_lines(got.data, commit)
            head = f"# {repo} @ {commit[:12]}"
            if code_lines:
                chunks.append("\n".join([head, *code_lines]))
            if doc_lines:
                docs.append("\n".join([head, *doc_lines]))
        if docs:
            chunks.append("# ── 문서 줄(" + "·".join(_DOC_SUFFIXES) + ") — 코드 줄 뒤에 둔다 ──")
            chunks += docs

        lines = "\n".join(chunks).splitlines()
        if len(lines) > _LINES:
            lines, complete = lines[:_LINES], False
            problems.append(f"{_LINES}줄에서 끊음(코드 줄 먼저)")
        text = "\n".join(lines)
        if len(text) > _MAX_CHARS:
            text, complete = text[:_MAX_CHARS], False
            problems.append(f"{_MAX_CHARS}자에서 끊음")
        if not chunks and problems and len(problems) == len(targets):
            # 전부 실패했으면 "못 찾았다"가 아니라 **못 봤다**이다(⑪).
            return ProbeResult.failed(" · ".join(problems), source=source,
                                      clock=self._clock)
        return ProbeResult.succeeded(
            text, source=source, clock=self._clock,
            truncated_reason=(" · ".join(problems) + " — 더 있을 수 있다"
                              if (problems or not complete) else None))

    async def read(self, service: str, path: str, *, offset: int | None = None,
                   limit: int | None = None, line: int | None = None) -> ProbeResult:
        """파일 하나. **`path`는 `grep`이 돌려준 경로다** — 지어내는 자리가 아니다.

        `offset`(1부터)·`limit`은 줄 범위다. 범위를 주면 파일을 통째로 받아 그 줄들을 **자르지 않고** 준다 — 사내
        실측에서 라우터 파일이 400줄에서 잘려 핸들러를 못 봤다. `offset`만 주면 거기서 기본 상한만큼이다.
        `line`은 그 줄을 **품은 함수 전체**(R3-3 ④-a) — 인덱스의 심볼 줄 범위로 가장 안쪽 함수·메서드·클래스를 통째로. 줄 둘레 N줄은 함수가
        길면 잘리고 짧으면 옆 함수를 섞는다. 인덱스에 그 줄을 품은 심볼이 없으면 줄 둘레다.
        """
        if line is not None:
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                return ProbeResult.failed("line은 1 이상의 정수다", source=f"code.read {service}:{path}", clock=self._clock)
            span = f" L{line} 함수"
        else:
            ranged = offset is not None or limit is not None
            span = f" L{offset or 1}" + (f"+{limit}" if limit is not None else "") if ranged else ""
        source = f"code.read {service}:{path}{span}"
        resolved = self._resolve(service, source)
        if isinstance(resolved, ProbeResult):
            return resolved
        known, commit, how = resolved
        pinned = self._pinned(source, commit, how)
        whole_fn = ""
        if line is not None:
            sym = self._enclosing(known.repo, path, line)
            if sym is not None:
                offset, limit, whole_fn = sym.line, sym.end_line - sym.line + 1, f" — {sym.qualname} 전체"
            else:
                offset, limit, whole_fn = max(1, line - _WINDOW // 2), _WINDOW, " — 그 줄을 품은 함수가 인덱스에 없어 줄 둘레"
        ranged = offset is not None or limit is not None
        problem = _range_problem(offset, limit)
        if problem:
            return ProbeResult.failed(problem, source=pinned, clock=self._clock)
        got = await self._reader.show(known.repo, commit, path, whole=ranged)
        if got.status == "error":
            return ProbeResult.failed(got.error + self._elsewhere(path, known.repo), source=pinned, clock=self._clock)
        if not ranged:
            return ProbeResult.succeeded(
                got.data, source=f"{pinned} ({got.source})", clock=self._clock,
                truncated_reason=got.envelope.truncated_reason)
        lines = got.data.splitlines()
        start = int(offset or 1)
        if start > len(lines):
            return ProbeResult.failed(f"{path}는 전체 {len(lines)}줄이라 L{start}부터는 없다",
                                      source=pinned, clock=self._clock)
        count = int(limit) if limit is not None else _LINES
        picked = lines[start - 1:start - 1 + count]
        end = start + len(picked) - 1
        reasons = [got.envelope.truncated_reason] if not got.envelope.complete else []
        if limit is None and end < len(lines):
            reasons.append(f"L{end}까지 — offset만 주면 기본 {_LINES}줄이다. limit을 주면 더 본다")
        return ProbeResult.succeeded(
            "\n".join(picked), source=f"{pinned} ({got.source}) L{start}-L{end} / 전체 {len(lines)}줄{whole_fn}",
            clock=self._clock, truncated_reason=" · ".join(reasons) if reasons else None)

    def _enclosing(self, repo: str, path: str, line: int):
        """그 줄을 품은 **가장 안쪽** 심볼(함수·메서드·클래스) — 인덱스가 없거나 품은 것이 없으면 None."""
        if self._index is None:
            return None
        inside = [s for s in self._index.symbols if s.repo == repo and s.file == path and s.kind in ("function", "method", "class")
                  and s.line <= line <= s.end_line]
        return min(inside, key=lambda s: s.end_line - s.line) if inside else None


# `line=`에 그 줄을 품은 심볼이 없을 때의 줄 둘레.
_WINDOW = 40


def _range_problem(offset, limit) -> str | None:
    """`offset`·`limit`은 리드가 적은 값이다 — 소켓 전에 거른다(`actions.action_problem`과 같은 이유)."""
    for name, value, floor in (("offset", offset, 1), ("limit", limit, 1)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < floor:
            return f"{name}은 {floor} 이상의 정수여야 한다 — {value!r}"
    return None


def split_doc_lines(text: str, commit: str) -> tuple[list[str], list[str]]:
    """grep 출력(`<커밋>:경로:줄:내용`, `parse_grep`과 같은 모양)을 코드 줄과 문서 줄로 가른다. 줄은 그대로 둔다 —
    경로는 커밋 접두사 뒤 첫 `:` 앞이고, git 경로는 레포 상대라 드라이브 문자가 없다."""
    code, docs, prefix = [], [], f"{commit}:"
    for line in text.splitlines():
        if not line.strip():
            continue
        bare = line[len(prefix):] if line.startswith(prefix) else line
        path = bare.split(":", 1)[0]
        (docs if path.lower().endswith(_DOC_SUFFIXES) else code).append(line)
    return code, docs


def key_map(value: Any, *, depth: int = _MAP_DEPTH) -> dict[str, str]:
    """합친 설정의 **지도** — `경로 → 거기 무엇이 있나`(두 단계까지). 값 전체를 잘라 보여 주는 대신 리드가 어디를 `key=`로
    좁혀 물을지 보이게 한다."""
    out: dict[str, str] = {}

    def walk(node, prefix: str, left: int):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            # 자식이 많은 객체(`rules.r0…r299`)는 안 내려간다 — 지도가 덤프만큼 커지면 지도가 아니다.
            if isinstance(v, dict) and left > 1 and 0 < len(v) <= _MAP_FANOUT:
                walk(v, path, left - 1)
            else:
                out[path] = _brief(v)

    walk(value, "", depth) if isinstance(value, dict) else out.update({"(맨 위)": _brief(value)})
    return out


def _brief(value: Any) -> str:
    if isinstance(value, dict):
        keys = ", ".join(str(k) for k in list(value)[:8]) + (" …" if len(value) > 8 else "")
        return f"객체 · 키 {len(value)}개: {keys}"
    if isinstance(value, list):
        return f"목록 {len(value)}개"
    text = repr(value)
    return text if len(text) <= _MAP_VALUE_CHARS else text[:_MAP_VALUE_CHARS] + "…"


class _GitSource:
    """인덱서가 읽는 창 — 배포 커밋의 파일들과 리터럴 grep(`-F`). 던지지 않는다: 못 읽으면 None,
    grep이 실패하면 빈 목록.

    **커밋 전체를 한 번에 받는다**(`snapshot`, 11e). 파일마다 `show`를 띄우던 것이 사내 `code graph`
    20분의 정체였다. 스냅샷을 못 받으면(옛 git·시간 초과) 예전처럼 파일별 `show`로 간다 — 느려질 뿐
    결과는 같다."""

    def __init__(self, reader: CodeReaderPort, repo: str, commit: str, *, shared=None):
        self._reader, self._repo, self._commit = reader, repo, commit
        self._snapshot: dict[str, str] | None = None
        self._tried = False
        # `DeployedCode.snapshot_for` — 어댑터가 이미 받은 스냅샷을 같이 쓴다(레포당 archive 한 번).
        self._shared = shared

    async def _snap(self) -> dict[str, str] | None:
        if not self._tried:
            self._tried = True
            if self._shared is not None:
                self._snapshot = await self._shared(self._repo)
                return self._snapshot
            got = await self._reader.snapshot(self._repo, self._commit)
            if got.status == "ok" and isinstance(got.data, dict):
                self._snapshot = got.data
        return self._snapshot

    async def read(self, path: str) -> str | None:
        snap = await self._snap()
        if snap is not None:
            return snap.get(path)
        got = await self._reader.show(self._repo, self._commit, path, whole=True)
        return got.data if got.status == "ok" and isinstance(got.data, str) else None

    async def files(self) -> list[str]:
        """배포 커밋의 파일 전부(인덱서용). **채워진 서브모듈 안도** 부모가 박은 버전으로 든다(리더의 `ls`·
        `snapshot`이 같은 규칙) — 공유 라이브러리를 따로 등재하지 않고 레포마다 자기 핀으로 인덱싱한다(11d 6b-2).
        안 채워진 것은 빠지고 그쪽 import는 `external_shared`로 남는다."""
        snap = await self._snap()
        if snap is not None:
            return sorted(snap)
        got = await self._reader.ls(self._repo, self._commit, max_names=200_000)
        if got.status != "ok" or not isinstance(got.data, list):
            return []
        return [n for n in got.data if isinstance(n, str)]

    async def grep(self, patterns: list[str]) -> list[Hit]:
        got = await self._reader.grep(self._repo, self._commit, list(patterns), fixed=True,
                                      max_lines=FLOW_MAX_LINES, max_chars=FLOW_MAX_CHARS)
        if got.status == "error" or not isinstance(got.data, str):
            return []
        return parse_grep(self._repo, self._commit, got.data)
