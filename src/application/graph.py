"""조사 엔진 그래프 배선 — 스펙 §2.1.

그래프 형태 (계획 3b 헤더와 동일):
    START → frame → (verdict 생겼으면 END, 아니면 select)
    select → (실행 가능 태스크를 Send로 execute×N | 0건이면 integrate)
    execute → integrate                    (Send 전부가 barrier로 수렴)
    integrate → (continue→select | ask→ask_human | conclude→conclude)
    ask_human → integrate                  (interrupt 최상단, resume 답변은 qa_log로)
    conclude → verify
    verify → (문제+첫 시도→conclude 재작성 | 통과·강등→END)

통제 경계(라운드 상한·select 게이트·병렬 폭·interrupt 위치·verify 규칙)는
노드(nodes.py)가 쥔다 — 이 모듈은 순수 배선만 한다.
"""
from langgraph.graph import END, START, StateGraph

from src.application.nodes import (make_nodes, route_after_frame, route_after_integrate,
                                   route_after_select, route_after_verify)
from src.application.state import CaseState


def build_engine(deps, *, checkpointer=None):
    """EngineDeps로 노드를 만들어 StateGraph(CaseState)에 배선하고 컴파일한다."""
    nodes = make_nodes(deps)
    builder = StateGraph(CaseState)
    for name in ("frame", "select", "execute", "integrate", "ask_human", "conclude", "verify"):
        builder.add_node(name, nodes[name])

    builder.add_edge(START, "frame")
    # route_after_frame이 "__end__"를 문자열로 돌려주므로 END 심볼로 매핑한다.
    builder.add_conditional_edges("frame", route_after_frame, {"__end__": END, "select": "select"})
    # route_after_select는 Send 리스트(execute×N) 또는 "integrate" 문자열을 돌려준다.
    builder.add_conditional_edges("select", route_after_select, ["execute", "integrate"])
    builder.add_edge("execute", "integrate")          # Send 전부가 barrier로 수렴(고정 엣지)
    builder.add_conditional_edges(
        "integrate", route_after_integrate,
        {"select": "select", "ask_human": "ask_human", "conclude": "conclude"})
    builder.add_edge("ask_human", "integrate")         # interrupt 재개 후 고정 엣지
    builder.add_edge("conclude", "verify")             # 고정 엣지
    builder.add_conditional_edges("verify", route_after_verify, {"__end__": END, "conclude": "conclude"})

    return builder.compile(checkpointer=checkpointer)
