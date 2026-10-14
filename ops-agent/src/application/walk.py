"""R3-3 ② — 코드가 상류를 걷는다.

측정 #9: 쓴 쪽 링크·pinned·예시 한 칸이 작동해도 c-1은 원천 키까지 세 홉이 걸렸고, c-2는 첫 나쁜 값을 읽고도 못 봤다 — 리드가 한 홉씩 걷는
구조의 한계다. 입구 노드의 고리(키 값의 잎 = 클래스 → `code.resources` → 자원 키 읽기)를 **코드가 키마다 반복**하고, 리드는 표를 받는다.

규칙(사람 결정 10-14): 나쁨은 없음·빈 배열·0행뿐 — 손 규칙 없음(단일값 필드는 ③에서 쓴 쪽 레포의 코드 줄이 정한다). 값이 그 클래스를 대는 키는
그 클래스의 **출력**이라 입력에서 뺀다(측정 #9 P0 3: `code.resources`가 발행 명령의 대상 자원 줄을 읽기로 세어 자기 출력이 입력에 섞였다).
나쁜 입력의 쓴 쪽을 알면 한 홉 더, 모르면 거기서 멈춘다(다음 홉은 그 키의 `code.uses`). 입력이 다 정상이면 멈춘다(그 자리의 다음 홉은 ④).
상한 깊이 4·읽기 15·30초(주입 시계) — 상한에서 멈추면 다음 홉은 그 자리의 `code.resources`(리드가 이어 걷는다; 이미 읽은 것이면 ③의 입력 칸
사다리가 이어 받는다). 걸은 읽기는 State에 증거로 남고(판정이 인용한다) 브리핑이 접는다. 던지지 않는다 — 호출부(노드)가 한 번 더 감싼다.
"""
import re
from datetime import datetime
from typing import Any, Callable

from src.application.facts import key_head
from src.application.intake_map import WALK_PREFIX, _finish, _task, _unwrap, writer_of
from src.application.runner_probe import profile
from src.application.state import CaseState
from src.domain.case import EvidenceRef, Hop, PlanTask, Walk
from src.domain.investigation import TaskRunnerPort

MAX_DEPTH = 4
MAX_READS = 15
MAX_SECONDS = 30.0
# 못 채우는 템플릿(대상 키에 없는 자리) — scan 뒤 읽는 실재 키 수.
SCAN_READS = 5

# `code.resources` 본문의 자원 줄 — `    <이름> [<종류>] — <메서드> (<파일>:L<줄>)`. 읽기 구간이 앞, 쓰기 구간이 뒤(`query.resources_lines`).
# 실행기가 repr로 눕힌 텍스트(개행이 `\n` 두 글자)도 받는다 — 브리핑이 증거 본문에서 같은 것을 뽑는다.
_RESOURCE = re.compile(r"(?:\\n|\n) {4}(\S+) \[(rediskey|collection|topic)\] — ")
_WRITES_AT = re.compile(r"(?:\\n|\n) {2}쓰기 \d+:")


def inputs_in(text: str) -> list[tuple[str, str]]:
    """`code.resources` 텍스트에서 그 클래스가 **읽는** 자원 `(이름, 종류)`들 — 쓰기 구간은 뺀다."""
    m = _WRITES_AT.search(text)
    return _RESOURCE.findall(text[:m.start()] if m else text)


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


