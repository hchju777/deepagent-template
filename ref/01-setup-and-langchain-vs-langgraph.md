# 1. 설치, 환경 구성, LangChain과의 차이

## 1.1 설치

```bash
# uv 권장
uv venv .venv
uv pip install --python .venv/bin/python3 langgraph langchain langchain-openai

source .venv/bin/activate
```

```bash
# pip
pip install -U langgraph langchain langchain-openai
# Python 3.10+ 필요
```

버전 확인:

```python
import langgraph
import importlib.metadata
print(importlib.metadata.version("langgraph"))  # 예: 1.2.11
```

## 1.2 API 키 (`.env`)

```bash
# .env — 커밋 금지
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_TRACING=true
```

```python
from dotenv import load_dotenv
load_dotenv()

from langchain.chat_models import init_chat_model
model = init_chat_model("openai:gpt-5.5")
```

다른 공급자(Anthropic Claude, Google Gemini)도 동일하게 `init_chat_model("anthropic:claude-sonnet-4-6")` 형태로 교체만 하면 됩니다 — LangGraph 노드 안에서 어떤 모델을 쓰든 그래프 구조는 바뀌지 않습니다.

## 1.3 LangGraph와 LangChain의 차이

| | LangChain | LangGraph |
|---|---|---|
| **핵심 추상화** | Chat Model, 메시지, 도구, `create_agent`(사전제작 에이전트) | State, Node, Edge로 이루어진 그래프(상태 기계) |
| **제어 흐름** | 정해진 루프(도구 호출 → 실행 → 반복)를 감춰서 제공 | 개발자가 노드/엣지/조건부 라우팅을 직접 설계 |
| **관계** | `create_agent`는 내부적으로 LangGraph 그래프를 컴파일해서 반환함 | LangChain 에이전트를 구동하는 저수준 런타임 |
| **언제 쓰나** | "도구를 쓰는 표준 에이전트"면 충분할 때 | 분기·병렬·사이클·멀티 에이전트처럼 커스텀 흐름이 필요할 때 |
| **학습 곡선** | 낮음 | 중간~높음이지만 표현력이 훨씬 큼 |

즉 **경쟁 관계가 아니라 계층 관계**입니다: LangChain의 `create_agent()`를 호출하면 실제로는 LangGraph의 `CompiledStateGraph`가 만들어집니다. `create_agent`로 시작했다가 더 세밀한 제어가 필요해지면 언제든 `StateGraph`로 "내려가서" 직접 그래프를 설계할 수 있습니다.

```python
from langchain.agents import create_agent

agent = create_agent(model="openai:gpt-5.5", tools=[...])
print(type(agent))  # <class 'langgraph.graph.state.CompiledStateGraph'>
```

## 1.4 왜 LangGraph를 써야 하나

1. **명시적 상태 관리** — 대화/작업 상태가 State 스키마로 명시되어 추적과 디버깅이 쉬움.
2. **세밀한 제어 흐름** — 조건부 분기, `Send`를 이용한 동적 병렬 실행, 사이클(루프)을 자유롭게 구성. 단순 LCEL 파이프라인으로는 표현하기 어려운 패턴.
3. **내장 영속성** — 체크포인터만 붙이면 대화 재개, Human-in-the-loop, Time Travel이 거의 공짜로 딸려옴 → [03-memory-and-persistence.md](03-memory-and-persistence.md).
4. **멀티 에이전트 조합** — 서브그래프로 에이전트를 조합해 Supervisor/Handoff 같은 복잡한 시스템 구성 → [11-multi-agent-systems.md](11-multi-agent-systems.md).

## 1.5 최소 예제 (검증됨)

```python
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    input: str
    result: str

def node_a(state: State):
    return {"result": state["input"] + "!"}

builder = StateGraph(State)
builder.add_node("node_a", node_a)
builder.add_edge(START, "node_a")
builder.add_edge("node_a", END)

graph = builder.compile()
graph.invoke({"input": "hi"})
# {'input': 'hi', 'result': 'hi!'}
```

다음 장에서 `State`/`Node`/`Edge`를 하나씩 자세히 다룹니다 → [02-stategraph-basics.md](02-stategraph-basics.md).
