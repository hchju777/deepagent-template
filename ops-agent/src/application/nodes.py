"""노드 하나하나가 하는 일과, **코드가 쥐는 통제 경계**.

`frame`·`integrate`는 **주입받는 함수**다 — 10b부터 그 자리에 리드 LLM이 들어온다
(`lead.py`). 그래서 이 파일은 LLM을 모르고, 여기 있는 것은 전부 코드가 고정한 규율이다.
**LLM이 오기 전에 경계가 먼저 있었다**는 것이 중요하다: 나중에 얹은 소독은 "그때는
필요 없던 것"이 아니라 "그동안 뚫려 있던 것"이다.

| 무엇 | 어디 |
|---|---|
| 라운드 상한 | `integrate` — 상한에 닿으면 LLM의 결정을 무시하고 끝낸다 |
| 병렬 폭 | `select` — 골라 낸 태스크를 잘라서 `running`으로 굴린다 |
| 실행 가능 판정 | `runnable_tasks` — 입력 증거가 **전부** 실재해야 |
| 태스크 개수 상한 | `frame`·`integrate` |
| LLM 출력 소독 | `_sanitize_task` — 수명주기 필드를 코드가 덮어쓴다 |
| 증거 인용 검사 | `_accept_hypotheses` — 실재하지 않는 id를 걷어낸다 |
| 태스크 id 재사용 | `_accept_tasks` — 이미 있는 id는 안 받는다 |
| **같은 질의 반복** | `_accept_tasks` — `action`+`params`가 같으면 안 받는다 |
| 예외 흡수 | `execute`·`conclude` 최외곽 |
| **판정은 항상 생긴다** | `conclude` — 조사가 안 돌았으면 LLM을 묻지 않고 `degraded` |
| **판정의 인용·부품 검사** | `verify` — LLM 없음. 한 번 되묻고, 그래도 안 되면 걷어내고 강등 |

## 노드는 `(state) -> dict`다

LangGraph 타입을 아는 것은 `graph.py` 하나뿐이고, 여기는 State를 받아 **갱신할 조각만**
담은 dict를 돌려주는 평범한 함수들이다. 테스트가 그래프 없이 노드를 부를 수 있고,
루프 런타임을 갈아끼워도 이 파일은 안 바뀐다.

## select가 **노드**인 이유

고르는 일을 라우터에서 바로 하면 "이번 라운드에 무엇을 왜 골랐나"가 어디에도 안
남는다. 노드가 고른 것을 `running`으로 굴려 State에 적으면 체크포인트에 남고,
라우터는 그걸 읽기만 한다 — 판단과 배선이 갈린다.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from src.application.facts import facts_for, present
from src.application.intake_map import IntakeSpec, cited_keys, hop_line, run_intake
from src.application.state import CaseState, merge_by_id
from src.domain.actions import ACTIONS, DISCOVERED_ARGS, action_problem, describe, name_known
from src.domain.case import Case, CauseLink, EvidenceRef, Hypothesis, PlanTask, TargetPick, Verdict
from src.domain.investigation import TaskOutcome, TaskRunnerPort

# 이 자리에 리드 LLM(`lead.make_lead`)이나 테스트의 대본이 들어온다.
NodeFn = Callable[[CaseState], Awaitable[dict]]


@dataclass(frozen=True)
class EngineDeps:
    runner: TaskRunnerPort
    frame: NodeFn
    integrate: NodeFn
    max_rounds: int
    parallel_width: int
    max_tasks: int
    # 태스크가 댄 이름을 "실제로 찾았는가"로 검사할지. **사람이 쓴 대본은 끈다** —
    # `case dryrun`의 계획 파일은 시스템을 아는 사람이 이름을 알고 적은 것이라,
    # 켜 두면 기록이 거짓 양성으로만 찬다. 리드 LLM 경로에서만 뜻이 있다.
    check_discovery: bool = True
    # 리드가 낸 것을 코드가 **거부**했을 때 같은 라운드에서 한 번 되묻는가. 두 번째 전체
    # 트레이스에서 리드가 질의를 좁힌 유일한 계기는 `<버려진 태스크>`에서 자기 질의가
    # 그 값 그대로 버려진 것을 본 일이었고, 그게 늘 **한 라운드 뒤**였다. 대본 경로
    # (`dryrun`)는 끈다 — 되물으면 대본의 다음 라운드를 당겨 먹는다.
    redo_on_rejection: bool = True
    # 브리핑이 리드에게 준 이름들(`<데이터 흐름>` 블록의 그래프 이름, 한 줄에 하나).
    # "찾지 않고 이름을 댔다" 검사는 증거와 이것을 합쳐 본다 — 블록이 준 컨슈머 그룹
    # 이름을 쓴 것을 찍었다고 적으면 계측기가 블록에 **불리한** 거짓 양성을 내고(3b 측정에서
    # 매 판 그랬다), 그 숫자로는 블록의 효과를 잴 수 없다. 그래프가 없으면 빈 문자열이라
    # 검사는 예전과 같다.
    known_names: str = ""
    # 12a — 판정자. 리드 경로에서는 `lead.make_lead`의 셋째 함수, 대본 경로에서는 대본의 `verdict`.
    # None이면 conclude가 "판정자가 없다"는 degraded를 찍는다 — 조용히 None 판정이 아니다.
    conclude: NodeFn | None = None
    # 판정의 `component`가 가리킬 수 있는 부품 — 토폴로지의 서비스 이름. 증거 본문과 그래프
    # 이름(`known_names`)은 `_universe`가 더하므로 여기엔 서비스만 온다.
    components: frozenset = frozenset()
    # 심볼 인덱스가 있나 — 다음 홉(`code.uses`, R3-1b ③)은 없는 문을 가리키면 안 된다. 브리핑이 받는 것과 같은 값.
    code_index: bool = False
    # 흐름 그래프가 선언한 이름, 종류별(`rediskey` → 접수 끝점이 읽는 키 템플릿, 없으면 config의 전부). 발견 읽기의 결과와
    # 대조해 "선언됐는데 없는 키"를 사실로 남긴다(`facts.py`). 그래프가 없으면 비어 있고 아무 사실도 안 남는다.
    declared: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # R3-1 ① — 입구 노드의 재료(접수 읽기·identity 필드, `intake_map.intake_spec`). None이면(사람이 연 케이스, 대본) 입구를 안 돈다.
    intake: IntakeSpec | None = None


# 되물을 때 `<버려진 태스크>` 줄에 붙는 머리말. 리드에게는 "방금 낸 답"이라는 뜻이고,
# 트레이스 요약에는 "이 파일은 JSON 재시도가 아니라 거부 뒤 되물음"이라는 표식이다.
REDO_MARK = "방금 낸 답의 "
# 되물은 뒤 State에 남는 첫 답의 거부 기록에 붙는 꼬리 — 진단이 되물음 횟수를 셀 수 있다.
REDO_NOTE = " (그 자리에서 다시 물었다)"
# 대기 태스크는 있는데 돌릴 것이 없을 때 리드에게 되묻는 기록의 종류(R3-1 ②(c)) — State에 남아 "케이스당 한 번"의 표식이 된다.
# 다른 기록과 같은 모양(`t-3: 종류 — 자세히`)이어야 진단이 종류를 센다(보드에서 `1× t-3(막힘`으로 깨져 보였다).
BLOCKED_MARK = "막힘 — 대기 태스크가 못 돈다"
# 중복 거부 기록의 두 조각 — `_rejected_reads`가 앞 라운드의 기록에서 "그 id의 결과는 어디"를 되읽는다. 모양을 바꾸면 둘 다 바꿔라.
DUP_KIND = ": 이미 한 읽기를 또 냈다"
DUP_WHERE = " — 그 결과는 "


def _sanitize_task(task: PlanTask) -> PlanTask:
    """만들어진 태스크의 수명주기 필드를 **코드가 강제로 초기화한다**(규율 4).

    리드 LLM이 `{"status": "ok", "result_evidence_ids": ["ev-9"]}`를 실어 보내면
    그 태스크는 실행되지 않은 채 끝난 것이 되고 select 게이트를 통째로 우회한다.
    10a에서 LLM이 오기 **전에** 이 경계를 먼저 세운 이유다.
    """
    # 원천 재집계(11b 3b)의 `expect.evidence`는 **입력 증거**다 — 리드가 `input_evidence_ids`를 비워 내도
    # 코드가 넣는다. 안 넣으면 select 게이트가 그 증거가 생기기 전에 돌려 "기대값을 못 찾았다"로 끝난다.
    ids = list(task.input_evidence_ids)
    expect = task.params.get("expect") if isinstance(task.params, dict) else None
    if isinstance(expect, dict) and isinstance(expect.get("evidence"), str) and expect["evidence"] not in ids:
        ids.append(expect["evidence"])
    return task.model_copy(update={"status": "pending", "result_summary": None,
                                   "result_evidence_ids": [], "error": None,
                                   "input_evidence_ids": ids})


def _accept_hypotheses(patch: dict, *, have: set[str]) -> tuple[list[Hypothesis], list[str]]:
    """리드가 만든 가설에서 **실재하지 않는 증거 인용을 걷어낸다**(규율 3).

    리드가 적은 증거 id는 환각일 수 있다. 그냥 들이면 다음 라운드의 브리핑이
    `<가설>` 블록에 그 id를 실어 보내고, 리드는 **자기가 지어낸 id를 근거로 다시
    추론한다.** 라운드마다 복리로 불어나는 종류의 오류라 루프가 있는 지금 막아야 한다.

    인용이 하나도 안 남으면 판정을 `open`으로 되돌린다 — 근거가 전부 사라진
    "supported"는 근거 없는 단정이고, 그게 12a의 최종 판정 재료가 된다.

    걷어낸 사실은 `llm_errors`에 남긴다. 조용히 고치면 프롬프트가 안 먹히고 있다는
    것을 아무도 모른다.
    """
    kept, complaints = [], []
    for h in patch.get("hypotheses", []):
        ghosts = [e for e in h.supporting_ids + h.refuting_ids if e not in have]
        if not ghosts:
            kept.append(h)
            continue
        supporting = [e for e in h.supporting_ids if e in have]
        refuting = [e for e in h.refuting_ids if e in have]
        demoted = h.status if (supporting or refuting) else "open"
        # **무엇이 맞는 모양인지 같이 말한다.** 이 메시지는 다음 라운드의
        # `<버려진 태스크>`로 리드에게 돌아간다 — "틀렸다"만 알려 주면 같은 형식으로
        # 다시 틀린다. 사내 측정에서 태스크 id(`t-5`)를 증거 id로 썼다.
        complaints.append(f"{h.id}: 없는 증거를 인용했다 — {', '.join(ghosts)}"
                          + ("" if demoted == h.status
                             else f" (판정을 {h.status}→open으로 되돌렸다)")
                          + ". 증거 id는 `t-3.e1` 모양이다 — 태스크 id가 아니다")
        kept.append(h.model_copy(update={"supporting_ids": supporting,
                                         "refuting_ids": refuting, "status": demoted}))
    return kept, complaints


def _seen(state: CaseState) -> str:
    """지금까지 본 증거를 이어 붙인 것 — "이 이름을 실제로 찾았는가"의 근거.

    **`body`를 본다. `summary`가 아니다.** 리드가 읽는 것이 `body`이므로, 우리가
    `summary`(160자)로 판정하면 **리드가 실제로 본 이름을 "찍었다"고 적는다.**

    사내에서 실제로 났다: 토픽 179개 중 리드는 `body`에 실린 78개를 보고 골랐는데,
    `summary`에는 9개만 들어가 있어 거짓 양성이 기록됐다. 계측기가 거짓 양성을 내면
    그 숫자로 "막을지 말지"를 정할 수 없다 — 계측기의 존재 이유가 사라진다.

    `summary`로 떨어지는 것은 `body`가 없는 증거(다른 생산자)를 위해서다.
    """
    return "\n".join(f"{ref.source} {ref.body or ref.summary}"
                     for ref in state.evidence)


def _universe(state: CaseState, deps: "EngineDeps") -> str:
    """리드가 이름을 알 수 있었던 곳 전부 — 본 증거와 브리핑이 준 그래프 이름."""
    return _seen(state) + "\n" + deps.known_names


def _taken(state: CaseState) -> frozenset:
    return frozenset(t.id for t in state.plan_tasks)


def _pending(state: CaseState) -> frozenset:
    return frozenset(t.id for t in state.plan_tasks if t.status == "pending")


def _waiting(state: CaseState) -> dict[str, PlanTask]:
    """질의 → 아직 안 돈 태스크. 같은 질의를 새 id로 다시 내면 **그 태스크의 갱신**이다."""
    return {_query(t): t for t in state.plan_tasks if t.status == "pending" and t.action}


def _done(state: CaseState) -> frozenset:
    return frozenset(_query(t) for t in state.plan_tasks if t.action)


def _accept_problem(task: PlanTask) -> str | None:
    """받을 때의 등재·인자 검사 한 줄 — 통과하면 None. 받는 인자 목록을 붙인다(선택 인자는 `?`)."""
    if not task.action:
        return "action이 없다 — 등재된 읽기 하나를 적어라"
    problem = action_problem(task.action, task.params if isinstance(task.params, dict) else {})
    if problem is None:
        return None
    spec = ACTIONS.get(task.action)
    hint = f" (인자: {', '.join([*spec[2], *(f'{name}?' for name in spec[3])])})" if spec else ""
    return problem + hint


def _intake_ids(state: CaseState) -> frozenset:
    """입구 노드가 남긴 증거 id들(`t-0`·`t-1`) — 이 결과의 중복에만 다음 홉을 붙인다."""
    if state.target is None:
        return frozenset()
    return frozenset(e for t in state.plan_tasks if t.id in ("t-0", "t-1") for e in t.result_evidence_ids)


def _rejected_reads(state: CaseState) -> dict[str, list[str]]:
    """앞 라운드에서 중복으로 거부된 태스크 id → 그 읽기의 증거 id들. 기록(`llm_errors`)에서 되읽는다.

    입구 노드가 접수를 먼저 읽으므로 리드의 frame 답 "증상 재현"은 으레 거부되고, 리드는 다음 라운드에 그 id를 입력으로 댄다(측정판에서
    그랬다). State에 따로 두지 않는 이유: 기록이 이미 그 사실(`그 결과는 t-0.e1`)을 담고 있고, 같은 사실을 두 곳에 두면 어긋난다.
    """
    out: dict[str, list[str]] = {}
    for line in state.llm_errors:
        head, kind, tail = line.removesuffix(REDO_NOTE).partition(DUP_KIND)
        if kind and _TASK_ID.match(head) and DUP_WHERE in tail:
            out[head] = tail.rsplit(DUP_WHERE, 1)[1].split(", ")
    return out


def _inputs_of(state: CaseState) -> dict[str, list[str] | None]:
    """끝난 태스크 id → 그 태스크의 증거 id들(실패했거나 증거가 없으면 None). 리드가 입력에 **태스크 id**를 쓰면 코드가 이걸로 바꾼다."""
    return {t.id: (list(t.result_evidence_ids) or None) if t.status == "ok" else None
            for t in state.plan_tasks if t.status in ("ok", "error")}


def _resolve_input(item: str, inputs_of: dict[str, list[str] | None]) -> list[str] | None:
    """입력 하나 → 증거 id들. 태스크 id가 아니면 그대로. 끝난 태스크면 그 증거들, 실패했거나 증거가 없으면 None(의존을 뗀다).

    아직 안 돈(또는 같은 답에 같이 낸) 태스크면 그 **첫 증거 id**(`t-7.e1`)다 — `ProbeRunner`는 ok 태스크마다 증거를 정확히 하나
    남기므로(`runner_probe.py`) 그 id가 생기는 때가 곧 그 태스크가 끝난 때고, 게이트가 그때 열린다. 그 태스크가 실패하면 영영 안
    생기는데, 그건 (c)의 되묻기와 진단의 "막힘 — 입력 … 실패"가 받는다.
    """
    if not _TASK_ID.match(item):
        return [item]
    if item in inputs_of:
        return list(inputs_of[item]) if inputs_of[item] else None
    return [EvidenceRef.make_id(item, 1)]


def _resolve_inputs(task: PlanTask, inputs_of: dict[str, list[str] | None]) -> tuple[PlanTask, list[str]]:
    """입력의 태스크 id를 증거 id로 — 실패했거나 증거가 없으면 **의존을 떼고** 기록한다(R3-1 ②(a)).

    사내 측정 #6 c-2: `input_evidence_ids=['t-6']`(실패한 태스크)로 낸 t-7이 영영 못 돌고 t-8이 t-7을 기다려 `no_runnable`로 끝났다
    — 리드는 continue였다. select 게이트는 증거 id만 안다. 판정 인용 정리(R2-4 ⑥)와 같은 규칙이되, 증거가 여럿이면 전부 입력이다
    (인용과 달리 입력은 "그 결과를 다 본 뒤"라는 뜻이라 고를 것이 없다). `expect.evidence`(재집계 기대값)도 같은 규칙 — 하나일 때만.
    """
    notes: list[str] = []
    ids: list[str] = []
    for item in task.input_evidence_ids:
        found = _resolve_input(item, inputs_of)
        if found is None:
            notes.append(f"{task.id}: 입력 {item}은 태스크 id이고 그 태스크는 실패했거나 증거가 없다 — 의존을 뗐다")
            continue
        ids += [e for e in found if e not in ids]
    params = task.params
    expect = params.get("expect") if isinstance(params, dict) else None
    if isinstance(expect, dict) and isinstance(expect.get("evidence"), str) and _TASK_ID.match(expect["evidence"]):
        found = _resolve_input(expect["evidence"], inputs_of)
        if found and len(found) == 1:
            params = {**params, "expect": {**expect, "evidence": found[0]}}
    if ids == list(task.input_evidence_ids) and params is task.params:
        return task, notes
    return task.model_copy(update={"input_evidence_ids": ids, "params": params}), notes


def _evidence_of(state: CaseState) -> dict[str, list[str]]:
    """질의 → 그 읽기가 남긴 증거 id들. 같은 읽기를 다시 낸 리드에게 "그 결과는 t-1.e1에 있다"고 말하기 위해서다."""
    out: dict[str, list[str]] = {}
    for t in state.plan_tasks:
        if t.action and t.result_evidence_ids:
            out.setdefault(_query(t), []).extend(t.result_evidence_ids)
    return out


def runnable_tasks(state: CaseState) -> list[PlanTask]:
    """지금 실행할 수 있는 태스크 — 우선순위 오름차순, 동률이면 FIFO.

    **입력 증거가 하나라도 없으면 실행 불가다.** `any()`가 아니라 `all()`인 이유:
    재계산 태스크는 "원천값"과 "로직 명세" 둘 다 있어야 성립한다. 하나만 있어도
    돌리면 절반의 입력으로 기대값을 만들고, 그 기대값이 실제와 다른 것을
    "이상 발견"으로 보고한다.
    """
    have = state.evidence_ids()
    ready = [t for t in state.plan_tasks
             if t.status == "pending" and all(e in have for e in t.input_evidence_ids)]
    # sorted는 안정 정렬이라 동률이면 원래 순서(FIFO)가 유지된다 — state.py의
    # 리듀서가 순서를 지키는 것이 여기서 값을 한다.
    return sorted(ready, key=lambda t: t.priority)


def _query(task: PlanTask) -> str:
    """이 태스크가 **실제로 나갈 질의**. id도 goal도 아니다.

    리드는 `goal` 문장만 바꿔 같은 질의를 다시 낸다 — "데이터가 있나" → "값이
    정상인가" → "값이 진짜 0인가"가 전부 `mongo.find collection='alarm' filter={}
    limit=5`였다. **말이 아니라 나가는 것으로 세야** 중복이 보인다.
    """
    return f"{task.action}|{json.dumps(task.params, sort_keys=True, ensure_ascii=False)}"


def _accept_tasks(patch: dict, *, room: int, seen: str | None = "",
                  taken: frozenset = frozenset(),
                  done: frozenset = frozenset(),
                  pending: frozenset = frozenset(),
                  waiting: dict[str, PlanTask] | None = None,
                  evidence_of: dict[str, list[str]] | None = None,
                  inputs_of: dict[str, list[str] | None] | None = None,
                  hint: str = "", hint_for: frozenset = frozenset()
                  ) -> tuple[list[PlanTask], list[str], list[str]]:
    """만들어진 태스크를 소독하고 개수 상한으로 자른다.

    `(받은 것, 거부 기록, 찍은 이름 기록)`. 둘째와 셋째를 가르는 이유: **거부**는 그
    태스크가 안 돌아간다는 뜻이라 리드에게 되물을 근거가 되지만, "찾지 않고 이름을
    댔다"는 받았다는 기록일 뿐이다 — 그걸로 되물으면 "받았는데 왜 다시 묻나"가 된다.

    상한을 리듀서가 아니라 여기서 거는 이유는 `state.py` 맨 위에 있다 — 리듀서에서
    raise하면 superstep이 통째로 죽는다.

    ## `taken` — **이미 있는 id는 다시 받지 않는다**

    소독(`_sanitize_task`)은 **새 태스크**에는 맞지만 이미 끝난 id에는 재앙이다.
    같은 id가 다시 들어오면 리듀서가 완료된 태스크를 `pending`으로 덮어쓰고, 그
    읽기가 **또** 실행된다. 증거는 따로 쌓이므로 살아남아서, 최종 State에
    "실행 안 됐는데 증거가 있는" 모순이 남는다 — 12b의 보고서가 그걸 그대로 쓰면
    **안 한 일을 했다고, 한 일을 안 했다고** 적는다.

    사내에서 실제로 났다: 리드가 예시의 `t-4`·`t-5`를 매 라운드 그대로 베껴서
    같은 읽기가 세 번씩 돌았고, 라운드 4개 중 둘이 통째로 반복이었다.

    ## `done` — **같은 질의는 한 번만**

    id를 막아도 리드는 새 id로 **같은 질의**를 다시 낸다(실제로 4라운드 중 셋이
    그랬다). 대상은 읽기 전용이고 라운드 간격은 초 단위라 재조회에 새 정보가 없다 —
    라운드만 태운다. 그래서 `action`+`params`가 같으면 받지 않는다.

    낼 것이 없어지면 `no_runnable`로 **정직하게 끝난다.** 상한까지 같은 것을 세 번
    더 읽고 "4라운드 조사했다"고 적는 것보다 낫다.

    ## `seen` — 찾지 않고 댄 이름을 **기록한다**

    `None`이면 검사하지 않는다(`EngineDeps.check_discovery`). 아니면 지금까지 본
    증거를 이어 붙인 텍스트다. 태스크가 `collection`·`topic` 같은 **찾아야 아는
    이름**(`DISCOVERED_ARGS`)에 거기 없는 값을 대면 기록한다 — **막지는 않는다.**
    증상 자체가 이름을 담고 있는 정당한 경우가 있고, 무엇보다 ⑮ 설계가 실제로
    먹히는지를 **빈도로 알아야** 막을지 정할 수 있다.
    """
    kept, reused, used, asked = [], [], set(taken), set(done)
    # 같은 답에서 거부되거나 접힌 id → 그 읽기의 증거. 입구 노드가 접수를 먼저 읽으므로 리드의 "증상 재현" 읽기는 으레 중복으로
    # 거부되는데, 리드는 그 id를 다음 태스크의 입력으로 댄다(측정판에서 그랬다) — 뜻은 "그 결과를 본 뒤"이고 결과는 이미 있다.
    aliases: dict[str, list[str] | None] = {}
    for task in patch.get("plan_tasks", []):
        # 등재·인자 검사를 **받을 때** 한다(R3-1 ③). 실행에서 거르면 리드는 한 라운드 뒤에야 안다 — 사내 측정 #6에서 `redis.scan
        # limit`·`mongo.count expect`가 그렇게 라운드를 잃었다. 거부라 되묻기가 그 자리에서 고쳐 받는다. 실행기의 같은 검사는
        # 마지막 방어선으로 그대로 둔다. 받는 인자를 같이 적는다 — "모르는 인자"만으로는 같은 모양으로 다시 틀린다.
        problem = _accept_problem(task)
        if problem is not None:
            reused.append(f"{task.id}: 받지 않는다 — {problem}")
            continue
        # **아직 안 돈 태스크는 같은 id로 다시 내면 갱신이다** — 거부가 아니다. `taken`이
        # 막는 것은 끝난 id의 부활(재실행)이지 대기 중인 것의 손질이 아니다. 로컬 대역
        # 측정에서 리드가 굶고 있던 t-4·t-5를 우선순위를 올려 다시 냈는데 "이미 있는
        # id"로 거부됐고, 그 둘은 4라운드 내내 `pending`으로 남았다. 새 라운드의 태스크가
        # 늘 앞 순위(10·20·30)라 오래된 40·50은 리드가 다시 내지 않는 한 영원히 안 돈다.
        if task.id in pending and task.id not in used - set(taken):
            kept.append(_sanitize_task(task))
            used.add(task.id)
            asked.add(_query(task))
            continue
        if task.id in used:
            reused.append(f"{task.id}: 이미 있는 태스크 id를 다시 냈다 — 받지 않는다")
            continue
        query = _query(task)
        # 같은 질의가 **아직 안 돈 채** 대기 중이면, 새 id로 다시 낸 것도 그 태스크의
        # 갱신이다 — 리드는 "그 읽기를 원한다"고 말한 것이지 id를 아는 게 아니다. 로컬 대역
        # 측정에서 r0의 t-4가 굶는 동안 리드가 t-12로 같은 읽기를 냈고, 거부 → 되물음 →
        # 되물은 답에서 결정적 읽기가 빠지는 연쇄로 원인을 잘못 짚었다.
        held = (waiting or {}).get(query)
        if held is not None and held.id not in used - set(taken):
            kept.append(_sanitize_task(held.model_copy(update={
                "priority": task.priority, "goal": task.goal,
                "input_evidence_ids": list(task.input_evidence_ids)})))
            aliases[task.id] = [EvidenceRef.make_id(held.id, 1)]
            used.add(held.id)
            asked.add(query)
            continue
        if task.action and query in asked:
            # 거부만 하면 리드는 "왜"를 모른 채 또 낸다(사내 실측에서 세 번). 결과가 어디 있는지를 같이 적는다.
            where = (evidence_of or {}).get(query) or []
            # 입구 노드가 이미 한 읽기면 다음 홉까지(R3-1b ③) — 측정 #7에서 리드가 접수 읽기를 두 번 냈고 둘 다 "또 냈다"만 받았다.
            at_intake = bool(where) and hint and set(where) <= hint_for
            reused.append(f"{task.id}{DUP_KIND} — 받지 않는다 "
                          f"({describe(task.action, task.params)})"
                          + (f"{DUP_WHERE}{', '.join(where)}" if where else "")
                          + (f" — 입구 노드가 이미 읽었다. {hint}" if at_intake else ""))
            aliases[task.id] = list(where) or None
            continue
        used.add(task.id)
        asked.add(query)
        kept.append(_sanitize_task(task))
    kept = kept[:max(0, room)]
    # 입력의 태스크 id → 증거 id(R3-1 ②(a)). 받은 뒤에 한다 — 갱신 경로(`waiting`)가 입력을 새 답의 것으로 바꾸기 때문이다.
    # 기록은 셋째 묶음이다 — 받았다는 기록이지 거부가 아니다(거부로 되물으면 "받았는데 왜 다시 묻나"가 된다).
    resolved: list[str] = []
    fixed: list[PlanTask] = []
    for task in kept:
        task, notes = _resolve_inputs(task, {**(inputs_of or {}), **aliases})
        fixed.append(task)
        resolved += notes
    kept = fixed
    if seen is None:
        return kept, reused, resolved
    guessed = list(resolved)
    for task in kept:
        for name, value in sorted(task.params.items()):
            if name in DISCOVERED_ARGS and isinstance(value, str) and not name_known(value, seen):
                guessed.append(f"{task.id}: 찾지 않고 이름을 댔다 — {name}={value!r}")
    return kept, reused, guessed


