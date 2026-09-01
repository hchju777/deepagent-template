# 7. ReAct 에이전트 구현

## 7.1 ReAct 패러다임

**ReAct** = **REA**soning + a**CT**ing. 2022년 Princeton/Google Research 논문에서 제안된 패턴으로, 모델이 **생각(Reasoning) → 행동(Acting, 도구 호출) → 관찰(Observation, 도구 결과) → 다시 생각**을 반복하며 답에 도달하는 구조입니다. LangGraph의 에이전트 루프(모델 호출 → 도구 호출 → 결과 반영 → 반복)가 이 패턴을 구현한 것입니다.

## 7.2 `create_agent`로 빠른 시작 (고수준)

가장 빠른 방법은 LangChain의 `create_agent`를 쓰는 것입니다. 내부적으로 `StateGraph`를 컴파일해서 반환하므로, LangGraph를 몰라도 바로 ReAct 루프를 얻을 수 있습니다.

```python
from langchain.agents import create_agent

def search_web(query: str) -> str:
    """웹에서 정보를 검색합니다."""
    return f"'{query}' 검색 결과: ..."

agent = create_agent(
    model="openai:gpt-5.5",
    tools=[search_web],
    system_prompt="당신은 유용한 리서치 어시스턴트입니다.",
)

result = agent.invoke({"messages": [{"role": "user", "content": "오늘 서울 날씨는?"}]})
```

### Think → Act → Observe 흐름 관찰하기

```python
for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "오늘 서울 날씨는?"}]},
    stream_mode="updates",
):
    for node_name, update in chunk.items():
        print(node_name, "->", update)  # node_name이 "model"이면 생각/행동 결정, "tools"면 관찰(도구 결과)
```

`create_agent`가 만드는 그래프의 노드 이름은 `"model"`(모델 호출)과 `"tools"`(도구 실행)입니다. 스트리밍 결과를 노드 이름으로 분기하면 Think/Act/Observe 각 단계를 추적할 수 있습니다.

### 스트리밍·미들웨어·디버깅

- 스트리밍 모드 전체는 [05-streaming.md](05-streaming.md) 참고.
- 미들웨어(요약, HITL, 재시도, PII 등)로 에이전트 동작을 확장하는 방법은 [09-guardrails.md](09-guardrails.md)와 LangChain 문서의 미들웨어 가이드 참고.
- `debug=True`를 `create_agent`에 넘기면 각 노드 실행/상태 전이에 대한 상세 로그가 출력됩니다.

## 7.3 `StateGraph`로 커스텀 에이전트 만들기 (저수준)

`create_agent`는 대부분의 사용 사례를 간결하게 해결하지만, **에이전트 내부 동작을 직접 제어해야 할 때**는 `StateGraph`로 직접 구현합니다.

**`StateGraph`가 필요한 경우:**

| 이유 | 예시 |
|---|---|
| 커스텀 상태 | 메시지 외에 검색 결과, 재시도 횟수 등 별도 필드가 필요할 때 |
| 복잡한 라우팅 | 단순 "도구 호출 vs 종료"를 넘어서는 분기가 필요할 때(재시도 루프, 다단계 검증) |
| 다중 노드 구조 | 입력 검증, 후처리, 로깅 같은 별도 노드가 필요할 때 |
| 멀티 에이전트 | 여러 에이전트를 조합해야 할 때 → [11-multi-agent-systems.md](11-multi-agent-systems.md) |

### 컴포넌트 대응표

| `create_agent` 구성요소 | `StateGraph`로 직접 만들 때 |
|---|---|
| 내부 State | `TypedDict`(또는 `MessagesState`) |
| 모델 호출 노드 | `model.invoke(state["messages"])`를 감싼 함수 |
| 도구 실행 노드 | `langgraph.prebuilt.ToolNode(tools)` |
| 도구 호출 여부 분기 | `add_conditional_edges` |
| 대화 메모리 | `InMemorySaver` 등 체크포인터 |

### 최소 ReAct 그래프 직접 구현

```python
from typing import Literal
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode
from langchain.chat_models import init_chat_model
from langchain.tools import tool

@tool
def search_web(query: str) -> str:
    """웹에서 정보를 검색합니다."""
    return f"'{query}' 검색 결과: ..."

tools = [search_web]
model = init_chat_model("openai:gpt-5.5").bind_tools(tools)

def call_model(state: MessagesState):
    response = model.invoke(state["messages"])
    return {"messages": [response]}

def should_continue(state: MessagesState) -> Literal["tools", "__end__"]:
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "__end__"

builder = StateGraph(MessagesState)
builder.add_node("agent", call_model)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "__end__": END})
builder.add_edge("tools", "agent")  # 도구 실행 후 다시 모델 호출 -> 루프

graph = builder.compile()
```

이 구조가 정확히 `create_agent`가 내부적으로 만드는 그래프의 단순화 버전입니다. 여기서부터 커스텀 노드(검증, 로깅, 재시도 루프)를 자유롭게 추가할 수 있습니다.

## 7.4 대화 메모리 추가

```python
from langgraph.checkpoint.memory import InMemorySaver

graph = builder.compile(checkpointer=InMemorySaver())

config = {"configurable": {"thread_id": "1"}}
graph.invoke({"messages": [{"role": "user", "content": "내 이름은 밥이야."}]}, config)
graph.invoke({"messages": [{"role": "user", "content": "내 이름이 뭐라고?"}]}, config)
# -> "밥"이라고 기억함
```

자세한 내용은 [03-memory-and-persistence.md](03-memory-and-persistence.md) 참고.
