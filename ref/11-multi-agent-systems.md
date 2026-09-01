# 11. 다중 에이전트 시스템 (Multi-Agent Systems)

## 11.1 아키텍처 패턴 세 가지

LangChain 1.0/LangGraph는 세 가지 표준 멀티 에이전트 패턴을 제시합니다.

| 패턴 | 제어 방식 | 사용자 대화 | 병렬 실행 | 상태 관리 | 구현 복잡도 |
|---|---|---|---|---|---|
| **Subagents (Supervisor)** | 중앙 감독자가 워커를 **도구처럼** 호출 | 워커는 사용자와 직접 대화 안 함(감독자만) | 한 턴에 여러 워커 동시 호출 가능 | 감독자가 전체 대화 상태 유지, 워커는 stateless | 낮음 |
| **Handoffs** | 상태 변수(`current_step` 등)에 따라 동적으로 설정/에이전트 전환 | 각 단계가 사용자와 직접 대화 | 순차적(단계별) | `Command`로 상태 갱신, 스레드 전체에 지속 | 중간 |
| **StateGraph (커스텀)** | 노드/엣지를 직접 정의 | 자유롭게 설계 | `Send`로 자유롭게 병렬화 | 완전히 커스텀 | 높음 |

**선택 가이드**:
- 리서치·분석·작성처럼 **병렬로 수행 후 종합**해야 하면 → **Subagents**
- 고객지원처럼 **순차적 워크플로우**(정보 수집 → 분류 → 해결)면 → **Handoffs**
- 복잡한 조건부 분기·피드백 루프가 필요하면 → **StateGraph**

## 11.2 Subagents (Supervisor) 패턴

중앙 **감독자(main agent)**가 서브에이전트를 **도구처럼** 호출해 조율합니다. 서브에이전트는 **stateless**(과거 대화를 기억 안 함)라서, 매 호출이 깨끗한 컨텍스트 윈도우에서 실행됩니다 — 메인 대화의 컨텍스트가 비대해지는 걸 방지합니다(컨텍스트 격리).

**특징**: 모든 라우팅이 감독자를 거침 · 서브에이전트는 사용자가 아니라 감독자에게 결과를 반환 · 한 턴에 여러 서브에이전트를 병렬 호출 가능.

> 💡 **Supervisor vs Router**: Supervisor는 대화 컨텍스트를 유지하며 여러 턴에 걸쳐 동적으로 어떤 서브에이전트를 부를지 결정하는 완전한 에이전트입니다. Router는 보통 대화 상태를 유지하지 않는 단발성 분류 후 디스패치입니다.

### 기본 구현

```python
from langchain.tools import tool
from langchain.agents import create_agent

# 1. 서브에이전트(워커) 생성
research_agent = create_agent(model="openai:gpt-5.5", tools=[search_web, read_url])

# 2. 도구로 감싸기 — docstring 품질이 중요! (감독자의 라우팅 판단 근거가 됨)
@tool("research", description="주제를 조사하고 조사 결과를 반환합니다")
def call_research_agent(query: str) -> str:
    result = research_agent.invoke({"messages": [{"role": "user", "content": query}]})
    return result["messages"][-1].text  # 최종 결과만 추출해서 감독자에게 반환

# 3. 서브에이전트를 도구로 가진 감독자 생성
supervisor = create_agent(model="openai:gpt-5.5", tools=[call_research_agent])
```

> ✅ 도구 래핑 패턴은 이 저장소 검증 venv에서 구조 확인됨.

### 설계 선택지

| 결정 | 옵션 |
|---|---|
| **동기 vs 비동기** | 동기(blocking, 결과가 있어야 다음 진행) vs 비동기(백그라운드 작업, 사용자를 기다리게 하지 않음) |
| **도구 패턴** | 에이전트당 도구 하나씩 vs 단일 디스패치 도구 |
| **서브에이전트 명세 전달** | 시스템 프롬프트 vs enum 제약 vs 도구 기반 발견 |
| **서브에이전트 입력** | 쿼리만 vs 전체 컨텍스트 |
| **서브에이전트 출력** | 결과 요약만 vs 전체 대화 이력 |

**동기 vs 비동기 선택 기준**: 감독자의 다음 행동이 서브에이전트 결과에 의존하면 **동기**(간단하지만 대화가 멈춤), 독립적인 작업이고 사용자를 기다리게 하면 안 되면 **비동기**(응답성은 좋지만 구현이 복잡, job_id로 상태를 추적).

## 11.3 Handoffs 패턴

**핸드오프**(용어는 OpenAI가 만듦)는 상태 변수(`current_step`, `active_agent` 등)에 따라 동작이 동적으로 바뀌는 패턴입니다. 도구가 이 상태 변수를 갱신하고, 시스템은 그 값을 읽어 다른 설정(시스템 프롬프트/도구)을 적용하거나 다른 에이전트로 라우팅합니다.

**특징**: 상태 기반 동작 전환 · 도구가 상태를 갱신해 전이를 유발 · 각 상태에서 사용자와 직접 대화 · 상태가 대화 턴을 넘어 지속됨.

