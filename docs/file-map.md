# 파일 지도 — 무엇이 어디에 있고 데이터가 어떻게 흐르는가

이 문서는 두 가지 질문에 답한다. **"이 흐름은 어느 파일들을 지나는가"**(§1)와
**"이 파일은 뭐 하는 파일인가"**(§2)다.

먼저 읽을 것: [architecture.md](architecture.md)(왜 이렇게 배선돼 있는가),
[CLAUDE.md](../CLAUDE.md)(절대 규율), [for-implementers.md](for-implementers.md)(무엇을
어떤 순서로 만지는가).

> 여기 적힌 함수 이름은 전부 실재를 확인한 것이다. 그래도 **코드를 고치기 전에 grep으로
> 다시 확인하라** — 이 리포는 문서가 주장하는 배선을 믿었다가 두 번 데였다
> (`resume_once`가 아무 데서도 안 불렸고, `patrol run`이 시나리오를 데몬에 안 넘겼다).

---

## 1. 데이터 흐름

### 1.1 순찰 → 케이스 (자동 유입)

```
APScheduler 잡                      patrol/scheduler.py  build_scheduler·build_trigger
  → 점검 하나 실행                  patrol/runner.py     run_check
      → 파라미터 값 해석            patrol/resolvers.py  resolve_params
      → 프로브 호출                 patrol/probes.py     rest_get·rest_query·redis_get·mongo_recent·mongo_find·kafka_lag
          → 어댑터                  infrastructure/      stubs.py 또는 {redis,mongo,kafka,rest}_reader.py
      → 스냅샷 박제                 domain/store.py      CaseStorePort.put_evidence
      → 판정                        patrol/rules.py      judge_by_rule       (무료·결정론)
                                    patrol/llm_judge.py  judge_by_llm        (유료·예산 상한)
      → 실행 기록                   patrol/ledger.py     CheckLedgerPort.record_run
  → finding이면 게이트              patrol/gate.py       admit_finding
      → 지문 계산                   domain/patrol.py     fingerprint
      → 케이스 개설 또는 첨부       domain/cases.py      CaseRepositoryPort
      → T0 증거 참조 조립           patrol/gate.py       evidence_refs_for_case
  → 큐 투입                         application/worker.py CaseQueue.put
```

**해석기가 하나라도 값을 못 내면 프로브를 아예 안 부른다**(전부-또는-전무). 빈 필터는
끝점에 따라 거짓 경보(0/0/0)나 거짓 안심(전체 조회)이 되고, 후자는 조용해서 더 위험하다.

### 1.2 조사 (그래프)

```
워커                                application/worker.py InvestigationWorker.run_once
  → lease 획득(원자)                domain/cases.py       CaseRepositoryPort.claim
  → 이력 검색(tier 1~4)             application/history.py read_history
                                    (`find_history`는 실패 사유를 버리는 얇은 겉면 —
                                     워커는 브리핑이 "못 읽었다"를 말해야 해서 read를 쓴다)
  → 엔진 호출                       application/usecase.py investigate_case
      → 그래프                      application/graph.py   build_engine
        frame     브리핑 조립·가설·계획   application/nodes.py + briefing.py
        select    실행 가능 게이트·병렬 폭 application/nodes.py
        execute   서브에이전트 파견        application/subagents.py  run_subagent·make_tools
        integrate 가설 갱신·continue/ask/conclude
        ask_human interrupt(파킹)
        conclude  판정 작성                application/schemas.py    parse_structured
        verify    인용 검사·강등
      → State                       application/state.py   CaseState
      → 이벤트 매핑                 application/events.py  map_update_to_events
  → 종결                            application/close.py   close_case
  → 판정 스냅샷 박제                application/worker.py  _record_snapshot
                                    (`close_case`가 아니다 — 종결 **뒤에** 워커가 쓴다)
  → 보고서 데이터 유도              domain/report_model.py build_report_model
  → 렌더·발행                       presentation/report.py·report_html.py·mail.py
```

**브리핑에 실리는 것**: 토폴로지 슬라이스(증상 끝점에서 상류 유계 BFS), 적용 룰(슬라이스에
걸리는 점검만), 유사 이력(tier 사유와 함께), 배포 버전(슬라이스 서비스만). 자유 문서는
없다 — 코퍼스도 선별기도 만들지 않았다.

### 1.3 사람 접수

