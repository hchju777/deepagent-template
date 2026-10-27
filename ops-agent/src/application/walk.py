"""R3-3 ② — 코드가 상류를 걷는다.

측정 #9: 쓴 쪽 링크·pinned·예시 한 칸이 작동해도 c-1은 원천 키까지 세 홉이 걸렸고, c-2는 첫 나쁜 값을 읽고도 못 봤다 — 리드가 한 홉씩 걷는
구조의 한계다. 입구 노드의 고리(키 값의 잎 = 클래스 → `code.resources` → 자원 키 읽기)를 **코드가 키마다 반복**하고, 리드는 표를 받는다.

규칙(사람 결정 10-14): 나쁨은 없음·빈 배열·0행뿐 — 손 규칙 없음(단일값 필드는 읽는 쪽 코드 줄이 정한다 — R3-4 ③: 어댑터 안의 조회, 범위는
그 클래스의 파일 + `get("X")`↔`register(component="X")`로 이은 컴포넌트 파일, 짝은 한 비교식의 양쪽; runner를 안 거쳐 읽기 상한·증거 밖). 값이 그 클래스를 대는 키는
그 클래스의 **출력**이라 입력에서 뺀다(측정 #9 P0 3: `code.resources`가 발행 명령의 대상 자원 줄을 읽기로 세어 자기 출력이 입력에 섞였다).
나쁜 입력의 쓴 쪽을 알면 한 홉 더, 모르면 거기서 멈춘다(다음 홉은 그 키의 `code.uses`). 입력이 다 정상이면 멈춘다(그 자리의 다음 홉은 ④).
같은 클래스 이름이 두 레포에 있으면 **레포 단위**로 가른다(R3-4 ①, 측정 #10 P0 2) — 한 레포를 여러 서비스가 `path` 없이 공유하면 표에
`<레포>(서비스 N개 공유)`로 적고 그 레포를 `code.resources repo=`로 넘긴다. 한 가지의 `code.resources` 실패는 그 행에만 적고 다른 가지는 계속 걷는다.
멈춘 자리는 **가장 깊은 나쁜 홉**이고(R3-4 ②, 측정 #10 P0 3: "마지막 실패"는 다른 가지의 일이었다) 다음 칸은 늘 낸다 — 리터럴 줄의 함수 → 그 자리의
질의(쓴 쪽 모름 `code.uses`, 못 걸음 `code.resources`, 걸었는데 입력이 다 정상이면 읽는 메서드 전체, 입력이 0이면 "입력을 모른다"와 클래스 본문).
pinned 본문은 그 홉까지의 사슬만(P0 4). 읽기의 id는 리드와 같은 한 단계(`t-2`…, `walked` 플래그 — P1 5).
상한 깊이 4·읽기 15·30초(주입 시계) — 상한에서 멈추면 다음 홉은 그 자리의 `code.resources`(리드가 이어 걷는다; 이미 읽은 것이면 ③의 입력 칸
사다리가 이어 받는다). 걸은 읽기는 State에 증거로 남고(판정이 인용한다) 브리핑이 접는다. 던지지 않는다 — 호출부(노드)가 한 번 더 감싼다.
메시지 입력(R3-5d ④, 사람 결정 10-19): 인덱스는 토픽 입력을 자원으로 못 센다 — 걸은 클래스를 댄 키 값의 포인터(`metadata.source`: 토픽·파티션·오프셋)가
그 키를 만든 메시지 **한 건**을 가리키니 그 한 건을 입력으로 읽는다(`kafka.message_at`, 증거는 세기만). 값 판정·단일값 판정은 키 값과 같다. 보존 범위
밖이면 "키를 만든 메시지가 토픽에 없다"(나쁨), 포인터가 없으면 "포인터 없음"만(토픽을 짐작하지 않는다). 발행 쪽은 클래스가 아니라 config 발행 선언
(같은 이름 클래스가 두 레포)이고 그쪽으로 걷지는 않는다 — 선언이 없으면 우리 밖.
"""
import json
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, NamedTuple

from src.application.facts import key_head
from src.application.intake_map import _finish, _task, _unwrap, empties, empty_text, pointer_of, writer_of
from src.application.runner_probe import _same_valued, profile
from src.application.state import CaseState
from src.domain.case import JOIN_NOTE_MARKS, EvidenceRef, FilterHit, FilterScan, Hop, PlanTask, Rung, Walk
from src.domain.investigation import TaskRunnerPort

MAX_DEPTH = 4
MAX_READS = 15
MAX_SECONDS = 30.0
# 못 채우는 템플릿(대상 키에 없는 자리) — scan 뒤 읽는 실재 키 수.
SCAN_READS = 5
# 단일값 필드의 나쁨은 **코드 줄**이 정한다(R3-3 ③ → R3-5 ③, 사람 결정 10-14·10-15 — 자리표시자 목록 같은 손 규칙은 두지 않는다): 입력을 읽는 쪽(걷고
# 있는 클래스)의 파일과 이은 컴포넌트 클래스 파일에서 필드 이름과 그 값이 한 비교식의 양쪽에 리터럴로 있고 **연산자가 `!=`면**(실측 `["<필드>"] != "-"`)
# 그 값의 행은 그 줄을 지나지 못한다. `==`는 그 값을 남기는 쪽이라 거름이 아니다(R3-5l ②, 사람 결정 10-25 — 측정 #22: `(df["<f>"] == "<v>")`를 거름으로
# 세어 기준 키가 가짜 나쁨). 필드 수 상한은 없다 — 어댑터가 비교식을 먼저 뽑아 단일값 전부와 맞춘다(측정 #11: 상한 6에 가려 7번째 필드를 못 봤다).


def dropping(hits: list[FilterHit]) -> list[FilterHit]:
    """거르는 줄 — `!=` 짝만(R3-5l ②). 한계(적어 둔다): `if r["f"] == "v": continue`처럼 `==`로 **버리는** 줄은 못 본다 — 결정이 연산자·리터럴까지라
    거기서 멈춘다."""
    return [h for h in hits if h.op == "!="]


def _hashable(value: Any):
    return value if isinstance(value, (str, int, float, bool, type(None))) else json.dumps(value, sort_keys=True, ensure_ascii=False)


def _shown_keys(keys: set) -> str:
    shown = sorted(keys, key=repr)[:3]
    return "{" + ", ".join(repr(k[0] if len(k) == 1 else k) for k in shown) + (", …" if len(keys) > 3 else "") + "}"


def _keyed(inputs, fields) -> list[tuple[str, str, set]]:
    """그 필드를 전부 가진 행이 있는 입력들 — (라벨, 증거, 그 필드 값(묶음)의 집합)."""
    found = []
    for label, ev, rows, *_ in inputs:
        with_fields = [r for r in rows if isinstance(r, dict) and all(f in r for f in fields)]
        if with_fields:
            found.append((label, ev, {tuple(_hashable(r.get(f)) for f in fields) for r in with_fields}))
    return found


def join_unmatched(merges, inputs) -> list[tuple[Any, str]]:
    """겹침을 못 센 병합식과 그 까닭(R3-5h ①·R3-5i ①) — 키 필드를 가진 입력 행이 한쪽에 없거나(`… 입력 행이 <왼쪽·오른쪽·양쪽>에 없다`), 양쪽 키 필드를
    가진 행이 한 입력에만 있어 짝이 안 나오거나(`… 행이 한 입력 <라벨>에만 있다(<쪽> 값 전부 None)`). 측정 #19 P0-a: 작업자 배지는 오른쪽 키 필드를 왼쪽
    입력의 전 행이 None으로 가진다 — 진짜 오른쪽은 열 이름이 변환을 거친 메시지라 AST 사실로는 못 잇는다(사람 결정 10-21: 이름 규칙 없이 여기까지)."""
    out = []
    for m in merges:
        fields = f"키 필드({'+'.join(m.left)} ↔ {'+'.join(m.right)})"
        lefts, rights = _keyed(inputs, m.left), _keyed(inputs, m.right)
        if not lefts or not rights:
            side = "양쪽" if not lefts and not rights else "왼쪽" if not lefts else "오른쪽"
            out.append((m, f"{fields}를 가진 입력 행이 {side}에 없다"))
            continue
        if any(a_ev != b_ev for _, a_ev, _ in lefts for _, b_ev, _ in rights):
            continue
        # 양쪽 키 필드를 가진 행이 한 입력에만 있다 — 짝이 안 나온다. 그 입력에서 한쪽 키 값이 전부 None이면 그 사실도 — **필드별로**(측정 #20: 오른쪽
        # 3필드 중 하나만 전 행 None이라 튜플 전체 기준으론 안 달렸다. 열은 있으나 비어 있다).
        label = lefts[0][0]
        blank = []
        for side, found, side_fields in (("왼쪽", lefts, m.left), ("오른쪽", rights, m.right)):
            empty = [f for i, f in enumerate(side_fields) if all(keys[i] is None for _, _, key_set in found for keys in key_set)]
            if empty:
                blank.append(f"{side} {'+'.join(empty)}")
        tail = f"({'·'.join(blank)} 값 전부 None)" if blank else ""
        out.append((m, f"{fields}를 가진 행이 한 입력 {label}에만 있다{tail}"))
    return out


