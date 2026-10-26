"""접수 끝점의 대상 행 → 대상 키 (R3-1 ①) — **frame 전에 코드가 읽고, 값 안에 identity가 그대로 있는 키 하나를 고른다.**

왜 있는가: 사내 측정 #5에서 흐름 줄의 키 순서를 증상 단어의 유사성으로 정했더니 리드가 이름이 비슷한 다른 배지의 키를 읽어
high로 틀렸다. #6에서 "대상 행의 값 토큰이 든 키"로 바꿨더니 무관한 라벨 필드의 조각이 한 키에만 들어 틀린 키를 사실처럼
박았다. 이름·토큰·선언은 전부 손 규칙이었다. 이제는 **실제 값**으로 판정한다 — 접수 끝점이 읽는 후보 키를 전부 읽고, 그 값의
같은 객체에 대상 행의 identity 값 **전부**가 JSON 잎으로 **완전 일치**하는 키가 정확히 하나면 그 키다(사내 c-1·c-2는 요약 키
값의 `record[0].summary_info`에 배지 행의 group/title이 그대로 있다). 필드 이름은 안 본다 — 키 값 쪽 이름이 행과 같다는 보장이
없다. 0개·여럿이면 고르지 않고 이유와 후보를 적는다(②가 확신을 medium으로 묶는다).

State에 남기는 것은 **결과뿐**이다(R3-1b ①, 측정 #7): `t-0`(접수 읽기)과 `t-1`(고른 키를 다시 읽은 것). 후보 scan·get은 내부 번호
(`t-0-N`)로만 돌고 `TargetPick`의 집계(읽음·있음·없음·일치)로 접힌다 — 후보 29개를 전부 증거로 실었더니 프롬프트가 예산을 넘었고, 증거
예산은 뒤(최근)에서부터 채우므로 가장 오래된 입구 읽기부터 본문을 잃어 **대상 키 본문이 빠졌다**. 리드가 `t-0-19.e1`을 `t-0.19.e1`로
적은 것도 그 두 단계 id 모양 탓이다 — 남는 둘은 리드와 같은 한 단계 모양이라 `next_task_number`가 `t-2`부터 보여 준다. 후보 읽기의
본문은 어디에도 남기지 않는다: State에 접은 채 두려면 증거를 읽는 소비자마다 "접힘"을 배워야 하고, 값은 `peek redis`로 다시 볼 수 있다.
고른 키의 다시 읽기는 통째로이되, 후보 읽기 본문이 상세 예산에서 잘렸으면 identity가 든 객체의 **부모**(`record[0]` — identity와 data가
같이 있는 자리)로 좁힌다. 후보 키는 그래프가 아는 선언 키(리터럴)와 템플릿 머리의 `scan` 결과(개수 상한)다. 대조는 실행기가 든
**원본**(`runner.raw`)으로 한다 — 렌더한 본문은 잘리고 모양이 바뀐다.

B(키가 없을 때 config 잎 → 항목 → 조립식)는 측정 #7 뒤다 — 이유는 plan-12a-r2-r4.md R3-1 표 아래.
"""
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from src.application.recompute import pluck
from src.application.state import CaseState
from src.domain.actions import describe, role_for
from src.domain.case import EvidenceRef, PlanTask, TargetPick
from src.domain.investigation import TaskOutcome, TaskRunnerPort
from src.domain.patrol import target_of

MAX_CANDIDATES = 50
# 증거 `source`는 우리가 만든 `describe` 문자열(`redis.get key='…' path='…'`)이라 그 모양으로 키를 되읽는다. scan은 키 하나를 읽은 것이
# 아니라 세지 않는다.
_KEY_READ = re.compile(r"^redis\.get key='((?:[^'\\]|\\.)*)'")


@dataclass(frozen=True)
class IntakeSpec:
    """입구 노드가 돌 재료 — 접수 읽기(점검이 본 프로브의 `(action, params)`)와 identity 필드 이름. 둘 다 점검 config에서."""
    read: tuple[str, dict]
    fields: tuple[str, ...]


def check_of(case, site_config):
    checks = getattr(getattr(site_config, "patrol", None), "checks", None) or {}
    if not case.check or case.check not in checks:
        return None
    return checks[case.check]


