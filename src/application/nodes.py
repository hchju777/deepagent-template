"""조사 엔진 노드들 — 스펙 §2.4. 통제는 코드가, 판단은 LLM이.

모든 노드는 절대 raise하지 않고 부분 상태 update(dict)를 반환한다.
LLM 파싱은 재시도 1회 후 노드별 안전 경로로 강등된다.
"""
from langgraph.types import Send, interrupt

from src.application.briefing import build_briefing, upstream_slice
from src.application.schemas import FrameOutput, IntegrateOutput, parse_structured
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

_INTEGRATE_PROMPT = """너는 디지털 트윈 운영 조사의 리드다. 지금까지의 조사 진행 상황을 검토하고
가설 보드를 갱신하라. 그리고 다음 라운드로 계속할지, 사람에게 물을지, 조사를 마칠지 결정하라.

[가설 보드]
{hypotheses}

[태스크 현황]
{tasks}

[증거 목록]
{evidence}

[라운드] {round}/{max_rounds}

규칙:
- hypotheses는 갱신하는 가설만 담는다(같은 id는 교체된다).
- new_tasks로 추가 조사가 필요하면 태스크를 제안한다(기존 계획에 없는 새 id를 쓴다).
- cancel_task_ids로 더 이상 필요 없는 대기 중 태스크를 취소할 수 있다.
- decision은 continue(다음 라운드 진행) / ask(사람에게 질문) / conclude(조사 종료) 중 하나.
- ask를 고르면 question에 구체적인 질문을 적는다.
- 반드시 JSON 하나만 출력한다:
{{"hypotheses": [{{"id": "h-1", "statement": "...", "status": "open"}}], "new_tasks": [], "cancel_task_ids": [], "decision": "continue", "question": null}}"""

_CONCLUDE_PROMPT = """너는 디지털 트윈 운영 조사의 리드다. 지금까지 모은 증거로 최종 판정을 작성하라.

[가설 보드]
{hypotheses}

[증거 목록]
{evidence}

[태스크 오류율]
{error_rate}{rewrite_note}

규칙:
- 모든 주장에는 실재하는 증거 id를 인용해야 한다 — 없는 id를 지어내면 안 된다.
- complete=False인 증거를 근거로 쓰면 caveats에 그 증거 id를 명시한다.
- 확신이 서지 않으면 verdict_type을 inconclusive로 남겨도 된다 — 억지 결론 금지.
- 반드시 JSON 하나만 출력한다:
{{"verdict_type": "logic_bug", "root_cause": {{"component": "...", "evidence_ids": ["ev-1"]}}, "contributing": [], "confidence": "high", "recommendations": [], "caveats": [], "narrative": "..."}}"""


def _format_hypothesis_board(hypotheses):
    if not hypotheses:
        return "없음"
    return "\n".join(f"- {h.id} [{h.status}] {h.statement}" for h in hypotheses)


def _format_task_status(tasks):
    if not tasks:
        return "없음"
    counts = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    summary = ", ".join(f"{status}={count}" for status, count in counts.items())
    lines = [f"[요약] {summary}"]
    errors = [f"- {task.id}: {task.error}" for task in tasks
              if task.status == "error" and task.error]
    if errors:
        lines.append("[오류 원인]")
        lines.extend(errors)
    return "\n".join(lines)


def _format_evidence_list(evidence):
    if not evidence:
        return "없음"
    lines = []
    for e in evidence:
        effective = e.effective_as_of.isoformat() if e.effective_as_of else "없음"
        lines.append(f"- {e.id}: {e.summary} (complete={e.complete}, "
                     f"effective_as_of={effective})")
    return "\n".join(lines)


def _supported_first(hypotheses):
    """supported 가설을 앞으로 — 안정 정렬이라 같은 상태 내 원래 순서는 유지된다."""
    return sorted(hypotheses, key=lambda h: h.status != "supported")


def _format_task_error_rate(tasks):
    if not tasks:
        return "태스크 없음"
    errors = sum(1 for task in tasks if task.status == "error")
    return f"{errors}/{len(tasks)}건 오류"


