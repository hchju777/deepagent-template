"""접수 — 케이스가 열린 뒤 대상(`target_locator`)을 턴 단위로 확정한다 (스펙 §4.4).

그래프 안의 frame 노드처럼 다단계 조사·병렬 태스크를 벌이지 않는다 — 한 턴이 LLM
호출 하나다. 진짜 조사는 접수가 끝난 뒤 엔진이 한다.

**턴 단위인 이유**: 이전 구조는 `ask` 콜백으로 프로세스 안에서 되묻고 문답을
마지막에 한 번 돌려줬다. 클라이언트가 끊기거나 서버가 재시작되면 전부 사라진다.
지금은 한 호출이 끝나거나 케이스를 파킹하고, 다음 호출이 답을 들고 이어간다 —
답은 **이어가기 전에 먼저** 증거로 박제된다.

**동시성**: 접수는 lease를 잡지 않는다. 대신 `_not_ours`가 **턴 시작·파킹 직전·저장
직전 세 지점**에서 같은 판정을 걸어, 그 사이 워커가 가로챈 레코드를 **되돌리지
않는다**(증거 기록은 별개다 — 가드 통과 후 가로채이면 그 턴의 증거는 이미 써진다.
증거는 append-only라 상태를 되돌리지 않으므로 해롭지 않다). 재읽기만으로는 부족한
이유는 `_not_ours` docstring에 있고, **남은 창**은 `_save` docstring에 있다.

무raise 규율: LLM 호출·파싱이 전부 실패해도 접수가 조사를 막아서는 안 된다 —
`target_locator=None`으로 진행하고 실패를 `problems`에 남길 뿐 raise하지 않는다.
"""
from datetime import datetime
from typing import Any, Callable, Literal

from src.domain.patrol import fingerprint
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


class IntakeTurn(StrictModel):
    # not_ours와 error를 가르는 이유: **호출부가 다르게 다뤄야 한다.** error는
    # "접수를 포기했으니 대상 없이 조사하라"이고, not_ours는 "다른 주체가 이
    # 레코드를 들고 있으니 아무것도 하지 마라"다. 둘을 뭉치면 호출부가 run_once를
    # 걸고, 그래프가 파킹한 케이스라면 새 스레드로 처음부터 재조사돼 원래 스레드와
    # 사람에게 물은 질문을 잃는다 — lifecycle.py가 금지한 그것이다.
    status: Literal["done", "asking", "error", "not_ours"]
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
                      max_turns: int = 3, on_event: Callable | None = None) -> IntakeTurn:
    """접수 한 턴 — LLM을 한 번 부르고 끝나거나 파킹한다. 절대 raise하지 않는다.

    `answer`가 있으면 **이어가기 전에 먼저 증거로 박제한다.** 미루면 그 사이
    프로세스가 죽었을 때 사람의 답이 사라진다 — 계획 4b의 F3 경로가 human:answer에서
    같은 판단을 했다.

    `max_turns`는 **코드가 쥐는 상한**이다(규율 6). 턴으로 바꾸면 호출자가 무한히
    부를 수 있는데, 넘으면 대상 없이 조사에 들어간다 — 기존 "이중 실패"와 같은
    착지점이라 새 실패 모양을 만들지 않는다.
    """
    try:
        try:
            record = repo.get(case_id)
        except KeyError:
            return IntakeTurn(status="error", problems=[f"케이스 {case_id}를 찾을 수 없다"])
        problem = _not_ours(record)
        if problem is not None:
            return IntakeTurn(status="not_ours", problems=[problem])

        now = clock()
        if answer is not None:
            store.put_evidence(case_id, _ANSWER_SOURCE,
                               {"question": record.question, "answer": answer}, as_of=now)

        answers = [str(store.get_evidence(case_id, r.id).get("answer"))
                   for r in store.list_evidence(case_id) if r.source == _ANSWER_SOURCE]
        turns = sum(1 for r in store.list_evidence(case_id) if r.source == _TURN_SOURCE)
        if turns >= max_turns:
            return _give_up(record, repo, clock,
                            [f"접수 턴 상한({max_turns})을 넘겼다 — 대상 없이 조사한다"], on_event)

        locators = sorted(topology.locators()) if topology is not None else []
        out, err = await _call(deps, _turn_prompt(record, locators, answers))
        store.put_evidence(case_id, _TURN_SOURCE,
                           {"missing": out.missing if out else None,
                            "target_locator": out.target_locator if out else None,
                            "error": err}, as_of=now)
        if out is None:
            return _give_up(record, repo, clock, [f"접수 응답 파싱 실패 — {err}"], on_event)

        if out.missing:
            question = out.missing[0]
            current = repo.get(case_id)
            problem = _not_ours(current)      # LLM 호출 동안 가로채였을 수 있다
            if problem is not None:
                return IntakeTurn(status="not_ours", problems=[problem])
            problem = _park(record, repo, clock, question)
            if problem is not None:
                return IntakeTurn(status="not_ours", problems=[problem])
            _emit(on_event, case_id, "awaiting_human", clock)
            return IntakeTurn(status="asking", question=question)

        return _finish(record, repo, clock, out.target_locator, on_event)
    except Exception as exc:                                # noqa: BLE001 — 무raise 계약
        return IntakeTurn(status="error",
                          problems=[f"접수 실패 — {type(exc).__name__}: {exc}"])


