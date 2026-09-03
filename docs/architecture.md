# 아키텍처

이 문서는 시스템이 실제로 어떻게 배선돼 있는지를 코드 기준으로 설명한다.
설계 근거(왜 이렇게 결정했는지)는
[docs/superpowers/specs/2026-09-02-ops-monitoring-design.md](superpowers/specs/2026-09-02-ops-monitoring-design.md)에
있다 — 이 문서는 "지금 코드가 어떻게 동작하는가"에 집중한다.

## 1. 4계층

```
presentation  →  application  →  domain  ←  infrastructure
```

의존은 항상 안쪽(domain)을 향한다. domain은 다른 계층을 import하지 않는다.

- **domain** (`src/domain/`) — 순수 모델과 포트(ABC)만 있다. `Case`, `Verdict`,
  `EvidenceRef`, `Envelope`/`ProbeResult`, `EngineEvent`, `CaseRecord`,
  `Finding`/`CheckOutcome`, 그리고 대상 시스템에 접근하는 포트
  (`RedisReaderPort`/`MongoReaderPort`/`KafkaInspectorPort`/`RestProberPort`/`CodeRepoReaderPort`)와
  케이스 저장소 포트(`CaseRepositoryPort`, `CaseStorePort`)가 여기 있다.
  모든 모델은 `StrictModel`(`extra="forbid"`)을 상속한다.
- **application** (`src/application/`) — 조사 엔진 자체. 그래프 배선
  (`graph.py`), 노드 로직(`nodes.py`), State(`state.py`), 서브에이전트 실행
  (`subagents.py`), 접수(`intake.py`), 케이스 큐·워커(`worker.py`), 종결 처리
  (`close.py`), 브리핑 조립(`briefing.py`), 이벤트 매핑(`events.py`)이 있다.
- **infrastructure** (`src/infrastructure/`) — domain 포트의 실제 구현체.
  `Real*`(실제 Redis/Mongo/Kafka/REST 클라이언트)와 `Stub*`(인메모리 대역)가
  같은 포트를 구현하며, `factory.py`의 `build_adapters`가 `SiteConfig.target.adapters`
  값("stub"|"real")만 보고 어느 쪽을 조립할지 결정한다 — 이게 스텁↔실구현
  전환의 유일한 지점이다. 그 외 체크포인터(LangGraph용, `checkpointer.py`),
  Mongo 케이스 저장소(`mongo_store.py`), LLM 팩토리(`llm.py`)도 여기 있다.
- **presentation** (`src/presentation/`) — 케이스 종결 후 산출물. 5절 마크다운
  보고서 렌더링(`report.py`)과 2단계(pending→sent) 메일 발송(`mail.py`).
- **CLI**(`src/__main__.py`)는 presentation 바깥의 진입점으로 취급한다 — 여기서만
  `datetime.now()`를 직접 부르는 것이 허용된다(아래 "시계 주입" 참고).

## 2. 두 모드의 실제 실행 경로

### 모드 ①: 사람이 문제 제기 (`chat`)

```
CLI(chat) → intake() → CaseRecord 개설(origin="human")
          → InvestigationWorker.run_once(interaction_policy="interactive")
          → (awaiting_human이면 stdin으로 답을 받아 resume_once 반복)
          → closed → 보고서 경로 출력
```

`src/__main__.py`의 `_run_chat`/`_drive_chat`이 이 경로를 조립한다. `intake()`
(`src/application/intake.py`)가 증상 문장을 받아 필요하면 되묻고, 최종적으로
`gbm`/`fct`/`symptom`/`target_locator`를 확정한다. 접수 중 오간 문답은
`store.put_evidence(case_id, "human:intake", ...)`로 케이스 Store에 박제돼
첫 라운드부터 리드(lead LLM)가 볼 수 있다.

### 모드 ②: 에이전트 자체 순찰 (`patrol run`)

