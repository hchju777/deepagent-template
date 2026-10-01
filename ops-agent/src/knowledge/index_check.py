"""심볼 인덱스 검증 하네스(11d 6a) — **정확도를 숫자로** 낸다.

이게 없으면 인덱스의 숫자는 믿을 수 없는 숫자다. 에어갭에서 한 줄로 돌리고 결과 몇 줄만 받아 적을 수 있어야
하므로 보고는 여덟 줄 이내다.

- A 구조 불변식 — 하나라도 깨지면 실패. `.py 수 == module 수`(누락 0)가 "794/794"를 말할 수 있는 근거다.
- B 정밀도 표본 — exact `calls` 엣지 N건을 뽑아 호출자 **본문 줄 범위 안에** 대상 이름이 실제로 있는지 본다.
  데코레이터(`@cached(30)`)는 `def` 줄 위라 본문 밖이다 — 심볼의 `decorators`도 본다. 안 보면 멀쩡한 엣지가
  거짓 실패다.
- C 재현율 표본 — 함수 본문을 다시 파싱해 `foo()` 꼴 호출 중 **같은 모듈의 최상위 정의**로 해석되는 것(틀릴 여지가
  없는 것)이 전부 엣지로 있는지 본다. 정밀도만 재면 아무것도 안 잇는 인덱서가 100%를 받는다.
"""
from __future__ import annotations

import ast
import random
import re
from typing import Awaitable, Callable

from src.knowledge.index import CERTAINTIES, EDGE_TYPES, Index, _body_walk, is_indexed

Reader = Callable[[str, str], Awaitable[str | None]]      # (repo, path) → 배포 커밋의 파일 본문


def invariants(index: Index, *, files: dict[str, set[str]], line_counts: dict[tuple[str, str], int]) -> list[str]:
    """깨진 것들. 비어 있으면 통과."""
    problems: list[str] = []
    n = len(index.symbols)
    for i, s in enumerate(index.symbols):
        if s.id != i:
            problems.append(f"심볼 id가 배열 위치와 다르다: [{i}] id={s.id} {s.qualname}")
        if s.kind == "method" and not (s.class_id is not None and 0 <= s.class_id < n
                                       and index.symbols[s.class_id].kind == "class"):
            problems.append(f"method의 class_id가 클래스를 안 가리킨다: {s.qualname} class_id={s.class_id}")
        total = line_counts.get((s.repo, s.file))
        if total is None:
            problems.append(f"심볼의 파일이 없다: {s.repo}:{s.file} ({s.qualname})")
        elif s.kind != "module" and not (1 <= s.line <= s.end_line <= max(total, 1)):
            problems.append(f"줄 범위가 파일 밖이다: {s.qualname} L{s.line}-{s.end_line} (파일 {total}줄)")
    seen: set[tuple[int, int, str]] = set()
    for e in index.edges:
        if not (0 <= e.src < n and 0 <= e.dst < n):
            problems.append(f"엣지가 범위 밖이다: {e.src}→{e.dst} {e.type}")
            continue
        if e.src == e.dst:
            problems.append(f"엣지가 self-loop다: {index.symbols[e.src].qualname} {e.type}")
        if e.type not in EDGE_TYPES or e.certainty not in CERTAINTIES:
            problems.append(f"엣지의 type/certainty를 모른다: {e.type}/{e.certainty}")
        if (e.src, e.dst, e.type) in seen:
            problems.append(f"엣지가 중복이다: {index.symbols[e.src].qualname}→{index.symbols[e.dst].qualname} {e.type}")
        seen.add((e.src, e.dst, e.type))
    for repo, paths in sorted(files.items()):
        want = {p for p in paths if is_indexed(p)}
        have = {s.file for s in index.symbols if s.kind == "module" and s.repo == repo}
        missing, extra = sorted(want - have), sorted(have - want)
        if missing or extra:
            problems.append(f"커버리지 {repo}: .py {len(want)} vs module {len(have)}"
                            + (f" — 없는 모듈 {', '.join(missing[:3])}" if missing else "")
                            + (f" — 파일 없는 모듈 {', '.join(extra[:3])}" if extra else ""))
    return problems


async def _cached(cache: dict, read: Reader, repo: str, path: str) -> str | None:
    if (repo, path) not in cache:
        cache[(repo, path)] = await read(repo, path)
    return cache[(repo, path)]