def fill_fields(merge, left_rows: list, right_rows: list) -> tuple[dict[str, str], dict[str, str]]:
    """채움값 후보 열(R3-5h ②) — 이어진 호출의 문자열을 받는 열은 보존 안 되는 쪽 입력의 열이다: left면 오른쪽, right면 왼쪽, outer면 양쪽. 양쪽 이름이 같은
    키는 하나로 합쳐져 채워지지 않으니 뺀다. → ({열: 채움값}, {열: 왼쪽|오른쪽}). 이어진 호출이 없으면 둘 다 빈 사전."""
    if not merge.chained:
        return {}, {}
    value = merge.chained[1]
    left_cols = {k for r in left_rows if isinstance(r, dict) for k in r}
    right_cols = {k for r in right_rows if isinstance(r, dict) for k in r}
    coalesced = {f for f, g in zip(merge.left, merge.right) if f == g}
    sides: dict[str, str] = {}
    if merge.how in ("right", "outer"):
        sides.update({c: "왼쪽" for c in left_cols - coalesced})
    if merge.how in ("left", "outer"):
        sides.update({c: "오른쪽" for c in right_cols - coalesced})
    return {c: value for c in sorted(sides)}, sides


def join_check(merges, inputs) -> list[tuple[tuple[str, str], tuple[str, str], str, Any]]:
    """조인 검사(R3-5g ④-b, P1-5 — 사람 결정 10-20): 병합식마다 그 키 필드를 가진 두 입력의 값 집합이 겹치는지. 안 겹치면 `((왼쪽 라벨, 증거), (오른쪽 라벨,
    증거), 문구, 병합식)`. 왼쪽 키 필드를 전부 가진 행이 있는 입력이 왼쪽, 오른쪽 키 필드를 가진 **다른** 입력이 오른쪽 — 변수를 따라가지 않는다(필드 이름이
    사실이다). 한쪽이 비었거나 겹치는 값이 하나라도 있으면 아니다. 코드 비교식이 아니라 양쪽 데이터의 키 차이로만 보이는 거름(사내 작업자 배지: 원천의 빈
    작업대 코드가 기준표에 없어 inner 병합이 0건)을 이것으로 본다. `inputs`는 `(라벨, 증거, 행들, 쓴 쪽, 원본, 선언 이름)`."""
    out: list[tuple[tuple[str, str], tuple[str, str], str, Any]] = []
    done: set[frozenset] = set()
    for m in merges:
        for a_label, a_ev, a_keys in _keyed(inputs, m.left):
            for b_label, b_ev, b_keys in _keyed(inputs, m.right):
                pair = frozenset((a_ev, b_ev))
                if a_ev == b_ev or pair in done or not a_keys or not b_keys or (a_keys & b_keys):
                    continue
                done.add(pair)
                note = (f"{a_label}.{'+'.join(m.left)} {_shown_keys(a_keys)} ↔ {b_label}.{'+'.join(m.right)} {_shown_keys(b_keys)} 겹침 0 "
                        f"({m.how} 병합)")
                out.append(((a_label, a_ev), (b_label, b_ev), note, m))
    return out


def rows_of(raw: Any) -> list[dict]:
    """값 안의 첫 문서 목록 — 맨 위 목록이거나 객체의 첫 목록 값(`status_of`와 같은 자리). 없으면 빈 목록."""
    value = _unwrap(raw)
    if isinstance(value, list):
        return value if value and all(isinstance(r, dict) for r in value) else []
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
                return v
    return []


# `code.resources` 본문의 자원 줄 — `    <이름> [<종류>] — <메서드> (<파일>:L<줄>)`. 읽기 구간이 앞, 쓰기 구간이 뒤(`query.resources_lines`).
# 실행기가 repr로 눕힌 텍스트(개행이 `\n` 두 글자)도 받는다 — 브리핑이 증거 본문에서 같은 것을 뽑는다.
_RESOURCE = re.compile(r"(?:\\n|\n) {4}(\S+) \[(rediskey|collection|topic)\] — ")
_WRITES_AT = re.compile(r"(?:\\n|\n) {2}쓰기 \d+:")


def inputs_in(text: str) -> list[tuple[str, str]]:
    """`code.resources` 텍스트에서 그 클래스가 **읽는** 자원 `(이름, 종류)`들 — 쓰기 구간은 뺀다."""
    m = _WRITES_AT.search(text)
    return _RESOURCE.findall(text[:m.start()] if m else text)


_LOC = re.compile(r"\(([^()\s]+):L(\d+)\)")


def class_at(text: str) -> tuple[str, int] | None:
    """`code.resources` 본문 머리줄의 클래스 자리 `(경로, 줄)` — 입력이 0인 클래스의 다음 칸은 그 본문이다(`code.read line=`이 머리 줄을 품은 심볼을 준다)."""
    head = re.split(r"\\n|\n", text, maxsplit=1)[0]
    m = _LOC.search(head)
    return (m.group(1), int(m.group(2))) if m else None


def reader_at(text: str) -> tuple[str, int] | None:
    """첫 읽기 자원 줄의 메서드 자리 `(경로, 줄)` — 입력이 다 정상인 클래스의 다음 칸은 **읽는 메서드 전체**다(사람 결정: `_run` 전체)."""
    m = _WRITES_AT.search(text)
    head = text[:m.start()] if m else text
    r = _RESOURCE.search(head)
    if r is None:
        return None
    at = _LOC.search(head, r.end())
    return (at.group(1), int(at.group(2))) if at else None


def fill_key(template: str, *, declared: str, key: str) -> str | None:
    """입력 템플릿의 자리를 대상 키에서 채운다 — 선언 `alarm:stats:{line}`과 실재 키 `alarm:stats:L1`에서 `{line}`=`L1`. 못 채우는 자리가 있으면
    None(호출부가 scan으로 간다)."""
    if "{" not in template:
        return template
    try:
        m = re.match("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>.+?)", re.escape(declared)) + "$", key)
        return template.format_map(m.groupdict() if m else {})
    except (re.error, KeyError, IndexError, ValueError):
        return None


# 쓰기 줄의 자리표는 `[<레포> · <서비스>]` 또는 공유 레포의 `[<레포>]`(서비스 없음 — 측정 #27: 사내 `<AGV 키>`의 쓰기 줄이 이 꼴이라 빠졌다, R3-5q ①).
_USES_ENTRY = re.compile(r"^\s*(?P<qual>[\w.]+) \([^)]*\) \[(?P<repo>[^\]·]+?)(?: · (?P<service>[^\]]+))?\]")
_USES_WRITES = re.compile(r"^\s*쓰기 (\d+):$")


def writers_in(text: str, classes: frozenset[str]) -> list[tuple[str, str]]:
    """`code.uses` 글의 **쓰기** 줄에서 `(인덱스 클래스, 서비스)`들 — 없는 키의 쓴 쪽 후보(R3-5p ①-c). 읽기 줄은 안 보고, 클래스 이름이 안 든 모듈 함수는
    안 센다(쓴 쪽은 늘 클래스였다 — 측정 #8). 글의 모양은 `query.uses_lines`가 정한다."""
    found: list[tuple[str, str]] = []
    writing = False
    for line in (text or "").splitlines():
        head = line.strip()
        if re.match(r"^(쓰기|읽기) \d+:$", head):
            writing = head.startswith("쓰기")
            continue
        m = _USES_ENTRY.match(line) if writing else None
        if m is None:
            continue
        cls = next((part for part in m.group("qual").split(".") if part in classes), "")
        service = (m.group("service") or "").strip()
        if cls and (cls, service) not in found:
            found.append((cls, service))
    return found


