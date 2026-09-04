"""접수 — 케이스가 열린 뒤 대상(`target_locator`)을 턴 단위로 확정한다 (스펙 §4.4).

그래프 안의 frame 노드처럼 다단계 조사·병렬 태스크를 벌이지 않는다 — 한 턴이 LLM
호출 하나다. 진짜 조사는 접수가 끝난 뒤 엔진이 한다.

**턴 단위인 이유**: 이전 구조는 `ask` 콜백으로 프로세스 안에서 되묻고 문답을
마지막에 한 번 돌려줬다. 클라이언트가 끊기거나 서버가 재시작되면 전부 사라진다.
지금은 한 호출이 끝나거나 케이스를 파킹하고, 다음 호출이 답을 들고 이어간다 —
답은 **이어가기 전에 먼저** 증거로 박제된다.

**동시성**: 접수는 lease를 잡지 않는다. 대신 `_not_ours`가 턴 시작과 저장 직전에
같은 판정을 걸어, 그 사이 워커가 가로챈 레코드를 건드리지 않는다. 재읽기만으로는
부족하다 — 그 이유는 `_not_ours` docstring에 있다.

무raise 규율: LLM 호출·파싱이 전부 실패해도 접수가 조사를 막아서는 안 된다 —
`target_locator=None`으로 진행하고 실패를 `problems`에 남길 뿐 raise하지 않는다.
"""
from datetime import datetime
from typing import Any, Callable, Literal

from src.application.lifecycle import transition
from src.application.schemas import parse_structured
from src.config.schema_app import StrictModel


class _IntakeLlmOutput(StrictModel):
    gbm: str = ""
    fct: str = ""
    target_locator: str | None = None
    missing: list[str] = []


def _prompt(symptom: str, gbm: str, fct: str, locators: list[str]) -> str:
    locator_list = ", ".join(locators) if locators else "없음"
    return (
        f"[사이트 목록] {gbm}/{fct}\n"
        f"[증상] {symptom}\n"
        f"[토폴로지 locator 목록] {locator_list}\n\n"
        "위 증상을 조사하기 위한 대상 target_locator를 locator 목록 중에서 고르거나, "
        "목록에 없으면 가장 근접한 값을 적어라. 확신이 없거나 추가로 필요한 정보가 "
        "있으면 missing에 사람에게 물을 질문을 한국어로 적어라(없으면 빈 배열).\n"
        'JSON만 출력하라: {"gbm": "...", "fct": "...", '
        '"target_locator": "..." 또는 null, "missing": ["질문", ...]}'
    )


async def _call(deps, prompt: str) -> tuple[_IntakeLlmOutput | None, str | None]:
    try:
        response = await deps.lead_llm.ainvoke([("user", prompt)])
    except Exception as exc:                                        # noqa: BLE001
        return None, f"LLM 호출 실패 — {type(exc).__name__}: {exc}"
    return parse_structured(response.content, _IntakeLlmOutput)


# ── 턴 단위 접수(계획 12) ─────────────────────────────────────────────────
# intake()는 ask 콜백으로 **프로세스 안에서** 되묻고 문답을 마지막에 한 번 돌려준다.
# 그 사이에 클라이언트가 끊기거나 서버가 재시작되면 전부 사라진다. 턴으로 쪼개면
# 한 호출이 LLM을 한 번 부르고, 더 물어야 하면 케이스를 파킹하며 질문을 레코드에
# 남긴다 — 다음 호출이 답을 들고 이어간다.


class IntakeTurn(StrictModel):
    status: Literal["done", "asking", "error"]
    question: str | None = None
    target_locator: str | None = None
    problems: list[str] = []


_ANSWER_SOURCE = "human:intake_answer"
_TURN_SOURCE = "intake:llm"


def _turn_prompt(record, locators: list[str], answers: list[str]) -> str:
    prompt = _prompt(record.symptom, record.gbm, record.fct, locators)
    if answers:
        prompt += "\n\n[추가 답변]\n" + "\n".join(f"- {a}" for a in answers)
    return prompt


