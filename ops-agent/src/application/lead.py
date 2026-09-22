"""리드 LLM — `frame`(가설과 계획)과 `integrate`(갱신과 결정).

## 무엇을 LLM이 정하고 무엇을 코드가 쥐는가

LLM은 **가설 내용 · 무엇을 볼지 · 계속할지 끝낼지**를 정한다. 그 밖은 전부 코드다:
라운드 상한 · 병렬 폭 · 실행 가능 판정 · 태스크 개수 상한 · 수명주기 소독
(`nodes.py`의 표 참고). 여기서 LLM이 하는 일은 **JSON 하나를 내는 것**이고, 그
JSON이 State에 들어가기 전에 전부 검사된다.

## 실패가 조용하면 안 된다

LLM이 죽거나 JSON이 안 나오면 태스크가 0개가 되고, 그러면 `no_runnable`로 끝난다 —
**"조사했는데 아무것도 안 했다"가 "조사할 게 없었다"와 같은 모양이 된다.** 5단계의
`unreachable` 문제와 정확히 같다.

그래서 `llm_errors`에 사유를 쌓고 `stopped_by="llm_error"`로 끝낸다. 12a가 이걸 보고
"미확정"이 아니라 **"조사 실패(degraded)"**로 낙인한다.

## 재시도는 한 번뿐이다

파싱 실패는 모델이 형식을 놓친 것이라 한 번 더 물으면 대개 된다. 두 번 이상은
같은 실패를 반복하며 라운드 시간만 늘린다 — 조사는 라운드마다 LLM을 부르므로
재시도가 길면 전체가 늘어진다.
"""
import asyncio
import re
from typing import Literal

from pydantic import Field

from src.application import briefing
from src.application.schemas import Parsed, parse_object, validate
from src.application.state import CaseState
from src.domain.base import StrictModel
from src.domain.case import Hypothesis, PlanTask
from src.domain.llm import LlmPort

RETRIES = 1
# 전송 오류(게이트웨이 403·타임아웃 등) 뒤에 쉬는 시간. 사내 세 번째 트레이스에서 되물음이
# 앞 호출 직후에 나가자 게이트웨이가 403을 냈다 — 연달아 두 번 부른 것이 원인으로 보인다.
RETRY_BACKOFF_S = 2.0


def fill(template: str, fields: dict[str, str]) -> str:
    """템플릿의 `{이름}` 자리를 채운다.

    **`str.format`을 쓰지 않는다.** 프롬프트에 JSON 예시가 들어 있어서 `{`가 그대로
    있고, `format`은 그걸 자리로 읽어 `KeyError`로 죽는다. 9e가 같은 함정을 겪고
    주석까지 남겨 뒀다(`report/comment.py`의 `build_prompt`).

    치환 자리가 몇 개뿐이라 replace가 맞다.

    **여기서는 남은 자리를 검사하지 않는다.** 오타 난 `{max_round}`는 라운드마다
    똑같이 남으므로 매번 검사할 이유가 없고, 여기서 발견해 봐야 조사가 이미 시작된
    뒤다. 템플릿은 **기동 때 한 번** `slots_in`으로 검사한다
    (`__main__._load_lead_prompt`) — 9e에서 `{max_chars}`가 치환되지 않은 채 LLM에게
    나간 적이 있고, 리포트는 정상으로 보여서 아무도 못 봤다.
    """
    filled = template
    for name, value in fields.items():
        filled = filled.replace("{" + name + "}", value)
    return filled


def slots_in(template: str) -> set[str]:
    """템플릿이 쓰는 `{이름}` 자리들.

    프롬프트에는 JSON 예시가 들어 있어 `{`가 널려 있다. 그래서 **식별자 모양만**
    자리로 센다 — `{ "decision": ... }`는 공백과 따옴표가 있어 안 걸리고,
    `"params": {}`도 안 걸린다.
    """
    return set(_SLOT.findall(template))


_SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class FrameReply(StrictModel):
    hypotheses: list[Hypothesis] = []
    tasks: list[PlanTask] = []


class IntegrateReply(StrictModel):
    decision: Literal["continue", "conclude"] = "continue"
    hypotheses: list[Hypothesis] = []
    tasks: list[PlanTask] = []
    # 모델이 설명을 덧붙이고 싶어 하는 자리. 없으면 지어내서 다른 칸에 넣는다.
    note: str = Field(default="", max_length=2000)


def repair_prompt(prompt: str, reason: str) -> str:
    """재시도 프롬프트 — **무엇이 틀렸는지 붙여서** 다시 묻는다.

    같은 프롬프트를 한 번 더 보내는 것은 약한 모델에겐 재시도가 아니다. 사내 모델은
    판단해서 답을 고르는 게 아니라 **주어진 틀을 채운다** — 같은 틀을 주면 같은 답이
    온다. 사유를 붙여야 그게 새 입력이 된다.

    사유를 **맨 뒤에** 붙이는 이유: 모델은 마지막에 읽은 지시를 더 따른다(9e의 울타리가
    <사실> 뒤에도 한 겹 있는 것과 같은 이유).
    """
    return (f"{prompt}\n\n---\n\n## 다시\n\n앞의 답을 읽을 수 없었다: **{reason}**\n\n"
            f"위 형식 그대로, **JSON 객체 하나만** 내라. 설명도 코드펜스도 붙이지 마라.")