def make_nodes(deps: EngineDeps) -> dict:
    async def intake(state: CaseState) -> dict:
        """frame **전에** 코드가 접수를 읽고 대상 키를 고른다(R3-1 ①). 리드가 아직 아무것도 보기 전이라 여기 실린 것이 리드의 첫
        사실이다. 대본 경로(`check_discovery=False`)와 재료가 없는 케이스는 안 돈다. 던지지 않는다 — 실패는 `target.problem`."""
        if deps.intake is None or not deps.check_discovery or state.target is not None:
            return {}
        try:
            return await run_intake(state, deps.intake, runner=deps.runner,
                                    declared=tuple(deps.declared.get("rediskey", ())))
        except Exception as exc:                                    # noqa: BLE001
            return {"target": TargetPick(problem=f"입구 노드가 던졌다 — {type(exc).__name__}: {exc}")}

    async def frame(state: CaseState) -> dict:
        patch = await deps.frame(state)
        # frame 시점에는 증거가 하나도 없다 — 그래서 여기서 인용을 단 가설은
        # 전부 `open`으로 돌아간다. 그게 맞다: 아직 아무것도 안 봤다.
        hypotheses, complaints = _accept_hypotheses(patch, have=state.evidence_ids())
        # frame 시점엔 증거가 없으므로 `seen`이 비어 있다 — 이름을 대면 전부 기록된다.
        # 그게 맞다: 아직 아무것도 안 봤는데 이름을 안다면 찍은 것이다.
        tasks, rejected, guessed = _accept_tasks(
            patch, room=deps.max_tasks, taken=_taken(state), done=_done(state),
            pending=_pending(state), waiting=_waiting(state), evidence_of=_evidence_of(state),
            hint=hop_line(state.target, code_index=deps.code_index), hint_for=_intake_ids(state),
            seen=_universe(state, deps) if deps.check_discovery else None)
        return {**patch,
                "hypotheses": hypotheses,
                "plan_tasks": tasks,
                "llm_errors": (list(patch.get("llm_errors", []))
                               + complaints + rejected + guessed),
                "round": 1}

    async def select(state: CaseState) -> dict:
        """실행할 것을 **병렬 폭만큼** 골라 `running`으로 굴린다."""
        chosen = runnable_tasks(state)[:deps.parallel_width]
        return {"plan_tasks": [t.model_copy(update={"status": "running"}) for t in chosen]}

    async def execute(payload: dict) -> dict:
        """태스크 하나. **Send로 병렬 발사되므로 여기서 던지면 안 된다.**

        가지 하나의 예외가 superstep 전체를 실패시켜 같은 라운드의 성공한 가지까지
        지운다. 실행기도 던지지 않기로 돼 있지만(`TaskRunnerPort`), 그 계약을
        믿고 방어를 빼면 계약을 어기는 구현 하나가 조사 전체를 멈춘다.
        """
        task = PlanTask.model_validate(payload["task"])
        case = Case.model_validate(payload["case"])
        try:
            outcome = await deps.runner.run(task, case=case)
        except Exception as exc:                                    # noqa: BLE001
            outcome = TaskOutcome(task_id=task.id, status="error",
                                  error=f"{type(exc).__name__}: {exc}")
        facts = facts_for(task, outcome, deps.declared)
        done = task.model_copy(update={
            "status": outcome.status,
            "result_summary": " · ".join([outcome.summary, *facts]) if facts else (outcome.summary or None),
            "result_evidence_ids": [e.id for e in outcome.evidence],
            "error": outcome.error})
        return {"plan_tasks": [done], "evidence": list(outcome.evidence), "facts": facts}

    async def integrate(state: CaseState) -> dict:
        def accept(reply: dict, base: CaseState):
            """`(받은 태스크, 받은 가설, 거부 기록, 찍은 이름 기록)` — `base` 기준으로."""
            fresh, rejected, guessed = _accept_tasks(
                reply, room=deps.max_tasks - len(base.plan_tasks),
                taken=_taken(base), done=_done(base), pending=_pending(base),
                waiting=_waiting(base), evidence_of=_evidence_of(base),
                inputs_of={**_rejected_reads(base), **_inputs_of(base)},
                hint=hop_line(base.target, code_index=deps.code_index), hint_for=_intake_ids(base),
                seen=_universe(base, deps) if deps.check_discovery else None)
            hypotheses, ghosts = _accept_hypotheses(reply, have=base.evidence_ids())
            return fresh, hypotheses, ghosts + rejected, guessed

        patch = await deps.integrate(state)
        fresh, hypotheses, refused, guessed = accept(patch, state)
        # (c) 대기 태스크는 있는데 돌릴 것이 없으면 — 입력이 영영 안 생기는 것이다 — 거부와 같은 길로 **케이스당 한 번** 되묻는다.
        # 사내 측정 #6 c-2는 그 자리에서 `no_runnable`로 끝났고 리드는 continue였다. (a)가 입력을 고치므로 드물지만 안전망이다.
        # 한 번인 이유: 같은 막힘을 라운드마다 되물으면 리드가 같은 답을 내는 동안 LLM 호출만 두 배가 된다.
        blocked = _blocked_note(state, fresh)
        if (blocked and patch.get("decision") != "conclude" and not patch.get("stopped_by")
                and not any(BLOCKED_MARK in e for e in state.llm_errors)):
            refused = refused + [blocked]
        # **거부가 있으면 그 자리에서 한 번 되묻는다.** 버려진 것을 다음 라운드의
        # `<버려진 태스크>`로만 돌려주면 리드는 한 라운드 뒤에야 좁힌다 — 두 번째 전체
        # 트레이스에서 두 번 그랬고, 매번 실행 한 사이클을 태웠다. 먹힌다고 증명된
        # 유일한 신호(자기 질의가 그 값 그대로 버려졌다)를 프롬프트가 아직 뜨거울 때
        # 준다. 되물은 답이 첫 답을 **통째로 대신한다** — JSON 재시도와 같은 규칙이다.
        # 마지막 라운드에는 안 한다(어차피 상한으로 끝난다). 되묻기 자체가 실패하면
        # 첫 답으로 간다 — 한 번 더 물어본 것이 라운드를 죽이면 안 된다.
        if (refused and deps.redo_on_rejection and not patch.get("stopped_by")
                and state.round < deps.max_rounds):
            # 첫 답에서 **받은 것은 대기 태스크로 실어서** 되묻는다. 안 실으면 예시의 다음
            # 번호가 그대로라 리드가 같은 id(t-8)로 **다른** 읽기를 내고, 합칠 때 그 id가
            # 첫 답의 받은 읽기를 덮는다 — 사내 세 번째 트레이스에서 좁힌 `mongo.find`가
            # 그렇게 `redis.scan`으로 바뀌어 사라졌다. 실으면 번호가 앞으로 가고, 리드는
            # 그것들이 남아 있음을 본다. 두 번째 답의 판정도 이 State 기준이다.
            again = state.model_copy(update={
                "plan_tasks": merge_by_id(state.plan_tasks, fresh),
                "llm_errors": state.llm_errors + [REDO_MARK + c for c in refused]})
            second = await deps.integrate(again)
            refused = [c + REDO_NOTE for c in refused]
            if second.get("stopped_by"):
                refused += list(second.get("llm_errors", []))
            else:
                # **첫 답에서 받은 것은 남긴다.** 되물음은 거부된 것을 고쳐 받으려는 것이지
                # 답을 새로 받으려는 것이 아니다. 처음엔 통째로 바꿨는데, 로컬 대역 측정에서
                # 첫 답의 결정적 읽기(`gumi-mx-sink` 오프셋)가 되물은 답에 없어서 사라졌고,
                # 리드는 끝까지 엉뚱한 서비스를 팠다. 같은 id는 되물은 쪽이 이기고, 같은
                # 질의를 새 id로 또 냈으면 한 번만 남는다. 가설은 되물은 쪽(최신 판단)이다.
                patch = second
                more, hypotheses, later, more_guessed = accept(second, again)
                # 같은 질의를 새 id로 또 냈으면 `waiting` 경로가 이미 첫 답의 id로 접어
                # 돌려준다 — 그래서 id 병합 하나면 된다(질의 중복 제거를 따로 뒀다가 RED
                # 스윕에 닿지 않는 코드로 잡혔다).
                fresh = merge_by_id(fresh, more)
                refused += later
                guessed = [g for g in guessed
                           if g.split(":")[0] in {t.id for t in fresh}] + more_guessed
        patch = {**patch, "plan_tasks": fresh, "hypotheses": hypotheses,
                 "llm_errors": list(patch.get("llm_errors", [])) + refused + guessed}

        # 리드가 이미 끝낸 이유를 댔으면(LLM 실패 등) 그것이 이긴다. 아래 규칙들이
        # 덮어쓰면 "LLM이 죽어서"가 "상한에 걸려서"로 둔갑한다 — 12a가 "미확정"과
        # "조사 실패"를 가르는 근거가 바로 이 값이다.
        if patch.get("stopped_by"):
            return {**patch, "decision": "conclude"}

        # 상한은 **결정을 본 뒤에** 본다. 먼저 보면 "계속하자"가 상한을 넘긴다.
        if state.round >= deps.max_rounds:
            return {**patch, "decision": "conclude", "stopped_by": "max_rounds"}
        if patch.get("decision") == "conclude":
            return {**patch, "stopped_by": "decision"}

        # 계속하기로 했는데 **돌릴 것이 없으면** 상한까지 빈 라운드를 돈다.
        # 그러면 보고서가 "N라운드 조사했다"고 적는데 실제로는 아무것도 안 했다.
        # 새 태스크를 합친 상태로 봐야 한다 — integrate가 방금 만든 것이 있을 수 있다.
        ahead = state.model_copy(update={
            "plan_tasks": merge_by_id(state.plan_tasks, fresh)})
        if not runnable_tasks(ahead):
            # 막혀서 끝나는 거면 왜 막혔는지를 기록에 남긴다 — `no_runnable`만으로는 "낼 것이 없었다"로 읽힌다.
            errors = list(patch.get("llm_errors", []))
            still = _blocked_note(state, fresh)
            return {**patch, "decision": "conclude", "stopped_by": "no_runnable",
                    "llm_errors": errors + ([still] if still and still not in errors else [])}
        return {**patch, "decision": "continue", "round": state.round + 1}

    return {"intake": intake, "frame": frame, "select": select, "execute": execute, "integrate": integrate,
            **make_verdict_nodes(deps)}


