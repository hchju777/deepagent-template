# 2. StateGraph 기초 — State, Node, Edge

LangGraph는 에이전트 워크플로우를 **그래프**로 모델링합니다. 세 가지 핵심 요소로 동작을 정의합니다.

1. **State** — 애플리케이션의 현재 스냅샷을 나타내는 공유 데이터 구조
2. **Node** — 에이전트의 로직을 담은 함수. 현재 상태를 받아 계산/부수효과를 수행하고 갱신된 상태를 반환
3. **Edge** — 현재 상태를 기반으로 다음에 어떤 노드를 실행할지 결정하는 함수

> 한 줄 요약: **노드는 일을 하고, 엣지는 다음에 뭘 할지 알려준다.**

내부적으로는 Google Pregel에서 영감을 받은 **메시지 패싱** 방식으로 동작합니다. 병렬로 실행되는 노드는 같은 "super-step"에 속하고, 순차 실행되는 노드는 서로 다른 super-step에 속합니다. 모든 노드가 `inactive` 상태가 되고 전달 중인 메시지가 없으면 그래프 실행이 종료됩니다.

## 2.1 State — 스키마와 리듀서

`State`는 **스키마**(`TypedDict` 권장, 기본값이 필요하면 `dataclass`, 재귀적 검증이 필요하면 Pydantic `BaseModel`)와 **리듀서 함수**(업데이트를 어떻게 적용할지)로 구성됩니다.

> ⚠️ `create_agent`(LangChain)는 Pydantic 상태 스키마를 지원하지 않습니다 — Pydantic이 필요하면 순수 `StateGraph`를 쓰세요.

### 기본 리듀서 (지정 안 하면 덮어쓰기)

```python
from typing_extensions import TypedDict

class State(TypedDict):
    foo: int
    bar: list[str]
```

리듀서를 지정하지 않으면 **오른쪽 값(노드의 업데이트)이 왼쪽 값(기존 상태)을 그냥 덮어씁니다**. 입력이 `{"foo": 1, "bar": ["hi"]}`이고 노드가 `{"foo": 2}`를 반환하면 상태는 `{"foo": 2, "bar": ["hi"]}`가 됩니다.

### 커스텀 리듀서 (누적하고 싶을 때)

```python
from operator import add
from typing import Annotated
from typing_extensions import TypedDict

class State(TypedDict):
    foo: int
    bar: Annotated[list[str], add]  # 새 값을 기존 리스트에 append
```

리듀서는 `(left, right) -> new_value` 형태의 이항 함수입니다. `left`는 기존 상태 값, `right`는 노드가 반환한 업데이트입니다.

```python
def append_strings(left: list[str], right: list[str]) -> list[str]:
    return left + right

class State(TypedDict):
    tags: Annotated[list[str], append_strings]
```

### 메시지 상태 — `MessagesState`

대화 이력을 저장하는 패턴이 워낙 흔해서 미리 만들어진 `MessagesState`가 있습니다.

```python
from langgraph.graph import MessagesState

class State(MessagesState):
    documents: list[str]   # 메시지 외에 필요한 필드를 추가
```

`MessagesState`는 `messages: Annotated[list[AnyMessage], add_messages]` 하나로 정의됩니다. `add_messages` 리듀서는 새 메시지는 append하지만, **메시지 ID가 같으면 기존 메시지를 덮어씁니다**(단순 `operator.add`와의 차이) — human-in-the-loop처럼 기존 메시지를 수정해야 할 때 중요합니다.

```python
# 둘 다 지원됨
{"messages": [HumanMessage(content="message")]}
{"messages": [{"type": "human", "content": "message"}]}
```

### 입력/출력/비공개 스키마 분리

```python
from typing import TypedDict
from langgraph.graph import END, START, StateGraph

class InputState(TypedDict):
    user_input: str

class OutputState(TypedDict):
    graph_output: str

class OverallState(TypedDict):
    foo: str
    user_input: str
    graph_output: str

class PrivateState(TypedDict):  # 노드 간 내부 통신용, 최종 출력엔 안 보임
    bar: str

def node_1(state: InputState) -> OverallState:
    return {"foo": state["user_input"] + " name"}

def node_2(state: OverallState) -> PrivateState:
    return {"bar": state["foo"] + " is"}

def node_3(state: PrivateState) -> OutputState:
    return {"graph_output": state["bar"] + " Lance"}

builder = StateGraph(OverallState, input_schema=InputState, output_schema=OutputState)
builder.add_node("node_1", node_1)
builder.add_node("node_2", node_2)
builder.add_node("node_3", node_3)
builder.add_edge(START, "node_1")
builder.add_edge("node_1", "node_2")
builder.add_edge("node_2", "node_3")
builder.add_edge("node_3", END)

graph = builder.compile()
graph.invoke({"user_input": "My"})
# {'graph_output': 'My name is Lance'}
```

