"""끝점 사슬의 **모양**(`Trace`·`Step`·`Read`·`Gap`)과 코드 본문에서 자원 참조를 찾는 판정(`_collect_reads`).

11b의 끝점 추적기(끝점에서 앞으로 걷는 BFS)가 여기 있었다. 11d 6d-3에서 사슬을 심볼 인덱스가 만들게 됐고
(`index_trace`), 사내 대조(같음 150 · 다름 6, 인덱스가 못한 지점 없음)가 두 번 서서 6d-4에서 추적기를 지웠다 —
엔진은 하나다(decisions ⑱). 남은 것은 인덱스가 그대로 쓰는 것뿐이다: 사슬의 자료형, 깊이·노드 상한, 그리고
"이 줄이 그 자원을 읽는가"의 판정 — 리터럴이 그대로 있으면 확실, config 키 토큰·별칭 경유면 추정.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from src.knowledge.flow import _DISTINCTIVE, Hit, Name, _quoted_whole, direction

MAX_DEPTH = 6
MAX_NODES = 200
# 코드 쪽 키 템플릿 — `f"gauge_face_{f}"`의 머리 `gauge_face_`. 따옴표 바로 뒤부터 `{` 앞까지.
_KEY_TEMPLATE = re.compile(r"""["']([A-Za-z0-9_.:\-]{2,})\{""")


@dataclass(frozen=True)
class Step:
    file: str
    line: int
    qualname: str
    parent: int | None = None   # 사슬 안 부모 걸음의 색인 — 사슬은 BFS 목록이지만 리드에게는 트리로 보여 준다


@dataclass(frozen=True)
class Read:
    kind: str
    name: str
    grade: str          # "확실" | "추정"
    file: str
    line: int
    via: str = "literal"    # 이름이 코드에 어떻게 있었나 — "literal"(문자열 그대로) | "key"(config 키 토큰) | "alias"(상수·Enum)
    step: int = -1          # 이 읽기가 난 걸음(사슬 색인) — 읽기로 이어진 가지만 남길 때 쓴다


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


def _line_of(segment: str, needle: str, base: int) -> int:
    for i, line in enumerate(segment.splitlines()):
        if needle in line:
            return base + i
    return base


def _quoted_at(segment: str, needle: str) -> int | None:
    """`needle`이 **문자열 리터럴 안에서** 처음 나오는 줄 오프셋. 템플릿의 리터럴(`line:{id}` → `line:`)은
    타입 주석 `line: str`과도 겹치므로, 같은 줄에서 따옴표가 먼저 열려 있어야 읽기로 친다."""
    m = re.search(r"""["'][^"'\n]*""" + re.escape(needle), segment)
    return None if m is None else segment.count("\n", 0, m.start())


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


def _collect_reads(segment: str, base: int, path: str, cap: str, names: list[Name],
                   aliases: dict[str, Name], add_read) -> None:
    """`segment`(함수 본문 등, 첫 줄이 `base`)에서 이름 목록의 자원 참조를 찾아 `add_read(kind, name, grade, file,
    line, via)`로 낸다. `cap`이 추정이면 전부 추정(이 코드까지 오는 길이 추정이었다)."""
    for n in names:
        at = _quoted_at(segment, n.literal) if n.literal else None
        if (at is not None and not _DISTINCTIVE.search(n.literal)
                and direction(segment.splitlines()[at]) is None):
            at = None       # 한 단어 리터럴은 같은 줄에 읽기/쓰기 동사가 있어야 읽기다 — 배지 상태값 "alarm"
        if at is not None:
            add_read(n.kind, n.value, "추정" if cap == "추정" else "확실", path, base + at, "literal")
        elif (at := _key_at(segment, n)) is not None:
            add_read(n.kind, n.value, "추정", path, base + at, "key")
    for ident, n in aliases.items():
        if re.search(rf"\b{re.escape(ident)}\b", segment):
            add_read(n.kind, n.value, "추정", path, _line_of(segment, ident, base), "alias")
    # 코드가 키 토큰을 템플릿으로 조립한다 — `cfg.get("redis_key", f"gauge_face_{f}")`. 토큰이 통째로 없어
    # 위 판정은 하나도 못 잡는다(사내 /summary 끝점의 redis 읽기가 이 모양뿐이었다). 조상 키가 같은 줄에 있고
    # 머리가 여러 조각짜리면, 그 머리로 시작하는 키 전부가 읽기(추정)다 — config 값 템플릿(`alarm:stats:{line}`
    # → `alarm:stats:`)을 리터럴로 잡는 것과 거울 관계다.
    for i, line in enumerate(segment.splitlines()):
        for head in _KEY_TEMPLATE.findall(line):
            if not _DISTINCTIVE.search(head):
                continue
            for n in names:
                if (n.key_token != head and n.key_token.startswith(head) and n.required_tokens
                        and all(t in line for t in n.required_tokens)):
                    add_read(n.kind, n.value, "추정", path, base + i, "key")
