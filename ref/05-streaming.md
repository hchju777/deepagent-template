# 5. 스트리밍 (Streaming)

## 5.1 기본 사용법

```python
for chunk in graph.stream(
    {"topic": "아이스크림"},
    stream_mode=["updates", "custom"],
):
    print(chunk)
```

> ✅ 검증됨. `version` 인자를 생략하면(기본값, v1 포맷) 단일 모드일 땐 원시 데이터, 여러 모드를 섞으면 `(mode, data)` 튜플로 반환됩니다.
>
> 출력 예: `('custom', {'status': '농담 생각중...'})`, `('updates', {'generate_joke': {'joke': '...'}})`

## 5.2 `version="v2"` — 통일된 출력 포맷 (LangGraph 1.1+)

`v1`(기본값)은 스트림 모드 개수/서브그래프 여부에 따라 반환 형식이 달라져서(단일 모드=원시 데이터, 다중 모드=튜플, 서브그래프=네임스페이스 튜플) 다루기 번거롭습니다. `version="v2"`를 쓰면 **항상 동일한 `StreamPart` 딕셔너리 형태**로 반환됩니다.

```python
{
    "type": "values" | "updates" | "messages" | "custom" | "checkpoints" | "tasks" | "debug",
    "ns": (),      # 서브그래프일 때 네임스페이스 튜플
    "data": ...,   # 실제 페이로드 (모드마다 타입 다름)
}
```

```python
for part in graph.stream(
    {"topic": "아이스크림"},
    stream_mode=["values", "updates", "messages", "custom"],
    version="v2",
):
    if part["type"] == "values":
        print(f"상태: topic={part['data']['topic']}")
    elif part["type"] == "updates":
        for node_name, state in part["data"].items():
            print(f"노드 `{node_name}` 업데이트: {state}")
    elif part["type"] == "messages":
        msg, metadata = part["data"]
        print(msg.content, end="", flush=True)
    elif part["type"] == "custom":
        print(f"진행률: {part['data']['progress']}%")
```

## 5.3 스트림 모드 전체 비교

| 모드 | 타입 | 설명 | 언제 쓰나 |
|---|---|---|---|
| `values` | `ValuesStreamPart` | 매 스텝 후 **전체 상태** | UI에 항상 최신 전체 상태를 반영하고 싶을 때 |
| `updates` | `UpdatesStreamPart` | 매 스텝의 **변경분만** (같은 스텝의 여러 업데이트는 개별 이벤트로) | 어떤 노드가 뭘 바꿨는지 로깅/디버깅할 때 |
| `messages` | `MessagesStreamPart` | LLM 호출의 (토큰, 메타데이터) 2-튜플 | 챗 UI에서 토큰 단위로 타이핑 효과를 낼 때 |
| `custom` | `CustomStreamPart` | 노드 안에서 `get_stream_writer()`로 보낸 임의 데이터 | 진행률 표시줄, 커스텀 상태 메시지 |
| `checkpoints` | `CheckpointStreamPart` | 체크포인트 이벤트(`get_state()`와 동일 포맷). **체크포인터 필요** | 상태 스냅샷을 실시간으로 관찰할 때 |
| `tasks` | `TasksStreamPart` | 태스크 시작/종료 이벤트(결과/에러 포함). **체크포인터 필요** | 개별 태스크 단위 진행 상황 추적 |
| `debug` | `DebugStreamPart` | `checkpoints`+`tasks`를 합친 모든 정보 | 심층 디버깅 |

## 5.4 상태 스트리밍 (`values` vs `updates`)

```python
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    topic: str
    joke: str

def refine_topic(state: State):
    return {"topic": state["topic"] + " and cats"}

def generate_joke(state: State):
    return {"joke": f"This is a joke about {state['topic']}"}

graph = (
    StateGraph(State)
    .add_node(refine_topic)
    .add_node(generate_joke)
    .add_edge(START, "refine_topic")
    .add_edge("refine_topic", "generate_joke")
    .add_edge("generate_joke", END)
    .compile()
)

# updates: 각 스텝이 "무엇을" 바꿨는지
for chunk in graph.stream({"topic": "ice cream"}, stream_mode="updates"):
    print(chunk)
# {'refine_topic': {'topic': 'ice cream and cats'}}
# {'generate_joke': {'joke': 'This is a joke about ice cream and cats'}}

# values: 매 스텝 후 "전체" 상태
for chunk in graph.stream({"topic": "ice cream"}, stream_mode="values"):
    print(chunk)
# {'topic': 'ice cream'}
# {'topic': 'ice cream and cats'}
# {'topic': 'ice cream and cats', 'joke': 'This is a joke about ice cream and cats'}
```

## 5.5 LLM 토큰 스트리밍 (`messages`)

```python
for msg, metadata in graph.stream(inputs, stream_mode="messages"):
    print(msg.content, end="", flush=True)
```

`metadata`에는 어느 노드/그래프에서 나온 토큰인지 등의 정보가 담깁니다. `create_agent`로 만든 에이전트는 이 모드로 도구 호출 여부와 관계없이 모델 응답 토큰을 실시간으로 스트리밍합니다.

## 5.6 커스텀 데이터 스트리밍 (`custom`)

노드 내부에서 진행 상황 등 임의의 데이터를 내보내고 싶을 때 `get_stream_writer()`를 씁니다.

```python
from langgraph.config import get_stream_writer

def generate_joke(state: State):
    writer = get_stream_writer()
    writer({"status": "농담 생각중..."})
    return {"joke": f"{state['topic']}에 대한 농담"}

for chunk in graph.stream({"topic": "아이스크림"}, stream_mode="custom"):
    print(chunk)  # {'status': '농담 생각중...'}
```

> ✅ 검증됨.

## 5.7 서브그래프 출력 스트리밍

기본적으로 부모 그래프를 스트리밍하면 **서브그래프 내부의 개별 노드 이벤트는 보이지 않습니다**. 보고 싶다면 `subgraphs=True`를 지정하세요.

```python
for chunk in graph.stream(inputs, stream_mode="updates", subgraphs=True):
    print(chunk)
# (namespace_tuple, data) 형태로 반환됨 — namespace가 ()면 부모, 아니면 서브그래프
```

`version="v2"`를 함께 쓰면 `part["ns"]`로 동일한 정보를 얻을 수 있습니다.

## 5.8 새 애플리케이션엔 Event Streaming(LangGraph 1.2+) 권장

> 💡 신규 프로젝트에는 `stream_mode` API 대신 **이벤트 스트리밍**(LangGraph 1.2에서 도입된 typed-projection API)을 권장합니다. `messages`/`values`/`subgraphs`/`output`마다 **독립된 이터레이터**를 제공해서, `stream_mode` 분기 없이 원하는 프로젝션만 따로 소비할 수 있습니다.

```python
stream = graph.stream_events(inputs, version="v3")

for msg, metadata in stream.messages:      # 토큰 스트림만 구독
    print(msg.content, end="")

result = stream.output   # 스트림을 끝까지 진행시키고 최종 출력을 받음
print(stream.interrupts)  # 발생한 인터럽트 목록
```

`stream_events`는 [02-stategraph-basics.md](02-stategraph-basics.md), [08-human-in-the-loop.md](08-human-in-the-loop.md)의 인터럽트 예제에서도 재개 흐름을 다룰 때 등장합니다.
