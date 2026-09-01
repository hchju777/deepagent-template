# LangGraph (Python) 가이드 — `ref/`

이 디렉터리는 **LangGraph**를 Python으로 사용하기 위해 정리한 참고 문서 모음입니다. 아래 세 소스를 종합했습니다.

| 소스 | 내용 | 특징 |
|---|---|---|
| [langchain.com](https://www.langchain.com/) | 제품/생태계 소개 | LangGraph가 LangChain 생태계에서 차지하는 위치 |
| [reference.langchain.com/python/langgraph](https://reference.langchain.com/python/langgraph) | LangGraph Python API 레퍼런스 | 함수/클래스 시그니처의 1차 소스 |
| [wikidocs.net "LangGraph 가이드북 - 에이전트 RAG with 랭그래프 [ver 1.0+]"](https://wikidocs.net/261577) | 한글 튜토리얼 | 한국어 용어, 실습 흐름의 기준 |

기준 버전: **LangGraph 1.2.x (2026년 8월 기준 stable)**. 예제의 기본 LLM 공급자는 **OpenAI**로 통일했습니다.

> **이 저장소와의 관계**: `ref/`는 **LangGraph 프레임워크 자체**를 다룹니다. 이 저장소를 **템플릿으로 쓰는 법**은 [README](../README.md) · [튜토리얼](../docs/tutorial.md) · [작업별 가이드](../docs/howto.md)에 있습니다.
>
> 템플릿의 규약이 왜 그렇게 생겼는지 궁금할 때 아래 대응표로 내려오세요.

| 템플릿에서 보게 되는 것 | 무엇의 적용인가 | 개념 문서 |
|---|---|---|
| 서브그래프 **4슬롯** (`validate_input`/`process`/`generate_output`/`handle_error`) | 서브그래프 + 조건부 라우팅 | [04-subgraphs](04-subgraphs.md), [02-stategraph-basics](02-stategraph-basics.md#23-edge--라우팅) |
| 슬롯 간 이동에 **정적 엣지를 안 쓰고 `Command`만** 쓰는 이유 | `Command`는 정적 엣지를 대체하지 않고 *추가*로 동작함 | [02-stategraph-basics](02-stategraph-basics.md#25-command--상태-업데이트--라우팅을-한-번에) |
| `sections`의 `merge_sections`, `errors`의 `operator.add` | 리듀서 | [02-stategraph-basics](02-stategraph-basics.md#21-state--스키마와-리듀서) |
| 6개 서브그래프가 **병렬로 돌고** `aggregate`에서 모이는 것 | fan-out + 자연스러운 barrier | [02-stategraph-basics](02-stategraph-basics.md#23-edge--라우팅) |
| `as_of`를 밖에서 주입하고 노드에서 `datetime.now()`를 금지하는 이유 | 재개·Time Travel·멱등성 | [03-memory-and-persistence](03-memory-and-persistence.md) |
| `thread_id` · `--show-checkpoints` · fork | 체크포인터와 Time Travel | [03-memory-and-persistence](03-memory-and-persistence.md#32-time-travel--replay와-fork) |
| `--stream`의 노드별 진행 표시 | `stream_mode="updates"` | [05-streaming](05-streaming.md) |
| 환각 가드레일(근거 id 검증) | 결정론적 가드레일 | [09-guardrails](09-guardrails.md) |

## 읽는 순서

이 순서는 wikidocs 가이드북의 Part 구성을 그대로 따릅니다.

| 파일 | 주제 (wikidocs Part 대응) |
|---|---|
| [01-setup-and-langchain-vs-langgraph.md](01-setup-and-langchain-vs-langgraph.md) | 설치, LangChain과의 차이, 왜 LangGraph인가 (Part 1-1, 1-2) |
| [02-stategraph-basics.md](02-stategraph-basics.md) | State/Reducer/MessagesState, Node, Edge, 조건부 엣지, `Command`, `Send` (Part 1-3) |
| [03-memory-and-persistence.md](03-memory-and-persistence.md) | 체크포인터, Time Travel, Durable Execution, Store(장기 메모리) (Part 1-4) |
| [04-subgraphs.md](04-subgraphs.md) | 서브그래프 구현과 통합 (Part 1-5) |
| [05-streaming.md](05-streaming.md) | `stream_mode` 전체 비교 (values/updates/messages/custom/debug) (Part 1-6) |
| [06-functional-api.md](06-functional-api.md) | `@entrypoint`/`@task`, Graph API와의 비교 (Part 1-7) |
| [07-react-agents.md](07-react-agents.md) | ReAct 패턴, `create_agent` vs `StateGraph` 커스텀 에이전트 (Part 2-1~2-3) |
| [08-human-in-the-loop.md](08-human-in-the-loop.md) | `interrupt()`, 승인 워크플로우 (Part 2-4) |
| [09-guardrails.md](09-guardrails.md) | 결정론적/모델 기반/HITL 가드레일 (Part 2-5) |
| [10-rag-with-langgraph.md](10-rag-with-langgraph.md) | 문서처리, 벡터DB, StateGraph 기반 RAG, 평가 (Part 3) |
| [11-multi-agent-systems.md](11-multi-agent-systems.md) | 멀티 에이전트 아키텍처, Subagents/Handoffs 패턴 (Part 4) |
| [12-korean-resources.md](12-korean-resources.md) | 한글 용어 정리, wikidocs 대응표 |

## 빠른 의사결정 표

### "LangChain `create_agent` vs LangGraph `StateGraph`, 뭘 써야 하나?"

| | `create_agent` (LangChain) | `StateGraph` (LangGraph 저수준) | Functional API (`@entrypoint`/`@task`) |
|---|---|---|---|
| 무엇인가 | 표준 도구-호출 루프를 가진 사전제작 에이전트 | 노드/엣지로 직접 설계하는 상태 기계 | 일반 Python 함수에 체크포인팅/재개 기능을 얹는 방식 |
| 관계 | 내부적으로 `StateGraph` 위에 빌드됨 | LangGraph의 핵심 엔진 | `StateGraph`와 같은 Pregel 런타임 위에서 동작(더 얇은 API) |
| 언제 쓰나 | 대부분의 "도구 쓰는 챗봇/에이전트" | 커스텀 분기, 병렬 처리(`Send`), 여러 에이전트 조합, 세밀한 상태 제어가 필요할 때 | 기존 함수형 코드에 최소 침습으로 체크포인팅/휴먼인더루프를 추가하고 싶을 때 |
| 학습 곡선 | 낮음 | 중간~높음 | 낮음~중간 |
| 이 저장소의 관계 | [07-react-agents.md](07-react-agents.md)의 빠른 시작 경로 | 이 가이드의 핵심(Part 1) | [06-functional-api.md](06-functional-api.md) |

### LangGraph를 써야 하는 이유 (wikidocs 1-1-3 기준)

1. **명시적 상태 관리**: 대화/작업 상태가 그래프의 State로 명시적으로 정의되어 추적·디버깅이 쉬움.
2. **세밀한 제어 흐름**: 조건부 분기, 병렬 실행(`Send`), 사이클(루프)을 자유롭게 구성 가능 — 단순 LCEL 체인으로는 표현하기 어려움.
3. **내장 영속성**: 체크포인터만 붙이면 대화 재개, Human-in-the-loop, Time Travel이 거의 공짜로 따라옴.
4. **멀티 에이전트 조합**: 서브그래프로 에이전트를 조합해 Supervisor/Handoff 같은 복잡한 시스템을 구성하기 쉬움.

## 검증 환경

이 가이드의 예제 코드는 아래 환경에서 import/구조/실행이 검증되었습니다 (실제 LLM API 키 호출까지는 아님).

```bash
uv venv .venv
uv pip install --python .venv/bin/python3 \
  langchain langchain-openai langchain-anthropic langgraph \
  langchain-text-splitters langchain-community faiss-cpu python-dotenv
```

`langgraph==1.2.11`, `langchain==1.3.15` 기준.
