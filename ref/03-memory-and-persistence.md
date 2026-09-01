# 3. 메모리와 영속성 — 체크포인터, Store, Time Travel

LangGraph는 **두 가지 보완적인 영속성 시스템**을 제공합니다.

| | 체크포인터 (Checkpointer) | Store |
|---|---|---|
| 저장 대상 | 그래프 상태 스냅샷 | 애플리케이션이 정의한 키-값 데이터 |
| 범위 | **단일 스레드**(대화 하나) | **스레드를 넘나듦** |
| 메모리 종류 | 단기(thread-scoped) 메모리 | 장기(cross-thread) 메모리 |
| 용도 | 대화 연속성, human-in-the-loop, Time Travel, 장애 복구 | 사용자 선호도, 사실, 공유 지식 |
| 접근 방식 | config에 `thread_id` 전달 | 노드/앱 코드에서 직접 read/write |

대부분의 앱은 **둘 다** 씁니다: 체크포인터로 현재 대화를 추적하고, Store로 대화를 넘나드는 영속 정보를 추적합니다.

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

checkpointer = InMemorySaver()
store = InMemoryStore()

graph = builder.compile(checkpointer=checkpointer, store=store)

result = graph.invoke(
    {"messages": [{"role": "user", "content": "안녕, 나는 밥이야."}]},
    {"configurable": {"thread_id": "thread-1"}},
)
```

## 3.1 체크포인터 — 단기 메모리

체크포인터는 **각 super-step마다** 그래프 상태의 스냅샷을 `checkpoint_id`로 저장하고, `thread_id`로 조직합니다.

**체크포인터가 필요한 이유:**

- **Human-in-the-loop**: 사람이 그래프 상태를 검사/중단/승인할 수 있어야 함 → [08-human-in-the-loop.md](08-human-in-the-loop.md)
- **메모리**: 같은 스레드로 후속 메시지를 보내면 이전 대화를 기억함
- **Time Travel**: 과거 실행을 리플레이/포크해서 디버깅하거나 다른 경로를 탐색
- **장애 복구**: 한 super-step에서 일부 노드가 실패해도, 성공한 노드의 결과(pending writes)는 보존되어 재실행 시 다시 계산하지 않음

### 체크포인터 종류

| 클래스 | 저장 위치 | 언제 쓰나 |
|---|---|---|
| `InMemorySaver` | 프로세스 메모리(RAM) | 개발/테스트. **프로세스 재시작 시 전부 소실** |
| `SqliteSaver` | 로컬 SQLite 파일 | 로컬 개발, 단일 프로세스 배포 |
| `PostgresSaver` / `AsyncPostgresSaver` | PostgreSQL | **프로덕션 권장** |
| `MongoDBSaver` | MongoDB | MongoDB 인프라를 이미 쓰는 경우 |

```python
from langgraph.checkpoint.memory import InMemorySaver

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "1"}}
graph.invoke({"foo": "", "bar": []}, config)
```

프로덕션(PostgreSQL):

```bash
pip install -U langgraph-checkpoint-postgres "psycopg[binary]"
```

```python
from langgraph.checkpoint.postgres import PostgresSaver

DB_URI = "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable"
checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()  # 최초 1회 — 테이블 생성
graph = builder.compile(checkpointer=checkpointer)
```

> ⚠️ **`thread_id` 길이 주의**: `PostgresSaver`는 `thread_id`를 길이 제한이 있는 컬럼에 저장합니다. 255자 이하로 유지하세요 (UUID 권장).
> ⚠️ 체크포인트가 무한정 쌓이면 지연/스토리지 비용이 증가합니다. 오래된 체크포인트를 주기적으로 정리하는 정책을 두세요.

### 상태 조회

```python
config = {"configurable": {"thread_id": "1"}}
graph.get_state(config)          # 최신 StateSnapshot
list(graph.get_state_history(config))  # 전체 이력(최신순)
```

`StateSnapshot` 필드: `values`(상태값), `next`(다음 실행할 노드, 빈 튜플이면 완료), `config`(thread_id/checkpoint_id), `metadata`(source/writes/step), `created_at`, `parent_config`, `tasks`.

## 3.2 Time Travel — Replay와 Fork

체크포인트를 기반으로 **과거 실행을 재생(Replay)**하거나 **다른 상태로 분기(Fork)**할 수 있습니다.

> ⚠️ Replay는 캐시에서 읽는 게 아니라 **노드를 실제로 재실행**합니다 — LLM 호출/API 요청이 다시 발생해 결과가 달라질 수 있습니다. 체크포인트 이전 노드는 재실행되지 않고(이미 저장된 결과 사용), 이후 노드만 재실행됩니다.

### Replay

```python
history = list(graph.get_state_history(config))
before_joke = next(s for s in history if s.next == ("write_joke",))