```
PatrolDaemon(스케줄러) → 주기마다 run_check() → CheckOutcome
   ok       → 레저에만 기록
   error    → 레저에 error로 기록(연속 self_check_errors회 넘으면 자기 감시가 잡음)
   finding  → admit_finding()(게이트)
                 opened   → CaseQueue에 적재 → InvestigationWorker가 lease를 잡고 조사
                 attached → 기존 열린 케이스에 증거로 첨부(새 케이스 안 엶)
                 rejected → 억제(스크래치 케이스만 갱신, 케이스 안 엶)
```

`src/patrol/daemon.py`의 `PatrolDaemon`이 스케줄링(`scheduler.py`,
APScheduler)부터 큐·워커·정리(retention sweep)까지 한 프로세스로 조립한다.
점검 하나(`CheckConfig`)는 `resolve_probe()`로 프로브를 고르고
(`src/patrol/probes.py` — `rest_get`/`redis_get`/`mongo_recent`/`kafka_lag`
4종, `target`의 kind 접두사로 기본 선택되거나 `probe` 필드로 명시), 그 결과를
`judge`(`"rule"`|`"llm"`|`"rule+llm"`)로 판정한다. rule 판정은
`src/patrol/rules.py`의 `range`/`exists`/`freshness`/`max` 4종뿐이다.

게이트(`src/patrol/gate.py`)는 같은 지문(`fingerprint(gbm, fct, check, target)`)의
열린 케이스가 있으면 새로 열지 않고 기존 케이스에 증거로 붙인다 — 중복 케이스
억제. 최초 finding은 스크래치 케이스(`patrol:{gbm}:{fct}:{check}`)에 스냅샷으로
먼저 박제된 뒤 판정에 따라 정식 케이스로 승격되거나 버려진다.

어느 경로든 케이스가 조사에 들어가면 **같은 조사 엔진**(§3)을 탄다. 유일한
차이는 `interaction_policy`다: `chat`은 `"interactive"`(질문이 생기면 사람에게
직접 묻는다), 순찰이 연 케이스는 기본 `"autonomous"`(질문이 생기면
`engine.autonomous_question_policy`에 따라 보수적 기본값으로 답하고 로그만
남기거나 — `"default_and_log"` — 사람에게 파킹한다 — `"park"`).

## 3. 조사 엔진 그래프

```mermaid
flowchart TD
    START([START]) --> frame
    frame -- "verdict 이미 있음(파싱 실패 등)" --> END1([END])
    frame -- "그 외" --> select
    select -- "실행 가능 태스크 있음(Send×N)" --> execute
    select -- "0건" --> integrate
    execute --> integrate
    integrate -- "continue" --> select
    integrate -- "ask" --> ask_human
    integrate -- "conclude" --> conclude
    ask_human --> integrate
    conclude --> verify
    verify -- "문제없음 또는 이미 1회 재작성함(강등 통과)" --> END2([END])
    verify -- "문제있음 &amp; 첫 시도" --> conclude
```

노드별 책임(`src/application/nodes.py`):

- **frame** — 케이스와 토폴로지 조각으로 브리핑을 만들어 리드 LLM에게 초기
  가설(`hypotheses`)과 계획 태스크(`plan_tasks`)를 뽑는다. LLM 출력의 태스크는
  `_sanitize_new_task`로 수명주기 필드(`status`/`result_*`/`error`)를 강제
  초기화한다 — LLM이 `"status": "running"` 같은 값을 실어 select 게이트를
  우회하는 것을 막는다.
- **select** — **실행 가능 게이트**: `status=pending`이고
  `input_evidence_ids`가 전부 `state.evidence`에 실재하는 태스크만 후보다.
  `(priority, 계획 등재 순)`으로 정렬해 `engine.parallel_width`(기본 3)개만
  골라 `status=running`으로 굴린다.