```
CLI `chat` / HTTP POST /cases       __main__.py·api/routes_cases.py
  → 사이트 축 해석                  application/scope.py   resolve_scope
  → 케이스 개설(**접수보다 먼저**)  application/open_case.py open_case
  → 접수 턴 반복                    application/intake.py  intake_turn
      → LLM이 대상 locator를 고르거나 질문을 낸다
      → 질문이면 파킹(`question_seq`가 오른다)
      → 답이 오면 증거로 **먼저** 박제하고 이어간다
  → intake_done=True               → 워커가 그때부터 집어 간다
```

케이스가 접수보다 먼저 열리는 이유: 접수 중 프로세스가 죽어도 사람이 쓴 증상이 남는다.
`intake_done`이 없으면 워커가 접수 중인 케이스를 대상 없이 조사한다.

### 1.4 답변 (두 표면이 **다른 자리에서** 갈린다)

CLI는 답을 **즉시 실행**한다. HTTP는 **조사 답변만** 싣고 끝낸다(`api`는 실행자가
아니다) — 접수 답변은 LLM 턴 하나라 대상 접근이 없어서 라우트가 동기로 돌리고 다음
질문을 바로 응답에 싣는다. 그래서 분기 함수 `answer_case`를 CLI는 직접 부르고, HTTP의
조사 답변은 워커를 통해 나중에 지난다.

```
CLI  `case resume --question-seq` / `chat`      __main__.py
  → 분기                            application/answer.py  answer_case
      → 접수 질문이면               application/intake.py  intake_turn(expect_seq=…)
      → 조사 질문이면               application/worker.py  resume_once
                                    application/usecase.py resume_case (그래프 재개)

HTTP POST /cases/{id}/intake-answers            api/routes_cases.py
  → 라우트가 **직접** 부른다        application/intake.py  intake_turn(expect_seq=…)

HTTP POST /cases/{id}/answers                   api/routes_cases.py
  → 라우트가 **직접** 부른다        application/submit.py  submit_answer
                                    domain/cases.py        attach_answer(expect_seq=…)
  → 여기서 끝난다. 나중에 워커가:   application/worker.py  consume → take_answer
                                    application/answer.py  answer_case (여기서 합류)
```

**`answer_case`의 호출부는 셋뿐이다**(`__main__.py` 둘, `worker.py` 하나) — HTTP 라우트는
안 부른다. 새 답변 표면을 만든다면 이 갈래 중 어느 쪽인지 먼저 정하라: 실행자면 CLI 쪽,
아니면 명령 채널에 싣고 끝내는 HTTP 쪽이다.

**질문 번호 대조의 자리가 종류마다 다르다**: 조사 질문은 `resume_once`가 lease를 잡은
뒤, 접수 질문은 `intake_turn`이 레코드를 읽은 직후(그 값이 곧 CAS 술어다).

### 1.5 Fleet 집계 (사이트를 가로지른다)

```
`scenario run` / 데몬 잡            __main__.py·patrol/daemon.py
  → 시나리오 로드(단독 검증)        config/loader.py       load_scenarios
  → scope 해석                      fleet/run.py           scenario_sites
  → 사이트 팬아웃(세마포어 상한)    fleet/run.py           run_scenario
      → 사이트당 표본               fleet/collect.py       collect_site  (프로브를 그대로 쓴다)
      → 값 추출·접기                fleet/reduce.py        extract·reduce_values
  → 롤업(정직성을 타입이 강제)      domain/rollup.py       MetricRollup
  → 렌더(커버리지가 숫자보다 먼저)  presentation/fleet_report.py
  → 실행 기록 저장                  domain/rollup.py       DigestStorePort.put
```

집계는 `Case`가 아니다 — 증상도 조사도 판정도 없다. `Case`에 넣으면 requeue가 집계
레코드마다 LLM 그래프를 돌린다.

### 1.6 학습 루프

```
종결 시                             application/worker.py  → VerdictSnapshotPort.put
  (retention이 90일에 Verdict를 지우므로 **여기서 안 남기면 영구 불가**)
사람이 실제 원인 되먹임             __main__.py `case label` / POST /cases/{id}/label
  → 유입구 한 곳                    application/labels.py  submit_label
게이트 확인                         application/labels.py  label_stats
게이트가 열리면 적중 계산           application/labels.py  calibration
  → `case label --stats`가 표를 찍는다
조사 소요 관측                      patrol/ledger.py       MetricsSinkPort.record_metric
  → `patrol status`가 요약한다      __main__.py            _print_investigation_metrics
```

