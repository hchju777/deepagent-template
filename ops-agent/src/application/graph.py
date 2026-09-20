"""그래프 배선. **LangGraph를 아는 유일한 파일이다.**

```
START → frame → (실패면 END | select)
                  select → (Send로 execute×N  |  0건이면 integrate)
                  ↑                ↓
                  │            integrate → continue면 select, 아니면 END
                  └────────────────┘
```

판단은 전부 `nodes.py`가 쥔다 — 여기는 순수 배선이다. 그렇게 갈라 두면 루프
런타임을 갈아끼울 때 바꿀 파일이 이것 하나다(사내 PyPI에 langgraph가 없을 때의
탈출구 — [decisions ①](../../STEPS/decisions.md)).

`execute → integrate`가 고정 엣지인 것이 **barrier**다. Send로 퍼진 가지들이 전부
끝나야 integrate가 한 번 돈다 — 라운드 경계가 여기서 생긴다.
"""
from langgraph.graph import END, START, StateGraph

from src.application.nodes import (EngineDeps, make_nodes, route_after_frame,
                                   route_after_integrate, route_after_select)
from src.application.state import CaseState


def build_engine(deps: EngineDeps, *, checkpointer=None):
    nodes = make_nodes(deps)
    builder = StateGraph(CaseState)
    for name in ("frame", "select", "execute", "integrate"):
        builder.add_node(name, nodes[name])

    builder.add_edge(START, "frame")
    # frame이 실패하면(리드 LLM이 죽었다) 라운드를 시작하지 않고 끝낸다 —
    # 흘려보내면 integrate가 LLM을 또 부르고, 끝난 이유가 덮인다.
    builder.add_conditional_edges("frame", route_after_frame,
                                  {"select": "select", "__end__": END})
    # route_after_select는 Send 리스트(execute×N) 또는 "integrate" 문자열을 돌려준다.
    builder.add_conditional_edges("select", route_after_select, ["execute", "integrate"])
    builder.add_edge("execute", "integrate")       # Send 전부가 수렴하는 barrier
    # route_after_integrate가 "__end__"를 문자열로 돌려주므로 END 심볼로 매핑한다.
    builder.add_conditional_edges("integrate", route_after_integrate,
                                  {"select": "select", "__end__": END})
    return builder.compile(checkpointer=checkpointer)