- **execute** — `route_after_select`가 만든 `Send`로 병렬 fan-out된 개별
  태스크 하나를 서브에이전트(§4)에게 맡긴다. 성공하면 서브에이전트가 **실제로
  만든** evidence id들만(LLM이 응답에 적은 id를 신뢰하지 않는다) Store에서
  실측으로 다시 읽어 `EvidenceRef`로 승격한다.
- **integrate** — 라운드 카운터를 올리고, 가설 보드·태스크 현황·증거 목록·
  qa_log를 리드에게 보여 다음 결정(`continue`/`ask`/`conclude`)과 신규
  태스크를 받는다. `interaction_policy="autonomous"` + `default_and_log`
  정책이면 `ask`를 가로채 보수적 기본값으로 자동 답하고 qa_log에 남긴 뒤
  `continue`로 바꾼다. `round >= engine.max_rounds`에 도달하면 무조건
  `conclude`로 강제 전환한다(이때 버려진 질문도 qa_log에 남는다).
- **ask_human** — `interrupt({"question": ...})`로 그래프를 멈춘다. **interrupt는
  노드 최상단**에 있다 — 재개 시 노드가 처음부터 다시 실행되므로, interrupt
  앞에 부수효과가 있으면 재개마다 반복되기 때문이다.
- **conclude** — 증거가 하나도 없으면(전 태스크 error) LLM 없이 즉시
  `degraded`/`low`로 판정한다. 그 외에는 리드에게 최종 판정(인과 사슬 —
  `root_cause` + `contributing[]`)을 받는다.
- **verify** — **LLM 없는 순수 결정론 가드레일**. 판정이 인용한 모든
  evidence id가 `state.evidence`(리드가 실제로 본 것)에 실재하는지, 불완전
  증거(`complete=False`)를 인용했다면 caveat에 명시했는지를 검사한다. 문제가
  있고 아직 재작성을 안 했으면(`verify_attempts==0`) `conclude`로 돌려보내
  한 번 재작성시킨다. 재작성 후에도 문제가 있으면 확신도를 `low`로 강등하고
  문제 내용을 caveat에 적어 그대로 통과시킨다 — 무한 루프 대신 "낮은 확신으로
  끝낸다".

**통제 경계**: 그래프 구조·라운드 상한·병렬 폭·select 게이트·interrupt
위치·verify 규칙은 전부 코드(`nodes.py`)가 고정값으로 쥔다. LLM(리드)이
결정하는 것은 가설 내용, 어떤 태스크를 만들지, 라운드마다 continue/ask/conclude
중 무엇을 고를지, 최종 판정의 서술뿐이다.

## 4. 서브에이전트 3종

`src/application/subagents.py`의 `run_subagent`이 `PlanTask.role`에 따라
LangChain `create_agent` 기반의 유계(bounded) ReAct 루프를 돌린다. 예산은
`engine.subagent_budgets`(기본: `data_prober=8, code_tracer=6,
recompute_verifier=4`)의 `recursion_limit`으로 강제한다 — 서브에이전트 내부
루프는 라이브러리에 맡기되, 얼마나 오래 돌 수 있는지는 코드가 정한다.

- **data_prober** — 대상 시스템 포트(Redis/Mongo/Kafka/REST)로 증거를 수집한다.
- **code_tracer** — `CodeRepoReaderPort`로 대상 서비스의 실제 소스를 읽어
  변환 로직을 추적한다(`hash_exists`/`show`/`head`/`grep`, git subprocess 기반
  — 유일한 sync 포트).
- **recompute_verifier** — code_tracer가 읽은 로직을 따라 값을 재계산해
  관측치와 대조한다. "원천은 정상인데 파생값만 이상하다"는 핵심 난제를 다루는
  자리다.

## 5. 케이스 수명주기와 lease

```
open → investigating → awaiting_human → closed
         ↑___________________|
```