async def intake_turn(case_id: str, *, repo, store, deps: Any, topology,
                      clock: Callable[[], datetime], answer: str | None = None,
                      max_turns: int = 3) -> IntakeTurn:
    """접수 한 턴 — LLM을 한 번 부르고 끝나거나 파킹한다. 절대 raise하지 않는다.

    `answer`가 있으면 **이어가기 전에 먼저 증거로 박제한다.** 미루면 그 사이
    프로세스가 죽었을 때 사람의 답이 사라진다 — 계획 4b의 F3 경로가 human:answer에서
    같은 판단을 했다.

    `max_turns`는 **코드가 쥐는 상한**이다(규율 6). 턴으로 바꾸면 호출자가 무한히
    부를 수 있는데, 넘으면 대상 없이 조사에 들어간다 — 기존 "이중 실패"와 같은
    착지점이라 새 실패 모양을 만들지 않는다.
    """
    try:
        record = repo.get(case_id)
        if record is None:
            return IntakeTurn(status="error", problems=[f"케이스 {case_id}를 찾을 수 없다"])
        problem = _not_ours(record)
        if problem is not None:
            return IntakeTurn(status="error", problems=[problem])

        now = clock()
        if answer is not None:
            store.put_evidence(case_id, _ANSWER_SOURCE,
                               {"question": record.question, "answer": answer}, as_of=now)

        answers = [str(store.get_evidence(case_id, r.id).get("answer"))
                   for r in store.list_evidence(case_id) if r.source == _ANSWER_SOURCE]
        turns = sum(1 for r in store.list_evidence(case_id) if r.source == _TURN_SOURCE)
        if turns >= max_turns:
            return _give_up(record, repo, clock,
                            [f"접수 턴 상한({max_turns})을 넘겼다 — 대상 없이 조사한다"])

        locators = sorted(topology.locators()) if topology is not None else []
        out, err = await _call(deps, _turn_prompt(record, locators, answers))
        store.put_evidence(case_id, _TURN_SOURCE,
                           {"missing": out.missing if out else None,
                            "target_locator": out.target_locator if out else None,
                            "error": err}, as_of=now)
        if out is None:
            return _give_up(record, repo, clock, [f"접수 응답 파싱 실패 — {err}"])

        if out.missing:
            question = out.missing[0]
            current = repo.get(case_id)
            problem = _not_ours(current)      # LLM 호출 동안 가로채였을 수 있다
            if problem is not None:
                return IntakeTurn(status="error", problems=[problem])
            parked = current if current.status == "awaiting_human" \
                else transition(current, "awaiting_human", clock=clock)
            repo.save(parked.model_copy(update={"question": question,
                                                "question_kind": "intake"}))
            return IntakeTurn(status="asking", question=question)

        return _finish(record, repo, clock, out.target_locator)
    except Exception as exc:                                # noqa: BLE001 — 무raise 계약
        return IntakeTurn(status="error",
                          problems=[f"접수 실패 — {type(exc).__name__}: {exc}"])


def _not_ours(record) -> str | None:
    """접수가 이 레코드를 만질 수 있는가 — **턴 시작과 저장 직전에 같은 판정**을 쓴다.

    재읽기만 넣고 가드를 다시 적용하지 않으면 두 가지가 난다. LLM 호출 동안 워커가
    claim해 그래프가 파킹한 레코드를 접수가 `open`으로 되돌리면 **사람에게 물은
    질문이 소멸하고** requeue_open이 다시 집어 조사가 둘 붙는다. 그리고 아직
    `investigating`인 레코드를 접수가 파킹하면 워커의 `_finish`가
    awaiting_human→awaiting_human 전이에서 LifecycleError로 터져 케이스가 강제
    종결된다. `requeue_interval_s` 기본이 30초라 이 창은 이론이 아니다.

    `question_kind`는 **부재가 아니라 존재를 요구한다** — None은 계획 12 이전에
    그래프가 파킹한 레코드이고, "investigation이 아니면 통과"로 두면 그것들이
    전부 새어 들어와 스레드가 재개 불가능해진다.
    """
    if record.status == "closed":
        return "닫힌 케이스는 접수할 수 없다"
    if record.status == "investigating":
        return "조사 중인 케이스다 — 접수할 수 없다"
    if record.status == "awaiting_human" and record.question_kind != "intake":
        return "조사 질문에 파킹된 케이스다 — case resume으로 답하라"
    return None


def _save(repo, case_id: str, clock, *, unpark: bool, **fields) -> str | None:
    """접수가 소유한 필드만 얹어 저장한다 — read-modify-write(워커 모듈의 I1).

    턴 시작 시 읽은 스냅샷을 wholesale 저장하면 LLM 호출 동안 다른 경로가 바꾼
    것(게이트의 finding 첨부 등)을 잃는다. 저장 직전에 다시 읽고, **그때 다시
    가드를 적용한다** — 재읽기만으로는 가로채인 레코드를 되돌리는 것을 못 막는다.
    """
    current = repo.get(case_id)
    problem = _not_ours(current)
    if problem is not None:
        return problem
    base = transition(current, "open", clock=clock) \
        if unpark and current.status == "awaiting_human" else current
    repo.save(base.model_copy(update=fields))
    return None


def _finish(record, repo, clock, target_locator) -> IntakeTurn:
    problem = _save(repo, record.id, clock, unpark=True, target_locator=target_locator,
                    question=None, question_kind=None)
    if problem is not None:
        return IntakeTurn(status="error", problems=[problem])
    return IntakeTurn(status="done", target_locator=target_locator)


def _give_up(record, repo, clock, problems: list[str]) -> IntakeTurn:
    """접수를 포기하고 케이스를 조사 가능한 상태로 되돌린다.

    고아로 남기지 않는 것이 핵심이다 — 파킹된 채 질문만 있고 아무도 답할 수 없는
    케이스는 타임아웃까지 아무 일도 일어나지 않는다.
    """
    problem = _save(repo, record.id, clock, unpark=True, question=None, question_kind=None)
    return IntakeTurn(status="error", problems=[*problems, *( [problem] if problem else [] )])
