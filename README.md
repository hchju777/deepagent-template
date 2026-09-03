# deepagent-template

디지털 트윈 시스템(edge → Redis/MongoDB/Kafka를 경유하는 서비스들 → REST API)을
대상으로 하는 **운영 모니터링 에이전트** 템플릿이다. `langgraph-template`(리포팅용
LangGraph 템플릿)의 후속작으로, LangGraph의 "개발자가 control flow를 지정한다"는
장점과 DeepAgent의 "태스크를 분할하고, 컨텍스트를 영속화하고, 서브에이전트에
위임한다"는 장점을 한 시스템 안에 결합했다.

> 대상 시스템에는 **완전 읽기 전용**이다. 이 에이전트는 아무것도 쓰지 않는다 —
> Redis/MongoDB/Kafka/REST 어댑터 전부가 코드 수준에서 쓰기 경로를 갖지 않는다.

## 이게 뭘 하는가

두 가지 방식으로 조사 케이스를 연다. 어느 쪽이든 **같은 조사 엔진**(`src/application/graph.py`)을
탄다 — 원인 판정과 조치 권고가 나오는 로직은 하나뿐이고, 케이스가 어떻게
열렸는지만 다르다.

1. **사람이 문제를 제기** — `python -m src chat --gbm mx --fct gumi`로 특정 사업부/시설의
   증상을 말로 설명하면, 에이전트가 대화형으로 접수(intake) 질문을 던지고 케이스를
   열어 조사한다.
2. **에이전트가 스스로 순찰** — `python -m src patrol run`이 config에 등록된 점검들을
   주기적으로(interval/cron) 돌며 이상을 탐지하면, 같은 조사 엔진으로 케이스를
   자동으로 연다.

조사는 가설 세우기 → 서브에이전트(`data_prober`/`code_tracer`/`recompute_verifier`)로
증거 수집 → 통합 → (필요하면 사람에게 질문) → 판정 → 인용 검증의 라운드를
최대 상한(`engine.max_rounds`, 기본 6)까지 반복한다. 판정은 **인과 사슬**(근본
원인 + 기여 요인)이며, 모든 주장은 실제로 수집된 증거 id를 인용해야 하고 이
인용은 그래프 안에서 결정론적으로 검증된다(`verify` 노드 — LLM 없음).

원천 데이터는 정상인데 파생 결과값만 이상한 "변환 버그" 케이스가 이 시스템의
핵심 난제다 — `code_tracer`가 대상 서비스의 실제 소스 코드를 읽고,
`recompute_verifier`가 코드를 따라 값을 재계산해 관측치와 대조하는 방식으로
접근한다.

## 문서 지도

| 문서 | 무엇을 위한 것인가 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 4계층 구조, 두 모드의 실제 실행 경로, 케이스 수명주기, 조사 엔진 그래프 |
| [docs/tutorial.md](docs/tutorial.md) | 새 순찰 점검 하나를 처음부터 끝까지 추가해보는 실습 |
| [docs/config-reference.md](docs/config-reference.md) | `config/`·`.env`·`knowledge/`의 모든 키를 총망라한 레퍼런스 |
| [docs/howto.md](docs/howto.md) | "~하고 싶다"로 찾아가는 작업별 색인 |
| [docs/glossary.md](docs/glossary.md) | Case, Verdict, Envelope 등 용어집 |
| [docs/going-live.md](docs/going-live.md) | 스텁 어댑터 → 실제 시스템 연결 전환 가이드 |
| [tests/README.md](tests/README.md) | 테스트 철학과 실행법 |
| [CLAUDE.md](CLAUDE.md) | 이 코드베이스에서 작업하는 AI 에이전트를 위한 규율 |
| [docs/superpowers/specs/2026-09-02-ops-monitoring-design.md](docs/superpowers/specs/2026-09-02-ops-monitoring-design.md) | 승인된 시스템 설계 스펙 원본(단일 진실 소스) |

## 5분 빠른 시작