async def precision_sample(index: Index, read: Reader, *, n: int = 100, seed: int = 1
                           ) -> tuple[int, int, list[str]]:
    """`(맞은 수, 표본 수, 틀린 것 셋)`. 표본은 exact `calls`만 — candidate는 애초에 추정이라 여기서 안 잰다."""
    rng = random.Random(seed)
    pool = [e for e in index.edges if e.type == "calls" and e.certainty == "exact"
            and index.symbols[e.dst].kind in ("function", "method")]
    sample = rng.sample(pool, min(n, len(pool)))
    cache: dict = {}
    subs: dict[int, list[int]] = {}
    for e in index.edges:
        if e.type == "inherits":
            subs.setdefault(e.dst, []).append(e.src)
    ok, failures = 0, []
    for e in sample:
        src, dst = index.symbols[e.src], index.symbols[e.dst]
        text = await _cached(cache, read, src.repo, src.file) or ""
        lines = text.splitlines()
        body = "\n".join(lines[src.line - 1:src.end_line]) + "\n" + "\n".join(src.decorators)
        # 생성자 호출은 클래스 이름으로 적힌다 — `__init__`은 본문에 없다. `Child()`가 `Base.__init__`을 실행하면
        # 본문엔 Child뿐이다 — 하위 클래스 이름도 맞은 것(사내 첫 실행의 틀린 넷이 전부 이 모양).
        needles = {dst.name}
        if dst.name == "__init__" and dst.class_id is not None:
            needles = {index.symbols[c].name for c in _with_descendants(dst.class_id, subs)}
        if any(re.search(rf"\b{re.escape(n)}\b", body) for n in needles):
            ok += 1
        else:
            failures.append(f"{src.qualname} → {dst.qualname} (L{e.line})")
    return ok, len(sample), failures[:3]


def _with_descendants(csid: int, subs: dict[int, list[int]]) -> set[int]:
    out, stack = {csid}, [csid]
    while stack:
        for s in subs.get(stack.pop(), []):
            if s not in out:
                out.add(s)
                stack.append(s)
    return out


async def recall_sample(index: Index, read: Reader) -> tuple[int, int, list[str]]:
    """`(있는 수, 기대 수, 빠진 것 셋)`. 같은 모듈 최상위 함수를 이름 그대로 부르는 호출은 틀릴 여지가 없다 —
    그게 전부 엣지로 있어야 한다. 자기 재귀는 self-loop라 엣지가 없는 것이 맞으므로 뺀다."""
    found = total = 0
    misses: list[str] = []
    cache: dict = {}
    by_file: dict[tuple[str, str], list] = {}
    for s in index.symbols:
        if s.kind in ("function", "method"):
            by_file.setdefault((s.repo, s.file), []).append(s)
    edge_set = {(e.src, e.dst) for e in index.edges if e.type == "calls"}
    for m in index.symbols:
        if m.kind != "module" or m.parse_error:
            continue
        text = await _cached(cache, read, m.repo, m.file)
        if not text:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        top = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        defs_at = {node.lineno: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for fsym in by_file.get((m.repo, m.file), []):
            node = defs_at.get(fsym.line)
            if node is None:
                continue
            params = {a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs}
            for call in _body_walk(node.body):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
                    continue
                name = call.func.id
                if name not in top or name in params or name == fsym.name:
                    continue
                dst = index.lookup(m.repo, f"{m.qualname}.{name}")
                if dst is None:
                    continue
                total += 1
                if (fsym.id, dst) in edge_set:
                    found += 1
                else:
                    misses.append(f"{fsym.qualname} → {name} (L{call.lineno})")
    return found, total, misses[:3]


def report(index: Index, problems: list[str], precision: tuple[int, int, list[str]],
           recall: tuple[int, int, list[str]]) -> list[str]:
    """여덟 줄 이내 — 사람이 옮겨 적는다."""
    s = index.summary()
    k = s["kinds"]
    e = s["edges"]
    lines = [
        f"1 심볼 {s['symbols']} (module {k.get('module', 0)} · class {k.get('class', 0)} · function "
        f"{k.get('function', 0)} · method {k.get('method', 0)}) · 파싱 실패 {s['parse_errors']}",
        "2 엣지 " + " · ".join(f"{t} {e[t]['exact']}/{e[t]['candidate']}" for t in EDGE_TYPES if t in e)
        + " (확실/추정)",
        f"3 불변식 {'OK — 커버리지(.py == module, 테스트 제외)·id·엣지·줄 범위 전부' if not problems else f'위반 {len(problems)}: ' + ' | '.join(problems[:2])}",
        f"4 정밀도 {precision[0]}/{precision[1]}" + (f" — 틀림: {' | '.join(precision[2])}" if precision[2] else ""),
        f"5 재현율 {recall[0]}/{recall[1]}" + (f" — 빠짐: {' | '.join(recall[2])}" if recall[2] else ""),
        "6 미해석 호출 " + (" · ".join(f"{k_} {v}" for k_, v in sorted(s["unresolved"].items())) or "없음"),
        f"7 자원 참조 {s['resources']} · gap {s['gaps']}",
    ]
    return lines
