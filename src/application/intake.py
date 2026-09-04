"""접수 대화 — `chat` CLI가 그래프 밖에서 진행하는 3단계 접수 (스펙 §2.4 interrupt
원칙의 CLI판).

케이스를 열기 전에 자연어 증상에서 target_locator를 뽑아낸다. 그래프 안의 frame
노드처럼 다단계 조사·병렬 태스크를 벌이지 않는다 — 단발 LLM 호출 하나(+ 있으면
사람에게 되물어 한 번 더)로 끝난다. 진짜 조사는 케이스가 열린 뒤 엔진이 한다.

deps/gbm/fct(구현 결정): 브리프의 인터페이스 목록은 `deps, topology, clock, ask`
넷만 적지만, IntakeResult가 요구하는 gbm/fct를 채울 근거가 EngineDeps에도
Topology에도 없다(둘 다 site 식별자를 담지 않는다) — CLI의 `--gbm/--fct`가 유일한
근거다. 그래서 이 함수는 gbm/fct를 필수 키워드 인자로 추가로 받는다(호출부가
CLI에서 이미 확정한 사이트를 그대로 넘긴다). "파싱 이중 실패 → 첫 사이트로 진행"
(브리프)은 이 구현에서 "유일하게 주어진 사이트로 진행"과 같다 — CLI가 매번
정확히 한 사이트의 deps/topology만 넘기기 때문이다.

무raise 규율: LLM 호출·파싱이 전부 실패해도(네트워크 오류, 구조화 출력 불량 등)
접수 자체가 조사를 막아서는 안 된다 — target_locator=None으로 진행하고 qa에
실패를 기록할 뿐 여기서 raise하지 않는다.
"""
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from src.application.lifecycle import LifecycleError, transition
from src.application.schemas import parse_structured
from src.config.schema_app import StrictModel


class IntakeResult(StrictModel):
    """접수 결과 — CLI가 CaseRecord를 열 때 쓴다."""
    symptom: str
    gbm: str
    fct: str
    target_locator: str | None = None
    qa: list[dict] = []


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


def _format_answers(answers: list[dict]) -> str:
    return "\n".join(f"- {a['question']}: {a['answer']}" for a in answers)


async def intake(symptom: str, *, deps, topology, clock, gbm: str, fct: str,
                 ask: Callable[[str], Awaitable[str]] | None = None) -> IntakeResult:
    """증상 텍스트 하나로 target_locator를 접수한다 — 재시도는 최대 1회.

    1차 호출이 파싱에 실패했거나(out is None), 파싱은 됐지만 missing 질문이
    있고 ask가 주어졌으면 정확히 한 번 더 호출한다(missing이 있으면 먼저
    ask로 답을 모아 프롬프트에 얹는다). 그 재시도까지 파싱에 실패하면("이중
    실패") gbm/fct는 주어진 값 그대로, target_locator=None으로 IntakeResult를
    돌려주고 qa에 실패 사유를 남긴다 — 절대 raise하지 않는다.
    """
    qa: list[dict] = []
    locators = sorted(topology.locators()) if topology is not None else []
    prompt = _prompt(symptom, gbm, fct, locators)
    out, err = await _call(deps, prompt)

    needs_retry = out is None or (bool(out.missing) and ask is not None)
    if needs_retry:
        retry_prompt = prompt
        if out is not None and out.missing and ask is not None:
            answers = []
            for question in out.missing:
                answer = await ask(question)
                answers.append({"question": question, "answer": answer, "at": clock().isoformat()})
            qa.extend(answers)
            retry_prompt = f"{prompt}\n\n[추가 답변]\n{_format_answers(answers)}"
        out, err = await _call(deps, retry_prompt)

    if out is None:
        qa.append({"kind": "intake_failed", "reason": err or "알 수 없는 실패",
                   "at": clock().isoformat()})
        return IntakeResult(symptom=symptom, gbm=gbm, fct=fct, target_locator=None, qa=qa)

    if out.missing and ask is None:
        qa.append({"kind": "missing_unresolved", "questions": out.missing,
                   "at": clock().isoformat()})

    return IntakeResult(symptom=symptom, gbm=gbm, fct=fct,
                        target_locator=out.target_locator, qa=qa)


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
        if record.status == "closed":
            return IntakeTurn(status="error", problems=["닫힌 케이스는 접수할 수 없다"])
        if record.question_kind == "investigation":
            # 그래프가 파킹한 케이스에 접수를 이어가면 스레드를 잃는다. 재개는
            # awaiting_human→investigating이어야 하고, 그것은 호출부의 분기다.
            return IntakeTurn(status="error",
                              problems=["조사 질문에 파킹된 케이스다 — case resume으로 답하라"])

        now = clock()
        if answer is not None:
            store.put_evidence(case_id, _ANSWER_SOURCE,
                               {"question": record.question, "answer": answer}, as_of=now)

        answers = [str(store.get_evidence(case_id, r.id).get("answer"))
                   for r in store.list_evidence(case_id) if r.source == _ANSWER_SOURCE]
        turns = sum(1 for r in store.list_evidence(case_id) if r.source == _TURN_SOURCE)
        if turns >= max_turns:
            return _give_up(record, repo, store, clock,
                            [f"접수 턴 상한({max_turns})을 넘겼다 — 대상 없이 조사한다"])

        locators = sorted(topology.locators()) if topology is not None else []
        out, err = await _call(deps, _turn_prompt(record, locators, answers))
        store.put_evidence(case_id, _TURN_SOURCE,
                           {"missing": out.missing if out else None,
                            "target_locator": out.target_locator if out else None,
                            "error": err}, as_of=now)
        if out is None:
            return _give_up(record, repo, store, clock, [f"접수 응답 파싱 실패 — {err}"])

        if out.missing:
            question = out.missing[0]
            parked = record if record.status == "awaiting_human" \
                else transition(record, "awaiting_human", clock=clock)
            repo.save(parked.model_copy(update={"question": question,
                                                "question_kind": "intake"}))
            return IntakeTurn(status="asking", question=question)

        return _finish(record, repo, clock, out.target_locator)
    except Exception as exc:                                # noqa: BLE001 — 무raise 계약
        return IntakeTurn(status="error",
                          problems=[f"접수 실패 — {type(exc).__name__}: {exc}"])


def _unpark(record, clock):
    """파킹돼 있으면 open으로 되돌리고 질문을 지운다."""
    base = transition(record, "open", clock=clock) if record.status == "awaiting_human" \
        else record
    return base.model_copy(update={"question": None, "question_kind": None})


def _finish(record, repo, clock, target_locator) -> IntakeTurn:
    repo.save(_unpark(record, clock).model_copy(update={"target_locator": target_locator}))
    return IntakeTurn(status="done", target_locator=target_locator)


def _give_up(record, repo, store, clock, problems: list[str]) -> IntakeTurn:
    """접수를 포기하고 케이스를 조사 가능한 상태로 되돌린다.

    고아로 남기지 않는 것이 핵심이다 — 파킹된 채 질문만 있고 아무도 답할 수 없는
    케이스는 타임아웃까지 아무 일도 일어나지 않는다.
    """
    try:
        repo.save(_unpark(record, clock))
    except LifecycleError as exc:                           # noqa: BLE001
        problems = [*problems, f"상태 복구 실패 — {exc}"]
    return IntakeTurn(status="error", problems=problems)
