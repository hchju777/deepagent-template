# 8. Human-in-the-Loop (HITL)

## 8.1 `interrupt()` — 그래프를 일시정지하고 사람 입력 기다리기

`interrupt()`는 노드 실행을 멈추고 값을 반환합니다. 이후 `Command(resume=값)`으로 그 지점부터 재개할 수 있습니다. **체크포인터가 필수**입니다 — 인터럽트 상태를 유지하려면 그래프 상태를 저장해야 하기 때문입니다.

```python
from typing import TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

class State(TypedDict):
    messages: list[dict]

def human_review(state: State):
    answer = interrupt("승인하시겠습니까?")   # 여기서 그래프가 멈춤
    return {"messages": [{"role": "user", "content": answer}]}

graph = (
    StateGraph(State)
    .add_node("human_review", human_review)
    .add_edge(START, "human_review")
    .add_edge("human_review", END)
    .compile(checkpointer=InMemorySaver())
)

config = {"configurable": {"thread_id": "resume-demo"}}

# 1차 실행 — 인터럽트에서 멈춤
graph.invoke({"messages": []}, config)

# 사람이 응답 후 재개 — interrupt()의 반환값이 "yes"가 됨
graph.invoke(Command(resume="yes"), config)
```

## 8.2 Human-in-the-Loop 미들웨어 (LangChain `create_agent` 사용 시)

`create_agent`로 만든 에이전트에서는 매번 직접 `interrupt()`를 쓰지 않고 `HumanInTheLoopMiddleware`로 특정 도구 호출에만 승인 게이트를 걸 수 있습니다.

```python
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent(
    model="gpt-5.5",
    tools=[write_file, execute_sql, read_data],
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                "write_file": True,   # approve/edit/reject/respond 전부 허용
                "execute_sql": {"allowed_decisions": ["approve", "reject"]},  # edit 불가
                "read_data": False,   # 안전한 작업 — 승인 불필요
            },
            description_prefix="도구 실행 승인이 필요합니다",
        ),
    ],
    checkpointer=InMemorySaver(),  # 필수
)
```

### 결정 타입 4가지

| 타입 | 설명 | 예시 |
|---|---|---|
| ✅ `approve` | 제안된 인자 그대로 실행 | 초안 그대로 이메일 발송 |
| ✏️ `edit` | 실행 전 인자를 수정 | 수신자를 바꾼 뒤 이메일 발송 |
| ❌ `reject` | 실행하지 않고 거부 피드백을 에이전트에 반환 | 파일 삭제를 거부하고 이유 설명 |
| 💬 `respond` | 사람의 메시지를 도구 실행 결과처럼 직접 반환(실행은 건너뜀) | `ask_user` 같은 "사용자에게 묻는" 도구에 답변 |

> ⚠️ `respond`는 "사람이 도구 역할을 대신할 때"만 쓰세요. 부수효과가 있는 도구를 거부할 땐 `reject`를 쓰세요 — `respond`의 메시지는 **성공한 도구 결과**로 취급됩니다.

### 실행 및 재개

```python
from langgraph.types import Command

config = {"configurable": {"thread_id": "some_id"}}

result = agent.invoke(
    {"messages": [{"role": "user", "content": "오래된 레코드를 DB에서 삭제해줘"}]},
    config=config,
    version="v2",
)
print(result.interrupts)
# (Interrupt(value={'action_requests': [{'name': 'execute_sql', 'arguments': {...}, ...}], 'review_configs': [...]}),)

# 승인
agent.invoke(
    Command(resume={"decisions": [{"type": "approve"}]}),
    config=config,
    version="v2",
)
```

여러 액션이 동시에 대기 중이면 `decisions` 리스트를 **요청 순서와 동일한 순서**로 제공해야 합니다.

```python
# edit 예시
agent.invoke(
    Command(resume={"decisions": [{
        "type": "edit",
        "edited_action": {"name": "new_tool_name", "args": {"key1": "new_value"}},
    }]}),
    config=config, version="v2",
)
```

> 💡 인자를 **`edit`할 때는 보수적으로** 수정하세요 — 크게 바꾸면 모델이 접근 방식을 재평가해서 도구를 여러 번 실행하거나 예상 밖의 행동을 할 수 있습니다.

## 8.3 조건부 인터럽트 — `when` 프레디케이트

`interrupt_on`에 나열된 모든 호출을 매번 멈추고 싶지 않다면, 도구 인자에 따라 조건부로 개입시킬 수 있습니다 (`langchain>=1.3.3` 필요).

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware, ToolCallRequest

def writes_outside_workspace(request: ToolCallRequest) -> bool:
    path = request.tool_call["args"].get("path", "")
    return not path.startswith("/workspace/")

def is_write_query(request: ToolCallRequest) -> bool:
    query = request.tool_call["args"].get("query", "")
    return not query.lstrip().upper().startswith("SELECT")

agent = create_agent(
    model="gpt-5.5",
    tools=[write_file, execute_sql, read_data],
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                "write_file": {"allowed_decisions": ["approve", "edit", "reject"], "when": writes_outside_workspace},
                "execute_sql": {"allowed_decisions": ["approve", "reject"], "when": is_write_query},
            },
        ),
    ],
    checkpointer=InMemorySaver(),
)
```

`when`이 `False`를 반환하면 그 호출은 자동 승인되어 리뷰어에게 보이지 않습니다 — 리뷰어는 실제로 개입이 필요한 액션만 보게 됩니다.

## 8.4 순수 `StateGraph`에서의 인터럽트 재사용 패턴

`create_agent`를 쓰지 않고 직접 `StateGraph`를 짤 때도 같은 원리로 특정 노드에 `interrupt()`를 넣어 임의 지점에서 사람 개입을 받을 수 있습니다 — [09-guardrails.md](09-guardrails.md)의 "HITL 가드레일" 절에서 위험한 도구 호출을 게이트하는 예제를 참고하세요.

## 8.5 Time Travel과의 관계

인터럽트가 있는 그래프를 [Time Travel](03-memory-and-persistence.md#32-time-travel--replay와-fork)로 리플레이하면 **인터럽트가 다시 트리거**됩니다 — 인터럽트를 포함한 노드가 재실행되어 새로운 `Command(resume=...)`를 기다립니다.