def _blocked_note(state: CaseState, fresh: list[PlanTask]) -> str | None:
    """대기 태스크는 있는데 돌릴 것이 없을 때의 한 줄 — 무엇이 어느 입력에 막혔나. 아니면 None."""
    ahead = state.model_copy(update={"plan_tasks": merge_by_id(state.plan_tasks, fresh)})
    pending = [t for t in ahead.plan_tasks if t.status == "pending"]
    if not pending or runnable_tasks(ahead):
        return None
    return (", ".join(t.id for t in pending) + f": {BLOCKED_MARK} — "
            + ", ".join(f"{t.id}({unrun_reason(ahead, t).removeprefix('막힘 — ')})" for t in pending)
            + " — 입력 없는 읽기를 내거나 conclude")


def unrun_reason(state: CaseState, task: PlanTask) -> str:
    """끝까지 안 돈 태스크의 사유 — 입력이 막혔으면 어느 입력이 왜(실패/없음), 아니면 우선순위에 밀림(R3-1 ②(b)).

    사내 측정 #6에서 입력 t-6(실패)에 막힌 t-7이 "우선순위에 밀림"으로 적혔다 — 틀린 말이고, 틀린 말은 다음 수정을 엉뚱한 데로 보낸다.
    """
    have = state.evidence_ids()
    by_id = {t.id: t for t in state.plan_tasks}
    for item in task.input_evidence_ids:
        if item in have:
            continue
        owner = by_id.get(item) or by_id.get(item.split(".e", 1)[0])
        if owner is not None and owner.status == "error":
            return f"막힘 — 입력 {item} 실패"
        return f"막힘 — 입력 {item} 없음"
    return "우선순위에 밀림"