`CaseRecord`(`src/domain/cases.py`)가 상태를 쥔다. `OPEN_STATUSES = (open,
investigating, awaiting_human)`. 동시에 한 조사자만 케이스를 붙잡도록
`owner` + `lease_until`(`investigations.lease_ttl_s`, 기본 900초)로 임차한다
— `InvestigationWorker`는 조사 도중 `lease_ttl_s/3` 간격으로 keepalive를
갱신한다. `awaiting_human`으로 파킹된 케이스는 `case resume --answer`로
사람이 답을 넣거나, 데몬이 다음 순찰 사이클에 자동으로 재개를 시도한다
(`awaiting_human_timeout_h` 초과 시 타임아웃 종결).

케이스가 어떤 경로로 닫히든(데몬 자동 진행 / `chat` / `case resume`) **동일한
발행 배선**을 탄다 — 보고서 파일을 먼저 쓰고, `report_ready` 이벤트를 내고,
메일이 켜져 있으면 발송한다(`_build_publisher` in `src/__main__.py`,
`PatrolDaemon._publish_report`). 이 셋이 각자 다른 배선을 베끼면 언젠가 하나가
빠뜨린다는 게 실제로 있었던 문제였다 — 그래서 조립을 한 곳으로 모았다.

## 6. 증거와 판정 모델

- **Envelope/ProbeResult**(`src/domain/envelope.py`) — 대상 시스템 호출
  하나의 원시 결과. "요청한 것"과 "실제로 얻은 것"의 차이를 표현한다:
  `complete=False`면 상한(`max_rows` 등)에 잘렸다는 뜻이고 이때
  `truncated_reason`이 필수다. `effective_as_of`는 요청한 시점과 실제
  달성한 시점이 다를 때(예: Kafka 보존 밖이라 더 나중 데이터로 폴백) 이를
  숨기지 않고 드러낸다 — 오염된 증거가 T0 시점 증거로 위장하는 것을 막는
  장치다.
- **EvidenceRef**(`src/domain/case.py`) — State에 남는 증거 참조. 본문은
  케이스 Store에 있고(`EvidenceRecord`), State에는 id/출처/요약/as_of/완전성만
  남는다.
- **Verdict** — `verdict_type`(`logic_bug`/`data_loss`/`config_error`/
  `stale_data`/`external`/`inconclusive`/`degraded`) + 인과 사슬
  (`root_cause: CauseLink | None` + `contributing: list[CauseLink]`) +
  `confidence`(`high`/`medium`/`low`) + `recommendations`/`caveats`/`narrative`.
  `inconclusive`/`degraded`를 빼면 `root_cause`가 필수다(모델 검증자).
- **EngineEvent**(`src/domain/events.py`) — 엔진이 밖으로 내보내는 이벤트는
  정확히 5종(`case_status_changed`/`round_started`/`task_finished`/
  `question_raised`/`report_ready`)으로 고정돼 있다. 그래프 내부 노드명이나
  State 키는 이 봉투 밖으로 절대 나가지 않는다 — 구독자(CLI 출력, 향후 웹 UI)가
  엔진 내부 구현에 결합되지 않도록 하는 어휘 경계다.

## 7. 알려진 한계 (v1 인계 노트)

- `resume` 이후 구간(conclude/verify)은 이벤트를 내지 않는다 — 5종 어휘 밖으로
  의도된 설계. 웹 UI 착수 시 어휘 확장을 검토해야 한다.
- 그래프 밖 실패(`_fail` 경로)로 종결된 케이스는 §5(조사 경위) 절이 비어
  보고서에 나온다 — `case_file`이 만들어지지 않기 때문.
- `LedgerPort`가 점검 이력과 발송 추적 두 책임을 함께 진다 — Slack/webhook 등
  채널을 추가하게 되면 분리를 검토할 만하다.
- "코드 수정·데이터 정합성 보정" 같은 능동적 개입을 하는 **개발 시스템**은
  범위 밖이다. `recompute_verifier`의 재계산-대조 프리미티브만 재사용 가능하게
  설계돼 있다.