> ⚠️ **주의**: 비공개(`Private`) 채널은 `invoke()`의 반환값에서는 숨겨지지만, `stream_mode="values"` 스트리밍에서는 **전부 노출됩니다**. 특정 채널만 보고 싶으면 `output_keys=[...]`를 지정하세요.

## 2.2 Node — 함수와 시그니처

노드는 동기/비동기 Python 함수이며 아래 인자를 받을 수 있습니다.

| 인자 | 설명 |
|---|---|
| `state` | 그래프의 현재 상태 |
| `config` | `thread_id`, 트레이싱 태그 등을 담은 `RunnableConfig` |
| `runtime` | `context`, `store`, `stream_writer`, `execution_info` 등을 담은 `Runtime` 객체 |

```python
from dataclasses import dataclass
from typing_extensions import TypedDict
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime

class State(TypedDict):
    input: str
    results: str

@dataclass
class Context:
    user_id: str

builder = StateGraph(State)

def plain_node(state: State):
    return state

def node_with_runtime(state: State, runtime: Runtime[Context]):
    print("user:", runtime.context.user_id)
    return {"results": f"Hello, {state['input']}!"}

builder.add_node("plain_node", plain_node)
builder.add_node("node_with_runtime", node_with_runtime)
```

노드를 그래프에 추가하면 내부적으로 `RunnableLambda`로 변환되어 배치/비동기 지원과 LangSmith 트레이싱을 자동으로 얻습니다.

### 재실행과 멱등성(Idempotency)

체크포인터를 쓰는 경우 LangGraph는 **super-step 경계**에서 체크포인트를 저장합니다(노드 함수 중간이 아님). 인터럽트나 재시도 후 재개하면 **영향받은 노드는 처음부터 다시 실행**됩니다. 따라서 노드 로직은 **재실행해도 상태가 깨지지 않도록(멱등하게)** 설계해야 합니다 — DB row를 삽입하는 노드라면 upsert나 멱등성 키를 쓰세요.

### `START` / `END` 노드

```python
from langgraph.graph import START, END

graph.add_edge(START, "node_a")  # 진입점
graph.add_edge("node_a", END)    # 종료점
```

## 2.3 Edge — 라우팅

| 종류 | 용도 |
|---|---|
| **일반 엣지** (`add_edge`) | 무조건 A → B |
| **조건부 엣지** (`add_conditional_edges`) | 라우팅 함수의 반환값에 따라 다음 노드 결정(분기/종료 선택 가능) |
| **진입점** | `START`에서 첫 노드로의 엣지 |
| **조건부 진입점** | `START`에서 라우팅 함수로 첫 실행 노드를 동적으로 결정 |

```python
# 일반 엣지
graph.add_edge("node_a", "node_b")

# 조건부 엣지
graph.add_conditional_edges("node_a", routing_function)

# 반환값 -> 노드 이름 매핑을 명시
graph.add_conditional_edges(
    "node_a", routing_function, {True: "node_b", False: "node_c"}
)
```

> ⚠️ 한 노드에서 **일반 엣지와 동적 라우팅(조건부 엣지/`Command`)을 섞지 마세요** — 둘 다 실행되어 그래프 동작을 예측하기 어려워집니다.

노드가 출력 엣지를 여러 개 가지면(또는 조건부 엣지가 리스트를 반환하면) 그 노드들은 **다음 super-step에서 전부 병렬 실행**됩니다.

## 2.4 `Send` — 동적 병렬 실행 (Map-Reduce 패턴)

엣지/노드 개수를 미리 알 수 없을 때(예: 리스트 항목마다 별도 처리) 씁니다.

```python
from typing import Annotated
from operator import add
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

class OverallState(TypedDict):
    subjects: list
    jokes: Annotated[list, add]  # 병렬 결과를 모으려면 리듀서 필수!

def continue_to_jokes(state: OverallState):
    return [Send("generate_joke", {"subject": s}) for s in state["subjects"]]

def generate_joke(state):
    return {"jokes": [f"{state['subject']}에 대한 농담"]}

g = StateGraph(OverallState)
g.add_node("generate_joke", generate_joke)
g.add_conditional_edges(START, continue_to_jokes, ["generate_joke"])
g.add_edge("generate_joke", END)
graph = g.compile()

graph.invoke({"subjects": ["고양이", "강아지"], "jokes": []})
# {'subjects': [...], 'jokes': ['고양이에 대한 농담', '강아지에 대한 농담']}
```