def route_after_frame(state: CaseState) -> str:
    """frame이 끝낸 이유를 댔으면(리드 LLM이 죽었다) 라운드를 시작하지 않는다.

    흘려보내면 select가 0건 → integrate가 LLM을 **또** 부르고, 또 죽고, 끝난 이유가
    `no_runnable`로 덮여 "조사할 게 없었다"가 된다. LLM이 안 붙은 것과 볼 게 없는
    것은 완전히 다른 사실이고, 12a가 그 둘을 갈라 적는다.

    END가 아니라 conclude로 간다 — 죽은 조사에도 판정(degraded)은 남아야 한다. conclude는
    `llm_error`를 보고 LLM을 묻지 않는다.
    """
    return "conclude" if state.stopped_by else "select"


def route_after_select(state: CaseState):
    """select가 방금 `running`으로 굴린 것만 발사한다.

    execute가 끝나면 ok/error로 바뀌므로 이 조건은 **이번 라운드 몫만** 정확히 잡는다.
    페이로드를 JSON으로 덤프하는 이유: Send 인자는 체크포인트에 실려 저장된다
    (13단계의 재개가 여기 걸린다).
    """
    from langgraph.types import Send

    running = [t for t in state.plan_tasks if t.status == "running"]
    if not running:
        return "integrate"
    return [Send("execute", {"task": t.model_dump(mode="json"),
                             "case": state.case.model_dump(mode="json")})
            for t in running]