def start_read(case, site_config) -> tuple[str, dict] | None:
    """접수 경로의 첫 칸 — 케이스를 연 판정이 **본** 프로브의 읽기 `(action, params)`. 없으면 None.

    `params.items.probe`가 판정이 본 프로브다(`only_when`의 프로브가 아니라). 리드는 같은 읽기로
    증상을 재현하는 데서 시작한다 — 사내 첫 조사 trace에서 리드가 끝점을 만드는 코드에 한 번도
    가지 않은 것은 이 출발점을 우리가 안 줬기 때문이다. 사람이 적는 표가 아니라 config다(⑮).
    """
    check = check_of(case, site_config)
    if check is None:
        return None
    probe = check.probes.get(check.params.items.probe)
    if probe is None:
        return None
    return probe.action, dict(probe.params)


def intake_spec(case, site_config) -> IntakeSpec | None:
    """순찰이 연 케이스(점검·target이 있고 점검에 identity가 있는 것)만 입구를 돈다. 사람이 연 케이스는 None."""
    read = start_read(case, site_config)
    check = check_of(case, site_config)
    fields = tuple(getattr(getattr(check, "params", None), "identity", None) or ()) if check is not None else ()
    if read is None or not fields or not case.target:
        return None
    return IntakeSpec(read=read, fields=fields)


