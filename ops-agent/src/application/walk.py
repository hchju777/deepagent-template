"""R3-3 ② — 코드가 상류를 걷는다.

측정 #9: 쓴 쪽 링크·pinned·예시 한 칸이 작동해도 c-1은 원천 키까지 세 홉이 걸렸고, c-2는 첫 나쁜 값을 읽고도 못 봤다 — 리드가 한 홉씩 걷는
구조의 한계다. 입구 노드의 고리(키 값의 잎 = 클래스 → `code.resources` → 자원 키 읽기)를 **코드가 키마다 반복**하고, 리드는 표를 받는다.

규칙(사람 결정 10-14): 나쁨은 없음·빈 배열·0행뿐 — 손 규칙 없음(단일값 필드는 ③에서 쓴 쪽 레포의 코드 줄이 정한다). 값이 그 클래스를 대는 키는
그 클래스의 **출력**이라 입력에서 뺀다(측정 #9 P0 3: `code.resources`가 발행 명령의 대상 자원 줄을 읽기로 세어 자기 출력이 입력에 섞였다).
나쁜 입력의 쓴 쪽을 알면 한 홉 더, 모르면 거기서 멈춘다(다음 홉은 그 키의 `code.uses`). 입력이 다 정상이면 멈춘다(그 자리의 다음 홉은 ④).
같은 클래스 이름이 두 레포에 있으면 **레포 단위**로 가른다(R3-4 ①, 측정 #10 P0 2) — 한 레포를 여러 서비스가 `path` 없이 공유하면 표에
`<레포>(서비스 N개 공유)`로 적고 그 레포를 `code.resources repo=`로 넘긴다. 한 가지의 `code.resources` 실패는 그 행에만 적고 다른 가지는 계속 걷는다.
상한 깊이 4·읽기 15·30초(주입 시계) — 상한에서 멈추면 다음 홉은 그 자리의 `code.resources`(리드가 이어 걷는다; 이미 읽은 것이면 ③의 입력 칸
사다리가 이어 받는다). 걸은 읽기는 State에 증거로 남고(판정이 인용한다) 브리핑이 접는다. 던지지 않는다 — 호출부(노드)가 한 번 더 감싼다.
"""
import re
from datetime import datetime
from typing import Any, Callable, NamedTuple

from src.application.facts import key_head
from src.application.intake_map import WALK_PREFIX, _finish, _task, _unwrap, writer_of
from src.application.runner_probe import _same_valued, profile
from src.application.state import CaseState
from src.domain.case import EvidenceRef, Hop, PlanTask, Walk
from src.domain.investigation import TaskRunnerPort

MAX_DEPTH = 4
MAX_READS = 15
MAX_SECONDS = 30.0
# 못 채우는 템플릿(대상 키에 없는 자리) — scan 뒤 읽는 실재 키 수.
SCAN_READS = 5
# 단일값 필드의 나쁨은 **코드 줄**이 정한다(R3-3 ③, 사람 결정 10-14 — 자리표시자 목록 같은 손 규칙은 두지 않는다): 입력을 읽는 쪽(걷고 있는 클래스)의
# 레포에서 필드 이름과 그 값이 한 줄에 **리터럴로** 같이 있으면(실측 `["<필드>"] != "-"`) 코드가 그 값을 거르는 것이고, 모든 행이 그 값이면 아무것도
# 안 남는다. 한 홉에 검사할 단일값 필드 수 상한 — grep 한 번이 읽기 한 번이다.
LITERAL_FIELDS = 3
# 머리는 커밋(SHA 또는 `main` 같은 ref)이고 없을 수도 있다 — 경로 뒤 `:줄:`이 기준이다.
_GREP_LINE = re.compile(r"^(?:[^:\s]+:)?([^:\n]+):(\d+):(.*)$")


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