def route_after_integrate(state: CaseState) -> str:
    return "select" if state.decision == "continue" else "conclude"


# ── 12a — 판정 ───────────────────────────────────────────────────────

MAX_ALTERNATES = 3        # 상한은 코드가 쥔다(규율 6) — 보고서가 읽히는 길이의 한계
MAX_RELATION_CHARS = 300  # LLM 산문에는 길이 상한이 없다
VERIFY_REWRITES = 1       # 되묻기는 한 번(decisions ⑯) — 세 번째는 없다


def stopped_summary(state: CaseState, why: str) -> str:
    """degraded 서술 — "조사가 돌지 않았다"는 사내 실측에서 거짓이었다(3라운드를 돌고 증거를 쥔 채 죽었다).
    몇 라운드에 무엇을 읽고 몇 건을 모았는지, 가설이 어디까지 갔는지를 적는다."""
    ran = [t for t in state.plan_tasks if t.status in ("ok", "error")]
    good = sum(1 for t in ran if t.status == "ok")
    hyps = " ".join(f"{h.id}[{h.status}]" for h in state.hypotheses[:4]) or "없음"
    return (f"조사 중단 — {why}. {state.round}라운드 · 읽기 {len(ran)}회(성공 {good}) · "
            f"증거 {len(state.evidence)}건 · 가설 {hyps}")


