"""조사 엔진 노드들 — 스펙 §2.4. 통제는 코드가, 판단은 LLM이.

모든 노드는 절대 raise하지 않고 부분 상태 update(dict)를 반환한다.
LLM 파싱은 재시도 1회 후 노드별 안전 경로로 강등된다.
"""
from src.application.briefing import build_briefing, upstream_slice
from src.application.schemas import FrameOutput, parse_structured
from src.domain.case import Verdict
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

    return {"frame": frame}


def route_after_frame(state):
    return "__end__" if state.verdict is not None else "select"
