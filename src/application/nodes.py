"""조사 엔진 노드들 — 스펙 §2.4. 통제는 코드가, 판단은 LLM이.

모든 노드는 절대 raise하지 않고 부분 상태 update(dict)를 반환한다.
LLM 파싱은 재시도 1회 후 노드별 안전 경로로 강등된다.
"""
from langgraph.types import Send

from src.application.briefing import build_briefing, upstream_slice
from src.application.schemas import FrameOutput, parse_structured
from src.application.subagents import run_subagent
from src.domain.case import EvidenceRef, PlanTask, Verdict
from src.knowledge.topology import Topology

_FRAME_PROMPT = """너는 디지털 트윈 운영 조사의 리드다. 아래 브리핑을 읽고 초기 가설과 조사 계획을 세워라.

{briefing}

규칙:
- 가설 id는 h-1.., 태스크 id는 t-1.. 로 붙인다.
- role은 data_prober(데이터 조회)/code_tracer(코드 로직 규명)/recompute_verifier(재계산 대조) 중 하나.
- 재계산 태스크는 input_evidence_ids에 의존하는 증거 id를 적는다(없으면 빈 배열 — 아직 없으면 이후 라운드에서 추가된다).
- 반드시 JSON 하나만 출력한다:
{{"hypotheses": [{{"id": "h-1", "statement": "..."}}], "tasks": [{{"id": "t-1", "goal": "...", "role": "...", "input_evidence_ids": [], "priority": 10}}]}}"""


async def _ask_llm(llm, prompt, schema):
    """파싱 재시도 1회 계약 — (obj, None) 또는 (None, 마지막 오류)."""
    response = await llm.ainvoke([("user", prompt)])
    obj, err = parse_structured(response.content, schema)
    if obj is not None:
        return obj, None
    retry = await llm.ainvoke([
        ("user", f"{prompt}\n\n이전 응답은 다음 이유로 거부됐다: {err}\nJSON만 다시 출력하라.")])
    return parse_structured(retry.content, schema)


def make_nodes(deps):
    async def frame(state):
        case = state.case
        topo_slice = (upstream_slice(deps.topology, case.target_locator)
                      if case.target_locator else Topology())
        briefing = build_briefing(case, topo_slice, rules_text=deps.rules_text,
                                  history_text=deps.history_text, docs_text=deps.docs_text)
        output, err = await _ask_llm(deps.lead_llm, _FRAME_PROMPT.format(briefing=briefing),
                                     FrameOutput)
        if output is None:
            return {"verdict": Verdict(
                verdict_type="degraded", confidence="low",
                narrative="frame 출력 파싱 실패 — 조사 개시 불가", caveats=[err])}
        return {"hypotheses": output.hypotheses, "plan_tasks": output.tasks}

    async def select(state):
        # 실행 가능 게이트: pending이고 input_evidence_ids가 전부 state.evidence에 실재해야 한다.
        evidence_ids = {e.id for e in state.evidence}
        eligible = [task for task in state.plan_tasks
                   if task.status == "pending" and set(task.input_evidence_ids) <= evidence_ids]
        # (priority, 계획 등재 순)으로 정렬 — enumerate가 plan_tasks 상의 원래 순서를 보존한다.
        ordered = sorted(enumerate(eligible), key=lambda pair: (pair[1].priority, pair[0]))
        width = deps.engine_cfg.parallel_width
        chosen = [task for _, task in ordered[:width]]
        running = [task.model_copy(update={"status": "running"}) for task in chosen]
        return {"plan_tasks": running}

    async def execute(payload):
        # Send 페이로드에서 PlanTask 복원 — route_after_select가 만든 것이므로 형태는 신뢰한다.
        task = PlanTask.model_validate(payload["task"])
        case_id = payload["case_id"]
        try:
            budget = getattr(deps.engine_cfg.subagent_budgets, task.role)
            report = await run_subagent(task, adapters=deps.adapters, store=deps.store,
                                        llm=deps.subagent_llm, budget=budget, case_id=case_id)
            if report.status == "error":
                updated = task.model_copy(update={"status": "error", "error": report.error})
                return {"plan_tasks": [updated]}
            # ok — 도구가 실제로 만든 증거 id들을 Store 메타(봉투)와 함께 State로 승격한다.
            evidence = []
            for evidence_id in report.evidence_ids:
                record = deps.store.get_evidence_record(case_id, evidence_id)
                body = deps.store.get_evidence(case_id, evidence_id)
                evidence.append(EvidenceRef(
                    id=evidence_id, source=record.source, summary=repr(body)[:160],
                    as_of=record.as_of, complete=record.complete,
                    effective_as_of=record.effective_as_of))
            updated = task.model_copy(update={
                "status": "ok", "result_summary": report.summary,
                "result_evidence_ids": report.evidence_ids})
            return {"plan_tasks": [updated], "evidence": evidence}
        except Exception as exc:   # 최외곽 방어망 — 노드는 어떤 예외도 raise하지 않는다(superstep 보호)
            updated = task.model_copy(update={
                "status": "error", "error": f"execute 실패 — {type(exc).__name__}: {exc}"})
            return {"plan_tasks": [updated]}

    return {"frame": frame, "select": select, "execute": execute}


def route_after_frame(state):
    return "__end__" if state.verdict is not None else "select"


def route_after_select(state):
    # select가 방금 running으로 굴린 태스크만 Send로 fan out한다 — execute가 끝나면
    # ok/error로 바뀌므로 이 조건은 이번 라운드 몫만 정확히 잡아낸다.
    running = [task for task in state.plan_tasks if task.status == "running"]
    if not running:
        return "integrate"
    return [Send("execute", {"task": task.model_dump(mode="json"), "case_id": state.case.id})
            for task in running]