def literal_lines(text: str, field: str, value: str) -> list[tuple[str, int, str]]:
    """`code.grep` 결과(`<커밋>:경로:줄:본문`)에서 필드 이름과 값이 **둘 다 따옴표 리터럴**로 든 줄 — `(경로, 줄, 본문)`. 변수 이름으로 쓰인 필드
    (`if wg != "-"`)는 안 센다 — 데이터 필드 이름이 코드에 문자열로 있어야 같은 것이다."""
    out = []
    for line in text.splitlines():
        m = _GREP_LINE.match(line)
        if not m:
            continue
        body = m.group(3)
        if any(q + field + q in body for q in ("'", '"')) and any(q + value + q in body for q in ("'", '"')):
            out.append((m.group(1), int(m.group(2)), body.strip()))
    return out

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
                   clock: Callable[[], datetime] | None = None) -> dict:
    """걷기 노드의 본문 — State 패치(`plan_tasks`·`evidence`·`walk`). 입구가 키와 쓴 쪽을 못 골랐으면 빈 패치."""
    target = state.target
    if target is None or not target.key or not target.writer:
        return {}
    services = class_services or {}
    repos = service_repos or {}
    owners = declared_by or {}
    notes: list[str] = []
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

    def where(repo: str, svcs: tuple[str, ...]) -> dict:
        return {"service": svcs[0]} if len(svcs) == 1 else {"repo": repo}

    def who(name: str) -> str:
        return ", ".join(shown_as(r, s) for r, s in homes(name))

    def branches(writer: str, template: str) -> tuple[list[_Branch], str, tuple[str, ...]]:
        """→ (걸을 가지들, 표에 적을 이름, 판정의 component 후보 서비스들). 한 레포뿐이면 인자 없이(리드의 중복이 같은 질의여야 잡힌다). 레포
        둘이면 **그 키를 선언한 서비스가 있는 레포**로 가른다(측정 #9 P1 4: `파일:qualname`도 같아 그걸로는 못 가른다 → 측정 #10 P0 2: 서비스가
        아니라 **레포** 단위 — 공유 레포의 서비스들은 같은 config를 읽어 선언으로 서로를 못 가르고, 가른 레포를 `code.resources`에 넘겨야 같은
        이름 둘 중 하나가 열린다; 사람 결정: 후보끼리 가를 때만, 표에 근거). 둘 다 선언이거나 선언이 없으면 둘 다 걷는다 — 후보도 전부."""
        found = homes(writer)
        if len(found) <= 1:
            svcs = found[0][1] if found else ()
            return [_Branch({}, "", svcs)], who(writer), svcs
        declared = set(owners.get(template, ()))
        picked = [(r, s) for r, s in found if declared & set(s)]
        shown = ", ".join(shown_as(r, s) for r, s in found)
        if len(picked) == 1:
            repo, svcs = picked[0]
            notes.append(f"{writer}는 레포 {len(found)}곳({shown})에 있다 — {template}을 선언한 {shown_as(repo, svcs)}로 골랐다")
            return [_Branch(where(repo, svcs), shown_as(repo, svcs), svcs)], shown_as(repo, svcs), svcs
        notes.append(f"{writer}는 레포 {len(found)}곳({shown})에 있다 — {template}을 선언한 쪽으로 못 가른다"
                     f"({'둘 다 선언' if picked else '선언 없음'}): 둘 다 걷는다")
        return [_Branch(where(r, s), shown_as(r, s), s) for r, s in found], shown, tuple(x for _, s in found for x in s)

    first = next((t for t in state.plan_tasks if t.id == "t-1" and t.result_evidence_ids), None)
    first_branches, first_shown, first_cands = branches(target.writer, target.declared)
    hops = [Hop(key=target.key, depth=0, writer=target.writer, service=first_shown, candidates=list(first_cands),
                status=f"빈 배열 ({', '.join(target.empty)})" if target.empty else "정상", bad=bool(target.empty),
                evidence_id=first.result_evidence_ids[0] if first else "",
                basis=f"대상 키 — 값의 {target.writer_at}이 {target.writer}")]
    seen = {target.key, target.declared}
    # (쓴 클래스, 가지 — 인자·꼬리표·후보, 깊이, 여기로 오게 한 증거, 이어 볼 단일값 `(필드, 값)` — ③)
    frontier: list[tuple[str, _Branch, int, str, tuple[str, str] | None]] = [(target.writer, b, 0, hops[0].evidence_id, None) for b in first_branches]
    unknown: list[tuple[str, str, str]] = []                                                # 쓴 쪽을 모르는 나쁜 입력 — (실재 키, 선언 이름, 증거)
    failed: list[tuple[str, str, str]] = []                                                 # 자원을 못 읽은 가지 — (쓴 쪽[꼬리표], 사유, 증거)
    literal: tuple[str, str, int, str] | None = None                                        # 첫 리터럴 줄 — (서비스, 경로, 줄, grep 증거)
    stopped, nxt, nxt_ev, last = "", None, "", target.writer
    while frontier and not stopped:
        writer, branch, depth, came_from, watch = frontier.pop(0)
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
            failed.append((f"{writer}{tag}", why, came_from))
            continue
        res_id = res.evidence[0].id
        text = runner.raw(res_id)
        for name, kind in inputs_in(text if isinstance(text, str) else ""):
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
                    reads = [("redis.get", {"key": k}, k) for k in (names if isinstance(names, list) else [])[:SCAN_READS] if isinstance(k, str)]
            elif kind == "collection":
                reads = [("mongo.find", {"collection": name, "filter": {}, "limit": 5}, name)]
            else:
                reads = [("kafka.tail", {"topic": name, "limit": 5}, name)]
            for action, params, label in reads:
                if label in seen:
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
                # 단일값 필드(③) — 아래 홉에서 잡힌 `(필드, 값)`이 여기도 그대로면 나쁨(grep 없이 이어진다). 아니면 읽는 쪽 코드 줄이 정한다.
                carry = None
                same = {k: v for k, v in _same_valued(rows_of(raw) if raw is not None else []) if isinstance(v, str)}
                if not bad and watch is not None and same.get(watch[0]) == watch[1]:
                    status, bad, carry = f"나쁨 — 같은 단일값 {watch[0]}={watch[1]!r} (아래 홉과 같다)", True, watch
                elif not bad and same and status.startswith("정상"):
                    for field, value in list(same.items())[:LITERAL_FIELDS]:
                        if (why := over(writer, f"{label}의 단일값 {field}")):
                            stopped, nxt, nxt_ev = why, ("code.resources", here), came_from
                            break
                        _, grepped = await run(f"걷기 — {writer}{tag} 코드에서 {field}={value!r} 리터럴 줄", "code.grep",
                                               {"patterns": [field], **({"service": scope} if scope else {})})
                        gev = grepped.evidence[0].id if grepped.evidence else ""
                        text = runner.raw(gev) if grepped.status == "ok" and gev else None
                        found = literal_lines(text, field, value) if isinstance(text, str) else []
                        if found:
                            path, line, body = found[0]
                            status, bad, carry = (f"나쁨 — 모든 행이 {field}={value!r}, 코드가 거른다: {path}:L{line} `{body}` ({gev})", True,
                                                  (field, value))
                            if literal is None:
                                literal = (scope, path, line, gev)
                            break
                # 나쁜 입력의 쓴 쪽은 **그 입력 키의 선언 이름**으로 가른다 — 대상 키를 선언한 쪽이 상류 키까지 선언했으리란 법은 없다.
                next_branches, shown, cands = (branches(w, name) if (bad and w)
                                               else ([], who(w), tuple(x for _, s in homes(w) for x in s)))
                hops.append(Hop(key=label, kind=kind, depth=depth + 1, writer=w, service=shown, candidates=list(cands), status=status, bad=bad,
                                evidence_id=ev, basis=f"{writer}{tag}가 읽는 자원 ({res_id})"))
                if stopped:
                    break
                if bad:
                    if w:
                        frontier += [(w, b, depth + 1, ev, carry) for b in next_branches]
                    else:
                        unknown.append((label, name, ev))
            if stopped:
                break
    if not stopped:
        if unknown:
            label, name, ev = unknown[0]
            stopped, nxt, nxt_ev = f"쓴 쪽을 모른다 — {label}(값에 인덱스의 클래스 이름이 없다)", ("code.uses", {"name": name}), ev
        elif failed:
            name, why, ev = failed[0]
            stopped, nxt, nxt_ev = f"{name}의 자원을 못 읽었다 — {why}", None, ev
        else:
            stopped, nxt, nxt_ev = f"입력이 다 정상 — {last}", None, ""
        if literal is not None:
            # 아래에 단일값을 거르는 코드 줄이 있었다 — 멈춘 이유가 무엇이든 리드가 볼 곳은 **그 줄을 품은 함수**다(사람 결정; `code.read line=`이
            # 인덱스의 심볼 줄 범위로 함수 전체를 준다 — ④-a). 읽는 쪽 서비스를 모르면 `code.read`를 못 만들어 위의 홉 그대로.
            scope, path, line, gev = literal
            stopped += f" (단일값을 거르는 코드 줄 {path}:L{line})"
            if scope:
                nxt, nxt_ev = ("code.read", {"service": scope, "path": path, "line": line}), gev
    return {"plan_tasks": tasks, "evidence": evidence,
            "walk": Walk(hops=hops, reads=counter, seconds=elapsed(), stopped=stopped,
                         next_action=nxt[0] if nxt else "", next_params=nxt[1] if nxt else {}, next_evidence=nxt_ev, notes=notes)}
