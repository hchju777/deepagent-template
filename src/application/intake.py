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
from typing import Awaitable, Callable

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