def degraded(narrative: str, caveats=()) -> Verdict:
    """"조사 실패"의 판정. **코드만 만든다** — 리드 어휘에는 없다(`lead.ConcludeReply`)."""
    return Verdict(verdict_type="degraded", confidence="low", narrative=narrative,
                   caveats=list(caveats))


def _clean_link(link: CauseLink, *, own_confidence: bool) -> CauseLink:
    relation = link.relation
    if relation is not None and len(relation) > MAX_RELATION_CHARS:
        relation = relation[:MAX_RELATION_CHARS - 1] + "…"
    return link.model_copy(update={
        "component": link.component.strip(), "relation": relation,
        # 최상위·기여 요인의 confidence는 항상 None — 최상위의 신뢰도는 Verdict.confidence다.
        "confidence": link.confidence if own_confidence else None})


def sanitize_verdict(verdict: Verdict) -> Verdict:
    """인과 사슬의 **형태**는 코드가 정한다(규율 4·6) — 후보 상한·중복·빈 부품, relation 길이,
    최상위·기여 요인의 confidence.

    거부가 아니라 소독인 이유: validator로 거부하면 후보 하나가 중복됐다고 판정 전체가
    degraded("판정을 못 받았다")로 떨어진다. 버린 후보는 caveat에 남긴다. 바뀐 것이 없으면
    같은 객체를 돌려준다.
    """
    root = _clean_link(verdict.root_cause, own_confidence=False) if verdict.root_cause else None
    contributing = [_clean_link(c, own_confidence=False) for c in verdict.contributing]
    seen = {root.component} if root is not None else set()
    kept, dropped = [], []
    for link in verdict.alternates:
        link = _clean_link(link, own_confidence=True)
        if not link.component:
            dropped.append("(빈 부품)")
            continue
        if link.component in seen or len(kept) >= MAX_ALTERNATES:
            dropped.append(link.component)
            continue
        seen.add(link.component)
        kept.append(link)
    caveats = verdict.caveats + (
        [f"후보 정리: {', '.join(dropped)} 제외(중복·빈 부품 또는 상한 {MAX_ALTERNATES} 초과)"]
        if dropped else [])
    cleaned = verdict.model_copy(update={"root_cause": root, "contributing": contributing,
                                         "alternates": kept, "caveats": caveats})
    return verdict if cleaned == verdict else cleaned