def _not_ours(record) -> str | None:
    """접수가 이 레코드를 만질 수 있는가 — **턴 시작과 저장 직전에 같은 판정**을 쓴다.

    턴 시작·파킹 직전·저장 직전 세 곳에서 부른다. 재읽기만 넣고 가드를 다시
    적용하지 않으면 두 가지가 난다. LLM 호출 동안 워커가
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
    if record.status == "open" and record.intake_done:
        # 접수가 끝난 케이스에 또 턴을 돌면 워커가 집을 수 있는 케이스와 _save의
        # TOCTOU 창이 다시 열린다 — intake_done 문이 최초 창에만 유효했다(리뷰).
        return "접수가 이미 끝난 케이스다"
    return None


def _emit(on_event, case_id: str, status: str, clock) -> None:
    """파킹/언파킹을 이벤트로 낸다 — 없으면 SSE가 접수 진행을 전혀 못 본다(리뷰 S9).
    어휘 밖의 새 종류가 아니라 case_status_changed다(규율 7)."""
    if on_event is None:
        return
    try:
        from src.application.events import case_status_event
        on_event(case_status_event(case_id, status, clock=clock))
    except Exception:                                              # noqa: BLE001
        pass                    # 이벤트 실패가 접수를 막아서는 안 된다


def _save(repo, case_id: str, clock, *, unpark: bool, expect_updated_at, **fields) -> str | None:
    """접수가 소유한 필드만 얹어 저장한다 — read-modify-write(워커 모듈의 I1).

    턴 시작 시 읽은 스냅샷을 wholesale 저장하면 LLM 호출 동안 다른 경로가 바꾼
    것(게이트의 finding 첨부 등)을 잃는다. 저장 직전에 다시 읽고, **그때 다시
    가드를 적용한다** — 재읽기만으로는 가로채인 레코드를 되돌리는 것을 못 막는다.

    **이 창은 좁혔을 뿐 닫히지 않았다.** `repo.get` → 판정 → `repo.save` 사이는
    원자적이 아니고 이 저장은 CAS가 없다(Mongo 구현은 문서 전체 `$set`). 계획 13은
    **워커 쪽 경로를 없애는 것**으로 답했다 — `intake_done`이 False인 케이스는
    requeue가 안 집고, 접수가 끝난 케이스에는 이 함수가 더 이상 턴을 돌지 않는다
    (`_not_ours`). **남는 창은 같은 케이스에 동시에 오는 두 접수 요청**이다 — 계획 13
    리뷰(S3)가 실증했다: 증거가 중복되고, 한 순서에서는 `awaiting_human`인데
    `intake_done=True`이고 대상까지 설정된 모순 레코드가 남는다. 닫으려면 이 저장에도
    CAS가 필요하고, 그것은 `attach_answer`가 연 조건부 `$set` 형태를 그대로 쓰면 된다
    (계획 14 인계). 지금은 그 사이 requeue가 못 집는다는 것만 보장한다.
    """
    current = repo.get(case_id)
    problem = _not_ours(current)
    if problem is not None:
        return problem
    base = transition(current, "open", clock=clock) \
        if unpark and current.status == "awaiting_human" else current
    # 읽은 시점의 updated_at을 술어로 걸어 조건부로 쓴다(계획 17). 재읽기만으로는
    # 창이 좁아질 뿐 닫히지 않았다 — 같은 케이스에 동시에 온 두 접수 요청이 증거를
    # 중복시키고, 한 순서에서는 `awaiting_human`인데 `intake_done=True`이고 대상까지
    # 설정된 모순 레코드를 남겼다(계획 13 리뷰 S3이 실증했다).
    merged = {k: v for k, v in base.model_dump(mode="python").items()
              if k in _SAVED_FIELDS} | dict(fields)
    # 술어는 **턴이 시작할 때 읽은** 값이다. 재읽기 값으로 걸면 창이 좁아질 뿐 닫히지
    # 않는다 — 두 턴이 각자 읽고 각자 LLM을 돌린 뒤 각자 재읽고 쓰면 둘 다 이긴다.
    if not repo.update_if_unchanged(case_id, expect_updated_at=expect_updated_at,
                                    fields=merged, now=clock()):
        return "접수 중 다른 주체가 레코드를 바꿨다 — 이 턴은 손을 뗀다"
    return None


# 접수가 소유한 필드. 전체 레코드를 쓰면 그 사이 남이 바꾼 것(게이트의 finding 첨부 등)을
# 되돌린다 — CAS가 그것을 감지하지만, 애초에 우리 것만 쓰는 편이 낫다.
_SAVED_FIELDS = ("status", "status_since", "question", "question_kind", "question_seq",
                 "intake_done", "target_locator", "fingerprint")


def _park(record, repo, clock, question: str) -> str | None:
    """되묻기도 접수가 소유한 필드를 쓴다 — `_save`와 같은 술어로 보호한다(계획 17).

    여기만 CAS가 빠지면 되묻기 턴에서 같은 모순 레코드가 생긴다.
    """
    current = repo.get(record.id)
    problem = _not_ours(current)
    if problem is not None:
        return problem
    parked = current if current.status == "awaiting_human" \
        else transition(current, "awaiting_human", clock=clock)
    return _save(repo, record.id, clock, unpark=False,
                 expect_updated_at=record.updated_at,
                 status=parked.status, status_since=parked.status_since,
                 question=question, question_kind="intake",
                 question_seq=parked.question_seq + 1)


def _finish(record, repo, clock, target_locator, on_event=None) -> IntakeTurn:
    was_parked = record.status == "awaiting_human"
    # 대상이 정해진 지금이 지문을 고칠 수 있는 첫 시점이다(계획 15/P8). 개설 시점의
    # 지문은 case_id가 재료라 human 케이스끼리 절대 안 겹쳤고, 그대로 두면 tier 1
    # 이력 검색이 human 케이스에서 영원히 빈손이다. "chat" 성분은 유지한다 — 순찰
    # 지문은 점검 이름을 쓰므로 네임스페이스가 갈라져 있고, 게이트가 순찰 finding을
    # 사람이 연 케이스에 붙이는 일이 생기지 않는다. 사람이 연 두 케이스가 같은 지문을
    # 갖는 것은 이제 의도다(open_case는 지문 중복 억제를 하지 않는다).
    problem = _save(repo, record.id, clock, unpark=True,
                    expect_updated_at=record.updated_at, target_locator=target_locator,
                    fingerprint=fingerprint(record.gbm, record.fct, "chat", target_locator),
                    question=None, question_kind=None, intake_done=True)
    if problem is not None:
        return IntakeTurn(status="not_ours", problems=[problem])
    if was_parked:
        _emit(on_event, record.id, "open", clock)      # 언파킹도 진행이다
    return IntakeTurn(status="done", target_locator=target_locator)


def _give_up(record, repo, clock, problems: list[str], on_event=None) -> IntakeTurn:
    """접수를 포기하고 케이스를 조사 가능한 상태로 되돌린다.

    고아로 남기지 않는 것이 핵심이다 — 파킹된 채 질문만 있고 아무도 답할 수 없는
    케이스는 타임아웃까지 아무 일도 일어나지 않는다. 다만 그 사이 남이 레코드를
    가져갔으면 되돌리지 않고 `not_ours`로 손을 뗀다.
    """
    # 포기도 문을 연다 — 대상 없이 조사하는 것이 착지점이고, 문을 안 열면 영영 안 집힌다.
    problem = _save(repo, record.id, clock, unpark=True,
                    expect_updated_at=record.updated_at, question=None, question_kind=None,
                    intake_done=True)
    if problem is not None:
        # 포기하려 했으나 그 사이 남이 가져갔다 — 상태를 되돌리지 않고 손을 뗀다.
        return IntakeTurn(status="not_ours", problems=[*problems, problem])
    if record.status == "awaiting_human":
        _emit(on_event, record.id, "open", clock)
    return IntakeTurn(status="error", problems=problems)
