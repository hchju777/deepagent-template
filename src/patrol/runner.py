"""점검 러너 — 프로브 실행부터 판정까지, 점검 하나의 전체 파이프라인 (스펙 §4.6).

절대 raise하지 않는다: 최외곽 try/except가 예상 밖 예외까지 마지막 방어선
으로 잡아 error 3상으로 돌린다. 순서는 고정이다 — ① 프로브 해석·실행
② 스냅샷 박제(성공한 프로브 결과는 이후 판정이 rule이든 llm이든 ok든
finding이든 상관없이 항상 증거로 남는다) ③ judge별 판정. 시계는 clock()
으로만 얻는다 — datetime.now() 직접 호출 금지(결정론 테스트 §5.5).
"""
from typing import Callable

from src.config.schema_site import CheckConfig
from src.domain.envelope import ProbeResult
from src.domain.patrol import CheckOutcome, Finding, scratch_case_id
from src.infrastructure.factory import AdapterSet
from src.patrol.llm_judge import LlmBudget, judge_by_llm
from src.patrol.probes import PROBES, resolve_probe
from src.patrol.rules import KnownRuleError, judge_by_rule

_DEFAULT_QUESTION = "이 증거에서 이상 징후가 있는가?"

_MakeFinding = Callable[[str, str, list[str]], Finding]


def _error(observed_at, msg: str, *, llm_calls: int = 0) -> CheckOutcome:
    return CheckOutcome(status="error", observed_at=observed_at, error=msg, llm_calls=llm_calls)


async def run_check(
    gbm: str, fct: str, name: str, check: CheckConfig, *,
    adapters: AdapterSet, store, clock, llm=None, budget: LlmBudget | None = None,
) -> CheckOutcome:
    """점검 하나를 실행하고 3상(ok/finding/skipped) + error를 돌려준다.

    llm이 실제로 필요한 지점(judge="llm"의 판정 진입부, judge="rule+llm"에서
    룰이 finding을 내고 예산까지 확보된 지점)에서 llm이 주입되지 않았으면
    그 자리에서 error("LLM 미주입")다. 스냅샷 박제(아래)는 이 검사보다
    먼저 끝난다 — 프로브가 성공했다면 판정 방식·결과와 무관하게 항상
    증거로 남아야 한다(모듈 docstring). rule+llm에서 룰이 ok로 끝나는
    경로는 애초에 llm을 참조하지 않으므로 이 검사 자체가 실행되지 않는다.
    """
    try:
        probe_name = resolve_probe(check)
        if probe_name is None:
            return _error(clock(), "프로브 해석 불가")
        result: ProbeResult = await PROBES[probe_name](adapters, check, clock=clock)
        observed_at = result.envelope.observed_at
        if result.status == "error":
            return _error(observed_at, result.error or "프로브 실행 실패")

        case_id = scratch_case_id(gbm, fct, name)
        snap_id = store.put_evidence(
            case_id, source=check.target or name, body=result.data,
            as_of=result.envelope.observed_at, complete=result.envelope.complete,
            effective_as_of=result.envelope.effective_as_of,
        )

        def make_finding(summary: str, judge: str, evidence_ids: list[str]) -> Finding:
            return Finding(
                id=f"{name}@{observed_at.isoformat()}", gbm=gbm, fct=fct, check=name,
                target=check.target, summary=summary, evidence_ids=evidence_ids,
                scratch_case_id=case_id, observed_at=observed_at, judge=judge,
            )

        if check.judge == "rule":
            return _judge_rule(check, result, clock, observed_at, snap_id, make_finding)
        if check.judge == "llm":
            return await _judge_llm(name, check, result, snap_id, llm, budget, observed_at, make_finding)
        return await _judge_rule_then_llm(
            name, check, result, clock, snap_id, llm, budget, observed_at, make_finding)
    except Exception as exc:
        return _error(clock(), f"러너 실행 실패 — {type(exc).__name__}: {exc}")


def _judge_rule(check, result, clock, observed_at, snap_id, make_finding: _MakeFinding) -> CheckOutcome:
    try:
        verdict = judge_by_rule(result, check.params, clock=clock)
    except KnownRuleError as exc:
        return _error(observed_at, f"rule 설정 오류 — {exc}")
    if verdict.status == "finding":
        finding = make_finding(verdict.reason, "rule", [snap_id])
        return CheckOutcome(status="finding", observed_at=observed_at, finding=finding)
    return CheckOutcome(status="ok", observed_at=observed_at)


async def _call_llm(name: str, check: CheckConfig, result: ProbeResult, snap_id: str, llm):
    question = check.params.get("question") or _DEFAULT_QUESTION
    snapshot_texts = {snap_id: repr(result.data)[:2000]}
    return await judge_by_llm([snap_id], snapshot_texts, name, question, llm=llm)


async def _judge_llm(
    name, check, result, snap_id, llm, budget: LlmBudget | None, observed_at, make_finding: _MakeFinding,
) -> CheckOutcome:
    if llm is None:  # judge="llm"은 이 판정 자체가 LLM 없이는 존재하지 않는다 — 무조건 필요
        return _error(observed_at, "LLM 미주입")
    if budget is None or not budget.try_acquire():
        return CheckOutcome(status="skipped", observed_at=observed_at, skipped_reason="llm 예산 소진")
    out, err = await _call_llm(name, check, result, snap_id, llm)
    if out is None:
        return _error(observed_at, err, llm_calls=1)
    if out.status == "finding":
        finding = make_finding(out.summary, "llm", out.evidence_ids)
        return CheckOutcome(status="finding", observed_at=observed_at, finding=finding, llm_calls=1)
    return CheckOutcome(status="ok", observed_at=observed_at, llm_calls=1)


async def _judge_rule_then_llm(
    name, check, result, clock, snap_id, llm, budget: LlmBudget | None, observed_at,
    make_finding: _MakeFinding,
) -> CheckOutcome:
    try:
        verdict = judge_by_rule(result, check.params, clock=clock)
    except KnownRuleError as exc:
        return _error(observed_at, f"rule 설정 오류 — {exc}")
    if verdict.status == "ok":
        return CheckOutcome(status="ok", observed_at=observed_at)

    if budget is None or not budget.try_acquire():
        if check.on_budget_exhausted == "escalate":
            summary = f"{verdict.reason} (LLM 미확인 — 예산 소진)"
            finding = make_finding(summary, "rule+llm", [snap_id])
            return CheckOutcome(status="finding", observed_at=observed_at, finding=finding)
        return CheckOutcome(status="skipped", observed_at=observed_at, skipped_reason="llm 예산 소진")

    if llm is None:  # 룰 finding + 예산 확보 — 이제야 LLM이 실제로 필요해진다
        return _error(observed_at, "LLM 미주입")

    out, err = await _call_llm(name, check, result, snap_id, llm)
    if out is None:
        return _error(observed_at, err, llm_calls=1)
    if out.status == "finding":
        finding = make_finding(out.summary, "rule+llm", out.evidence_ids)
        return CheckOutcome(status="finding", observed_at=observed_at, finding=finding, llm_calls=1)
    return CheckOutcome(status="ok", observed_at=observed_at, llm_calls=1)
