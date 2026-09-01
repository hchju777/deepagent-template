# 12. 한글 자료 대응 — wikidocs "LangGraph 가이드북" 참고

이 절은 [wikidocs.net "LangGraph 가이드북 - 에이전트 RAG with 랭그래프 [ver 1.0+]"](https://wikidocs.net/261577) (2026년 8월 기준 최근 갱신, LangGraph 1.0.x 대상)의 용어와 구성을 이 가이드와 대응시킵니다.

## 12.1 용어 대응표

| 한글 용어 | 영문/코드 대응 | 이 가이드 참고 위치 |
|---|---|---|
| 상태 / 상태 스키마 | `State` / `TypedDict`, `MessagesState` | [02](02-stategraph-basics.md#21-state--스키마와-리듀서) |
| 리듀서 | Reducer (`Annotated[T, fn]`) | [02](02-stategraph-basics.md#21-state--스키마와-리듀서) |
| 노드 | Node | [02](02-stategraph-basics.md#22-node--함수와-시그니처) |
| 엣지 / 조건부 엣지 | Edge / Conditional Edge | [02](02-stategraph-basics.md#23-edge--라우팅) |
| 원자적 연산 | `Command`의 update+goto 동시 적용 | [02](02-stategraph-basics.md#25-command--상태-업데이트--라우팅을-한-번에) |
| 체크포인터 | Checkpointer(`InMemorySaver`/`SqliteSaver`/`PostgresSaver`) | [03](03-memory-and-persistence.md#31-체크포인터--단기-메모리) |
| 서브그래프 | Subgraph | [04](04-subgraphs.md) |
| 스트리밍 | Streaming (`stream_mode`) | [05](05-streaming.md) |
| 진입점 / 작업 | `@entrypoint` / `@task` | [06](06-functional-api.md) |
| 워커 에이전트 | Worker/Subagent | [11](11-multi-agent-systems.md#112-subagents-supervisor-패턴) |
| 감독자 | Supervisor | [11](11-multi-agent-systems.md#112-subagents-supervisor-패턴) |
| 핸드오프 | Handoffs | [11](11-multi-agent-systems.md#113-handoffs-패턴) |
| 가드레일 | Guardrails | [09](09-guardrails.md) |
| 결정론적 / 모델 기반 (가드레일) | Deterministic / Model-based | [09](09-guardrails.md) |
| 환각 | Hallucination | [09](09-guardrails.md), [10](10-rag-with-langgraph.md#104-평가-및-최적화) |

## 12.2 LangGraph vs LangChain — wikidocs의 설명

wikidocs 1-1-2절은 "무엇이 더 우월한가"가 아니라 **프로젝트 복잡도에 따른 선택**으로 프레이밍합니다.

| 축 | LangChain | LangGraph |
|---|---|---|
| 주요 목적 | 빠른 LLM 앱 개발 | 복잡하고 맞춤화된 AI 시스템 |
| 구조 | 체인 및 에이전트 기반 | 그래프 기반 |
| 상태 관리 | 암시적/자동 | 명시적/세밀 |
| 유연성 | 보통 | 높음 |
| 학습 곡선 | 완만함 | 가파름 |
| 용도 | 간단한 LLM 앱/RAG | 복잡한 다중 에이전트 |

**결론**: 복잡하고 맞춤화된 AI 시스템이 필요하면 LangGraph, 빠른 개발과 간단한 LLM 통합이 목표면 LangChain이 더 적합합니다. 이는 [01-setup-and-langchain-vs-langgraph.md](01-setup-and-langchain-vs-langgraph.md)의 "계층 관계" 설명과 일맥상통합니다 — `create_agent`(LangChain)는 내부적으로 LangGraph 그래프이므로, "쉽게 시작해서 필요하면 세밀하게 내려간다"는 전략이 유효합니다.

## 12.3 설치 — wikidocs 권장 방식 (이 가이드와 동일하게 `uv` 우선)

```bash
python --version   # 3.10+ 확인

# uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux
# Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

uv init langgraph-project
cd langgraph-project
uv venv --python 3.11
source .venv/bin/activate   # macOS/Linux

uv add langgraph langchain langchain-openai

# 설치 확인
python -c "from importlib.metadata import version; print(version('langgraph'))"
python -c "from langgraph.graph import StateGraph; print('설치 완료!')"
```

> wikidocs는 LangGraph 버전대별 기능을 이렇게 구분합니다: **0.2.x** → `Command`/`interrupt` 도입, **0.3.x** → Functional API 도입, **1.0.x** → 안정화(stable). 이 가이드는 1.2.x 기준입니다.

## 12.4 wikidocs가 다루는 RAG 고급 패턴과 이 가이드의 차이

wikidocs 3-3-2절은 **Multi-Query RAG / HyDE / Self-RAG** 세 가지를 다룹니다 (Corrective RAG라는 별도 명칭은 쓰지 않지만, Self-RAG 설명에 재시도 루프가 포함되어 사실상 동일한 개념). 이 가이드의 [10-rag-with-langgraph.md](10-rag-with-langgraph.md)는 공식 문서의 코드를 기준으로 동일한 패턴(관련성 채점 + 재시도 루프)을 다룹니다 — 용어만 다를 뿐 구조는 같습니다.

## 12.5 wikidocs의 가드레일 구현 — 참고할 만한 한국어 특화 패턴

wikidocs 2-5-1절은 **한국어 PII 정규식**을 구체적으로 제시합니다 (이 가이드 [09-guardrails.md](09-guardrails.md)에도 반영):

| PII 유형 | 정규식 패턴 |
|---|---|
| 주민등록번호 | `\d{6}-[1-4]\d{6}` |
| 휴대전화 | `01[0-9]-?\d{3,4}-?\d{4}` |

프롬프트 인젝션 유형은 **Context Ignoring**(이전 지시 무시) / **Role Assumption**(역할 탈취) / **Command Injection**(명령 삽입) / **Token Injection**(특수 토큰 삽입) 네 범주로 분류하고, 오탐 관리를 위한 화이트리스트 운용을 권장합니다.

가드레일 판정용 모델로는 메인 모델보다 저렴한 모델(OpenAI `gpt-4.1-nano`/`gpt-4.1-mini`, Anthropic `claude-haiku-4-5`)을 쓰고, 키워드 사전검사 후 의심스러울 때만 LLM 판정을 호출하는 **계층형 접근(비용 최적화)**을 제안합니다.

## 12.6 참고: 이 가이드가 다루지 않는 wikidocs 범위

- **Part 0** (글쓴이 소개) — 가이드 성격상 생략
- 세부 실습 연습문제(예: 4-2-1절의 ArXiv 에이전트 추가 실습) — 핵심 패턴만 추출해 [11-multi-agent-systems.md](11-multi-agent-systems.md)에 반영
- LangSmith Studio UI 사용법 — 이 가이드는 코드 중심이라 별도로 다루지 않음

전체 목차와 최신 내용은 원문 [wikidocs.net/261577](https://wikidocs.net/261577)에서 확인하세요.