async def ask_json(llm: LlmPort, prompt: str, model: type[StrictModel], *,
                   on_exchange=None) -> Parsed:
    """묻고, JSON을 꺼내고, 모델로 검증한다. **절대 raise하지 않는다.**

    `on_exchange(prompt, reply_text, error)`는 시도마다 불린다 — `--trace`가 여기서
    날것을 건진다. **트레이스가 던져도 조사는 계속돼야 한다**(아래 흡수).
    """
    last = Parsed(False, error="시도하지 않았다")
    asked = prompt
    transport = False           # 직전 실패가 모델의 답이 아니라 **호출 자체**였나
    for attempt in range(RETRIES + 1):
        if attempt and transport:
            # 호출이 실패한 것은 모델이 틀린 것이 아니다. "앞의 답을 읽을 수 없었다:
            # OpenAIPermissionDeniedError…"를 붙여 다시 물으면 모델에게 오류 문자열을
            # 고치라고 시키는 꼴이다 — 사내에서 실제로 그렇게 나갔다. 같은 프롬프트로,
            # 잠깐 쉬고 다시 부른다.
            await asyncio.sleep(RETRY_BACKOFF_S)
            asked = prompt
        elif attempt:
            # 사유를 실어 다시 묻는다. 사유가 없으면 같은 질문을 반복하는 것과 같다.
            asked = repair_prompt(prompt, last.error or "알 수 없음")
        text, failure = None, None
        try:
            reply = await llm.ask(asked)
        except Exception as exc:                                    # noqa: BLE001
            # 어댑터가 계약을 어기고 던져도 superstep이 죽으면 안 된다.
            last = Parsed(False, error=f"{type(exc).__name__}: {exc}")
            transport = True
        else:
            transport = reply.status == "error"
            if reply.status == "error":
                last = Parsed(False, error=reply.error or "알 수 없는 LLM 오류")
            else:
                text = reply.text or ""
                parsed = parse_object(text)
                last = validate(parsed.data, model) if parsed.ok else parsed
        failure = None if last.ok else last.error
        _tell(on_exchange, asked, text, failure)
        if last.ok:
            return last
    return Parsed(False, error=f"{RETRIES + 1}회 시도 실패 — {last.error}")


def _tell(on_exchange, prompt: str, text, error) -> None:
    if on_exchange is None:
        return
    try:
        on_exchange(prompt, text, error)
    except Exception:                                               # noqa: BLE001
        pass          # 트레이스는 편의다. 이것 때문에 조사가 멈추면 안 된다


def _failure(where: str, reason: str) -> dict:
    return {"llm_errors": [f"{where}: {reason}"],
            "decision": "conclude", "stopped_by": "llm_error"}


def _dropped_note(where: str, got: Parsed) -> list[str]:
    """걷어낸 곁다리 키를 `llm_errors`에 남길 한 줄. 조용히 고치지 않는다."""
    if not got.dropped:
        return []
    return [f"{where}: 스키마에 없는 키를 걷어냈다 — {', '.join(got.dropped)}"]


def make_lead(llm: LlmPort, *, site_config, prompts: dict[str, str], max_rounds: int,
              evidence_budget: int = 12000, trace=None,
              services: tuple[str, ...] = (), roles: dict[str, str] | None = None):
    """`EngineDeps`의 `frame`·`integrate` 자리에 꽂을 두 함수를 만든다.

    `services`는 대상 코드(11a)가 준비됐을 때만 채워진다. 비어 있으면 `code.*`가
    목록에도 예시에도 안 나온다 — 없는 문을 열라고 적어 두면 리드가 거기로 가고,
    매 라운드가 "미등재 action"으로 날아간다.

    `trace(node, round, prompt, reply_text, error)`를 주면 매 시도가 그대로 흘러간다.
    **프롬프트를 고치려면 모델이 뭐라 했는지 봐야 한다** — 10b를 끝낼 때 이게 없어서
    프롬프트 설계가 전부 추측 위에 있었다.
    """

    def _hook(node: str, state: CaseState):
        if trace is None:
            return None
        return lambda prompt, text, error: trace(node, state.round, prompt, text, error)

    async def frame(state: CaseState) -> dict:
        prompt = fill(prompts["frame"],
                      briefing.frame_fields(state, site_config=site_config,
                                            services=services, roles=roles))
        got = await ask_json(llm, prompt, FrameReply, on_exchange=_hook("frame", state))
        if not got.ok:
            return _failure("frame", got.error)
        return {"hypotheses": [Hypothesis.model_validate(h) for h in got.data["hypotheses"]],
                "plan_tasks": [PlanTask.model_validate(t) for t in got.data["tasks"]],
                "llm_errors": _dropped_note("frame", got)}

    async def integrate(state: CaseState) -> dict:
        prompt = fill(prompts["integrate"],
                      briefing.integrate_fields(state, site_config=site_config,
                                                max_rounds=max_rounds,
                                                evidence_budget=evidence_budget,
                                                services=services, roles=roles))
        got = await ask_json(llm, prompt, IntegrateReply,
                             on_exchange=_hook("integrate", state))
        if not got.ok:
            return _failure("integrate", got.error)
        return {"decision": got.data["decision"],
                "hypotheses": [Hypothesis.model_validate(h) for h in got.data["hypotheses"]],
                "plan_tasks": [PlanTask.model_validate(t) for t in got.data["tasks"]],
                "llm_errors": _dropped_note("integrate", got)}

    return frame, integrate
