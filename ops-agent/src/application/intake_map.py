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
from dataclasses import dataclass
from typing import Any

from src.application.runner_probe import focus_of
from src.application.state import CaseState
from src.domain.actions import role_for
from src.domain.case import EvidenceRef, PlanTask, TargetPick
from src.domain.investigation import TaskOutcome, TaskRunnerPort

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


def target_row(data: Any, *, fields: tuple[str, ...], values: tuple[str, ...]) -> tuple[str, dict] | None:
    """접수 응답 원본에서 identity 필드가 전부 `values`와 같은 첫 행 — `(경로, 행)`. 경로 번호는 0부터(경로 문법과 같다)."""
    for path, rows in _doc_lists(data):
        for i, row in enumerate(rows):
            if all(str(row.get(f)) == v for f, v in zip(fields, values)) and len(fields) == len(values):
                return f"{path}[{i}]", row
    return None


def holds_identity(value: Any, values: tuple[str, ...], path: str = "(맨 위)") -> str | None:
    """값 안에서 **같은 객체**의 잎(문자열·숫자, bool 제외)에 identity 값 전부가 완전 일치하는 자리 — 없으면 None. 첫 자리."""
    if isinstance(value, dict):
        leaves = {str(v) for v in value.values() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
        if values and all(v in leaves for v in values):
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


def _task(task_id: str, goal: str, action: str, params: dict) -> PlanTask:
    return PlanTask(id=task_id, goal=goal, role=role_for(action), action=action, params=params, status="running")


def _finish(task: PlanTask, outcome: TaskOutcome) -> PlanTask:
    return task.model_copy(update={"status": outcome.status, "result_summary": outcome.summary or None,
                                   "result_evidence_ids": [e.id for e in outcome.evidence], "error": outcome.error})


async def run_intake(state: CaseState, spec: IntakeSpec, *, runner: TaskRunnerPort, declared: tuple[str, ...]) -> dict:
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
    values = focus_of(state.case)
    found = target_row(runner.raw(outcome.evidence[0].id), fields=spec.fields, values=values)
    if found is None:
        return patch(TargetPick(problem=f"접수 응답에 대상 행이 없다 — identity {'/'.join(spec.fields)} = {'/'.join(values)} ({outcome.evidence[0].id})"))
    where, row = found
    identity = tuple(str(row.get(f)) for f in spec.fields)

    # 후보 — 리터럴 선언 키 먼저, 템플릿은 머리로 scan해 실재하는 키. 상한을 넘으면 그 사실을 적는다.
    candidates: list[str] = [k for k in declared if "{" not in k]
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
    if not candidates:
        return patch(TargetPick(problem=f"후보 키가 없다 — 접수 끝점이 읽는 선언 키를 모른다(흐름 그래프) ({where} 대상 행은 찾았다)"))

    hits: dict[str, tuple[str, bool]] = {}
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
        at = holds_identity(_unwrap(raw), identity)
        if at:
            hits[key] = (at, got.evidence[0].complete)
    tail = f" — 후보 {MAX_CANDIDATES}개 상한에서 끊었다" if capped else ""
    label = f"대상 identity({'/'.join(identity)})"
    folded = dict(candidates=candidates, read=len(candidates), absent=missing, failed=failed, hits=len(hits))
    if len(hits) == 1:
        (key, (at, complete)), = hits.items()
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
        return patch(TargetPick(key=key, basis=basis, **folded,
                                problem=f"후보 {MAX_CANDIDATES}개 상한에서 끊었다" if capped else ""))
    if not hits:
        gone = f"; 없는 키 {len(missing)}개: {', '.join(missing)}" if missing else ""
        return patch(TargetPick(**folded, problem=f"후보 {len(candidates)}개 중 값에 {label}가 든 키가 없다{gone}{tail}"))
    shown = "; ".join(f"{k} ({at})" for k, (at, _) in hits.items())
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