_TASK_ID = re.compile(r"^t-\d+$")


def resolve_task_citations(verdict: Verdict, tasks) -> Verdict:
    """판정이 **태스크 id**(`t-8`)를 증거로 인용했고 그 태스크가 만든 증거가 하나뿐이면 그 증거 id로 바꾼다 — caveat에 적는다.

    verify는 `t-8`을 "없는 id"로 보고 판정 턴을 다시 묻는다. 사내 판정 턴은 한 번에 4분이라 뜻이 하나인 실수에 그 값을 치를
    이유가 없다. 바꾼 id는 State에 실재하는 증거라 규율 3(LLM이 댄 id를 믿지 않는다) 안이다. 증거가 둘 이상이면 고르지 않는다 —
    코드가 고르면 판정이 인용하지 않은 것을 인용한 것이 된다. 그대로 두고 verify가 전처럼 되묻는다. 바뀐 것이 없으면 같은 객체.
    """
    only = {t.id: t.result_evidence_ids[0] for t in tasks if len(t.result_evidence_ids) == 1}
    fixed: list[str] = []

    def fix(link: CauseLink) -> CauseLink:
        ids = []
        for evidence_id in link.evidence_ids:
            if _TASK_ID.match(evidence_id) and evidence_id in only:
                ids.append(only[evidence_id])
                fixed.append(f"{evidence_id}→{only[evidence_id]}")
            else:
                ids.append(evidence_id)
        return link if ids == link.evidence_ids else link.model_copy(update={"evidence_ids": ids})

    root = fix(verdict.root_cause) if verdict.root_cause is not None else None
    alternates = [fix(link) for link in verdict.alternates]
    contributing = [fix(link) for link in verdict.contributing]
    if not fixed:
        return verdict
    note = f"인용 정리: 태스크 id를 그 태스크의 유일한 증거 id로 바꿨다 — {', '.join(dict.fromkeys(fixed))}"
    return verdict.model_copy(update={"root_cause": root, "alternates": alternates, "contributing": contributing,
                                      "caveats": verdict.caveats + [note]})


def _links(verdict: Verdict) -> list[CauseLink]:
    # 후보·기여 요인도 리드가 인용한 id다(규율 3) — 최상위만 검사하면 후보가 환각 id를 실은 채
    # 보고서에 나간다.
    return ([verdict.root_cause] if verdict.root_cause is not None else []) \
        + list(verdict.alternates) + list(verdict.contributing)


def _id_mentioned(evidence_id: str, caveats: list[str]) -> bool:
    """caveat 안에 증거 id가 **토큰 경계**로 있는가 — `t-1.e1`은 `t-1.e10` 안에 있는 것이 아니다.

    경계는 ASCII로 본다 — 정규식의 단어 문자(w)는 한글도 포함해서 "t-1.e1은 잘렸다"의 조사(은)가 id에 붙어
    "명시하지 않았다"가 된다.
    """
    pattern = re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(evidence_id)}(?![A-Za-z0-9_-])")
    return any(pattern.search(c) for c in caveats)


def verify_verdict(verdict: Verdict, *, citable: set[str], incomplete: set[str],
                   component_problem: Callable[[str], str | None]) -> list[str]:
    """LLM 없는 검사. 인용 우주는 **`state.evidence`**(리드가 실제로 본 것)다 — Store 전체를
    기준으로 삼으면 리드가 본 적도 없는 id를 인용해도 통과한다(규율 3).

    1. 다리마다 인용이 있어야 하고, 인용한 id는 전부 실재해야 한다.
    2. 잘린 표본(`complete=False`)으로 주장했으면 caveat에 그 id를 적어야 한다 — 잘린 표본으로는
       "없다"를 주장할 수 없고, 그 사실이 보고서에 남아야 한다.
    3. `component`는 **토폴로지의 서비스 이름**이어야 한다(`_component_problem`) — 사내 10-08에서 Redis 키 이름이 원인 칸에
       들어가 통과했다. 원인은 그 데이터를 만들거나 쓰는 쪽이지 데이터의 이름이 아니다. 토폴로지가 없는 조사만 옛 규칙
       (증거에 나온 이름)으로, `external` 판정만 증거에 나온 바깥 이름을 허용한다.
    """
    problems = []
    for link in _links(verdict):
        if not link.evidence_ids:
            problems.append(f"다리에 인용 없음: {link.component}")
            continue
        for evidence_id in link.evidence_ids:
            if evidence_id not in citable:
                problems.append(f"없는 id {evidence_id} 인용 ({link.component})")
            elif evidence_id in incomplete and not _id_mentioned(evidence_id, verdict.caveats):
                problems.append(f"불완전 증거 {evidence_id}가 caveat에 명시되지 않음")
    for link in _links(verdict):
        problem = component_problem(link.component)
        if problem:
            problems.append(problem)
    return problems


def demote_verdict(verdict: Verdict, problems: list[str], *, citable: set[str],
                   component_problem: Callable[[str], str | None] = lambda component: None) -> Verdict:
    """재작성도 실패한 판정 — **없는 인용과 서비스 아닌 component를 걷어내고** 낮은 확신으로 통과시킨다.

    근거가 전부 사라진 다리와 component가 verify 규칙 3에 걸리는 다리는 뺀다. 최상위가 그러면 `inconclusive`가 된다 —
    `_accept_hypotheses`가 근거를 잃은 supported를 open으로 되돌리는 것과 같은 규칙이다. 근거 없는 단정이 "확신 low"를 달고
    보고서에 나가는 것보다 낫다. `component_problem`은 verify가 쓴 그 검사다 — 검사하는 규칙과 걷어내는 규칙이 다르면 되물은
    문제가 강등 출력에 그대로 남는다(사내 측정 #4: c-2의 `후보 external`).
    """
    def strip(link: CauseLink) -> CauseLink:
        return link.model_copy(update={"evidence_ids": [e for e in link.evidence_ids if e in citable]})

    unsupported: list[str] = []
    foreign: list[str] = []

    def keep(kind: str, link: CauseLink) -> CauseLink | None:
        kept = strip(link)
        if not kept.evidence_ids:
            unsupported.append(f"{kind} {link.component}")
            return None
        if component_problem(link.component):
            foreign.append(f"{kind} {link.component}")
            return None
        return kept

    alternates = [k for k in (keep("후보", link) for link in verdict.alternates) if k is not None]
    contributing = [k for k in (keep("기여 요인", link) for link in verdict.contributing) if k is not None]
    root = keep("최상위", verdict.root_cause) if verdict.root_cause is not None else None
    verdict_type = verdict.verdict_type
    if verdict.root_cause is not None and root is None:
        verdict_type = "inconclusive"
    caveats = verdict.caveats + ["검증 미통과: " + "; ".join(problems)] + (
        [f"근거 없는 다리 제외: {', '.join(unsupported)}"] if unsupported else []) + (
        [f"서비스 아닌 component 제외: {', '.join(foreign)}"] if foreign else [])
    return verdict.model_copy(update={
        "verdict_type": verdict_type, "root_cause": root, "alternates": alternates,
        "contributing": contributing, "confidence": "low", "caveats": caveats})


