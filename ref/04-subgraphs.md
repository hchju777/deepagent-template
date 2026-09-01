# 4. 서브그래프 (Subgraphs)

서브그래프란 **다른 그래프의 노드로 쓰이는 그래프**입니다.

**용도:**

- 멀티 에이전트 시스템 구축 → [11-multi-agent-systems.md](11-multi-agent-systems.md)
- 여러 그래프에서 노드 집합 재사용
- 팀 분업 — 서브그래프 인터페이스(입출력 스키마)만 지키면, 부모 그래프는 서브그래프 내부를 몰라도 조립 가능

## 4.1 부모-서브그래프 통신 방식 두 가지

| 패턴 | 언제 쓰나 | 상태 스키마 |
|---|---|---|
| **노드 안에서 서브그래프 호출** | 부모와 서브그래프의 **상태 스키마가 다를 때**(공유 키 없음), 또는 상태를 변환해야 할 때 | 부모 상태 ↔ 서브그래프 입력, 서브그래프 출력 ↔ 부모 상태를 매핑하는 래퍼 함수 작성 |
| **서브그래프를 노드로 직접 추가** | 부모와 서브그래프가 **상태 키를 공유**할 때 — 같은 채널을 읽고 씀 | 컴파일된 서브그래프를 `add_node`에 바로 전달, 래퍼 불필요 |

## 4.2 노드 안에서 서브그래프 호출 (스키마가 다를 때)

멀티 에이전트 시스템에서 에이전트마다 **비공개 메시지 이력**을 유지하고 싶을 때 흔히 씁니다.

```python
from typing_extensions import TypedDict
from langgraph.graph.state import StateGraph, START

# 서브그래프
class SubgraphState(TypedDict):
    bar: str

def subgraph_node_1(state: SubgraphState):
    return {"bar": "hi! " + state["bar"]}

subgraph_builder = StateGraph(SubgraphState)
subgraph_builder.add_node(subgraph_node_1)
subgraph_builder.add_edge(START, "subgraph_node_1")
subgraph = subgraph_builder.compile()

# 부모 그래프
class State(TypedDict):
    foo: str

def call_subgraph(state: State):
    # 부모 상태 -> 서브그래프 입력으로 변환
    subgraph_output = subgraph.invoke({"bar": state["foo"]})
    # 서브그래프 출력 -> 부모 상태로 다시 변환
    return {"foo": subgraph_output["bar"]}

builder = StateGraph(State)
builder.add_node("node_1", call_subgraph)
builder.add_edge(START, "node_1")
graph = builder.compile()

graph.invoke({"foo": "world"})
# {'foo': 'hi! world'}
```

> ✅ 위 예제는 이 저장소 검증 venv에서 실행 확인됨.

**2단계 중첩(부모 → 자식 → 손자)**도 동일한 패턴을 반복하면 됩니다 — 각 레벨은 자기 바로 아래 레벨의 상태만 알면 되고, 조부모/증손자 레벨의 키에는 접근할 수 없습니다.

## 4.3 서브그래프를 노드로 직접 추가 (스키마를 공유할 때)

부모와 서브그래프가 **상태 키를 공유**하면(예: 멀티 에이전트가 공통 `messages` 키로 통신) 래퍼 함수 없이 컴파일된 서브그래프를 그대로 `add_node`에 넘길 수 있습니다.

```python
from typing_extensions import TypedDict
from langgraph.graph.state import StateGraph, START

class State(TypedDict):
    foo: str

# 서브그래프 — 부모와 동일한 State 스키마 사용
def subgraph_node_1(state: State):
    return {"foo": "hi! " + state["foo"]}

subgraph_builder = StateGraph(State)
subgraph_builder.add_node(subgraph_node_1)
subgraph_builder.add_edge(START, "subgraph_node_1")
subgraph = subgraph_builder.compile()

# 부모 그래프 — 컴파일된 서브그래프를 노드로 직접 등록
builder = StateGraph(State)
builder.add_node("node_1", subgraph)   # 래퍼 함수 불필요
builder.add_edge(START, "node_1")
graph = builder.compile()

graph.invoke({"foo": "world"})
# {'foo': 'hi! world'}
```

> ✅ 검증됨.

## 4.4 서브그래프와 체크포인트 네임스페이스

각 체크포인트는 `checkpoint_ns` 필드로 어느 그래프/서브그래프에 속하는지 식별합니다.

| 값 | 의미 |
|---|---|
| `""` (빈 문자열) | 부모(루트) 그래프 |
| `"node_name:uuid"` | 해당 이름의 노드로 실행된 서브그래프. 중첩 서브그래프는 `|`로 연결(`"outer:uuid|inner:uuid"`) |

```python
def my_node(state: State, config: RunnableConfig):
    checkpoint_ns = config["configurable"]["checkpoint_ns"]
```

> ⚠️ 서브그래프가 상태를 업데이트해도 **부모 그래프에 즉시 보이지 않을 수 있습니다** — 서브그래프가 자체 체크포인트 네임스페이스를 갖기 때문입니다. 스레드를 넘나드는 공유 데이터는 [Store](03-memory-and-persistence.md#33-store--장기-메모리)를 쓰거나, 서브그래프가 부모 체크포인트에 쓰도록 구성하세요.

## 4.5 서브그래프에서 부모 그래프로 라우팅 — `Command(graph=Command.PARENT)`

```python
from typing import Literal
from langgraph.types import Command

def my_node(state: State) -> Command[Literal["other_subgraph"]]:
    return Command(
        update={"foo": "bar"},
        goto="other_subgraph",       # 부모 그래프의 노드 이름
        graph=Command.PARENT,
    )
```

`graph=Command.PARENT`는 가장 가까운 부모 그래프로 이동합니다. 부모/서브그래프가 공유하는 키를 이런 식으로 업데이트하려면, 그 키에 대해 **부모 그래프 쪽에 리듀서가 정의되어 있어야** 합니다. 멀티 에이전트의 **Handoff 패턴**을 구현할 때 특히 유용합니다 → [11-multi-agent-systems.md](11-multi-agent-systems.md).