### 1.7 보존 스윕

```
데몬 잡(1시간)                      patrol/daemon.py       sweep_job
  → 파킹 타임아웃                   application/close.py   sweep_timeouts
  → 보존 정리                       infrastructure/retention.py sweep_retention
      ① 종결 케이스의 증거·판정·케이스 파일 purge (레코드는 `purged_at`만 찍고 남는다)
      ② 이벤트·스냅샷·레저·메트릭 prune
  → 미발송 메일 재시도              presentation/mail.py   retry_pending
```

**스냅샷과 라벨은 retention보다 오래 산다** — 둘이 짝이라 한쪽만 지우면 대조가 불가능하다.

---

## 2. 파일별

의존은 항상 안쪽을 향한다. 아래 순서가 그 방향이다.

### `src/domain/` — 도메인 모델과 포트 (13)

바깥을 향한 의존이 없다. 여기 있는 ABC가 인프라 구현을 부른다.

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `case.py` | 123 | `Case`·`Verdict`·`EvidenceRef`·`Hypothesis`·`PlanTask`·`CauseLink`·`HistoryHit`. `evidence_summary`가 증거 본문을 프롬프트 한 줄로 만든다(개행 이스케이프가 방어다). **판정 필드를 늘릴 때.** |
| `cases.py` | 349 | `CaseRecord`(저장 형태)와 `CaseRepositoryPort`. lease(`claim`), 답 채널(`attach_answer`/`take_answer`/`restore_answer`), CAS(`update_if`), 이력 질의(`closed_by_*`). **수명주기·동시성을 만질 때.** |
| `store.py` | 178 | `CaseStorePort` — 증거 본문과 코드 지식 캐시. `get_evidence`의 `KeyError`는 계약이다(규율 1의 예외 셋 중 하나). |
| `ports.py` | 97 | 대상 시스템 읽기 전용 포트 5종. **`post`/`put`/`delete`를 여기 만들지 마라** — `tests/domain/test_ports.py`가 표면을 단정한다(규율 9). |
| `envelope.py` | 44 | `Envelope`·`ProbeResult` — "요청한 것"과 "얻은 것"의 차이. `complete=False`면 사유가 필수다. |
| `patrol.py` | 77 | `Finding`·`CheckOutcome`·`fingerprint`. 지문이 중복 억제와 이력 tier 1의 키다. |
| `events.py` | 81 | `EngineEvent`(어휘 6종)·`EventStorePort`. **어휘를 늘리기 전에 규율 7의 성질 시험을 통과시켜라.** |
| `concern.py` | 42 | `Concern` 축(system/operation) — 메일 수신자와 브리핑 방향이 이걸 따라간다. |
| `label.py` | 74 | `RootCauseLabel`·`LabelStorePort`. append-only, retention이 안 걷는다. |
| `snapshot.py` | 78 | `VerdictSnapshot`·포트. **retention보다 오래 산다** — 종결 시점에 안 남기면 영구 불가(일방향 문). |
| `report_model.py` | 271 | `build_report_model` — 보고서 데이터 유도(렌더와 2단 분리). 단계 체크리스트·Timeline·관측성. |
| `rollup.py` | 136 | Fleet 집계 도메인. validator 다섯이 "누락을 숨긴 숫자"를 **표현 불가능**하게 만든다. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/config/` — 스키마와 로더 (7)

`StrictModel`(`extra="forbid"`)이 전부의 조상이다. **새 모델은 `BaseModel`이 아니라
`StrictModel`을 상속한다**(규율 5).

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `schema_app.py` | 238 | 전역 config — 엔진 상한, 조사 정책, LLM 프로파일, 순찰 예산, retention, 보고서·메일, 접근 정책. `StrictModel`이 여기 있다. |
| `schema_site.py` | 309 | 사이트 config — 대상 접속, **REST 등재 항목**(`RestEntry`: 메서드가 여기 있는 것이 읽기 전용의 핵심), 점검(`CheckConfig`), 해석기(`ResolverSpec`). |
| `schema_scenario.py` | 86 | Fleet 시나리오 — `patrol.checks` 확장이 아닌 별도 파일(사이트마다 잡이 등록되면 같은 집계가 N번 돈다). |
| `loader.py` | 192 | 배치 규약과 로드 파이프라인. `load_scenarios`는 던지고 `collect_scenarios`는 읽힌 것과 문제를 **함께** 돌려준다(기동 검증용). |
| `merge.py` | 43 | 계층 deep-merge와 출처 추적. **null 마커(명시적 삭제)를 지나치는 지름길을 넣지 마라** — 실제로 그 버그가 있었다. |
| `envresolve.py` | 31 | `${ENV_KEY}` 치환. **`load_app_config`·`load_site_config` 양쪽 다 `env`를 받아야 한다** — 안 넘기면 Mongo 백엔드가 조용히 안 켜진다. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/knowledge/` — 사람이 유지하는 지식 (5)