def status_of(raw: Any) -> tuple[str, bool]:
    """읽은 값의 상태 `(문장, 나쁜가)` — 없음·0행·빈 배열만 나쁘다. 정상이면 행 요약(`profile`)을 같이 — 표 한 줄에서 "554행 두 필드 전부 '-'"가
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
        empties = [k for k, v in value.items() if isinstance(v, list) and not v]
        if empties:
            return f"빈 배열 ({', '.join(empties)})", True
    return "정상", False


async def run_walk(state: CaseState, *, runner: TaskRunnerPort, classes: frozenset[str],
                   class_services: dict[str, tuple[str, ...]] | None = None,
                   clock: Callable[[], datetime] | None = None) -> dict:
    """걷기 노드의 본문 — State 패치(`plan_tasks`·`evidence`·`walk`). 입구가 키와 쓴 쪽을 못 골랐으면 빈 패치."""
    target = state.target
    if target is None or not target.key or not target.writer:
        return {}
    services = class_services or {}
    tasks: list[PlanTask] = []
    evidence: list[EvidenceRef] = []
    started = clock() if clock is not None else None
    counter = 0

    def elapsed() -> float:
        return (clock() - started).total_seconds() if started is not None else 0.0

    def over(writer: str, label: str) -> str:
        """읽기 수·시간 상한에 걸렸으면 그 문장(깊이는 호출부가 본다). 상한은 코드가 쥔다(규율 6)."""
        if counter >= MAX_READS:
            return f"읽기 {MAX_READS} 상한 — {writer}의 {label}부터는 리드가"
        if elapsed() > MAX_SECONDS:
            return f"{MAX_SECONDS:.0f}초 상한 — {writer}의 {label}부터는 리드가"
        return ""

    async def run(goal: str, action: str, params: dict):
        nonlocal counter
        counter += 1
        task = _task(f"{WALK_PREFIX}{counter}", goal, action, params)
        outcome = await runner.run(task, case=state.case)
        done = _finish(task, outcome)
        tasks.append(done)
        evidence.extend(outcome.evidence)
        return done, outcome

    def who(name: str) -> str:
        return ", ".join(services.get(name, ()))

    first = next((t for t in state.plan_tasks if t.id == "t-1" and t.result_evidence_ids), None)
    hops = [Hop(key=target.key, depth=0, writer=target.writer, service=who(target.writer),
                status=f"빈 배열 ({', '.join(target.empty)})" if target.empty else "정상", bad=bool(target.empty),
                evidence_id=first.result_evidence_ids[0] if first else "",
                basis=f"대상 키 — 값의 {target.writer_at}이 {target.writer}")]
    seen = {target.key, target.declared}
    frontier: list[tuple[str, int, str]] = [(target.writer, 0, hops[0].evidence_id)]     # (쓴 클래스, 깊이, 여기로 오게 한 증거)
    unknown: list[tuple[str, str, str]] = []                                                # 쓴 쪽을 모르는 나쁜 입력 — (실재 키, 선언 이름, 증거)
    stopped, nxt, nxt_ev, last = "", None, "", target.writer
    while frontier and not stopped:
        writer, depth, came_from = frontier.pop(0)
        last = writer
        if depth >= MAX_DEPTH:
            stopped, nxt, nxt_ev = f"깊이 {MAX_DEPTH} 상한 — {writer}부터는 리드가", ("code.resources", {"name": writer}), came_from
            break
        if (why := over(writer, "자원")):
            stopped, nxt, nxt_ev = why, ("code.resources", {"name": writer}), came_from
            break
        _, res = await run(f"걷기 — {writer}가 읽고 쓰는 자원", "code.resources", {"name": writer})
        if res.status != "ok" or not res.evidence:
            stopped, nxt, nxt_ev = f"{writer}의 자원을 못 읽었다 — {res.error or '증거 없음'}", None, came_from
            break
        res_id = res.evidence[0].id
        text = runner.raw(res_id)
        for name, kind in inputs_in(text if isinstance(text, str) else ""):
            if kind == "rediskey":
                filled = fill_key(name, declared=target.declared, key=target.key)
                if filled is not None:
                    reads = [("redis.get", {"key": filled}, filled)]
                else:
                    if (why := over(writer, name)):
                        stopped, nxt, nxt_ev = why, ("code.resources", {"name": writer}), came_from
                        break
                    _, scanned = await run(f"걷기 — {writer}의 입력 {name}의 실재 키", "redis.scan", {"pattern": f"{key_head(name)}*"})
                    names = runner.raw(scanned.evidence[0].id) if scanned.status == "ok" and scanned.evidence else None
                    reads = [("redis.get", {"key": k}, k) for k in (names if isinstance(names, list) else [])[:SCAN_READS] if isinstance(k, str)]
            elif kind == "collection":
                reads = [("mongo.find", {"collection": name, "filter": {}, "limit": 5}, name)]
            else:
                reads = [("kafka.tail", {"topic": name, "limit": 5}, name)]
            for action, params, label in reads:
                if label in seen:
                    continue
                if (why := over(writer, label)):
                    stopped, nxt, nxt_ev = why, ("code.resources", {"name": writer}), came_from
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
                hops.append(Hop(key=label, kind=kind, depth=depth + 1, writer=w, service=who(w), status=status, bad=bad, evidence_id=ev,
                                basis=f"{writer}가 읽는 자원 ({res_id})"))
                if bad:
                    if w:
                        frontier.append((w, depth + 1, ev))
                    else:
                        unknown.append((label, name, ev))
            if stopped:
                break
    if not stopped:
        if unknown:
            label, name, ev = unknown[0]
            stopped, nxt, nxt_ev = f"쓴 쪽을 모른다 — {label}(값에 인덱스의 클래스 이름이 없다)", ("code.uses", {"name": name}), ev
        else:
            stopped, nxt, nxt_ev = f"입력이 다 정상 — {last}", None, ""
    return {"plan_tasks": tasks, "evidence": evidence,
            "walk": Walk(hops=hops, reads=counter, seconds=elapsed(), stopped=stopped,
                         next_action=nxt[0] if nxt else "", next_params=nxt[1] if nxt else {}, next_evidence=nxt_ev)}