def _format_rewrite_note(verify_problems):
    if not verify_problems:
        return ""
    problems = "\n".join(f"- {p}" for p in verify_problems)
    return f"\n\n[재작성 요청] 다음 문제를 고쳐 다시 작성하라:\n{problems}"


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

    async def integrate(state):
        round_next = state.round + 1
        prompt = _INTEGRATE_PROMPT.format(
            hypotheses=_format_hypothesis_board(state.hypotheses),
            tasks=_format_task_status(state.plan_tasks),
            evidence=_format_evidence_list(state.evidence),
            round=round_next, max_rounds=deps.engine_cfg.max_rounds)
        output, err = await _ask_llm(deps.lead_llm, prompt, IntegrateOutput)
        if output is None:
            return {
                "round": round_next, "decision": "conclude", "question": None,
                "qa_log": [{"kind": "integrate_parse_failure", "error": err}]}

        qa_log = []
        existing_ids = {task.id for task in state.plan_tasks}
        kept_new_tasks = []
        for task in output.new_tasks:
            if task.id in existing_ids:
                qa_log.append({"kind": "task_id_collision", "id": task.id})
            else:
                kept_new_tasks.append(task)

        cancellable = {task.id: task for task in state.plan_tasks if task.status == "pending"}
        cancelled_tasks = [cancellable[task_id].model_copy(update={"status": "cancelled"})
                           for task_id in output.cancel_task_ids if task_id in cancellable]

        decision = output.decision
        question = output.question if decision == "ask" else None

        if (decision == "ask" and state.interaction_policy == "autonomous"
                and state.autonomous_question_policy == "default_and_log"):
            qa_log.append({"kind": "auto_answered", "question": output.question,
                           "answer": "보수적 기본값으로 진행"})
            decision = "continue"
            question = None

        if round_next >= deps.engine_cfg.max_rounds:
            decision = "conclude"
            question = None
            qa_log.append({"kind": "round_cap"})

        return {
            "round": round_next, "hypotheses": output.hypotheses,
            "plan_tasks": kept_new_tasks + cancelled_tasks,
            "decision": decision, "question": question, "qa_log": qa_log}

    async def ask_human(state):
        # 설계 원칙: interrupt는 노드 최상단 — resume 시 노드가 선두부터 재실행되므로
        # interrupt 앞에 부수효과가 있으면 재개마다 반복된다.
        answer = interrupt({"question": state.question})
        return {
            "qa_log": [{"kind": "human_answer", "question": state.question, "answer": answer}],
            "decision": None, "question": None}

    async def conclude(state):
        if not state.evidence:
            caveats = [f"{task.id}: {task.error or '원인 불명'}" for task in state.plan_tasks
                       if task.status == "error"]
            return {"verdict": Verdict(
                verdict_type="degraded", confidence="low",
                narrative="증거 수집 전멸 — 조사 실패", caveats=caveats)}

        prompt = _CONCLUDE_PROMPT.format(
            hypotheses=_format_hypothesis_board(_supported_first(state.hypotheses)),
            evidence=_format_evidence_list(state.evidence),
            error_rate=_format_task_error_rate(state.plan_tasks),
            rewrite_note=_format_rewrite_note(state.verify_problems))
        verdict, err = await _ask_llm(deps.lead_llm, prompt, Verdict)
        if verdict is None:
            return {"verdict": Verdict(
                verdict_type="degraded", confidence="low",
                narrative="conclude 출력 파싱 실패 — 조사 종료 불가", caveats=[err])}
        return {"verdict": verdict}

    async def verify(state):
        # LLM 없음 — 순수 결정론 가드레일(§2.4). 노드는 raise하지 않는다.
        verdict = state.verdict
        case_id = state.case.id
        incomplete_ids = {e.id for e in state.evidence if not e.complete}
        caveats_text = " ".join(verdict.caveats)
        problems = []

        links = ([verdict.root_cause] if verdict.root_cause is not None else [])
        links += list(verdict.contributing)
        for link in links:
            if not link.evidence_ids:
                problems.append(f"다리에 인용 없음: {link.component}")
                continue
            for evidence_id in link.evidence_ids:
                if not deps.store.has_evidence(case_id, evidence_id):
                    problems.append(f"없는 id {evidence_id} 인용")
                elif evidence_id in incomplete_ids and evidence_id not in caveats_text:
                    problems.append(f"불완전 증거 {evidence_id}가 caveat에 명시되지 않음")

        if not problems:
            return {"verify_problems": []}
        if state.verify_attempts == 0:
            return {"verify_problems": problems, "verify_attempts": 1}
        # 강등 통과 — 재작성도 실패했으면 낮은 확신으로 통과시키고 문제 목록을 비운다.
        demoted = verdict.model_copy(update={
            "confidence": "low",
            "caveats": verdict.caveats + ["검증 미통과: " + "; ".join(problems)]})
        return {"verdict": demoted, "verify_problems": []}

    return {"frame": frame, "select": select, "execute": execute, "integrate": integrate,
            "ask_human": ask_human, "conclude": conclude, "verify": verify}


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


def route_after_integrate(state):
    return {"continue": "select", "ask": "ask_human", "conclude": "conclude"}[state.decision]


def route_after_verify(state):
    return "__end__" if not state.verify_problems else "conclude"