def _doc_lists(value: Any, path: str = ""):
    """값 안의 문서 목록들 — `(경로, 목록)`. 맨 위가 목록이면 경로는 빈 문자열."""
    if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
        yield path, value
        return
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _doc_lists(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _doc_lists(v, f"{path}[{i}]")


def target_row(data: Any, *, fields: tuple[str, ...], target: str) -> tuple[str, dict] | None:
    """접수 응답 원본에서 identity를 순찰과 같은 함수(`target_of`)로 이은 문자열이 케이스 대상과 **통째로** 같은 첫 행 — `(경로, 행)`. 경로 번호는
    0부터(경로 문법과 같다). 대상 문자열을 쪼개지 않는다(측정 #12: 제목에 '/'가 든 행을 쪼갠 조각 수로 맞춰 보다 놓쳤다)."""
    for path, rows in _doc_lists(data):
        for i, row in enumerate(rows):
            if fields and all(f in row for f in fields) and target_of(row, fields) == target:
                return f"{path}[{i}]", row
    return None


def holds_identity(value: Any, values: tuple[str, ...], path: str = "(맨 위)") -> str | None:
    """값 안에서 **같은 객체**의 잎(문자열·숫자, bool 제외)에 identity 값 전부가 완전 일치하는 자리 — 없으면 None. 첫 자리. 개수까지 센다 — 같은
    값 둘(그룹=제목)이면 그 값이 두 번 든 객체만(측정 #12b P0 2: 집합으로 비교해 그 값이 한 번 든 키 셋과 맞았다)."""
    if isinstance(value, dict):
        leaves = Counter(str(v) for v in value.values() if isinstance(v, (str, int, float)) and not isinstance(v, bool))
        if values and all(leaves[v] >= n for v, n in Counter(values).items()):
            return path
        for k, v in value.items():
            found = holds_identity(v, values, f"{path}.{k}" if path != "(맨 위)" else str(k))
            if found:
                return found
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found = holds_identity(v, values, f"{path}[{i}]" if path != "(맨 위)" else f"[{i}]")
            if found:
                return found
    return None


def _unwrap(raw: Any) -> Any:
    """`redis.get` 원본(`{"type": "string", "value": …}`)에서 값 — 문자열이면 JSON으로 풀어 본다."""
    value = raw.get("value") if isinstance(raw, dict) and "value" in raw else raw
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _parent(path: str) -> str | None:
    """identity가 든 객체 자리의 부모 — `record[0].summary_info` → `record[0]`, `x[0]` → `x`. 맨 위거나 한 단계면 None(통째로)."""
    if path == "(맨 위)":
        return None
    m = re.match(r"^(.*)(\.[^.\[\]]+|\[\d+\])$", path)
    return m.group(1) or None if m else None


def empties(obj: Any, at: str = "") -> tuple[list[str], list[str]]:
    """"비었다"의 판정 하나(R3-5b ②) — `(빈 목록 자리들, 빈 객체 자리들)`. 객체 바로 아래에 행이 든 목록이 하나라도 있으면 둘 다 없음(정상)이고,
    없을 때만 빈 목록·빈 객체 필드를 적는다. 대상 키 홉은 identity의 부모에(`empties_at`), 다른 홉은 값의 맨 위에(`walk.status_of`) 쓴다 — 측정 #12:
    두 자리가 따로 놀아 `record {}`를 정상이라 했다(사람 결정 10-16). 객체가 아니면 없음."""
    if not isinstance(obj, dict) or any(isinstance(v, list) and v and all(isinstance(r, dict) for r in v) for v in obj.values()):
        return [], []

    def place(k) -> str:
        return f"{at}.{k}" if at else str(k)

    return ([place(k) for k, v in obj.items() if isinstance(v, list) and not v],
            [place(k) for k, v in obj.items() if isinstance(v, dict) and not v])


def empty_text(arrays: list[str], objects: list[str]) -> str:
    """빈 자리들의 한 줄 — `빈 배열 (a, b) · 빈 객체 (c)`. 없으면 빈 문자열."""
    parts = ([f"빈 배열 ({', '.join(arrays)})"] if arrays else []) + ([f"빈 객체 ({', '.join(objects)})"] if objects else [])
    return " · ".join(parts)


def empties_at(value: Any, at: str) -> tuple[list[str], list[str]]:
    """identity가 든 객체의 **부모**의 빈 자리(`record[0].data`) — 사내 c-1의 모양(키는 있고 data가 `[]`)을 코드가 사실로 적는다(측정 #7에서 그 빈
    배열을 아무도 못 봤다). 부모가 객체가 아니면 없다."""
    parent = _parent(at)
    return empties(value if parent is None else pluck(value, parent), parent or "")


def _string_leaves(value: Any, path: str = ""):
    """값 안의 문자열 잎 — `(자리, 문자열)`, **문서 목록(행 — dict들의 목록) 밖의 것만**. 자리 문법은 `holds_identity`와 같다(`metadata.event_type`).
    측정 #12b P0 1: 행 안의 화면 라벨이 인덱스의 스키마 클래스 이름과 같아 쓴 쪽으로 붙었다 — 쓴 쪽 이름은 행 밖에 있다."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _string_leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        if value and all(isinstance(r, dict) for r in value):
            return
        for i, v in enumerate(value):
            yield from _string_leaves(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def writer_of(value: Any, classes: frozenset[str]) -> tuple[str, str, str]:
    """`(쓴 쪽 클래스, 자리, 문제)` — 값의 문자열 잎(문서 목록 밖, R3-5c ①) 중 인덱스의 **클래스 이름**과 완전 일치하는 것(R3-1c ①-a). 하나면 그 클래스, 둘 이상
    다른 클래스면 고르지 않고 문제를, 없거나 인덱스가 없으면 셋 다 빈 문자열.

    측정 #8: 자원 줄 뒤의 동사 목록(send·produce…)으로 쓴 쪽을 고르던 규칙은 사내 모양(뒷줄 `save`, 앞줄 클래스 이름)에서 틀렸다. 사내 요약 키
    값의 `metadata.event_type`은 쓴 파이프라인 클래스 이름과 완전 일치한다 — `holds_identity`와 같은 종류의 판정(닫힌 집합과 완전 일치)이고,
    입구가 이미 읽은 값이라 읽기도 늘지 않는다.
    """
    hits = [(at, text) for at, text in _string_leaves(value) if text in classes]
    names = sorted({text for _, text in hits})
    if len(names) == 1:
        return hits[0][1], hits[0][0], ""
    if names:
        return "", "", f"값에 인덱스의 클래스 이름이 {len(names)}개 — 하나로 못 좁힌다: {', '.join(names)}"
    return "", "", ""


def _objects(value: Any, path: str = ""):
    """값 안의 객체들 — `(자리, dict)`, 문서 목록(행) 밖의 것만(`_string_leaves`와 같은 범위)."""
    if isinstance(value, dict):
        yield path or "(맨 위)", value
        for k, v in value.items():
            yield from _objects(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        if value and all(isinstance(r, dict) for r in value):
            return
        for i, v in enumerate(value):
            yield from _objects(v, f"{path}[{i}]")


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def pointer_of(value: Any) -> dict:
    """키를 만든 메시지의 포인터 `{"topic", "partition", "offset", "at"}`(R3-5d ④) — 값 안(문서 목록 밖)의 객체에 문자열 `topic`과 정수 `partition`·`offset`이
    함께 있으면 그것(사내 `metadata.source`, 측정 #13 추가분 2: 법인 둘 122/122키 — 그 오프셋 한 건이 그 키를 만든 메시지). 필드 이름은 그 셋으로 **가정**한다
    — 다르면 빈 dict("포인터 없음")이고 걷기는 토픽을 짐작하지 않는다(사람 결정 10-19). 여럿이면 첫 것."""
    for at, obj in _objects(value):
        topic, partition, offset = obj.get("topic"), _int(obj.get("partition")), _int(obj.get("offset"))
        if isinstance(topic, str) and topic and partition is not None and offset is not None:
            return {"topic": topic, "partition": partition, "offset": offset, "at": at}
    return {}


def next_hop(target: TargetPick | None, *, code_index: bool, walk=None) -> tuple[str, dict] | None:
    """입구 결과로 **코드가 정하는** 다음 홉 — 쓴 쪽을 알면 그 클래스가 읽는 자원(`code.resources <클래스>`, R3-1c ①-a), 모르면 고른 키를 누가
    쓰나(`code.uses <선언 이름>`, R3-1b ③ — 인덱스는 템플릿 이름으로 답하므로 선언 이름이다). 심볼 인덱스가 없으면 없는 문이라 None —
    그때는 옛 사다리(접수 끝점의 사슬)다. 걷기가 돌았으면(R3-3 ②) 다음 홉은 **걷기가 멈춘 자리**다 — 입구 홉에 고정돼 있으면 리드가 코드가
    이미 읽은 자리로 또 간다(측정 #9)."""
    if walk is not None and walk.hops:
        return walk.next_hop() if code_index else None
    if target is None or not target.key or not code_index:
        return None
    if target.writer:
        return "code.resources", {"name": target.writer}
    return ("code.uses", {"name": target.declared}) if target.declared else None


def hop_line(target: TargetPick | None, *, code_index: bool, walk=None) -> str:
    hop = next_hop(target, code_index=code_index, walk=walk)
    if hop is None:
        return ""
    # 인자는 `describe`로 — 홉이 `name` 하나라고 가정했다가 `code.read` 홉(R3-3 ③)에서 frame 노드가 KeyError로 죽었다(측정판).
    if walk is not None and walk.hops:
        return f"다음 홉: {describe(*hop)} — 걷기가 멈춘 자리({walk.stopped})"
    why = "쓴 쪽이 읽는 자원(입력)" if hop[0] == "code.resources" else "이 키를 누가 쓰나(값이 어디서 오나)"
    return f"다음 홉: {describe(*hop)} — {why}"


def intake_lines(state: CaseState, *, code_index: bool, join_notes: bool = True) -> str:
    """케이스 블록의 입구 줄들 — 입구 노드가 이미 읽은 것(다시 내지 마라), identity 옆의 빈 목록, 다음 홉. 측정 #7: 리드의 첫 수가 입구가 이미
    한 읽기라 중복 거부 → 되물어도 중복 → `no_runnable`로 끝났다. 입구가 안 돌았으면 빈 문자열. `join_notes=False`는 판정 블록(R3-5l ①)."""
    target = state.target
    if target is None:
        return ""
    by_id = {t.id: t for t in state.plan_tasks if t.status == "ok" and t.result_evidence_ids}
    done = []
    if "t-0" in by_id:
        done.append(f"접수 읽기({by_id['t-0'].result_evidence_ids[0]})")
    if "t-1" in by_id and target.key:
        done.append(f"대상 키 {target.key}({by_id['t-1'].result_evidence_ids[0]})")
    walked = [t for t in state.plan_tasks if t.walked and t.status == "ok"]
    if walked:
        done.append(f"걷기 읽기 {len(walked)}건(아래 홉 표)")
    lines = []
    if done:
        lines.append("입구 노드가 이미 읽었다: " + " · ".join(done) + " — 같은 읽기를 다시 내지 마라")
    if target.empty or target.empty_objects:
        lines.append("값: " + ", ".join(f"{p}가 비었다" for p in [*target.empty, *target.empty_objects]))
    if target.writer_line():
        lines.append(target.writer_line())
    if state.walk is not None:
        lines += state.walk.lines(join_notes=join_notes)
    hop = hop_line(target, code_index=code_index, walk=state.walk)
    if hop:
        lines.append(hop)
    return "\n".join(lines)


def _task(task_id: str, goal: str, action: str, params: dict) -> PlanTask:
    return PlanTask(id=task_id, goal=goal, role=role_for(action), action=action, params=params, status="running")


def _finish(task: PlanTask, outcome: TaskOutcome) -> PlanTask:
    return task.model_copy(update={"status": outcome.status, "result_summary": outcome.summary or None,
                                   "result_evidence_ids": [e.id for e in outcome.evidence], "error": outcome.error})


async def run_intake(state: CaseState, spec: IntakeSpec, *, runner: TaskRunnerPort, declared: tuple[str, ...],
                     classes: frozenset[str] = frozenset()) -> dict:
    """입구 노드의 본문 — State 패치(`plan_tasks`·`evidence`·`target`). 던지지 않는다(호출부가 한 번 더 감싼다)."""
    from src.application.facts import key_head

    tasks: list[PlanTask] = []
    evidence: list[EvidenceRef] = []
    counter = 0

    async def run(goal: str, action: str, params: dict, *, keep: bool, task_id: str | None = None):
        # 후보 읽기는 내부 번호 `t-0-1`, `t-0-2`… — 실행기의 원본 보관에만 쓰이고 State에는 안 남는다(`keep=False`). State에 남는 둘
        # (`t-0`·`t-1`)은 리드의 번호와 같은 한 단계 모양이다.
        nonlocal counter
        if task_id is None:
            counter += 1
            task_id = f"t-0-{counter}"
        task = _task(task_id, goal, action, params)
        outcome = await runner.run(task, case=state.case)
        done = _finish(task, outcome)
        if keep:
            tasks.append(done)
            evidence.extend(outcome.evidence)
        return done, outcome

    def patch(pick: TargetPick) -> dict:
        return {"plan_tasks": tasks, "evidence": evidence, "target": pick}

    action, params = spec.read
    done, outcome = await run("접수 읽기 — 증상 재현(코드)", action, params, keep=True, task_id="t-0")
    if outcome.status != "ok" or not outcome.evidence:
        return patch(TargetPick(problem=f"접수 읽기 실패 — {outcome.error or '증거 없음'}"))
    wanted = state.case.target or ""
    found = target_row(runner.raw(outcome.evidence[0].id), fields=spec.fields, target=wanted)
    if found is None:
        return patch(TargetPick(problem=f"접수 응답에 대상 행이 없다 — identity {'/'.join(spec.fields)} = {wanted} ({outcome.evidence[0].id})"))
    where, row = found
    identity = tuple(str(row.get(f)) for f in spec.fields)

    # 후보 — 리터럴 선언 키 먼저, 템플릿은 머리로 scan해 실재하는 키. 상한을 넘으면 그 사실을 적는다.
    candidates: list[str] = [k for k in declared if "{" not in k]
    declared_of: dict[str, str] = {k: k for k in candidates}       # 실재 키 → 그 키가 나온 선언(템플릿) — 다음 홉의 `code.uses` 이름
    capped = False
    for template in (k for k in declared if "{" in k):
        if len(candidates) >= MAX_CANDIDATES:
            capped = True
            break
        head = key_head(template)
        _, scanned = await run(f"대상 키 후보 — {template}의 실재 키", "redis.scan", {"pattern": f"{head}*"}, keep=False)
        names = runner.raw(scanned.evidence[0].id) if scanned.status == "ok" and scanned.evidence else None
        for name in (names if isinstance(names, list) else []):
            if len(candidates) >= MAX_CANDIDATES:
                capped = True
                break
            if isinstance(name, str) and name not in candidates:
                candidates.append(name)
                declared_of[name] = template
    if not candidates:
        return patch(TargetPick(problem=f"후보 키가 없다 — 접수 끝점이 읽는 선언 키를 모른다(흐름 그래프) ({where} 대상 행은 찾았다)"))

    hits: dict[str, tuple[str, bool, Any]] = {}                    # 키 → (identity 자리, 본문이 다 실렸나, 후보 읽기의 전체 값)
    missing: list[str] = []
    failed = 0
    for key in candidates:
        _, got = await run(f"대상 키 후보 읽기 — {key}", "redis.get", {"key": key}, keep=False)
        if got.status != "ok" or not got.evidence:
            failed += 1
            continue
        raw = runner.raw(got.evidence[0].id)
        if raw is None:
            missing.append(key)
            continue
        value = _unwrap(raw)
        at = holds_identity(value, identity)
        if at:
            hits[key] = (at, got.evidence[0].complete, value)
    tail = f" — 후보 {MAX_CANDIDATES}개 상한에서 끊었다" if capped else ""
    label = f"대상 identity({'/'.join(identity)})"
    folded = dict(candidates=candidates, read=len(candidates), absent=missing, failed=failed, hits=len(hits))
    if len(hits) == 1:
        (key, (at, complete, value)), = hits.items()
        # 고른 키를 **State에 남는 읽기**로 한 번 더 — 후보 읽기 본문이 잘렸으면 identity 객체의 부모로 좁혀 data까지 통째로.
        narrow = None if complete else _parent(at)
        goal = f"대상 키 읽기 — {key}" + (f" (identity 자리 {at}의 부모 {narrow}로 좁혀)" if narrow else "")
        _, again = await run(goal, "redis.get", {"key": key, **({"path": narrow} if narrow else {})}, keep=True, task_id="t-1")
        raw = runner.raw(again.evidence[0].id) if again.status == "ok" and again.evidence else None
        at2 = holds_identity(_unwrap(raw), identity) if raw is not None else None
        if at2:
            basis = f"키 값의 {at2}에 {label}가 그대로 있다 ({again.evidence[0].id})"
        else:
            basis = f"키 값의 {at}에 {label}가 그대로 있다 (다시 읽기 실패: {again.error or '증거 없음'})"
        # 쓴 쪽은 후보 읽기의 **전체 값**에서 — 좁힌 다시 읽기는 identity의 부모만 돌려줘 형제 자리(`metadata.event_type`)가 빠진다(측정 #12: data가
        # 커서 잘린 16행이 "쓴 쪽: 모른다"였다). 원본은 실행기가 잘리지 않은 채 들고 있다.
        writer, writer_at, writer_problem = writer_of(value, classes)
        # 빈 자리도 후보 읽기의 전체 값에서 — 자리 문법이 전체 값 기준 하나다(좁힌 값이면 `record[0].data`가 `data`가 됐다).
        arrays, objects = empties_at(value, at)
        return patch(TargetPick(key=key, basis=basis, declared=declared_of.get(key, key),
                                empty=arrays, empty_objects=objects, pointer=pointer_of(value),
                                writer=writer, writer_at=writer_at, writer_problem=writer_problem,
                                writer_searched=bool(classes), **folded,
                                problem=f"후보 {MAX_CANDIDATES}개 상한에서 끊었다" if capped else ""))
    if not hits:
        gone = f"; 없는 키 {len(missing)}개: {', '.join(missing)}" if missing else ""
        return patch(TargetPick(**folded, problem=f"후보 {len(candidates)}개 중 값에 {label}가 든 키가 없다{gone}{tail}"))
    shown = "; ".join(f"{k} ({at})" for k, (at, _, _) in hits.items())
    return patch(TargetPick(**folded, problem=f"값에 {label}가 든 키가 {len(hits)}개 — 하나로 못 좁힌다: {shown}{tail}"))


def cited_keys(evidence_ids, evidence) -> list[str]:
    """인용한 증거 중 **키 읽기**(`redis.get`)의 키들 — 인용 순서대로, 같은 키는 한 번."""
    by_id = {ref.id: ref for ref in evidence}
    found: list[str] = []
    for evidence_id in evidence_ids:
        ref = by_id.get(evidence_id)
        match = _KEY_READ.match(ref.source) if ref is not None else None
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found
