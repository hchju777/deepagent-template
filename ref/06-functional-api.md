# 6. Functional API — `@entrypoint` / `@task`

Graph API(`StateGraph`)가 노드/엣지를 명시적으로 선언하는 방식이라면, **Functional API**는 일반 Python 함수에 데코레이터만 붙여서 체크포인팅·재개·병렬 실행 같은 LangGraph의 이점을 얻는 방식입니다. 둘 다 **같은 Pregel 런타임 위에서 동작**하므로 한 애플리케이션 안에서 섞어 쓸 수 있습니다.

## 6.1 임포트와 기본 개념

```python
from langgraph.func import entrypoint, task
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
```

| 데코레이터 | 역할 |
|---|---|
| `@entrypoint(checkpointer=..., store=...)` | 워크플로우 전체의 진입점. 체크포인트 1개에 대응 |
| `@task(retry_policy=None)` | 체크포인팅되는 최소 작업 단위. 호출하면 즉시 실행되지 않고 **future**를 반환(`.result()`로 대기) |

## 6.2 기본 예제

```python
import time
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint, task
from langgraph.types import interrupt, Command

@task
def write_essay(topic: str) -> str:
    """체크포인팅되는 개별 작업 단위."""
    time.sleep(1)  # 오래 걸리는 작업 시뮬레이션
    return f"{topic}에 대한 에세이"

@entrypoint(checkpointer=InMemorySaver())
def workflow(topic: str) -> dict:
    essay = write_essay(topic).result()  # task 실행 후 결과 대기
    is_approved = interrupt({
        "essay": essay,
        "action": "에세이를 승인/거부해주세요",
    })
    return {"essay": essay, "is_approved": is_approved}

config = {"configurable": {"thread_id": "thread-1"}}

# 최초 실행 — interrupt 지점에서 정지
workflow.invoke("고양이", config)

# 사람이 승인 후 재개 (같은 thread_id)
workflow.invoke(Command(resume=True), config)
```

## 6.3 `previous` — 이전 실행 결과 자동 주입

같은 `thread_id`로 다시 호출하면 **직전 반환값**이 `previous` 파라미터로 자동 주입됩니다 — 단기 메모리 역할을 합니다.

```python
@entrypoint(checkpointer=InMemorySaver())
def my_workflow(number: int, *, previous: int | None = None) -> int:
    previous = previous or 0
    return number + previous

config = {"configurable": {"thread_id": "t1"}}
my_workflow.invoke(1, config)  # -> 1
my_workflow.invoke(2, config)  # -> 3  (1 + 2, previous=1이 자동 주입됨)
```

> ✅ 검증됨.

**반환값과 저장값을 분리**하고 싶으면 `entrypoint.final[return_type, save_type]`을 씁니다 — 호출자에게는 다른 값을 보여주고, 다음 호출의 `previous`에는 다른 값을 저장할 수 있습니다.

```python
@entrypoint(checkpointer=InMemorySaver())
def my_workflow(number: int, *, previous: int | None = None) -> entrypoint.final[int, int]:
    previous = previous or 0
    return entrypoint.final(value=previous, save=2 * number)  # 반환은 previous, 저장은 2*number

config = {"configurable": {"thread_id": "t2"}}
my_workflow.invoke(5, config)  # -> 0   (첫 호출, previous=0)
my_workflow.invoke(5, config)  # -> 10  (직전 save=2*5=10이 previous로 들어옴)
```

> ✅ 검증됨.

## 6.4 인젝터블 파라미터

| 파라미터 | 설명 |
|---|---|
| `previous` | 같은 스레드의 직전 반환값(또는 `entrypoint.final`의 `save` 값) |
| `store` | `BaseStore` — 스레드를 넘나드는 장기 메모리 접근 |
| `writer` | 스트리밍 출력용 writer |
| `config` | `RunnableConfig` |

## 6.5 병렬 실행 — task를 여러 번 호출

```python
@task
def add_one(x: int) -> int:
    return x + 1

@entrypoint(checkpointer=InMemorySaver())
def workflow(numbers: list[int]) -> list[int]:
    futures = [add_one(n) for n in numbers]      # 즉시 여러 task 실행 시작
    return [f.result() for f in futures]          # 나중에 결과 수집
```

IO-bound 작업(예: 여러 LLM 호출을 동시에)에 유용합니다.

## 6.6 재시도

```python
from langgraph.types import RetryPolicy

@task(retry_policy=RetryPolicy(retry_on=ValueError))
def flaky_call(x: int) -> int:
    ...
```

## 6.7 중첩 entrypoint

```python
@entrypoint()  # checkpointer 생략 시 부모 것을 상속
def sub_workflow(inputs: dict) -> int:
    return inputs["value"]

@entrypoint(checkpointer=InMemorySaver())
def parent(inputs: dict) -> int:
    return sub_workflow.invoke({"value": inputs["value"]})
```

## 6.8 결정성과 멱등성

- **비결정적/부수효과가 있는 코드는 반드시 `@task` 안에** 두세요 (난수, 파일 쓰기, API 호출 등). `entrypoint` 본문의 순수 제어 흐름은 재실행 시 다시 평가됩니다.
- `@task` 결과는 체크포인트에 귀속되어 재실행(replay) 시 재계산 없이 로드됩니다 — 단, **task 재실행 가능성을 고려해 멱등하게 설계**해야 합니다.
- entrypoint 입출력과 task 출력은 **JSON 직렬화 가능**해야 합니다.

## 6.9 `@task` 안에 뭘 넣고 뭘 빼야 하나

| 감싸야 함 (`@task`) | 감싸지 않아도 됨 |
|---|---|
| 외부 API 호출 | 단순 계산 |
| DB/파일 I/O | 제어 흐름(if/for) |
| 이메일 발송 | 로깅 |
| — | 입력 검증 |

## 6.10 Functional API vs Graph API

| 항목 | Functional API | Graph API (`StateGraph`) |
|---|---|---|
| 제어 흐름 | 일반 Python `if`/`for`/함수 호출 | 노드-엣지 DAG를 명시적으로 정의 |
| 상태 관리 | 함수 스코프에 한정. `previous`로 스레드 간 값만 전달 | 명시적 State 스키마 + 리듀서 |
| 체크포인트 단위 | entrypoint 1개당 1개 | super-step마다 새로 생성 |
| 시각화 | 미지원(런타임에 동적 생성) | `get_graph().draw_mermaid()` 등 지원 |
| 코드량 | 상대적으로 적음 | 파이프라인을 명시하느라 더 장황 |
| 적합한 경우 | 기존 함수형 코드를 최소 변경으로 감쌀 때, 짧고 순차적인 워크플로우 | 복잡한 다중 노드 오케스트레이션, 시각적 디버깅이 필요한 팀 프로젝트 |

**언제 뭘 쓸까**

- **Functional API 단독**: 스크립트형 파이프라인, 순차/부분 병렬 로직, 시각화 불필요, 빠른 프로토타이핑
- **Graph API 단독**: 멀티 에이전트 라우팅, 조건부 분기가 많은 그래프, 서브그래프 재사용, 시각적 디버깅이 중요한 프로덕션 시스템
- **하이브리드** (실무에서 흔함): 전체 오케스트레이션은 `StateGraph`로 짜고, 그 안의 개별 노드(문서 배치 처리, 병렬 API 호출 등)를 `@task`/`@entrypoint`로 감싸 재시도·체크포인트 이점을 취함
