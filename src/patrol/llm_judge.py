"""LLM 판정기 + 시간당 호출 예산 — 스펙 §4.6.

점검(check)과 조사(investigation)는 다르다: 판정기는 스냅샷 텍스트만 보고
단일 ainvoke로 ok/finding을 고른다 — 도구는 주지 않는다(조사 서브에이전트와
달리 다단계 추론이 필요 없고, 예산을 지키려면 호출 한 번으로 끝나야 한다).
재시도도 없다 — parse_structured 파싱이 실패해도 다시 부르지 않고 그대로
error 3상으로 돌려준다(예산 절약이 재시도보다 우선).

전역 계약: judge_by_llm은 절대 raise하지 않는다. ainvoke 전송 실패, 파싱
실패, 환각 인용(존재하지 않는 snapshot id를 finding 근거로 댄 경우) 모두
(None, 한국어 사유) 튜플로 돌아온다.
"""
from datetime import datetime
from typing import Literal

from src.application.schemas import parse_structured
from src.config.schema_app import StrictModel


class LlmBudget:
    """시간당 LLM 호출 예산 — 최근 3600초 슬라이딩 창 안의 호출 수로 제한한다.

    고정된 만료 시각이 아니라 매 호출마다 clock()으로 다시 창을 재는 이유는
    ScriptedLLM과 마찬가지로 테스트가 가짜 시계를 밀어 결정론적으로 창의
    회복을 검증할 수 있게 하기 위해서다(스펙 §5.5).
    """

    def __init__(self, max_calls_per_hour: int, *, clock):
        self._max = max_calls_per_hour
        self._clock = clock
        self._calls: list[datetime] = []

    def _prune(self) -> None:
        now = self._clock()
        self._calls = [t for t in self._calls if (now - t).total_seconds() <= 3600]

    def try_acquire(self) -> bool:
        """최근 3600초 내 호출 수가 상한 미만이면 지금 시각을 기록하고 True.

        상한이 0이면 창을 정리한 뒤에도 len(calls)=0 >= 0이 항상 참이라
        별도 분기 없이 항상 False가 된다.
        """
        self._prune()
        if len(self._calls) >= self._max:
            return False
        self._calls.append(self._clock())
        return True

    def remaining(self) -> int:
        self._prune()
        return max(0, self._max - len(self._calls))


class LlmJudgeOutput(StrictModel):
    status: Literal["ok", "finding"]
    summary: str
    evidence_ids: list[str] = []


_INSTRUCTION = (
    'finding이면 근거로 쓴 증거 id를 evidence_ids에 그대로 적어라. '
    'JSON만 출력하라: {"status": "ok 또는 finding", "summary": "...", '
    '"evidence_ids": ["ev-1", ...]}'
)


def _build_prompt(snapshot_ids: list[str], snapshot_texts: dict[str, str],
                   check_name: str, question: str) -> str:
    lines = [f"[증거 {sid}] {str(snapshot_texts.get(sid, ''))[:2000]}" for sid in snapshot_ids]
    evidence_block = "\n".join(lines) if lines else "(증거 없음)"
    return (
        f"점검 이름: {check_name}\n"
        f"질문: {question}\n\n"
        f"{evidence_block}\n\n"
        f"{_INSTRUCTION}"
    )


async def judge_by_llm(
    snapshot_ids: list[str], snapshot_texts: dict[str, str],
    check_name: str, question: str, *, llm,
) -> tuple[LlmJudgeOutput | None, str | None]:
    """스냅샷 텍스트를 단일 ainvoke로 ok/finding 판정한다.

    반환 evidence_ids는 LLM이 댄 것을 그대로 믿지 않고 snapshot_ids와의
    교집합으로 정제한다(snapshot_ids 순서 유지) — 지어낸 id는 여기서 소멸한다.
    정제 후에도 finding인데 인용이 하나도 안 남으면, 근거 없는 finding이므로
    아예 기각한다(환각으로 인한 가짜 이상 탐지를 막는 방어선).
    """
    prompt = _build_prompt(snapshot_ids, snapshot_texts, check_name, question)
    try:
        response = await llm.ainvoke([("user", prompt)])
    except Exception as exc:
        return None, f"LLM 호출 실패 — {type(exc).__name__}: {exc}"
    out, err = parse_structured(response.content, LlmJudgeOutput)
    if out is None:
        return None, err
    cited = [sid for sid in snapshot_ids if sid in out.evidence_ids]
    if out.status == "finding" and not cited:
        return None, "환각 인용 — 근거 없는 finding 기각"
    return out.model_copy(update={"evidence_ids": cited}), None