replay_result = graph.invoke(None, before_joke.config)
# write_joke만 재실행됨, generate_topic은 재실행 안 됨
```

### Fork — 상태를 바꿔서 분기

```python
# update_state는 스레드를 되돌리지 않고, 지정 지점에서 새 브랜치를 만듭니다
fork_config = graph.update_state(
    before_joke.config,
    values={"topic": "chickens"},
)
fork_result = graph.invoke(None, fork_config)
```

병렬 분기라 어떤 노드가 마지막으로 썼는지 애매하거나, 빈 스레드에 상태를 세팅하거나, 특정 노드를 건너뛴 것처럼 만들고 싶으면 `as_node`를 명시합니다:

```python
fork_config = graph.update_state(
    before_joke.config, values={"topic": "chickens"}, as_node="generate_topic",
)
```

인터럽트가 있는 그래프를 Time Travel하면 **인터럽트도 다시 트리거**됩니다 — `interrupt()`가 있는 노드가 재실행되어 새 `Command(resume=...)`를 기다립니다.

## 3.3 Store — 장기 메모리

체크포인터가 스레드 하나에 묶인 상태라면, Store는 **네임스페이스(튜플)와 키**로 조직되는 JSON 문서 저장소입니다.

```python
from langgraph.store.memory import InMemoryStore
import uuid

store = InMemoryStore()

user_id = "1"
namespace = (user_id, "memories")  # 임의 깊이의 튜플, 계층 구조 표현 가능

memory_id = str(uuid.uuid4())
store.put(namespace, memory_id, {"food_preference": "피자를 좋아함"})

memories = store.search(namespace)          # 최대 10개(기본 limit)
memories[-1].value  # {'food_preference': '피자를 좋아함'}
```

`Item`(반환 객체) 필드: `value`(dict), `key`, `namespace`(tuple), `created_at`, `updated_at`.

> `namespace_prefix`는 **접두사 매칭**입니다 — `("alice",)`는 `("alice","memories")`, `("alice","preferences")`를 모두 반환합니다. 페이지네이션은 `limit`/`offset`으로.

### 시맨틱 검색

```python
from langchain.embeddings import init_embeddings

store = InMemoryStore(
    index={
        "embed": init_embeddings("openai:text-embedding-3-small"),
        "dims": 1536,
        "fields": ["food_preference", "$"],  # 임베딩할 필드
    }
)

memories = store.search(namespace, query="사용자가 뭘 먹는 걸 좋아하나?", limit=3)
```

### 그래프에 연결하기

```python
graph = builder.compile(checkpointer=checkpointer, store=store)
```

노드/도구 안에서는 `runtime.store`로 접근합니다 (도구에서의 사용법은 LangChain 쪽 `ToolRuntime` 참고).

프로덕션 Store: `PostgresStore`, `MongoDBStore`, `RedisStore`, `UpstashStore` 등 — 전부 `BaseStore`를 상속.

## 3.4 체크포인터 vs Store — 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| 재시작하면 대화가 사라짐 | `InMemorySaver`/`MemorySaver`는 RAM 저장 | `PostgresSaver`/`SqliteSaver`로 교체 |
| 서브그래프 업데이트가 부모 그래프에 안 보임 | 서브그래프가 자체 체크포인트 네임스페이스를 가짐 | 스레드를 넘나드는 데이터는 Store로, 또는 서브그래프가 부모 체크포인트에 쓰도록 구성 → [04-subgraphs.md](04-subgraphs.md) |
| `thread_id`가 너무 길다는 DB 에러 | `PostgresSaver` 컬럼 길이 제한 | 255자 이하 UUID 사용 |