리포에는 실제로 동작이 검증된 예시 트리가 들어 있다: `config.example/`(mx/gumi
사이트 하나, 순찰 점검 하나)와 `knowledge.example/`(그 사이트의 토폴로지). 아래
명령들을 그대로 복사해 실행하면 아무것도 새로 만들지 않고 바로 동작을 확인할 수
있다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

```bash
cp .env.example .env
```

`.env.example`은 예시 트리(`config.example`/`knowledge.example`)가 그대로
참조할 수 있는 값들로 이미 채워져 있어(가짜 호스트명·`LLM_API_KEY=sk-example`
등) 별도로 편집하지 않아도 아래 명령들이 바로 통과한다. 참고로
`app.json`이 `llm.profiles`(judge/subagent/lead)를 지정하고 있으면, 실제로
LLM을 호출하는 점검이 하나도 없어도 **기동 검증이 `LLM_API_KEY` 존재 자체를
요구한다**(§4.6 검사 11 — "키가 없으면 나중에 조용히 깨지느니 기동 시점에
막는다"는 철학). 예시 사이트(`config.example/gbm/mx.json`)의 점검은
`judge: "rule"`이라 실제로 LLM을 부르지는 않는다. 자신의 사이트를 채울 때는
이 값들을 실제 접속 정보로 바꾸면 된다.

```bash
# 사이트 목록
python -m src registry --config-root config.example --repo-root .

# 기동 검증 단독 실행 — config·토폴로지·룰 타깃 정합성을 전부 확인
python -m src knowledge validate --config-root config.example --repo-root .

# 병합된 사이트 config와 값의 출처(어느 계층에서 왔는지) 확인
python -m src config show --gbm mx --fct gumi --config-root config.example --repo-root .

# 순찰 데몬을 5초만 띄워본다 — 점검이 한 번 이상 돈다
python -m src patrol run --for-seconds 5 --config-root config.example --repo-root .

# 케이스 목록(메모리 백엔드는 프로세스가 끝나면 사라진다)
python -m src case list --config-root config.example --repo-root .
```

여기까지 됐다면 [docs/tutorial.md](docs/tutorial.md)로 넘어가 실제로 이상을
하나 만들어서 순찰이 잡아내고 조사해 보고서를 내는 과정을 끝까지 따라가 보라.

자신의 프로젝트로 시작하려면 두 예시 트리를 복사해서 채워 나가면 된다:

```bash
cp -r config.example config
cp -r knowledge.example knowledge
```

키·계층 구조는 [docs/config-reference.md](docs/config-reference.md)를 참고.

## 저장소 구조

```
src/
  application/    조사 엔진 — 그래프 배선(graph.py), 노드(nodes.py), State, 워커, 큐, 접수(intake)
  domain/         순수 도메인 모델 — Case, Verdict, Envelope, 이벤트, 포트(ABC) 정의
  infrastructure/ 실제/스텁 어댑터, 체크포인터, Mongo 저장소, LLM 팩토리
  patrol/         순찰 — 프로브 레지스트리, rule 판정, 게이트, 스케줄러, 데몬
  presentation/   보고서 렌더링, 메일 발송
  knowledge/      토폴로지·배포 지식 로더
  config/         config 스키마·로더·병합·env 해석
  boot.py         기동 검증(11개 검사) — 시끄럽게 실패하는 철학
  __main__.py     CLI 엔트리
tests/            src/와 미러링된 테스트 트리 (계층별)
config.example/   동작이 검증된 예시 config 트리
knowledge.example/ 그 예시의 토폴로지
docs/             이 표에 나열된 문서들
docs/superpowers/ 설계 스펙과 구현 계획 원본(과거 기록)
ref/              LangGraph/LangChain 참고 자료(설계 시 사용)
```

## 상태

v1 스코프 완결 — 설계 스펙(§0~§7 + 부록 A)의 계획 1~5가 전부 `main`에
머지됐고, 321개 테스트가 통과한다. "개발 시스템"(코드 수정·데이터 정합성
보정 등 능동적 개입)은 별도 설계로 유보돼 있다.