def writes_in(text: str) -> int:
    """`code.uses` 글의 쓰기 줄 수(머리의 `쓰기 N:`) — 0이면 인덱스가 쓴 쪽을 모른다(측정 #27 (b): `<PGM 키>`)."""
    for line in (text or "").splitlines():
        m = _USES_WRITES.match(line)
        if m:
            return int(m.group(1))
    return 0


def _topic_status(data: Any, error: str) -> tuple[str, bool, str]:
    """구독 토픽의 보존 범위 홉 상태 `(문장, 나쁜가, 멈춤에 붙일 토막)` — 비었으면(남은 메시지 0) 나쁨. 못 읽었으면 모르는 것."""
    if not isinstance(data, dict):
        return f"읽기 실패 — {error}", False, "범위 읽기 실패"
    parts = data.get("partitions") or []
    rng = (f"{parts[0]['begin']}~{parts[0]['end']}" if len(parts) == 1
           else ", ".join(f"{p['partition']}: {p['begin']}~{p['end']}" for p in parts))
    left = data.get("messages", 0)
    text = f"보존 범위 {rng} · 남은 메시지 {left}"
    return (f"비었다 — {text}" if not left else text), not left, f"범위 {rng}, 남은 메시지 {left}"


def status_of(raw: Any) -> tuple[str, bool]:
    """읽은 값의 상태 `(문장, 나쁜가)` — 없음·0행·빈 배열·빈 객체만 나쁘다. 정상이면 행 요약(`profile`)을 같이 — 표 한 줄에서 "554행 두 필드 전부 '-'"가
    보여야 한다(측정 #9 P0 2)."""
    if raw is None:
        return "없음", True
    value = _unwrap(raw)
    if isinstance(value, list):
        if not value:
            return "0행", True
        return (f"정상 — {profile(value)}" if all(isinstance(r, dict) for r in value) else f"정상 — {len(value)}건"), False
    if isinstance(value, dict):
        docs = [(k, v) for k, v in value.items() if isinstance(v, list) and v and all(isinstance(r, dict) for r in v)]
        if docs:
            name, rows = docs[0]
            return f"정상 — {name}: {profile(rows)}", False
        # 빈 목록·빈 객체는 판정 하나(`empties` — 대상 키 홉과 같다, R3-5b ②). 측정 #12: `record {}`를 정상이라 했다.
        arrays, objects = empties(value)
        if arrays or objects:
            return empty_text(arrays, objects), True
        if not value:
            return "빈 객체", True
    return "정상", False


class _Walked(NamedTuple):
    """걸은 클래스 하나의 기록 — 멈춘 자리가 이 클래스면 사유와 다음 칸을 여기서 만든다(R3-4 ②)."""
    writer: str
    tag: str
    scope: str
    inputs: int                         # 실제로 읽은 입력 수 — 자기 출력과 읽기 실패는 안 센다(R3-5b ②)
    res_id: str
    at: tuple[str, int] | None          # 읽은 입력이 있으면 읽는 메서드의 자리, 없으면 클래스 머리 줄
    listed: int = 0                     # 인덱스가 적은 읽기 자원 수
    missed: tuple[str, ...] = ()        # 못 본 입력 — `읽기 실패 <키>`, `<템플릿> scan N개 중 M개`


class _Branch(NamedTuple):
    """걸을 가지 하나 — `code.resources`에 더 줄 인자(한 레포뿐이면 없음, 한 서비스 레포면 `service`, 공유 레포면 `repo`), 표·기록의 꼬리표,
    판정의 component 후보 서비스들(공유 레포면 그 서비스 전부)."""
    params: dict
    tag: str
    candidates: tuple[str, ...]