git에 커밋되고, digest가 케이스 T0에 박제되고, 런타임에 넓어지지 않는다.

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `topology.py` | 85 | 서비스·derivation 그래프. 브리핑의 상류 슬라이스와 점검 target 해석의 근거. |
| `target_api.py` | 308 | pinned OpenAPI를 우리 등재 항목과 **대조**한다. **명세는 증거이고 config가 권한이다** — 런타임에 명세로 허용 범위를 넓히지 마라(fail-open). |
| `deployment.py` | 28 | 사이트×서비스 → 배포 커밋. 없으면 코드 증거가 "배포 버전 미검증"을 단다. |
| `digest.py` | 12 | content digest — as_of 박제용. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/infrastructure/` — 어댑터와 영속 (14)

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `factory.py` | 89 | `SiteConfig` → `AdapterSet`. **stub↔real 전환의 유일한 지점.** |
| `stubs.py` | 188 | 개발·테스트용 in-memory 어댑터. 봉투와 읽기 전용 규칙은 실구현과 **같은 함수**를 쓴다. |
| `redis_reader.py` | 49 | TYPE 분기 읽기, SCAN+상한. `KEYS *` 없음. |
| `mongo_reader.py` | 82 | find/count/aggregate. 규칙 검사가 소켓보다 먼저. **`aggregate()`는 명시적으로 await해야 한다.** |
| `kafka_inspector.py` | 156 | group 미참여 `assign()`, 커밋 없음. **fresh consumer는 `topics()`로 메타데이터를 먼저 채워야** 빈 결과가 안 나온다. |
| `rest_prober.py` | 88 | 등재 항목만. 메서드는 항목 선언이 정한다 — 호출자는 이름만 댄다. |
| `code_repo.py` | 51 | git subprocess, 읽기 명령만. 유일한 sync 포트. |
| `query_rules.py` | 241 | **읽기 전용을 메커니즘으로 만드는 순수 판정들.** `filter_problems`·`endpoint_allowed`·`entry_call_problems`를 어댑터·스텁·기동 검증이 **공유한다**. |
| `guards.py` | 24 | 타임아웃·동시성 세마포어 —(행 상한은 어댑터별로 `factory.py`가 주입한다) — "아픈 시스템을 더 아프게 하지 않는다". |
| `mongo_store.py` | 702 | Store·Repo·Ledger·EventStore·Digest·Label·Snapshot 7종의 Mongo 구현. **시각은 ISO 문자열이라 DB 정렬 전에 `_fixed_width_iso`로 폭을 맞춰야 한다**(정각이 최신으로 뒤집힌다). |
| `checkpointer.py` | 71 | 체크포인터와 `Persistence` 묶음 조립. `ensure_indexes` 호출부. |
| `retention.py` | 175 | 보존 스윕 2단. **범위 비교는 DB에 `$lt`를 안 맡기고 파싱해서 비교한다**(같은 폭 함정). |
| `llm.py` | 24 | `build_chat_model`(실)·`ScriptedLLM`(테스트). 노드가 요구하는 표면은 `ainvoke`뿐. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/patrol/` — 순찰 (11)

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `daemon.py` | 588 | 스케줄러·게이트·큐·워커·스윕·집계를 한 프로세스로 조립한다. `assemble_sites`가 사이트별 런타임을 만든다(CLI 네 명령이 공유). |
| `scheduler.py` | 94 | 점검 정의 → APScheduler 잡. `interval`/`cron` 트리거. |
| `runner.py` | 169 | 점검 하나의 전 파이프라인(해석→프로브→박제→판정→레저). `snapshot_text`가 판정 프롬프트용 표현을 만든다. |
| `probes.py` | 282 | 프로브 레지스트리 — 점검을 어댑터 호출로 잇는다. `resolve_probe`가 target 접두사로 기본 프로브를 고른다. |
| `resolvers.py` | 165 | 파라미터 **값** 해석기(rest/mongo/redis/clock/unfiltered). **전부-또는-전무.** |
| `rules.py` | 377 | rule 판정 6종. `KnownRuleError`는 **설정** 오류 전용 — 데이터 이상은 finding이다(규율 1의 예외 셋 중 하나). |
| `llm_judge.py` | 116 | LLM 판정과 시간당 예산. `MAX_SNAPSHOT_CHARS`가 여기 산다(runner가 import). |
| `gate.py` | 118 | Finding → 케이스 개설/첨부/억제. 지문 중복과 열린 케이스를 본다. |
| `ledger.py` | 186 | `CheckLedgerPort`·`SendLedgerPort`·`MetricsSinkPort` 3분할과 합집합. **메트릭만 실패를 삼켜도 되는 레저다.** |
| `selfcheck.py` | 47 | 순찰이 자기 연속 error를 감시한다. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/application/` — 유스케이스와 그래프 (20)

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `graph.py` | 42 | 그래프 배선. **구조는 코드가 쥔다**(규율 6). |
| `nodes.py` | 444 | 노드 7종. `_sanitize_new_task`가 LLM이 만든 객체의 수명주기 필드를 강제 초기화한다(규율 4). |
| `state.py` | 45 | `CaseState`와 reducer. |
| `deps.py` | 25 | `EngineDeps` — 노드가 클로저로 받는 묶음. 브리핑 재료는 **텍스트가 아니라 구조**로 싣는다. |
| `briefing.py` | 182 | 상류 슬라이스·룰·배포 렌더와 `build_briefing`. **`one_line`이 여기 있다** — `[...]` 섹션 어휘를 쓰는 모든 프롬프트가 공유한다. |
| `subagents.py` | 245 | 서브에이전트 3종과 도구. 도구 반환은 모델 컨텍스트에 그대로 들어간다. |
| `schemas.py` | 75 | LLM 구조화 출력 스키마와 파서. |
| `usecase.py` | 184 | `investigate_case`·`resume_case` — 그래프 호출의 유일한 입구. |
| `worker.py` | 878 | `CaseQueue`(중복 억제 `_held` 포함)와 `InvestigationWorker`. lease·keepalive·답 소비·실패 정리. **여기서 실패하면 케이스가 `investigating`으로 영원히 남는다.** |
| `close.py` | 65 | 종결과 파킹 타임아웃 스윕. |
| `open_case.py` | 56 | 사람 경로의 케이스 개설 — **접수보다 먼저.** |
| `intake.py` | 298 | 접수 턴. `_SAVED_FIELDS`와 `_expect`가 **같이 움직인다**(전자는 무엇을 쓰는가, 후자는 무엇이 안 바뀌었어야 하는가). |
| `scope.py` | 107 | 사이트 축 해석 — 후보는 코드가 registry에서, LLM은 그 안에서만 고른다. |
| `answer.py` | 71 | 접수 답변 vs 조사 답변을 가르는 **한 곳**. 호출부는 셋(CLI 둘, 워커 하나) — **HTTP 라우트는 안 부른다**(§1.4). |
| `submit.py` | 91 | 케이스 제출(`submit_case` — CLI와 API 공유)과 답 싣기(`submit_answer` — **API 전용**, 명령 채널에 싣고 끝난다). |
| `history.py` | 171 | tier 1~4 결정론 검색과 렌더. **tier마다 저장소를 따로 질의한다**(안 그러면 사다리가 뒤집힌다). 렌더된 줄에서 **과거 evidence id를 지운다**(규율 3). |
| `labels.py` | 170 | 라벨 유입구·게이트·캘리브레이션. 분모 규칙은 코드가 쥔다. |
| `events.py` | 138 | State 변화 → `EngineEvent` 매핑과 이벤트 로그 읽기(`collect_events`는 raise하지 않는다). |
| `lifecycle.py` | 100 | 상태 전이 순수 함수와 lease 헬퍼. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/fleet/` — 집계 (4)

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `run.py` | 214 | 시나리오 팬아웃과 `FleetReport` 조립. `scenario_digest`가 추세 비교 가능 여부를 정한다. |
| `collect.py` | 72 | 사이트 하나의 표본 — **기존 프로브를 그대로 쓴다**(집계 전용 경로를 만들지 않았다). |
| `reduce.py` | 79 | `extract`·`reduce_values` 순수 함수. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/presentation/` — 렌더와 발송 (5)

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `report.py` | 357 | 마크다운 보고서. `write_report`가 파일을 쓴다. |
| `report_html.py` | 184 | HTML 보고서(기본 포맷). |
| `fleet_report.py` | 164 | 집계 리포트 — **커버리지가 숫자보다 앞에** 온다. |
| `mail.py` | 195 | 2상 멱등 발송(`record_send` → 전송 → 표시)과 재시도. |
| `__init__.py` | — | 빈 패키지 표식. |

### `src/api/` — HTTP 표면 (7)

**이 패키지는 어댑터·워커·그래프를 import하지 않는다.** `tests/api/test_boundary.py`가
import 그래프로 지킨다 — 끌어오면 `api` 풀 전체가 실행자가 되고 lease가 그 사이를
중재해야 한다.

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `app.py` | 66 | FastAPI 앱 팩토리와 접근 판정 헬퍼(`visible_record`·`hidden`). 전역 상태 없음. |
| `assembly.py` | 79 | `api` 런타임 조립 — 어댑터 없이 config·토폴로지·저장소만. |
| `auth.py` | 39 | 토큰 → 주체. 미인가는 404로 숨긴다(존재 오라클 차단). |
| `routes_cases.py` | 148 | 쓰기 — 접수·답·라벨. 두 409의 **본문 모양이 다르다**(주석 참고). |
| `routes_reads.py` | 190 | 읽기 — 목록·상세·이벤트(SSE)·보고서·집계·점검. |
| `models.py` | 63 | 응답 모델(`CaseDetail` 등) — dict로 돌려주지 않는다. |
| `__init__.py` | 11 | **빈 파일이 아니다** — "api는 실행자가 아니다" 규율을 담은 docstring. |

### `src/` 최상위 (3)

| 파일 | 줄 | 역할 · 언제 여는가 |
|---|---|---|
| `__main__.py` | 989 | CLI 전체. **규율 2의 주 경계**(`datetime.now()`) — 기동 검증(`boot.py`)과 `usecase.py`의 이벤트 시각 폴백에 문서화된 예외가 있다(경과는 시계와 다른 양이라 `Ticker`가 따로 잰다). `_build_publisher`가 `chat`·`case resume` 두 경로의 발행 배선을 조립한다 — **데몬은 자기 `_publish_report`를 워커의 `on_closed`로 배선한다**(같은 계약, 다른 조립). |
| `boot.py` | 503 | 기동 검증. **문제를 전부 모아서** `list[BootError]`로 돌려준다. 항목 번호는 `docs/config-reference.md`가 단일 소스다 — **여기 개수를 적지 마라**(두 곳이 갈라진다). |
| `__init__.py` | — | 빈 패키지 표식. |

---

## 3. 이 지도를 최신으로 유지하려면

```bash
# 파일명과 **줄 수**를 절 헤더의 패키지와 함께 대조한다. 줄 수를 빼면 드리프트가 조용히
# 남고(실제로 그렇게 낡았다), 패키지를 빼면 `events.py`처럼 이름이 겹치는 파일이 충돌한다.
.venv/bin/python - <<'EOF'
import pathlib, re
doc = pathlib.Path("docs/file-map.md").read_text()
listed, pkg = {}, None
for line in doc.splitlines():
    header = re.match(r'### `src/([a-z]*)/?`', line)
    if header:
        pkg = header.group(1)
    row = re.match(r'\| `([a-z_]+\.py)` \| (\d+) \|', line)
    if row and pkg is not None:
        listed[(pkg, row.group(1))] = int(row.group(2))
bad = []
for path in sorted(pathlib.Path("src").rglob("*.py")):
    if path.name == "__init__.py":
        continue
    key = ("" if path.parent.name == "src" else path.parent.name, path.name)
    real = len(path.read_text().splitlines())
    if key not in listed:
        bad.append(f"표에 없음: {path}")
    elif listed[key] != real:
        bad.append(f"줄 수 어긋남: {path} 표={listed[key]} 실제={real}")
print("\n".join(bad) or "동기화됨")
EOF
```

파일을 추가하면 해당 절의 표에 한 줄을 더하고 패키지 헤더의 개수를 고쳐라.