**언제 쓰나**: 순서 제약을 강제해야 할 때(보증 확인 전엔 환불 처리 불가 등), 각 단계마다 사용자와 직접 대화해야 할 때, 다단계 대화 흐름을 만들 때 — 특히 정해진 순서로 정보를 수집해야 하는 고객지원 시나리오.

### 핵심 메커니즘 — `Command`를 반환하는 도구

```python
from langchain.tools import tool
from langchain.messages import ToolMessage
from langgraph.types import Command

@tool
def transfer_to_specialist(runtime) -> Command:
    """전문가 에이전트로 전환합니다."""
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content="전문가로 전환되었습니다",
                    tool_call_id=runtime.tool_call_id,  # 필수! 없으면 대화 이력이 깨짐
                )
            ],
            "current_step": "specialist",  # 이 값이 동작 전환을 유발
        }
    )
```

> ⚠️ **`ToolMessage`를 꼭 포함하세요**: 모델이 도구를 호출하면 응답을 기대합니다. 일치하는 `tool_call_id`를 가진 `ToolMessage`가 이 요청-응답 사이클을 완성합니다 — 없으면 대화 이력 형식이 깨집니다.

### 구현 방식 두 가지

| 방식 | 설명 |
|---|---|
| **단일 에이전트 + 미들웨어** | 하나의 에이전트가 상태에 따라 동적으로 설정을 바꿈. `@wrap_model_call` 미들웨어가 매 모델 호출마다 시스템 프롬프트/도구를 조정 |
| **여러 에이전트 서브그래프** | 별개의 에이전트를 그래프 노드로 배치, 상태에 따라 다른 노드로 라우팅 |

### 단일 에이전트 + 미들웨어 예제

```python
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import wrap_model_call, ModelRequest, ModelResponse
from langchain.tools import tool, ToolRuntime
from langchain.messages import ToolMessage
from langgraph.types import Command
from typing import Callable

# 1. current_step을 추적하는 상태 정의
class SupportState(AgentState):
    current_step: str = "triage"
    warranty_status: str | None = None

# 2. 도구가 Command로 current_step을 갱신
@tool
def record_warranty_status(status: str, runtime: ToolRuntime[None, SupportState]) -> Command:
    """보증 상태를 기록하고 다음 단계로 전환합니다."""
    return Command(update={
        "messages": [ToolMessage(
            content=f"보증 상태 기록됨: {status}",
            tool_call_id=runtime.tool_call_id,
        )],
        "warranty_status": status,
        "current_step": "specialist",  # 다음 단계로 전환
    })

# 3. 미들웨어가 current_step에 따라 동적으로 설정 적용
@wrap_model_call
def apply_step_config(request: ModelRequest, handler: Callable) -> ModelResponse:
    step = request.state.get("current_step", "triage")
    if step == "triage":
        request = request.override(
            system_prompt="당신은 접수 담당자입니다. 보증 상태를 먼저 확인하세요.",
            tools=[record_warranty_status],
        )
    elif step == "specialist":
        request = request.override(
            system_prompt="당신은 전문 상담원입니다. 문제 해결을 도와주세요.",
            tools=[provide_solution, escalate_to_human],
        )
    return handler(request)

agent = create_agent(
    model="openai:gpt-5.5",
    tools=[record_warranty_status, provide_solution, escalate_to_human],
    middleware=[apply_step_config],
    state_schema=SupportState,
)
```

## 11.4 StateGraph 패턴 (커스텀)

복잡한 조건부 분기, 피드백 루프, 세밀한 병렬 제어가 필요하면 서브그래프로 여러 에이전트를 직접 조립합니다 — [04-subgraphs.md](04-subgraphs.md)의 두 가지 통신 방식(스키마 다름/공유)을 그대로 활용합니다.

```python
from langgraph.graph import StateGraph, START, END, MessagesState

# 각 에이전트를 서브그래프(또는 create_agent 결과)로 준비
researcher = create_agent(model="openai:gpt-5.5", tools=[search_web])
writer = create_agent(model="openai:gpt-5.5", tools=[])

def research_node(state: MessagesState):
    result = researcher.invoke(state)
    return {"messages": result["messages"]}

def write_node(state: MessagesState):
    result = writer.invoke(state)
    return {"messages": result["messages"]}

builder = StateGraph(MessagesState)
builder.add_node("research", research_node)
builder.add_node("write", write_node)
builder.add_edge(START, "research")
builder.add_edge("research", "write")
builder.add_edge("write", END)
graph = builder.compile()
```

## 11.5 요약 비교

| | Subagents | Handoffs | StateGraph |
|---|---|---|---|
| 사용자와의 관계 | 감독자만 대화 | 각 단계가 대화 | 자유 설계 |
| 병렬성 | 여러 서브에이전트 동시 호출 가능 | 기본적으로 순차 | `Send`로 완전 자유 |
| 적합한 예 | 리서치+분석+작성 종합 | 순차적 고객지원 워크플로우 | 조건부 분기가 많은 복잡한 시스템 |
| 관련 장 | 본 절 11.2 | 본 절 11.3 | [02](02-stategraph-basics.md), [04](04-subgraphs.md) |