async def run_walk(state: CaseState, *, runner: TaskRunnerPort, classes: frozenset[str],
                   class_services: dict[str, tuple[str, ...]] | None = None,
                   service_repos: dict[str, str] | None = None,
                   declared_by: dict[str, tuple[str, ...]] | None = None,
                   topic_producers: dict[str, tuple[str, ...]] | None = None,
                   subscriptions: dict[str, tuple[str, ...]] | None = None,
                   lookup: Callable[..., Awaitable[FilterScan]] | None = None,
                   clock: Callable[[], datetime] | None = None) -> dict:
    """걷기 노드의 본문 — State 패치(`plan_tasks`·`evidence`·`walk`). 입구가 키와 쓴 쪽을 못 골랐으면 빈 패치. `lookup`은 어댑터의 단일값 조회
    (`DeployedCodePort.filter_lines` — `lookup(클래스, fields=, service=|repo=)`); 없으면(인덱스 없음) 단일값은 표시만."""
    target = state.target
    if target is None or not target.key or not target.writer:
        return {}
    services = class_services or {}
    repos = service_repos or {}
    owners = declared_by or {}
    producers = topic_producers or {}
    notes: list[str] = []

    def remember(note: str) -> None:
        if note not in notes:
            notes.append(note)

    tasks: list[PlanTask] = []
    evidence: list[EvidenceRef] = []
    started = clock() if clock is not None else None
    counter = 0
    aside = 0.0                                                    # 메시지 읽기에 쓴 초 — 시간 상한 밖(R3-5e ⑥)
    message_failed = ""                                            # 이 걷기에서 처음 실패한 메시지 읽기의 사유 — 있으면 다음 메시지는 안 읽는다(R3-5g ③)
    from src.application.briefing import next_task_number          # 지연 import — briefing이 이 모듈을 import한다

    start = next_task_number(state)

    def elapsed() -> float:
        return (clock() - started).total_seconds() if started is not None else 0.0

    def over(writer: str, label: str) -> str:
        """읽기 수·시간 상한에 걸렸으면 그 문장(깊이는 호출부가 본다). 상한은 코드가 쥔다(규율 6)."""
        if counter >= MAX_READS:
            return f"읽기 {MAX_READS} 상한 — {writer}의 {label}부터는 리드가"
        if elapsed() - aside > MAX_SECONDS:
            return f"{MAX_SECONDS:.0f}초 상한 — {writer}의 {label}부터는 리드가"
        return ""

    async def run(goal: str, action: str, params: dict):
        nonlocal counter
        counter += 1
        # 리드와 같은 한 단계 번호(입구 뒤 다음 빈 번호부터) + `walked` — 두 단계 `t-1-N`은 리드가 `t-1`을 다시 내게 했다(측정 #10 P1 5).
        task = _task(f"t-{start + counter - 1}", goal, action, params).model_copy(update={"walked": True})
        outcome = await runner.run(task, case=state.case)
        done = _finish(task, outcome)
        tasks.append(done)
        evidence.extend(outcome.evidence)
        return done, outcome

    def homes(name: str) -> list[tuple[str, tuple[str, ...]]]:
        """이 클래스가 있는 레포들과 레포마다 그 레포를 쓰는 서비스들(레포 이름순). 레포 지도가 없으면 서비스가 곧 레포다(전과 같다)."""
        by_repo: dict[str, list[str]] = {}
        for s in services.get(name, ()):
            by_repo.setdefault(repos.get(s, s), []).append(s)
        return [(repo, tuple(sorted(by_repo[repo]))) for repo in sorted(by_repo)]

    def shown_as(repo: str, svcs: tuple[str, ...]) -> str:
        # 한 레포를 여러 서비스가 `path` 없이 공유하면 어느 것인지 코드로는 못 가른다(사내 처리기 레포: 다섯이 같은 config를 읽는다) — 표에 그렇게
        # 적고 판정의 component 후보는 그 전부다(사람 결정 10-14).
        return svcs[0] if len(svcs) == 1 else f"{repo}(서비스 {len(svcs)}개 공유)"

    def params_for(repo: str, svcs: tuple[str, ...]) -> dict:
        # 이 함수 안의 이름은 아래 분기의 지역 변수와 겹치면 안 된다 — 측정 #11 P0 1: 단일값 분기의 문자열 `where = …`가 이 중첩 함수를 덮어 그 뒤
        # `branches()`가 문자열을 불렀고(TypeError), 걷기가 통째로 사라졌다.
        return {"service": svcs[0]} if len(svcs) == 1 else {"repo": repo}

    def who(name: str) -> str:
        return ", ".join(shown_as(r, s) for r, s in homes(name))

    def split(writer: str, template: str, *, walking: bool) -> tuple[list[tuple[str, tuple[str, ...]]], str, tuple[str, ...]]:
        """레포 가르기 → (고른 레포들 `(레포, 서비스들)`, 표에 적을 이름, 판정의 component 후보 서비스들). 레포 둘이면 **그 키를 선언한 서비스가
        있는 레포**로 가른다(측정 #9 P1 4: `파일:qualname`도 같아 그걸로는 못 가른다 → 측정 #10 P0 2: 서비스가 아니라 **레포** 단위 — 공유 레포의
        서비스들은 같은 config를 읽어 선언으로 서로를 못 가른다; 사람 결정: 후보끼리 가를 때만, 표에 근거). 둘 다 선언이거나 선언이 없으면 둘 다 —
        후보도 전부. **모든 홉**에 쓴다(R3-5d ①-a, 측정 #13 고침 1: 나쁜 홉에서만 갈라 정상 홉의 쓴 쪽은 두 레포의 서비스 전부가 후보로 실렸고,
        판정 번역이 그 목록을 가른 결과로 믿어 첫 것을 골랐다). 주 줄은 한 번만."""
        found = homes(writer)
        if len(found) <= 1:
            return found, who(writer), (found[0][1] if found else ())
        declared = set(owners.get(template, ()))
        picked = [(r, s) for r, s in found if declared & set(s)]
        shown = ", ".join(shown_as(r, s) for r, s in found)
        if len(picked) == 1:
            repo, svcs = picked[0]
            note = f"{writer}는 레포 {len(found)}곳({shown})에 있다 — {template}을 선언한 {shown_as(repo, svcs)}로 골랐다"
            result = picked, shown_as(repo, svcs), svcs
        else:
            note = (f"{writer}는 레포 {len(found)}곳({shown})에 있다 — {template}을 선언한 쪽으로 못 가른다"
                    f"({'둘 다 선언' if picked else '선언 없음'}): {'둘 다 걷는다' if walking else '둘 다 후보'}")
            result = found, shown, tuple(x for _, s in found for x in s)
        remember(note)
        return result

    def branches(writer: str, template: str) -> tuple[list[_Branch], str, tuple[str, ...]]:
        """→ (걸을 가지들, 표에 적을 이름, 후보 서비스들). 한 레포뿐이면 인자 없이(리드의 중복이 같은 질의여야 잡힌다). 가른 레포를 `code.resources`에
        넘겨야 같은 이름 둘 중 하나가 열린다."""
        found, shown, cands = split(writer, template, walking=True)
        if len(homes(writer)) <= 1:
            return [_Branch({}, "", cands)], shown, cands
        return [_Branch(params_for(r, s), shown_as(r, s), s) for r, s in found], shown, cands

    async def keyed_writer(template: str):
        """없는 키의 쓴 쪽 — 값에는 없으니 인덱스의 쓰기 자원(`code.uses`)에서, 그 키를 선언한 서비스의 클래스를 먼저(R3-5p ①-c, 평가자 분석 10-28). 한
        클래스로 좁혀지면 `(클래스, 표시, 후보 서비스들, 질의 인자, 증거)`, 아니면 None(전처럼 `키가 없다`)."""
        _, got = await run(f"걷기 — 없는 키 {template}을 쓰는 클래스(인덱스)", "code.uses", {"name": template})
        uev = got.evidence[0].id if got.evidence else ""
        text = runner.raw(uev) if got.status == "ok" and uev else None
        if not isinstance(text, str):
            # 조용히 넘기지 않는다(측정 #27: 쓴 쪽 0이 어디서 났는지 표에 안 보였다) — 사실 한 줄.
            remember(f"{template}: code.uses 실패 — {(got.error or '증거 없음').splitlines()[0][:120]}")
            return None
        found = writers_in(text, classes)
        if not found:
            lines = writes_in(text)
            remember(f"{template}: 인덱스에 쓰는 함수가 없다(code.uses 쓰기 줄 0) — 쓴 쪽을 채우지 않는다" if not lines
                     else f"{template}: 쓰는 함수 {lines}줄이 인덱스 클래스의 것이 아니다 — 쓴 쪽을 채우지 않는다")
            return None
        declared = set(owners.get(template, ()))
        picked = [(c, s) for c, s in found if s in declared] or found
        writers = list(dict.fromkeys(c for c, _ in picked))
        if len(writers) != 1:
            remember(f"{template}을 쓰는 클래스가 여럿({', '.join(writers)}) — 쓴 쪽을 채우지 않는다")
            return None
        found_homes, shown, cands = split(writers[0], template, walking=False)
        params = params_for(*found_homes[0]) if len(homes(writers[0])) > 1 and found_homes else {}
        return writers[0], shown, cands, params, uev

    async def judged(rows: list, status: str, bad: bool, ev: str) -> tuple[str, bool, tuple[str, str] | None]:
        """단일값 필드(③) — 아래 홉에서 잡힌 `(필드, 값)`이 여기도 그대로면 나쁨(grep 없이 이어진다). 아니면 읽는 쪽 코드 줄이 정한다. 키 값과
        포인터 메시지(④)가 같은 판정을 받는다. → (상태, 나쁜가, 위로 이어 볼 단일값). 걷고 있는 클래스·가지·이어 온 단일값은 바깥 루프의 것."""
        nonlocal literal
        carry = None
        same_all = dict(_same_valued(rows))
        same = {k: v for k, v in same_all.items() if isinstance(v, str)}
        if not bad and watch is not None and same.get(watch[0]) == watch[1]:
            status, bad, carry = f"나쁨 — 같은 단일값 {watch[0]}={watch[1]!r} (아래 홉과 같다)", True, watch
        elif not bad and same and status.startswith("정상") and lookup is not None:
            # 어댑터 안의 조회(R3-4 ③ → R3-5 ③) — 가지의 인자(service·repo)를 그대로 넘겨 같은 이름 둘 중 그 레포의 파일을 본다. 단일값 전부를 한 번에
            # 대조한다(상한 없음). runner를 안 거치므로 읽기 상한·증거 밖이다. 문구는 사실만 — 행 수·그 값인 필드·그 값이 든 비교식의 자리(사람 결정 10-15).
            scan = await lookup(writer, fields=same, **branch.params)
            passed = ", ".join(f"{f}={v!r}" for f, v in same.items())
            drops = dropping(scan.hits)
            kept = [h for h in scan.hits if h.op != "!="]
            if drops:
                hit = drops[0]
                caught = dict.fromkeys((h.field, h.value) for h in drops)
                shown_fields = " ".join(f"{f}={v!r}" for f, v in caught)
                # 줄로 센다 — 한 줄에 짝이 둘(`r.get("state") != "-" and r.get("wg") != "-"`)이면 짝 수로 센 "외 1줄"은 없는 줄을 말한다(R3-5 ④).
                lines_hit = len(dict.fromkeys((h.path, h.line) for h in drops))
                more = f" 외 {lines_hit - 1}줄" if lines_hit > 1 else ""
                status, bad, carry = (f"나쁨 — {len(rows)}행 전부 {shown_fields} · 이 필드 값이 든 비교식 {hit.path}:L{hit.line} '{hit.text}'{more}", True,
                                      (hit.field, hit.value))
                if literal is None:
                    literal = (scope, hit.path, hit.line, ev)
            elif scan.problem:
                # 조회가 안 됐으면 대조한 것이 없다(0/M) — 정상이라 하지 않는다(사람 결정 10-15: 일부만 봤으면 "일부만 검사").
                status = "미확인" + status[len("정상"):] + f" · 단일값 {passed} — 일부만 검사(0/{len(same)}): {scan.problem}"
                partial.add(came_from)
            elif scan.unlinked:
                # 못 이은 컴포넌트가 하나라도 있으면 N=M이라도 일부만이다 — 그 컴포넌트에 비교식이 있을 수 있다.
                status = ("미확인" + status[len("정상"):]
                          + f" · 단일값 {passed} — 일부만 검사({len(scan.checked)}/{len(same)}, 못 이음: {', '.join(scan.unlinked[:3])})")
                partial.add(came_from)
            else:
                scope_note = f"{len(scan.checked)}/{len(same)} 대조, {len(scan.files)}파일" + (f", 이은 컴포넌트 {', '.join(scan.linked)}" if scan.linked else "")
                status += f" · 단일값 {passed} — 거르는 줄 없음({scope_note})"
            if kept and not drops:
                # 같은 짝의 `==` 줄은 사실로만 — 리드가 그 줄을 보고 "코드가 거른다"로 읽지 않게 왜 안 세는지까지(측정 #22의 틀린 사실 모양).
                k = kept[0]
                more_kept = len(dict.fromkeys((h.path, h.line) for h in kept)) - 1
                status += f" · == 비교식 {k.path}:L{k.line} '{k.text}'" + (f" 외 {more_kept}줄" if more_kept else "") + " — 그 값을 남기는 쪽이라 안 센다"
            if len(same_all) > len(same):
                status += f" · 대조 안 함 {len(same_all) - len(same)}개(문자열 아님)"
        return status, bad, carry

    first = next((t for t in state.plan_tasks if t.id == "t-1" and t.result_evidence_ids), None)
    first_branches, first_shown, first_cands = branches(target.writer, target.declared)
    hops = [Hop(key=target.key, depth=0, writer=target.writer, service=first_shown, candidates=list(first_cands),
                status=empty_text(target.empty, target.empty_objects) or "정상", bad=bool(target.empty or target.empty_objects),
                evidence_id=first.result_evidence_ids[0] if first else "",
                basis=f"대상 키 — 값의 {target.writer_at}이 {target.writer}")]
    seen = {target.key, target.declared}
    # (쓴 클래스, 가지 — 인자·꼬리표·후보, 깊이, 여기로 오게 한 증거, 이어 볼 단일값 `(필드, 값)` — ③, 그 키 값의 메시지 포인터 — ④)
    frontier: list[tuple[str, _Branch, int, str, tuple[str, str] | None, dict]] = [(target.writer, b, 0, hops[0].evidence_id, None, dict(target.pointer))
                                                                                  for b in first_branches]
    declared_names: dict[str, str] = {}                                                     # 홉의 증거 → 그 키의 선언 이름(`code.uses`는 선언 이름으로 묻는다)
    failed_at: dict[str, tuple[str, str, dict]] = {}                                        # 자원을 못 읽은 가지 — 그 홉의 증거 → (쓴 쪽[꼬리표], 사유, 질의 인자)
    walked_at: dict[str, _Walked] = {}                                                      # 걸은 가지 — 그 홉의 증거 → 기록
    linked_at: dict[str, list[FilterHit]] = {}                                              # 걸은 가지 — 그 홉의 증거 → 이은 컴포넌트 클래스들의 자리(③)
    partial: set[str] = set()                                                               # 입력을 다 못 본 가지 — 그 홉의 증거(일부만 검사한 입력이 있다)
    pointerless: set[str] = set()                                                           # 입력을 못 본 가지 중 포인터도 없는 것 — 그 홉의 증거(④)
    pointed_at: dict[str, tuple[str, Any, Any]] = {}                                        # 메시지 홉의 증거 → (토픽, 보존 시작, 끝)
    joined_at: dict[str, tuple[Any, str, str, tuple[str, int] | None]] = {}                 # 병합으로 나빠진 홉의 증거 → (병합식, 문구, 멈춤 머리, 거르는 줄)(④-b·R3-5h ②)
    subscribed = dict(subscriptions or {})                                                  # 서비스 → config로 구독을 선언한 토픽들(R3-5p ①-b)
    keyed_at: dict[str, tuple[str, str, dict, list[tuple[str, str, str]]]] = {}             # 없는 키의 홉 증거 → (쓰는 클래스, 표시, 질의 인자, [(토픽, 범위 토막, 증거)])(①-c)
    topics_at: dict[str, tuple[str, str, dict, str]] = {}                                   # 구독 토픽 홉의 증거 → (쓰는 클래스, 표시, 질의 인자, 없는 키 홉의 증거)
    literal: tuple[str, str, int, str] | None = None                                        # 첫 리터럴 줄 — (서비스, 경로, 줄, grep 증거)
    # 읽은 키 → (읽기 결과 "ok"·"fail", 값이 댄 쓴 쪽) — 이미 읽은 키를 다른 클래스가 또 나열하면 그 결과를 따르고, 값이 그 클래스를 대면 자기
    # 출력이라 입력으로 안 센다(R3-5b ②). 대상 키는 입구가 읽었다.
    readings: dict[str, tuple[str, str]] = {target.key: ("ok", target.writer), target.declared: ("ok", target.writer)}
    stopped, nxt, nxt_ev, last = "", None, "", target.writer
    while frontier and not stopped:
        writer, branch, depth, came_from, watch, pointer = frontier.pop(0)
        last = writer
        here = {"name": writer, **branch.params}
        tag = f"[{branch.tag}]" if branch.tag else ""
        # 단일값 검사의 grep 범위 — 가지의 레포(그 레포의 서비스 하나면 된다 — 어댑터가 서비스를 레포로 푼다). 모르면 전체.
        scope = branch.candidates[0] if branch.candidates else ""
        if depth >= MAX_DEPTH:
            stopped, nxt, nxt_ev = f"깊이 {MAX_DEPTH} 상한 — {writer}부터는 리드가", ("code.resources", here), came_from
            break
        if (why := over(writer, "자원")):
            stopped, nxt, nxt_ev = why, ("code.resources", here), came_from
            break
        _, res = await run(f"걷기 — {writer}{tag}가 읽고 쓰는 자원", "code.resources", here)
        if res.status != "ok" or not res.evidence:
            # 이 가지만 접는다(측정 #10 P0 2: 공용 키 하나의 실패가 걷기 전체를 끊었다) — 여기로 오게 한 행에 적고 다른 가지는 계속 걷는다.
            why = res.error or "증거 없음"
            at = next((i for i, h in enumerate(hops) if h.evidence_id == came_from and h.writer == writer), len(hops) - 1)
            hops[at] = hops[at].model_copy(update={"status": f"{hops[at].status} · {writer}{tag}의 자원 못 읽음 — {why}"})
            failed_at[came_from] = (f"{writer}{tag}", why, here)
            continue
        res_id = res.evidence[0].id
        text = runner.raw(res_id)
        text = text if isinstance(text, str) else ""
        found_inputs = inputs_in(text)
        walked_at[came_from] = _Walked(writer, tag, scope, 0, res_id, class_at(text), len(found_inputs))
        real, missed = 0, []                                                                # 실제로 읽은 입력 수, 못 본 입력(R3-5b ②)
        read_inputs: list[tuple[str, str, list, str, Any, str]] = []                        # 이 클래스가 읽은 입력 — (라벨, 증거, 행들, 쓴 쪽, 원본, 선언 이름)
        merges: list = []
        if lookup is not None:
            # 컴포넌트 링크는 입력의 단일값과 무관하게 클래스 파일에서 뽑는다(사람 결정 10-15: 자원 0인 클래스도 `get("X")` 리터럴이 있다) — 멈춘 자리의 뒤 칸.
            # 이어 온 단일값이 있으면 그 필드로 묻는다 — 어댑터가 그 필드 이름이 리터럴로 든 컴포넌트를 앞에 둔다(10-15 (c)). 필드 없이 물으면 코드
            # 차례 그대로라 뒤 칸 셋에서 잘릴 수 있다(측정판 R3-5 ④).
            scan0 = await lookup(writer, fields=dict([watch]) if watch else {}, **branch.params)
            linked_at[came_from], merges = list(scan0.linked_at), list(scan0.merges)
        for name, kind in found_inputs:
            if kind == "rediskey":
                filled = fill_key(name, declared=target.declared, key=target.key)
                if filled is not None:
                    reads = [("redis.get", {"key": filled}, filled)]
                else:
                    if (why := over(writer, name)):
                        stopped, nxt, nxt_ev = why, ("code.resources", here), came_from
                        break
                    _, scanned = await run(f"걷기 — {writer}의 입력 {name}의 실재 키", "redis.scan", {"pattern": f"{key_head(name)}*"})
                    names = runner.raw(scanned.evidence[0].id) if scanned.status == "ok" and scanned.evidence else None
                    scanned_keys = [k for k in names if isinstance(k, str)] if isinstance(names, list) else []
                    reads = [("redis.get", {"key": k}, k) for k in scanned_keys[:SCAN_READS]]
                    # scan이 실패했거나 실재 키를 다 못 읽었거나 0개면 그 입력은 다 본 것이 아니다(사람 결정 10-16: `<템플릿> scan N개 중 M개`).
                    if not isinstance(names, list):
                        missed.append(f"읽기 실패 {name}(scan)")
                    elif len(reads) < len(scanned_keys) or not scanned_keys:
                        missed.append(f"{name} scan {len(scanned_keys)}개 중 {len(reads)}개")
            elif kind == "collection":
                reads = [("mongo.find", {"collection": name, "filter": {}, "limit": 5}, name)]
            else:
                reads = [("kafka.tail", {"topic": name, "limit": 5}, name)]
            for action, params, label in reads:
                if label in seen:
                    # 이미 읽은 키 — 값이 이 클래스를 대면 자기 출력이라 입력으로 안 센다(사람 결정 10-16: 자원이 자기 출력뿐이면 "입력을 모른다"). 다른
                    # 쪽이 읽어 둔 입력이면 그 결과를 따른다.
                    result, named = readings.get(label, ("", ""))
                    if named != writer:
                        if result == "ok":
                            real += 1
                        elif result == "fail":
                            missed.append(f"읽기 실패 {label}")
                    continue
                if (why := over(writer, label)):
                    stopped, nxt, nxt_ev = why, ("code.resources", here), came_from
                    break
                seen.add(label)
                _, got = await run(f"걷기 — {writer}의 입력 {label}", action, params)
                ev = got.evidence[0].id if got.evidence else ""
                raw = runner.raw(ev) if got.status == "ok" and ev else None
                if got.status != "ok":
                    status, bad = f"읽기 실패 — {got.error or '증거 없음'}", False        # 모르는 것이지 비어 있는 것이 아니다
                else:
                    status, bad = status_of(raw)
                w, at, _ = writer_of(_unwrap(raw), classes) if raw is not None else ("", "", "")
                if w and w == writer:
                    # 값이 이 클래스를 댄다 — 이 클래스의 **출력**이지 입력이 아니다(측정 #9 P0 3). 표에는 남긴다(리드가 또 읽지 않게).
                    status, bad = f"출력 ({writer} 자신의 것 — 값의 {at})", False
                # 읽기 실패는 모르는 것이지 나쁜 것이 아니다 — 행은 나쁨이 아니고, 멈춤은 "다 못 봤다"다. 키 없음(None)은 읽은 것이다 — 사실 `없음`(나쁨).
                readings[label] = ("ok" if got.status == "ok" else "fail", w)
                if got.status != "ok":
                    missed.append(f"읽기 실패 {label}")
                elif w != writer:
                    real += 1
                status, bad, carry = await judged(rows_of(raw) if raw is not None else [], status, bad, ev)
                # 없는 키의 쓴 쪽은 값에 없다 — 인덱스의 쓰기 자원과 그 키를 선언한 서비스로 채운다(R3-5p ①-c). 그 클래스로 더 걷지는 않고(다른 입력은 리드의
                # `code.resources` 칸), 그 서비스가 구독하는 토픽의 보존 범위만 아래에서 홉으로 적는다.
                keyed = (await keyed_writer(name)) if (got.status == "ok" and raw is None and not w and kind == "rediskey") else None
                # 나쁜 입력의 쓴 쪽은 **그 입력 키의 선언 이름**으로 가른다 — 대상 키를 선언한 쪽이 상류 키까지 선언했으리란 법은 없다. 정상 홉의 쓴 쪽도
                # 같은 가르기로(걷지는 않는다) — 표의 쓴 쪽 칸과 판정의 후보가 같은 근거를 갖는다.
                if keyed is not None:
                    w, shown, cands, params_k, uses_ev = keyed
                    next_branches = []
                elif bad and w:
                    next_branches, shown, cands = branches(w, name)
                elif w:
                    next_branches, (_, shown, cands) = [], split(w, name, walking=False)
                else:
                    next_branches, shown, cands = [], "", ()
                declared_names[ev] = name
                basis = f"{writer}{tag}가 읽는 자원 ({res_id})"
                if keyed is not None:
                    basis += f" · 쓴 쪽은 인덱스의 쓰기 자원({uses_ev})에서, {name}을 선언한 서비스의 것"
                hops.append(Hop(key=label, kind=kind, depth=depth + 1, writer=w, service=shown, candidates=list(cands), status=status, bad=bad,
                                evidence_id=ev, basis=basis, parent=came_from))
                if got.status == "ok" and raw is not None and w != writer:
                    read_inputs.append((label, ev, rows_of(raw), w, raw, name))
                if keyed is not None:
                    # 쓰는 클래스의 서비스가 config로 구독하는 토픽마다 보존 범위 한 번(`kafka.offsets`, 본문 없음) — 비었으면 그 홉이 나쁨이다.
                    ranges: list[tuple[str, str, str]] = []
                    for topic in sorted({t for s in cands for t in subscribed.get(s, ())})[:3]:
                        if (why := over(writer, topic)):
                            stopped, nxt, nxt_ev = why, ("code.resources", here), came_from
                            break
                        _, tgot = await run(f"걷기 — {w}가 구독하는 토픽 {topic}의 보존 범위", "kafka.offsets", {"topic": topic})
                        tev = tgot.evidence[0].id if tgot.evidence else ""
                        tdata = runner.raw(tev) if tgot.status == "ok" and tev else None
                        tstatus, tbad, piece = _topic_status(tdata, tgot.error or "증거 없음")
                        hops.append(Hop(key=topic, kind="topic", depth=depth + 2, service=f"{w} [{shown}] 구독", candidates=list(cands), status=tstatus,
                                        bad=tbad, evidence_id=tev, basis=f"{w}의 구독 선언(config) — {name}을 쓰는 클래스의 메시지 입력 ({ev})", parent=ev))
                        ranges.append((topic, piece, tev))
                        topics_at[tev] = (w, shown, params_k, ev)
                    keyed_at[ev] = (w, shown, params_k, ranges)
                if stopped:
                    break
                # 나쁜 입력의 아는 쓴 쪽만 — 그때만 가지가 있다. 그 키 값의 포인터를 들고 간다(④) — 그 클래스를 만든 메시지가 거기 적혀 있다.
                frontier += [(w, b, depth + 1, ev, carry, pointer_of(_unwrap(raw)) if raw is not None else {}) for b in next_branches]
            if stopped:
                break
        if not stopped and pointer:
            # 키를 만든 메시지 한 건(④, 사람 결정 10-19) — 이 클래스를 댄 키 값의 포인터가 가리키는 메시지를 입력으로 읽는다. 메시지(토픽) 입력은 인덱스가
            # 자원으로 못 센다(측정 #12 "입력을 모른다"의 자리). 발행 쪽은 클래스가 아니라 config 발행 선언 — 같은 이름 클래스가 두 레포(F1); 선언이 없으면
            # 우리 밖이다. 그 메시지로는 더 걷지 않는다.
            topic, part, off = pointer["topic"], pointer["partition"], pointer["offset"]
            label = f"{topic}[{part}]@{off}"
            svcs = tuple(producers.get(topic, ()))
            shown = f"발행 선언: {', '.join(svcs)}" if svcs else "발행 선언 없음(우리 밖)"
            if label in seen:
                result, _ = readings.get(label, ("", ""))
                if result == "ok":
                    real += 1
                elif result == "fail":
                    missed.append(f"읽기 실패 {label}")
            elif message_failed:
                # 같은 걷기에서 앞 메시지 읽기가 실패했으면 다시 안 붙는다(R3-5g ③) — 측정 #17: 접속 실패가 한 건에 50~60초이고 시간 상한 밖(R3-5e ⑥)이라 한
                # 걷기가 135초였다. 읽기 수에 안 세고 증거도 없다 — 못 본 입력으로만 적는다(멈춤은 전처럼 "입력을 다 못 봤다").
                seen.add(label)
                readings[label] = ("fail", "")
                missed.append(f"읽기 실패 {label}")
                hops.append(Hop(key=label, kind="message", depth=depth + 1, service=shown, candidates=list(svcs),
                                status=f"읽기 실패 — 앞 메시지 읽기가 실패해 건너뜀({message_failed})", bad=False, evidence_id="",
                                basis=f"{writer}{tag}를 댄 키 값의 포인터 {pointer.get('at', '')} ({came_from})", parent=came_from))
            elif (why := over(writer, "메시지")):
                stopped, nxt, nxt_ev = why, ("code.resources", here), came_from
            else:
                seen.add(label)
                before = elapsed()
                _, got = await run(f"걷기 — {writer}{tag}를 댄 키의 포인터 메시지 {label}", "kafka.message_at",
                                   {"topic": topic, "partition": part, "offset": off})
                # 메시지 한 건 읽기는 브로커 왕복이라 걷기의 시간 상한 밖이다(R3-5e ⑥, 측정 #15 메모: 두 건이 53초로 상한에 걸렸다). 읽기 수 상한은
                # 그대로 — 세는 것이지 재는 것이 아니다. `seconds`는 실제 시간 그대로.
                aside += elapsed() - before
                ev = got.evidence[0].id if got.evidence else ""
                data = runner.raw(ev) if got.status == "ok" and ev else None
                readings[label] = ("ok" if isinstance(data, dict) else "fail", "")
                carry = None
                if not isinstance(data, dict):
                    status, bad = f"읽기 실패 — {got.error or '증거 없음'}", False
                    missed.append(f"읽기 실패 {label}")
                    message_failed = message_failed or (got.error or "증거 없음").splitlines()[0][:80]
                elif not data.get("found"):
                    # 보존 범위 밖·압축 구멍 — 읽은 것이고 사실 `없음`(나쁨)이다(측정 #13: 범위 밖 2개가 둘 다 진짜 원인 자리). 키 없음과 같은 종류.
                    status, bad = f"없음 — 키를 만든 메시지가 토픽에 없다(보존 범위 {data.get('begin')}~{data.get('end')})", True
                    real += 1                                                          # 읽었고 없다 — 키 없음과 같은 종류
                    pointed_at[ev] = (topic, data.get("begin"), data.get("end"))
                else:
                    # 값 판정은 키 값과 하나(`status_of` — 봉투 없는 목록도 맨 위 목록으로 센다). 포장은 `redis.get` 원본 모양으로 — 메시지 자신의 `value`
                    # 필드가 풀리지 않게.
                    boxed = {"value": (data.get("message") or {}).get("value")}
                    # 값이 null인 레코드(톰스톤)는 "없다"가 아니라 "비었다"다 — `status_of(None)`의 `없음`은 키 없음의 말이라 여기선 안 쓴다.
                    status, bad = status_of(boxed) if boxed["value"] is not None else ("빈 메시지 — 값이 null", True)
                    real += 1                                                          # 메시지도 읽은 입력이다 — 정상이면 "입력이 다 정상"에 든다
                    pointed_at[ev] = (topic, data.get("begin"), data.get("end"))
                    status, bad, carry = await judged(rows_of(boxed), status, bad, ev)
                    read_inputs.append((label, ev, rows_of(boxed), "", None, topic))
                hops.append(Hop(key=label, kind="message", depth=depth + 1, service=shown, candidates=list(svcs), status=status, bad=bad,
                                evidence_id=ev, basis=f"{writer}{tag}를 댄 키 값의 포인터 {pointer.get('at', '')} ({came_from})", parent=came_from))
        elif not stopped and not real and not missed:
            pointerless.add(came_from)
        if merges and not stopped:
            # 조인 검사(④-b, P1-5): 병합식의 키 필드로 두 입력의 값이 겹치는지 — 안 겹치면 두 홉이 나쁨이고, 읽을 때는 정상이라 안 낸 가지(아는 쓴 쪽)를 지금 낸다.
            for (a_label, a_ev), (b_label, b_ev), note, merge in join_check(merges, read_inputs):
                head, what, text, first = "병합이 0건", "병합 짝 없음", note, None
                if merge.how != "inner":
                    # inner가 아니면 겹침 0이어도 행은 남는다(측정 #18 P0-b: left 병합의 겹침 0이 가짜 나쁨 6행 — c-1이 `키가 없다` 대신 읽기 상한까지).
                    # 사실은 주로만 — 결과에 바로 이어진 호출이 빈 칸을 채우고 **그 값을 거르는 줄이 범위에 있을 때만**(P0-a: 작업자 배지 — outer 병합 뒤
                    # '-' 채움을 같은 파일의 비교식이 거른다) 두 홉이 나쁨이다. 채워지는 열은 보존 안 되는 쪽의 열(`fill_fields`), 조회는 단일값 검사와 같은 조회.
                    filled = None
                    if merge.chained and lookup is not None:
                        fields, sides = fill_fields(merge, next(e[2] for e in read_inputs if e[1] == a_ev), next(e[2] for e in read_inputs if e[1] == b_ev))
                        scan = await lookup(writer, fields=fields, **branch.params) if fields else None
                        drops = dropping(scan.hits) if scan is not None else []
                        if drops:
                            hit, (call, value) = drops[0], merge.chained
                            filled = (f"{note} → {merge.how} 병합이라 행은 남고 짝 없는 행의 {hit.field}({sides.get(hit.field, '?')} 열) 값이 {value!r}"
                                      f"({call}({value!r})) · 이 값을 거르는 줄 {hit.path}:L{hit.line} '{hit.text}'", (hit.path, hit.line))
                    if filled is None:
                        remember(f"{writer}: {note} — {merge.how} {JOIN_NOTE_MARKS[0]} ({merge.path}:L{merge.line})")
                        continue
                    head, what, (text, first) = "병합 뒤 채움값을 거른다", "병합 뒤 채움값", filled
                for ev_ in (a_ev, b_ev):
                    at = next((i for i, h in enumerate(hops) if h.evidence_id == ev_), None)
                    if at is None or hops[at].bad:
                        continue
                    old = hops[at].status
                    hops[at] = hops[at].model_copy(update={"bad": True, "status": f"나쁨 — {what}: {text} ({merge.path}:L{merge.line}) · "
                                                                                    + (old[len("정상 — "):] if old.startswith("정상 — ") else old)})
                    joined_at[ev_] = (merge, text, head, first)
                    _, _, _, w_, raw_, name_ = next(e for e in read_inputs if e[1] == ev_)
                    if w_:
                        next_branches, shown_, cands_ = branches(w_, name_)
                        hops[at] = hops[at].model_copy(update={"service": shown_, "candidates": list(cands_)})
                        frontier += [(w_, b, depth + 1, ev_, None, pointer_of(_unwrap(raw_)) if raw_ is not None else {}) for b in next_branches]
            if read_inputs:
                # 키 필드를 가진 입력 행이 없는 병합식(측정 #18 P0-a의 다른 가능성 — 변환 뒤 이름이 다르면 겹침을 못 센다). 읽은 입력이 없으면 "입력을 모른다"가 말한다.
                for merge, why in join_unmatched(merges, read_inputs):
                    remember(f"{writer}: 병합식 {merge.path}:L{merge.line}의 {why} {JOIN_NOTE_MARKS[1]}")
        # 이 클래스가 실제로 읽은 입력과 못 본 입력 — 멈춘 자리가 이 클래스면 사유가 여기서 나온다. 다음 칸은 읽기가 있었으면(실패한 읽기도) 읽는 메서드,
        # 읽은 입력이 없으면("모른다") 클래스 본문. 메시지만 읽은 클래스(인덱스 자원 0)는 읽는 메서드 줄이 없어 클래스 본문이다.
        walked_at[came_from] = walked_at[came_from]._replace(inputs=real, missed=tuple(missed),
                                                             at=(reader_at(text) if real or missed else None) or class_at(text))
    # 멈춘 자리 = 가장 깊은 나쁜 홉(같은 깊이면 먼저 읽은 것). 사유와 다음 칸은 **그 홉**의 일로 만든다 — 마지막 실패나 첫 "모름"이 아니라.
    bad_hops = [h for h in hops if h.bad]
    stop_hop = max(bad_hops, key=lambda h: h.depth) if bad_hops else hops[0]
    ev = stop_hop.evidence_id
    rungs: list[Rung] = []
    if not stopped:
        if stop_hop.kind == "topic" and ev in topics_at:
            # 없는 키를 쓰는 클래스가 구독하는 토픽이 비었다(R3-5p ①-c) — 입력이 없다. 첫 칸은 지금 오는 것, 그다음 그 클래스의 자원.
            w_, shown_, params_, key_ev = topics_at[ev]
            stopped = f"구독 토픽이 비었다 — {stop_hop.key}({w_} [{shown_}])"
            rungs += [Rung(action="kafka.tail", params={"topic": stop_hop.key, "limit": 5}, evidence=ev),
                      Rung(action="code.resources", params={"name": w_, **params_}, evidence=key_ev)]
        elif ev in keyed_at:
            # 키가 없는데 쓰는 클래스는 안다(R3-5p ①-c) — 그 클래스가 구독하는 토픽에 남은 것이 멈춤 문구에 같이 간다(입력을 받고도 키를 안 만든 것인지를
            # 리드가 사실로 가른다). 첫 칸은 그 클래스의 자원, 그다음 토픽마다 지금 오는 것.
            w_, shown_, params_, ranges_ = keyed_at[ev]
            tail = " / ".join(f"구독 {t} {piece}" for t, piece, _ in ranges_) or "구독 선언 없음"
            stopped = f"키가 없다 — {stop_hop.key}(쓰는 {w_} [{shown_}] · {tail})"
            rungs.append(Rung(action="code.resources", params={"name": w_, **params_}, evidence=ev))
            rungs += [Rung(action="kafka.tail", params={"topic": t, "limit": 5}, evidence=tev) for t, _, tev in ranges_ if tev]
        elif ev in joined_at and ev not in walked_at and ev not in failed_at:
            # 병합 0건(④-b) — 짝 없는 입력의 쓴 쪽으로 더 걸었으면 멈춤은 전처럼 거기(그쪽 입력의 사유 — 홉 상태에 병합이 남는다). 더 못 걸었으면(쓴 쪽
            # 모름·메시지) 사유는 병합이고 첫 칸은 병합 줄(그 클래스의 레포), 그다음은 전처럼 그 입력의 칸.
            merge, text, head, first = joined_at[ev]
            owner = walked_at.get(stop_hop.parent)
            stopped = f"{head} — {owner.writer if owner else '?'}: {text} ({merge.path}:L{merge.line})"
            scope_, gev = (owner.scope, owner.res_id) if owner else ("", ev)
            if first is not None:
                # 채움값을 거르는 줄(R3-5h ②)이 먼저 — 리드가 먼저 볼 곳은 그 비교식을 품은 함수다(단일값 비교식과 같은 자리). 그다음이 병합 줄.
                rungs.append(Rung(action="code.read", params={"service": scope_, "path": first[0], "line": first[1]}, evidence=gev))
            rungs.append(Rung(action="code.read", params={"service": scope_, "path": merge.path, "line": merge.line}, evidence=gev))
            if stop_hop.kind == "message":
                rungs.append(Rung(action="kafka.tail", params={"topic": stop_hop.key.split("[", 1)[0], "limit": 5}, evidence=ev))
            else:
                rungs.append(Rung(action="code.uses", params={"name": declared_names.get(ev, stop_hop.key)}, evidence=ev))
        elif stop_hop is not hops[0] and stop_hop.kind == "message":
            # 키를 만든 메시지가 나쁘거나 없다(④) — 그 메시지를 받는 클래스가 멈춘 자리다. 발행 쪽은 config 선언(없으면 우리 밖)이고 그쪽으로 걷지는 않는다.
            # 다음 칸: 없음이면 지금 토픽에 오는 것(`kafka.tail`)이 먼저, 나쁨이면 그 메시지를 받는 클래스 본문과 이은 컴포넌트가 먼저.
            info = walked_at.get(stop_hop.parent)
            who = f"{info.writer}의 " if info is not None else ""
            topic, begin, end = pointed_at.get(ev, (stop_hop.key.split("[", 1)[0], None, None))
            tail = Rung(action="kafka.tail", params={"topic": topic, "limit": 5}, evidence=ev)
            code: list[Rung] = []
            if info is not None and info.scope and info.at is not None:
                code.append(Rung(action="code.read", params={"service": info.scope, "path": info.at[0], "line": info.at[1]}, evidence=info.res_id))
                code += [Rung(action="code.read", params={"service": info.scope, "path": found_at.path, "line": found_at.line}, evidence=info.res_id)
                         for found_at in linked_at.get(stop_hop.parent, [])[:3]]
            if stop_hop.status.startswith("없음"):
                stopped = f"키를 만든 메시지가 토픽에 없다 — {who}{stop_hop.key}(보존 범위 {begin}~{end} · {stop_hop.service})"
                rungs += [tail, *code]
            else:
                stopped = f"입력 메시지가 나쁘다 — {who}{stop_hop.key}({stop_hop.service})"
                rungs += [*code, tail]
        elif stop_hop is not hops[0] and not stop_hop.writer:
            # 값이 없으면 볼 것도 없다 — "값에 클래스 이름이 없다"는 값이 있을 때의 말이다(측정 #13 추가분 4). 다음 칸은 셋 다 그 키를 누가 쓰나.
            stopped = (f"키가 없다 — {stop_hop.key}" if stop_hop.status == "없음"
                       else f"0행 — {stop_hop.key}(값이 비어 쓴 쪽을 알 수 없다)" if stop_hop.status == "0행"
                       else f"쓴 쪽을 모른다 — {stop_hop.key}(값에 인덱스의 클래스 이름이 없다)")
            rungs.append(Rung(action="code.uses", params={"name": declared_names.get(ev, stop_hop.key)}, evidence=ev))
        elif ev in failed_at:
            tag, why, here = failed_at[ev]
            stopped = f"{tag}의 자원을 못 읽었다 — {why}"
            rungs.append(Rung(action="code.resources", params=here, evidence=ev))
        elif ev in walked_at:
            info = walked_at[ev]
            # `입력이 다 정상`은 나열된 입력을 전부 실제로 읽었고 전부 정상이고 단일값 검사도 완전할 때만이다(사람 결정 10-16). 못 본 입력(읽기 실패·
            # scan 일부)이나 일부만 검사(못 이은 컴포넌트에 비교식이 있을 수 있다, 10-15)가 있으면 "다 못 봤다". 실제로 읽은 입력이 0이면 "모름" — 메시지
            # (토픽) 입력은 인덱스가 자원으로 못 세고, 읽는 자원이 자기 출력뿐인 클래스도 있다(측정 #12).
            unseen = [*info.missed, *(["일부만 검사"] if ev in partial else [])]
            # 포인터가 없으면 그 사실만(사람 결정 10-19) — 토픽을 짐작하지 않는다.
            none = " · 포인터 없음" if ev in pointerless else ""
            # 읽은 입력이 하나라도 있으면(포인터 메시지만 읽은 클래스 — 인덱스 자원 0 — 도) 그 결과가 말한다.
            stopped = (f"입력을 다 못 봤다 — {info.writer}({' · '.join(unseen)})" if unseen
                       else f"입력이 다 정상 — {info.writer}" if info.inputs
                       else f"입력을 모른다 — {info.writer}(인덱스 자원 0 — 메시지 입력일 수 있다{none})" if not info.listed
                       else f"입력을 모른다 — {info.writer}(인덱스 자원이 자기 출력뿐{none})")
            if info.scope and info.at is not None:
                rungs.append(Rung(action="code.read", params={"service": info.scope, "path": info.at[0], "line": info.at[1]}, evidence=info.res_id))
                # 이은 컴포넌트 **클래스 본문**이 그 뒤 칸들 — 결정적인 줄은 컴포넌트에 있다(사람 결정 10-14·10-15: 등록 줄이 아니라 그 클래스).
                rungs += [Rung(action="code.read", params={"service": info.scope, "path": found_at.path, "line": found_at.line}, evidence=info.res_id)
                          for found_at in linked_at.get(ev, [])[:3]]
        else:
            stopped = f"입력이 다 정상 — {last}"
    elif nxt is not None:
        rungs.append(Rung(action=nxt[0], params=nxt[1], evidence=nxt_ev))
    if literal is not None:
        # 아래에 단일값이 든 비교식이 있었다 — 멈춘 이유가 무엇이든 리드가 먼저 볼 곳은 **그 줄을 품은 함수**다(사람 결정; `code.read line=`이
        # 인덱스의 심볼 줄 범위로 함수 전체를 준다 — ④-a). 그 뒤 칸이 멈춘 자리의 질의다. 읽는 쪽 서비스를 모르면 `code.read`를 못 만든다.
        scope, path, line, gev = literal
        stopped += f" (단일값이 든 비교식 {path}:L{line})"
        if scope:
            head = Rung(action="code.read", params={"service": scope, "path": path, "line": line}, evidence=gev)
            rungs = [head] + [r for r in rungs if r != head]
    return {"plan_tasks": tasks, "evidence": evidence,
            "walk": Walk(hops=hops, reads=counter, seconds=elapsed(), stopped=stopped, stop_at=ev, rungs=rungs, notes=notes)}