def cap_unlinked_key(verdict: Verdict, *, target: TargetPick, evidence) -> Verdict:
    """최상위 원인이 인용한 **키 읽기**가 대상 키(링크 토큰에서 고른 것)가 아니면 확신 상한 medium + caveat. 되묻지 않는다 — 한도다.

    사내 측정 #5: 리드가 이름이 비슷한 다른 배지의 키를 읽어 `None`을 유실의 증거로 삼아 `data_loss(high)`로 틀렸다. 이 규칙은
    판정을 맞게 만들지 않는다 — 거짓 확신을 막을 뿐이다(맞게 만드는 것은 R3-0 ①과 R3-1의 frame 전 읽기). 키를 인용하지 않은
    최상위는 이 규칙 밖(R3-2 (b)(c)). 대상 키를 모를 때(고를 재료 없음·못 고름)도 건다 — 연결이 확인되지 않은 것은 같다. 바뀐 것이
    없으면 같은 객체.
    """
    if verdict.root_cause is None:
        return verdict
    keys = cited_keys(verdict.root_cause.evidence_ids, evidence)
    if not keys or (target.key and any(present(target.key, [k]) for k in keys)):
        return verdict
    known = (f"대상 키 {target.key}" if target.key
             else "대상 키를 못 골랐다 — " + (target.problem or "입구 노드가 고르지 않았다(접수 읽기 없음)"))
    note = f"대상과 키의 연결이 확인되지 않았다 — 최상위가 인용한 키 {', '.join(keys)}; {known}"
    confidence = "medium" if verdict.confidence == "high" else verdict.confidence
    return verdict.model_copy(update={"confidence": confidence, "caveats": verdict.caveats + [note]})


def _component_problem(state: CaseState, deps: EngineDeps, verdict: Verdict) -> Callable[[str], str | None]:
    """component 하나의 문제 — 없으면 None. 대본 경로(`check_discovery=False`)는 사람이 이름을 알고 적은 것이라 검사하지
    않는다(태스크의 "찾지 않고 이름을 댔다"와 같은 스위치)."""
    if not deps.check_discovery:
        return lambda component: None
    universe = _universe(state, deps)
    services = ", ".join(sorted(deps.components))

    def problem(component: str) -> str | None:
        if component in deps.components:
            return None
        # 토폴로지를 아는 조사에서는 서비스만 — 키·컬렉션 이름은 데이터지 원인이 아니다(사내 c-2가 Redis 키 이름으로 통과했다).
        # `external`만 예외: 바깥 시스템은 토폴로지에 없고, 증거에 나온 이름이면 된다.
        if deps.components and verdict.verdict_type != "external":
            return f"토폴로지 서비스가 아닌 component {component!r} — 원인은 그 데이터를 만들거나 쓰는 서비스다 ({services})"
        if name_known(component, universe):
            return None
        return f"증거에도 토폴로지에도 없는 component {component!r}"
    return problem


def make_verdict_nodes(deps: EngineDeps) -> dict:
    async def conclude(state: CaseState) -> dict:
        """판정. **조사가 어떻게 끝났든 Verdict가 생긴다.**

        코드가 먼저 가르는 둘은 LLM을 묻지 않는다 — 리드가 응답하지 못해 끝났으면 죽은 LLM을
        한 번 더 부르는 것이고(성공하면 안 돈 조사에 판정이 생긴다), 증거가 0건이면 인용할
        것이 없어 어떤 판정도 근거가 없다. 둘 다 "미확정"이 아니라 **조사 실패**다.
        """
        if not state.evidence:
            why = ("리드 LLM이 응답하지 못했고 모은 증거가 없다" if state.stopped_by == "llm_error"
                   else "읽기가 하나도 성공하지 않아 판정할 재료가 없다")
            return {"verdict": degraded(
                stopped_summary(state, why),
                caveats=list(state.llm_errors) + [f"{t.id}: {t.error or '원인 불명'}" for t in state.plan_tasks
                                                  if t.status == "error"])}
        if state.stopped_by == "llm_error" and deps.conclude is not None:
            # 액션 턴이 죽었다고 판정 턴까지 죽은 것은 아니다(다른 모델일 수 있다, R2-1). 모은 증거가 있으면 **한 번**
            # 묻는다 — 사내 실측 두 판은 증거를 쥐고도 여기서 degraded로 끝났다. 그것도 실패하면 그때 degraded.
            try:
                reply = await deps.conclude(state)
            except Exception as exc:                                # noqa: BLE001
                reply = {"llm_errors": [f"conclude: {type(exc).__name__}: {exc}"]}
            notes = list(reply.get("llm_errors", []))
            if reply.get("verdict") is not None:
                return {"verdict": sanitize_verdict(resolve_task_citations(reply["verdict"], state.plan_tasks)),
                        "llm_errors": notes}
            return {"verdict": degraded(stopped_summary(state, "리드 LLM이 응답하지 못했고 판정 한 번도 실패했다"),
                                        caveats=list(state.llm_errors) + notes),
                    "llm_errors": notes}
        if deps.conclude is None:
            return {"verdict": degraded("판정 불가 — 이 경로에는 판정자가 배선되지 않았다",
                                        caveats=["판정자 없음 — 리드 경로는 lead.make_lead, "
                                                 "대본 경로는 대본의 verdict"])}
        try:
            reply = await deps.conclude(state)
        except Exception as exc:                                    # noqa: BLE001
            # 판정자는 LLM 어댑터를 품고 있다. 여기서 죽으면 케이스가 investigating으로 남는다.
            reply = {"llm_errors": [f"conclude: {type(exc).__name__}: {exc}"]}
        notes = list(reply.get("llm_errors", []))
        verdict = reply.get("verdict")
        if verdict is None:
            # "판정 없음"이 아니라 degraded — 보고서가 "판정 불가"를 적는다. LLM 쪽 사유는
            # llm_errors에도 남아 프롬프트가 안 먹히고 있는 것이 보이고, 대본의 "판정을 안 실었다"
            # 같은 비LLM 사유(`note`)는 caveat에만 남는다 — 리드 계약 위반으로 세면 거짓 양성이다.
            reasons = notes + ([str(reply["note"])] if reply.get("note") else [])
            return {"verdict": degraded("판정 불가 — 판정자가 판정을 주지 않았다", caveats=reasons),
                    "llm_errors": notes}
        return {"verdict": sanitize_verdict(resolve_task_citations(verdict, state.plan_tasks)), "llm_errors": notes}

    async def verify(state: CaseState) -> dict:
        """LLM 없는 가드레일. 노드는 raise하지 않는다."""
        verdict = state.verdict
        if verdict is None:
            # conclude가 항상 verdict를 만들지만, 그래프 변경·재개 엣지에 대한 방어다.
            return {"verify_problems": []}
        citable = state.evidence_ids()
        incomplete = {e.id for e in state.evidence if not e.complete}
        component_problem = _component_problem(state, deps, verdict)
        problems = verify_verdict(verdict, citable=citable, incomplete=incomplete, component_problem=component_problem)
        if not problems:
            if not deps.check_discovery:
                return {"verify_problems": []}            # 대본은 사람이 이름을 알고 쓴 것 — `_component_problem`과 같은 스위치
            capped = cap_unlinked_key(verdict, target=state.target or TargetPick(), evidence=state.evidence)
            return {"verify_problems": []} if capped is verdict else {"verdict": capped, "verify_problems": []}
        if state.verify_attempts < VERIFY_REWRITES:
            # 되묻는다 — 문제 목록이 State에 실려 conclude 프롬프트의 재작성 블록이 된다.
            return {"verify_problems": problems, "verify_attempts": state.verify_attempts + 1,
                    "llm_errors": [f"verify: {p}" for p in problems]}
        return {"verdict": demote_verdict(verdict, problems, citable=citable, component_problem=component_problem),
                "verify_problems": [],
                "llm_errors": [f"verify: 재작성 뒤에도 미통과 — 걷어내고 강등 ({len(problems)}건)"]}

    return {"conclude": conclude, "verify": verify}


def route_after_verify(state: CaseState) -> str:
    return "conclude" if state.verify_problems else "__end__"