> ✅ 위 예제는 이 저장소 검증 venv에서 실행 확인됨. **병렬로 쓰는 상태 키에는 반드시 리듀서(`Annotated[list, add]` 등)를 지정해야** `InvalidUpdateError`가 나지 않습니다.

## 2.5 `Command` — 상태 업데이트 + 라우팅을 한 번에

`Command`는 아래 네 파라미터를 받습니다.

| 파라미터 | 용도 |
|---|---|
| `update` | 상태 업데이트 (노드가 dict 반환하는 것과 동일) |
| `goto` | 특정 노드로 이동 (조건부 엣지와 유사) |
| `graph` | 서브그래프에서 부모 그래프로 이동할 때 대상 지정(`Command.PARENT`) |
| `resume` | 인터럽트 후 실행 재개 시 값 전달 |

```python
from typing import Literal
from langgraph.types import Command

def my_node(state: State) -> Command[Literal["node_b"]]:
    return Command(update={"foo": "bar"}, goto="node_b")
```

> ✅ 검증됨. `state["foo"]="bar"`로 갱신되고 `node_b`로 라우팅되어 최종 `{"foo": "bar!"}`가 나옵니다.

**언제 `Command` vs 조건부 엣지?** 상태 업데이트와 라우팅을 **동시에** 해야 하면 `Command`, 라우팅만 하면 조건부 엣지를 씁니다.

`Command`는 세 곳에서 쓰입니다: **(1) 노드에서 반환** — `update`+`goto`(+서브그래프면 `graph=Command.PARENT`), **(2) `invoke`/`stream`의 입력** — `resume=`으로 인터럽트 후 재개, **(3) 도구(tool)에서 반환** — 도구 실행 결과로 상태 업데이트+라우팅.

> ⚠️ **주의**: 멀티턴 대화를 이어가려면 `Command(update=...)`를 입력으로 쓰지 마세요 — `Command`를 입력으로 주면 그래프는 `__start__`가 아니라 **마지막 체크포인트에서 재개**되므로, 이미 끝난 그래프는 멈춘 것처럼 보입니다. 새 턴을 이어가려면 그냥 평범한 dict를 입력으로 주세요. `Command(resume=...)`는 인터럽트 재개 전용입니다 → [08-human-in-the-loop.md](08-human-in-the-loop.md).

## 2.6 그래프 컴파일과 실행

```python
graph = graph_builder.compile()  # 반드시 컴파일해야 사용 가능
```

컴파일 시 그래프 구조(고아 노드 없음 등)를 검사하고, 체크포인터·브레이크포인트 같은 런타임 옵션을 지정합니다.

```python
graph.invoke(inputs)                      # 동기 전체 실행
await graph.ainvoke(inputs)                # 비동기
for chunk in graph.stream(inputs, stream_mode="updates"):  # 스트리밍
    print(chunk)
```

스트리밍 모드 전체 비교는 [05-streaming.md](05-streaming.md) 참고.

## 2.7 런타임 컨텍스트와 재귀 제한

```python
from dataclasses import dataclass
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime

@dataclass
class ContextSchema:
    llm_provider: str = "openai"

graph = StateGraph(State, context_schema=ContextSchema).compile()
graph.invoke(inputs, context={"llm_provider": "anthropic"})

def node_a(state: State, runtime: Runtime[ContextSchema]):
    llm = get_llm(runtime.context.llm_provider)
```

**재귀 제한**: 그래프가 실행할 수 있는 super-step 최대 횟수 (기본값 1000, `langgraph>=1.0.6`). 초과하면 `GraphRecursionError`.

```python
graph.invoke(inputs, config={"recursion_limit": 5})  # config의 최상위 키, configurable 안이 아님!
```

`RemainingSteps` managed value로 한도 근접을 노드 안에서 미리 감지해 우아하게 종료할 수도 있습니다:

```python
from langgraph.managed import RemainingSteps

class State(TypedDict):
    remaining_steps: RemainingSteps

def my_node(state: State):
    if state["remaining_steps"] <= 2:
        return {"messages": ["한도에 근접해 마무리합니다..."]}
    ...
```
