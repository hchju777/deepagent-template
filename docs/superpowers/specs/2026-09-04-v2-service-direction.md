# v2 방향 — 운영 모니터링 서비스

**상태**: 초안(승인 대기)
**선행 문서**: [2026-09-02-ops-monitoring-design.md](2026-09-02-ops-monitoring-design.md) (v1 스펙, 단일 진실 소스)
**이 문서의 역할**: v2의 분해를 확정하고, 하위 계획들이 서로 다른 가정으로 갈라지지 않도록 **비협상 항목**과 **최종 결과의 형태**를 먼저 못 박는다. 개별 구현 방법은 하위 계획 문서가 정한다.

v1은 단일 프로세스 CLI + 순찰 데몬으로 "증상 하나 → 조사 → 판정 → 마크다운 보고서"를 닫았다. v2의 목표는 **일회성 자동화가 아니라 운영 모니터링 서비스 자체**다.

---

## 1. 요구와 재해석

사람 파트너가 제시한 세 타깃:

1. **Alarm Trend를 전 법인/사업부 집계 → HTML 리포트 발송**
2. **운영 시스템 이상 탐지 → 조사 → 리포트** — `/summary/badge`·`/summary/prod`·`/summary/prod_status`(POST)가 `0/0/0`이거나 "생산중이어야 하는데 NO PLAN" 같은 상태를 잡는다
3. **웹 질의 → RCA 후보 + 증거 + 권고** — "우리 사업부/법인에 데이터는 있는데 왜 운영 시스템에는 안 나오지?"

### 세 개는 같은 모양이 아니다

이 재해석이 분해의 근거다.

| 타깃 | 지금 구조와의 관계 | 필요한 것 |
|---|---|---|
| (2) | **지금 흐름 그대로다** — 점검→Finding→게이트→케이스→조사→판정→검증→발행 | 프로브 확장(POST·인증·파라미터)과 rule 확장. 새 아키텍처 아님 |
| (3) | **지금 `chat` + intake 흐름이다** | 접수 경로(CLI→API)와 판정 형태(단일 원인→후보 다수) 확장 |
| (1) | **완전히 다른 모양이다** | 증상도, 조사도, 근본 원인 판정도 없다. 별도 1급 개념 |

**(1)을 `Case`에 밀어넣으면 안 된다.** 추상적인 부적합이 아니라 구체적으로 깨진다:

- `PatrolDaemon.build()`가 부르는 `CaseQueue.requeue_open`이 `repo.list_open()` **전부**를 큐에 넣고 `InvestigationWorker`가 `investigate_case`로 넘긴다 → **집계 레코드마다 LLM 조사 그래프가 돈다.** 기동마다 재발한다.
- `Verdict._conclusive_needs_root_cause`가 `root_cause` 없는 판정을 `inconclusive`/`degraded`만 허용한다 → 모든 집계가 영구히 "조사 실패" 라벨을 달고, 구조화 필드로 채점하는 벤치까지 오염된다.
- `CaseRecord`의 `symptom`/`t0`/`fingerprint`가 필수다 → 집계에 정직한 값이 없어 "증상: 없음"류를 발명해야 한다. 이 리포가 금지하는 조용한 거짓말 그 자체다.
- `fingerprint(gbm, fct, check, target)`가 사이트를 요구한다 → 사이트 없는 집계는 값을 조작해야 하고, 그러면 같은 튜플을 쓰는 실제 finding이 `find_open_by_fingerprint`로 집계 레코드에 증거로 첨부된다.

그리고 지금 **모든 것이 `(gbm, fct)` 하나에 매여 있다** — `SiteRuntime`, `deps_for_site`, 레저 키, 잡 id, 보고서, 메일. (1)은 사이트를 가로지르는 최초 기능이고, 그것이 v2의 가장 큰 구조적 확장이다.

---

## 2. 비협상 항목

어겨도 **조용히** 틀리기 때문에 하위 계획이 개별적으로 재협상할 수 없는 것들이다. v1의 절대 규율(무raise·시계 주입·StrictModel·LLM 인용 불신·수명주기는 코드가 쥔다·이벤트 봉투·기동 거부)은 그대로 유효하고, 아래는 v2가 새로 여는 표면에 대한 추가분이다.

### N1. 읽기 전용 강제 — 문서는 증거, config는 권한

v1의 완전 읽기 전용은 문서상 약속이 아니라 **메커니즘**이었다: `RestProberPort`에 `get`밖에 없어 쓰기가 물리적으로 불가능했다. POST를 열면 그 성질이 사라진다. `POST /summary/prod`는 읽기지만 `POST /plan/update`는 쓰기이고, **HTTP 메서드는 이 둘을 구별하지 못한다.**

대상 시스템의 `/openapi.json`은 항상 받을 수 있다(운영 팀이 Swagger로 상시 호출한다). 그러나:

> **OpenAPI는 전사(transcription) 도구이고 권한(authorization) 근거가 아니다.**
>
> OpenAPI는 대상이 **스스로 쓴 자기 서술**이고, 자기 서술은 자신에 대한 제약이 될 수 없다. `operationId`·`summary`·`tags`·경로 규약은 우리가 소유하지 않고 변경 통보도 받지 못하는 남의 산문이다. 신뢰 근거를 "내 config"에서 "저쪽 함수명"으로 옮기는 것은 엄격성의 **하락**이다. 대상 팀이 함수명을 `get_prod_summary`로 둔 채 안에 감사 로그 INSERT를 추가하면 우리는 조용히 쓰기를 유발하면서 아무것도 모른다.
>
> `description`을 LLM에게 판정시키는 것은 명시적으로 금지한다 — 규율 6("재현·상한·감사 가능해야 하는 것은 코드가 쥔다")에 정면으로 위배된다. 읽기 전용 강제는 그 정의에 완벽히 들어맞는다.

그래서 강제는 층으로 쌓는다. **읽기 전용 스코프 토큰은 이 대상 시스템에 존재하지 않으므로**(확인됨) 최상단 층이 없다 — 남은 층이 전 하중을 진다.

| 층 | 내용 |
|---|---|
| **소유권** | `(method, path, 닫힌 body 스키마, 허용 쿼리 키)` 항목을 config에 손으로 등재. 목록에 없으면 문이 안 열린다 |
| **타입** | 포트에 제네릭 `post()`를 **추가하지 않는다.** 좁은 `query(entry, body)`만 열고, **메서드는 호출자 인자가 아니라 등재 항목에서 어댑터가 고른다.** `PUT`/`PATCH`/`DELETE`는 메서드가 존재하지 않는다(분류가 아니라 부재) |
| **기동** | 등재 항목 ↔ pinned 명세 대조. 불일치를 전부 모아 보고 후 거부 |
| **런타임** | 소켓 전 거부. 파싱된 URL로 판정하고, body는 닫힌 스키마 검증을 통과해야 한다 |
| **감사** | 증거 `source`에 body digest를 실어 재현 가능하게 |

`read_only: true` 플래그는 **없앤다.** 열려 있는 문에 붙은 boolean이 아니라 **목록에 있는 것만 문이 열리는** 구조이므로 플래그가 중복이 된다. 사람이 손으로 쓰는 것은 플래그가 아니라 항목 자체다. 목록이 짧아서(점검이 실제 참조하는 POST 몇 개) 실제로 읽고 생각하게 된다 — 40개에 `read_only`를 적으라고 하면 전부 `true`로 적는 고무도장이 되고, 아무도 생각하지 않는 체크박스의 안전 가치는 0이다.

이것이 v1이 Mongo에 이미 한 것과 **같은 이동**이다. Mongo에도 쓰기 API가 있지만 v1은 메서드를 금지해서 막은 게 아니라 스테이지 allowlist(`aggregate_problems`) + 롤 검사로 막았다. REST가 Mongo처럼 되는 것이다.

**포트 표면을 테스트가 지킨다.** `RestProberPort`에 `post`/`put`/`patch`/`delete`가 없음을 단정하는 테스트를 둔다 — 6개월 뒤 누군가 편의상 `post()`를 추가하는 순간 v1의 성질이 조용히 사라지고, CLAUDE.md 산문은 읽지 않으면 무력하다. 테스트를 메커니즘으로 쓴다.

### N2. 파라미터 값은 선언이 아니라 해석

`requestBody` 스키마는 **어떤 칼럼을 줘야 하는지**만 말한다. `part_code`에 무엇을 넣을지는 명세에 없고, 사업부/법인마다 다르고, 매일 바뀐다. **값을 config에 적는 어떤 설계도 즉시 썩는다.**

시나리오가 선언하는 것은 값이 아니라 **값이 어디서 오는지**다. 값은 점검 실행 시점에 살아 있는 소스에서 해석(resolve)된다.

```json
"body": {
  "part_code":  { "from": "rest",  "entry": "list_parts", "field": "part_code",
                  "cardinality": "all" },
  "line_code":  { "from": "mongo", "collection": "lines", "field": "line_code",
                  "filter": { "active": true } },
  "graph_type": { "from": "openapi_enum" },
  "date":       { "from": "clock", "expr": "today" }
}
```

값 소스를 강한 것부터: **형제 조회 endpoint**(대상 자신이 유효하다고 인정한 목록 — OpenAPI 인벤토리가 그 endpoint의 존재를 알려준다) > **Mongo/Redis 직접 조회**(데이터에 실재하는 값) > **OpenAPI enum**(`graph_type`처럼 닫힌 어휘) > **코드 grep**(배포 커밋과 어긋날 수 있어 `as_of`의 코드 축이 필요). **config에 값 나열은 기각.**

즉 OpenAPI는 "어떤 값이 합법인지"는 알려주지만 **"어떤 값을 원하는지"는 영원히 알려주지 않는다.** 후자는 사람 지식이고, 그것을 config에 박제하는 대신 **어디서 읽어올지**를 박제한다.

### N3. body 조립은 전부-또는-전무

해석기가 **하나라도** 값을 못 내면 **호출 자체를 하지 않는다.** `CheckOutcome(status="error")`이고, rule 판정기는 호출되지 않는다. 스키마상 필수인지 아닌지는 무관하다.

이유가 두 겹이다. 빈 필터로 나간 요청은 endpoint에 따라 갈린다:

| endpoint 동작 | 결과 | 성격 |
|---|---|---|
| 빈 필터 = 빈 결과 | `0/0/0` | **거짓 경보.** 시끄럽다 — 누군가 조사하다 원인을 찾는다 |
| 빈 필터 = 전체 조회 | 숫자가 정상으로 보인다 | **거짓 안심. 조용해서 아무도 조사하지 않는다** |

두 번째가 더 위험하고, **OpenAPI로는 어느 쪽인지 알 수 없다.** 그러니 endpoint별로 알아내려 하는 대신 구별이 필요 없는 규율을 세운다.

"불러놓고 판정만 안 한다"로는 부족하다. 전체 조회로 돌아온 응답이 **증거로 Store에 박제되면** 나중에 서브에이전트가 그것을 "정상 확인됨"의 근거로 인용한다. 규율 3이 "LLM이 말한 것과 실제로 일어난 일"을 분리하는데, 여기서 오염되는 것은 그 아래층이다 — **실제로 일어난 일 자체가 다른 질문에 대한 답**이다. 잘못된 범위의 응답은 증거가 아니라 오염원이므로 애초에 만들지 않는다.

**의도한 전체 조회는 명시한다.** `{"from": "unfiltered"}`. 해석기 실패로 우연히 전체 조회에 도달하는 경로와 처음부터 전체를 보려는 의도를 코드가 구별할 수 있어야 한다. 요청 조립판 "조용한 생략 금지"다.

**카디널리티도 정직성 문제다.** `part_code`가 5,000개면 전량을 실을지, N개를 샘플할지, "어제 생산 이력이 있는 것만"으로 좁힐지를 시나리오가 명시하고, **보고서가 무엇을 봤는지 적는다.** "5,000개 중 50개만 확인"을 안 적으면 조용한 생략이다.

### N4. 증거는 "무엇을 물었는지"를 실어야 한다

`0/0/0`이라는 응답만 보관하면 보고서를 읽는 사람이 그것이 "P001이 멈췄다"인지 "질문을 잘못했다"인지 **구별할 수 없다.** POST 프로브의 증거 봉투에는 **실제로 보낸 body**가 들어간다.

GET 프로브는 URL이 곧 질문이라 이 문제가 없었다. POST를 열면서 처음 생기는 요구다. 증거 `source`는 `rest:POST:/summary/prod#<body digest 8자>` 형태가 되고, 요청 body 원본이 증거 본문에 동봉된다.

### N5. ReportModel → 렌더러 2단

`render_report`가 지금 데이터 유도와 마크다운 조립을 한 함수에서 한다. HTML을 추가하면서 이것을 쪼갠다:

```
(record, verdict, evidence, case_file, snapshot) → ReportModel → render_md | render_html
```

벤치가 보고서 텍스트를 채점하지 않는 이유("템플릿 고칠 때마다 깨지는 벤치는 즉시 썩는다")와 같은 논리다. 단계 체크리스트·이모지·Timeline·증거 요청 스펙이 전부 같은 데이터를 원하므로 **한 번만 계산하고 렌더러만 갈린다.** "동적 지원"의 실제 의미가 이것이다 — 템플릿을 늘리는 게 아니라 데이터를 늘린다.

### N6. `Clock`과 `Ticker`는 다른 양이다

```
Clock  = Callable[[], datetime]   # 언제 일어났나 — 재현 가능해야 한다
Ticker = Callable[[], float]      # 얼마나 걸렸나 — 단조, 재현 불가능이 정상
```

`datetime.now()` 금지의 목적은 *기록되는 시점*의 재현·감사다. 경과 시간은 성질이 다르다. 고정 시계에서 duration이 0이 나오는 것은 둘을 한 포트로 섞었기 때문이고, `Ticker`를 CLI 경계에서 별도 주입하면(프로덕션 `time.perf_counter`) **결정론이면서 0이 아닌** 값이 나온다.

"테스트에서 기존 `clock`을 전진시키자"는 안은 기각한다. `LlmBudget`의 슬라이딩 창·`is_timed_out`·lease TTL 비교·`purge_*_before` 경계·벤치의 `observed_at`이 전부 고정 `T`를 전제하는 262개 테스트를 흔들고, 게다가 프로덕션에서 `datetime.now()` 뺄셈은 **NTP가 뒤로 점프하면 음수가 나오는 버그**다.

"직접 `now()` 금지"가 "직접 `perf_counter()` 금지"로 자연 확장된다.

### N7. 미측정과 0은 다르다

토큰 사용량은 `usage_metadata`가 `None`일 수 있다 — 스텁 LLM이거나, 게이트웨이가 `usage`를 스트립한 경우다. 후자는 **알아야 하는 고장**이다. `None`을 0으로 접어 합산한 비용 숫자는 거짓말이므로, 모든 합계는 **"미측정 N건"을 동반한다.** 토크나이저 기반 추정 폴백은 금지 — 청구서와 불일치하는 제2의 진실을 만들고 아무도 믿지 않는다.

같은 원칙이 집계에 적용된다(§4.2의 `MetricRollup` validator).

---

## 3. 최종 아키텍처

### 3.1 프로세스와 경계

```
                         ┌─────────────────────────────────────┐
   사람 (웹 브라우저)  ──▶│  api  (N 인스턴스, 무상태)          │
                         │  접수 · 조회 · SSE · 답변 · 라벨     │
                         └──────────────┬──────────────────────┘
                                        │ 명령만 쓴다(실행하지 않는다)
                                        ▼
   ┌──────────────────────────────────────────────────────────────┐
   │            Mongo — 유일한 프로세스 간 진실 소스               │
   │  cases(큐+수명주기, 원자 claim) · case_events(seq)            │
   │  evidence · verdicts · case_files · verdict_snapshots         │
   │  root_cause_labels · digest_runs                              │
   │  ledger_runs · sends · run_metrics · checkpoints              │
   └───────┬──────────────────────────────────────────┬───────────┘
           │ claim_next / 이벤트 append               │ 읽기
           ▼                                          ▼
   ┌───────────────────────┐              ┌──────────────────────────┐
   │ worker (N 인스턴스)   │              │ patrol (1 인스턴스)      │
   │ 케이스 조사 그래프    │              │ 스케줄러 · 점검 · 게이트 │
   │ lease · keepalive     │              │ Fleet 집계 · 스윕 · 자기 │
   │ wall-clock 상한       │              │ 감시 · 발행              │
   └───────────┬───────────┘              └────────────┬─────────────┘
               │              읽기 전용 어댑터          │
               └──────────────────┬────────────────────┘
                                  ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  대상 시스템 (완전 읽기 전용)                                 │
   │  Redis · Mongo · Kafka · REST(GET + 등재된 POST) · 코드 repo  │
   └──────────────────────────────────────────────────────────────┘

   knowledge/ (git 커밋, digest 박제)
     topology/ · deployment/ · target_api/(pinned OpenAPI 부분집합) · history/
```

핵심 성질:

- **`api`는 실행자가 아니라 클라이언트다.** 스펙 §5.2-F2가 이미 규정한 것이고, v1이 명령 채널이 없어 인라인 실행자로 구현한 것을 되돌린다. `api`는 케이스를 쓰고 이벤트를 읽는다. 조사는 `worker`만 한다.
- **진실은 Mongo에 있고 프로세스 메모리에 없다.** 그래서 `api`가 무상태이고, 어느 인스턴스든 어느 케이스의 이벤트를 서빙할 수 있다.
- **`patrol`은 단일 인스턴스다.** 스케줄러 잡의 `max_instances=1`·`coalesce`·misfire 리스너가 거기 있다. 수평 확장은 `worker`만.
- **대상 시스템 접근은 어댑터 층에서만.** `api`는 대상 시스템에 직접 붙지 않는다.

### 3.2 포트 (목표 상태)

| 포트 | 변화 |
|---|---|
| `RedisReaderPort` / `MongoReaderPort` / `KafkaInspectorPort` / `CodeRepoReaderPort` | 무변화 |
| `RestProberPort` | `get(endpoint)` 유지 + **`query(entry, body)` 신설**. `post`/`put`/`patch`/`delete` 없음(테스트가 단정) |
| `CaseRepositoryPort` | **`claim_next(owner, now, ttl)` 신설**(원자 claim), **`find_closed(...)` 신설**(이력 검색·유계·정렬) |
| `CaseStorePort` | 무변화 |
| `EventStorePort` | **신설** — `append(event) → seq`, `since(case_id, seq)` |
| `LedgerPort` | **3분할** → `CheckLedgerPort`(점검 이력·하트비트) / `SendLedgerPort`(발송 2상) / `MetricsSinkPort`(신설) |
| `DigestStorePort` | **신설** — Fleet 집계 실행 기록 |
| `MailSenderPort` | HTML 대체 본문 지원(`add_alternative`) |
| `VerdictSnapshotPort` · `LabelStorePort` | **신설** — retention보다 오래 사는 판정 스냅샷과 사람 라벨 |

`LedgerPort` 3분할이 정당한 이유: **`MongoLedger`는 이미 컬렉션이 갈라져 있고**(`ledger_runs`/`sends`/`ledger_meta`, 인덱스도 각각) **retention knob도 이미 분리돼 있다**(`ledger_d`/`sends_d`). 저장은 갈라졌고 인터페이스만 융착돼 있어서 ABC 분할이 거의 공짜다.

**분할 순서를 지켜야 한다**: ①ABC 분할(기계적, 동작 무변화) → ②메트릭 sink 추가 → ③합성 이벤트 이주. 워커와 게이트가 지금 `check=f"worker:{case_id}"`·`f"gate:{name}"`로 점검 레저를 범용 이벤트 로그로 쓰고 있고 **벤치가 그 문자열을 단정한다.** 이주를 먼저 하면 깨진다.

### 3.3 이벤트 어휘 — 5종 → 6종 + `seq`

봉투 설계는 옳고 유지한다. 구멍 셋 중 둘은 어휘 문제가 아니었다.

| 조치 | 내용 |
|---|---|
| **`verdict_formed` 1종 추가** | `conclude`/`verify`가 낸다. data: `verdict_type`·`confidence`·`verify_attempts`·`rewritten`. resume 이후의 침묵이 이걸로 닫힌다 |
| **누락 호출부 보충** | `case_status_changed(status="open")`을 게이트·접수 지점에서 낸다. **어휘 증설 0** |
| **점검 결과는 어휘에 넣지 않는다** | `EngineEvent.case_id`가 필수인데 순찰의 `ok` 결과에는 케이스가 없다. `case_id`를 optional로 완화하면 모든 구독자에게 봉투가 약해진다. 점검 이력은 레저 read API로 노출 |
| **`EngineEvent.seq: int` 추가** | 지금은 `at`뿐이고, 결정론 테스트에서 시계가 고정값이라 같은 superstep의 이벤트가 동일 타임스탬프를 갖는다 — **`at`으로는 전순서가 안 나온다.** Timeline과 재접속 재생이 둘 다 저장된 `seq`를 요구한다. 구독이 시작되기 **전에** 넣어야 하는 wire 변경이다 |

규율 7의 시험을 개수에서 **성질**로 바꾼다:

> **"이 이름이 그래프를 다시 배선해도 그대로 유효한가?"**

`verdict_formed`는 도메인 사실(Verdict가 생겼다)이라 conclude/verify를 합치든 쪼개든 유효하다. `node_entered`·`state_patch`·`select_gate_evaluated`·`stream_mode` 패스스루는 무효다. 명시적 기각: `round_finished`(`round_started` + 다음 경계로 유도 가능), `evidence_added`(`task_finished.evidence_ids`에 이미 있다), `hypothesis_updated`(케이스 파일에 있다).

### 3.4 concern 축 — 시스템 이상 vs 운영 이상

두 관심사가 지금 한 통에 섞여 있다. `Literal["system", "operation"]`을 `CheckConfig`·`ScenarioConfig`·`CaseRecord`에 실어 **수신자·심각도·브리핑·권고**를 갈라 라우팅한다.

| concern | 무엇 | 예 |
|---|---|---|
| `system` | 파이프라인이 고장 | Kafka lag, Redis TTL 만료, Mongo 미갱신, API 5xx, 스키마 드리프트 |
| `operation` | 데이터는 흐르는데 현장 상태가 이상 | `0/0/0`, 생산중이어야 하는데 NO PLAN |

필드 하나가 나머지 전부의 라우팅 기반이 된다. **지금 넣어야 한다** — 나중에 소급하면 모든 레코드를 마이그레이션해야 한다.

### 3.5 인증 — 필드 1개 + 술어 1개

읽기 전용이라고 폭발 반경이 작지 않다. 인증 없는 `POST /cases {gbm:"mx", fct:"suwon", ...}`은 실질적으로 **"수원 법인의 Redis/Mongo/Kafka와 소스 저장소에 읽기 권한을 가진 LLM 에이전트를 돌리고 결과를 메일로 보내라"**는 요청이다. 그리고 `awaiting_human`이 프롬프트 주입구가 된다 — `POST /cases/{id}/answers`를 부를 수 있는 누구든 넣은 텍스트가 `_format_qa_log`를 거쳐 리드 LLM 프롬프트에 직행하고 evidence로 박제된다.

최소형: 호출자 인증 + **`CaseRecord.requested_by`** + **`(subject, gbm, fct)` 허용 판정을 접수 경계 한 곳에서만** + 모든 읽기 엔드포인트를 같은 술어로 필터. 필드 1개, 포트 1개, 검사 1곳이다. **테넌트별 DB·저장소 격리는 기각**(§6).

지금 하는 이유: `(gbm, fct)` 축이 이미 `CaseRecord`·fingerprint·레저 키·사이트 런타임 맵 전체에 꿰여 있어 그 위에 주체를 얹는 건 싸다. 이벤트 스토어·read API·UI를 주체 없이 다 만든 뒤 소급하면 그 셋을 전부 다시 만져야 한다.

### 3.6 비동기 장기 조사 vs HTTP — 제출→조회

조사는 `max_rounds` 6 × `parallel_width` 3으로 상한이 있지만 **wall-clock 상한이 없고**, `interrupt`/`resume`은 하나의 논리적 조사가 **최소 두 번의 별개 엔진 호출**로 쪼개지며 그 사이에 최대 72시간의 사람 시간이 들어간다.

**골격은 제출→조회 분리다. SSE는 그 위의 읽기 최적화다. WebSocket은 기각(§6).**

- `interrupt`/`resume`을 건드리지 않는다 — `awaiting_human` + `CaseRecord.question`은 이미 프로세스 사망을 견디는 "입력 대기" 상태다. GET이 읽고 POST가 답을 **명령**으로 넣는다.
- lease의 의미가 하나로 유지된다("누가 실행 중인가"). HTTP 요청 자체가 실행하면 오토스케일되는 `api` 풀 전체가 실행자가 되고 lease가 그 사이를 중재해야 한다.
- 재접속이 공짜다 — 이벤트에 `seq`가 있으면 `GET /cases/{id}/events?since=N`이고, SSE는 그 위의 무상태 어댑터다.
- **스트리밍을 1차 계약으로 두면 안 된다.** `on_event`는 **조사를 실패시킬 수 없는 부수효과**로 의도적으로 설계됐다(세 군데의 독립적 삼킴). 전송으로 격상하면 방향이 뒤집혀 **구독자의 건강이 엔진에 영향을 준다.** 그리고 HTTP 커넥션 수명이 그래프 실행 수명이 되어 프록시 idle timeout·롤링 배포가 이미 LLM 비용을 쓴 조사를 죽인다.

추가로 반드시 필요한 것: **wall-clock 상한.** 없으면 멈춘 LLM 호출 하나가 lease와 동시 상한 슬롯을 영구 점유하고 UI는 "investigating"을 영원히 보여준다. `investigations.max_wall_clock_s`를 `asyncio.wait_for`로 강제해 기존 `_fail` 경로로 착지시킨다.

---

## 4. 최종 결과의 형태

v2가 끝났을 때 사람이 실제로 받는 것들의 구조. **이것이 하위 계획들이 수렴해야 하는 목표다.**

### 4.1 조사 보고서 (HTML 기본, MD 병존)

`(2)`와 `(3)` 두 타깃이 같은 산출물을 낸다. 5절 구조는 v1을 계승하고 네 곳이 늘어난다.

```
헤더    케이스 id · 스코프(gbm/fct) · concern(system|operation) · 개설 경로 · 요청자
        as_of 4겹: 데이터 · 코드 commit · 지식 digest · target_api digest

1. 요약  판정 한 줄 · 신뢰도 · 태스크 에러율
        ┌ 조사 단계 체크리스트 ─────────────────────────┐   ← 신설
        │ ✅ 가설 수립      가설 3건                     │
        │ ✅ 조사 계획      태스크 5건                   │
        │ ❌ 조사 실행      3 ok / 2 error               │
        │ ✅ 결과 통합      라운드 2                     │
        │ ⬜ 판정           미도달                       │
        │ ⬜ 검증           미도달                       │
        └───────────────────────────────────────────────┘
        (⚠ = verify 재작성 후 통과. 정상 종결이면 전부 ✅)

2. 판정  근본 원인 후보 (다수)                              ← 확장
        1) plan-sync 서비스   신뢰도 high  증거 ev-2, ev-5
        2) twin-state 갱신    신뢰도 low   증거 ev-7
        기여 요인 · caveat

3. 조치 권고   번호 목록, 각 항목이 근거 증거를 인용

4. 증거  id | 출처 | 무엇을 물었나 | as_of | 완전성 | effective_as_of | 요지
                     ↑ 신설 — POST body. GET은 URL이 질문이었다

5. 조사 경위   Timeline(라운드별 시각·이벤트)               ← 신설
        태스크 표 · 기각된 가설 · 검증 문제 · QA 로그
        스냅샷 출처(실패 종결이면 "실패 시점 부분 스냅샷")  ← 신설
        커버리지("part_code 5,000개 중 50개 확인")

푸터    관측성 요약(라운드 · 토큰 · 도구 실패 · 미측정 N건)
        `case label <id> --agreement ... --actual-component ...`  ← 라벨 유입구
```

세 가지가 정직성 장치다: **단계 체크리스트**는 "조사한 게 없다"와 "조사 흔적을 잃었다"를 구별하고, **증거의 "무엇을 물었나"**는 `0/0/0`이 고장인지 오질의인지 구별하고, **푸터의 라벨 한 줄**은 라벨률(= 나중에 어떤 정확도든 계산할 수 있는지의 상한)에 가장 레버리지가 크다.

**다중 후보의 도메인 형태**: `Verdict.root_cause`(최상위 후보)를 그대로 두고 **`alternates: list[CauseLink]`를 추가**한다. 벤치가 `root_cause.component`로 채점하므로 깨지지 않고, `verify`의 인용 검사를 `alternates`까지 확장하면 가드레일도 유지된다.

### 4.2 Fleet 집계 리포트 (HTML, 메일)

```
헤더    시나리오명 · 실행 창(window_from ~ window_to, 최대 편차) · 시나리오 digest

┌ 커버리지 ─── 숫자보다 반드시 앞에 온다 ──────────────────┐
│ 27 / 30 사이트                                            │
│ 미확인:  mx/suwon   REST 타임아웃    마지막 성공 3일 전    │
│          mx/hanoi   미등록 사이트    —                    │
│          ds/xian    Mongo 인증 실패  마지막 성공 12시간 전 │
│ 폴백:    mx/gumi    effective_as_of가 요청보다 6시간 이전  │
└──────────────────────────────────────────────────────────┘

지표     metric | value | reduce | 커버리지 | 완전성
축 분해  사업부 × 법인 표
추세     전회 대비 (시나리오 digest가 다르면 비교 불가 caveat)
```

정직성을 **문서가 아니라 타입이 강제한다.** `MetricRollup`의 validator:

```
covered_sites < expected_sites   →  complete=False 강제
complete=False                   →  coverage_note 필수
covered_sites == 0               →  value is None      (0이 아니다)
표본에 complete=False 하나라도    →  complete=False     (AND-fold)
```

이 넷이 있으면 **"30개 중 3개 누락인데 숫자만 내보내는" 상태를 표현할 수 없다.** `Envelope._incomplete_needs_reason`과 같은 관용구다. "알람 12% 감소"가 실은 "3개 법인 데이터 누락"인 사고를 타입이 막는다.

`window_from`/`window_to`를 반드시 찍는 이유: 40분에 걸쳐 모은 표본으로 만든 숫자와 1분 창의 숫자는 **다른 주장**이다. 사이트 간 클럭 스큐는 보정하지 않고 **드러낸다** — 보정이 아니라 표시가 이 시스템의 방식이다.

`scenario_digest`가 중요한 이유: 시나리오의 `extract`/`reduce`를 바꾼 뒤 어제 숫자와 나란히 그리면 **추세가 거짓이 된다.** digest 불일치를 caveat으로 찍어 "추세가 꺾였다"는 착시를 막는다.

### 4.3 선언적 시나리오 (Scenario Registry)

**`patrol.checks` 확장이 아니라 별도 `scenarios` 스키마다.** 근거: ① `patrol.checks`는 사이트 계층 전용이라 전역 시나리오를 넣으면 사이트마다 잡이 등록돼 **같은 집계가 N번 돌고 메일도 N통** 간다(`_SITE_LAYERS`에 전역 베이스 층이 없다) ② `CheckConfig.judge`가 필수이고 하류 전체가 "프로브 한 방 결과의 이상 판정"에 묶여 있는데 집계엔 판정이 없다 ③ 한 dict에 두 모양을 섞으면 boot·스케줄러·자기감시·digest·`patrol status` **다섯 소비자가 전부 kind 분기**를 해야 한다.

배치: **`config/scenarios/{name}.json`** — 시나리오 하나당 파일 하나, 층 병합 없음(`registry.json`처럼 단독 검증). 사이트별 옵트아웃·보정만 `SitePatrol.scenarios`의 dict로 두어 기존 `deep_merge`와 null 마커가 그대로 동작한다. **리스트를 쓰지 않는 이유는 이미 리포에 문서화돼 있다** — 리스트 deep-merge는 통째 대체 아니면 append라 사이트별 편집에 틀린 의미가 된다.

```
ScenarioConfig(StrictModel):
    kind: Literal["aggregate"]        # 향후 종류 확장의 discriminator
    concern: Literal["system", "operation"]
    enabled: bool = True
    title: str
    schedule: Schedule                # 기존 재사용 — interval xor cron 검증이 공짜
    scope: ScenarioScope              # sites("all" | 목록) · exclude · max_parallel_sites
    metrics: dict[str, MetricSpec]    # 이름→스펙 (list 아님)
    group_by: list[str] = []
    output: OutputSpec                # format(html|md) · output_dir · mail

MetricSpec: target · probe · params · body(§N2 해석기) · sample
            extract(점 경로, rules.get_path 재사용) · reduce · window · required
```

`max_parallel_sites`가 **코드가 쥐는 상한**이다. 지금 세마포어는 `guards.max_concurrent`(기본 4)로 **사이트당 하나**이고 사이트를 가로지르는 전역 상한이 없다 — 30 사이트 팬아웃이면 최대 120 in-flight가 조사 워커 트래픽과 동시에 대상 시스템으로 나간다. 규율 6: 상한이 있어야 하는 것은 코드가 쥔다.

### 4.4 웹 서비스 표면

```
POST /cases                    → 202 {case_id, status:"open"}   접수는 즉시 반환
POST /cases/{id}/intake-answers   접수 문답(매 턴 evidence로 박제)
GET  /cases/{id}               → 상태 · 질문 · 판정 요약 · 단계 체크리스트
GET  /cases/{id}/events?since=N   seq 순서 이벤트 (SSE도 같은 형)
GET  /cases/{id}/report?format=html|md
POST /cases/{id}/answers          awaiting_human 재개 명령 (멱등 키)
POST /cases/{id}/label            실제 원인 (append-only)
GET  /cases?gbm=&fct=&status=     사이트 스코프 필터 필수
GET  /checks                      점검 이력 (레저 read)
GET  /digests/{scenario}          Fleet 집계 실행 기록
```

접수가 **케이스보다 먼저** 일어나야 한다. 지금 `_drive_chat`은 `await intake(...)` 후에야 `repo.new_case_id()`를 부르므로 HTTP 첫 요청이 돌려줄 case_id가 없고, 접수 중 되묻기가 필요한 케이스는 클라이언트 끊김·서버 재시작에 문답 전체가 사라진다. 그래서 `POST /cases`가 **먼저 레코드를 열고** case_id를 반환하고, 접수 문답은 별도 엔드포인트로 분리해 매 턴 박제한다.

**사이트 축 해석이 필요하다.** 지금 `--gbm/--fct`가 필수 인자이고 `intake.py`가 "이 둘이 유일한 근거"라고 명시한다. 웹 사용자는 그 쌍을 주지 않으므로, registry 전체(활성 사이트 + 각 topology locator)를 후보로 주는 **사이트 해석 단계를 intake 앞에** 둔다. 확정 못 하면 기존 되묻기 메커니즘을 그대로 쓴다.

**RCA 응답 payload**:

```json
{
  "case_id": "...", "concern": "operation", "verdict_type": "data_loss",
  "candidates": [
    {"component": "plan-sync", "confidence": "high", "evidence_ids": ["ev-2","ev-5"],
     "rationale": "..."},
    {"component": "twin-state", "confidence": "low", "evidence_ids": ["ev-7"], "rationale": "..."}
  ],
  "contributing": [...], "recommendations": [...], "caveats": [...],
  "coverage": {"tasks_ok": 3, "tasks_error": 2, "stages": {...}, "sampled": "50/5000"},
  "as_of": {"data": "...", "code": "...", "knowledge": "...", "target_api": "..."}
}
```

### 4.5 학습 루프의 산출물

**`VerdictSnapshot`(종결 시 무조건)** — 이것이 v2의 **유일한 일방향 문**이다. `sweep_retention`이 90일에 `store.purge_case`로 `Verdict`·증거·case_file을 전부 삭제하므로(살아남는 건 `verdict_summary` 200자뿐), 그때까지 남기지 않은 것은 **나중에 어떤 상관도 계산할 수 없다.** 100일 뒤 사람이 실제 원인을 알려줘도 대조할 대상이 없다.

담는 것: `verdict_type`·`root_cause_component`·`alternates`·`confidence`·`rounds`·`evidence_count`·`task_error_rate`·`verify_demoted`·`knowledge_digests`·**`history_shown: [(case_id, tier)]`**.

마지막 항목이 특히 중요하다 — **이력 검색이 frame에 무엇을 먹였는지 남기지 않으면 "이력을 보여준 게 도움이 됐나, 앵커링이었나"를 영원히 답할 수 없다.** 지금은 공짜, 나중엔 복구 불가.

`_fail` 경로(실패 종결)에도 마커와 함께 써야 한다 — 안 쓰면 분모에 생존 편향이 생긴다.

**`RootCauseLabel`(append-only, 케이스당 복수 허용)** — `actual_root_cause_component` · `actual_verdict_type` · **`agreement: Literal["correct","partially_correct","wrong","unknown"]`** · `saw_report: bool`(앵커링 탐지) · `resolution: Literal["fixed","not_reproducible","wont_fix","false_positive"]` · `labeled_by` · `labeled_at`.

`agreement`가 component 문자열 비교보다 중요하다. 자유 문자열은 절대 정확히 일치하지 않는다("plan-sync" vs "plan sync 서비스") — 자동 문자열 비교는 자기 정규화기를 측정하는 짓이다. 사람이 주는 4분류가 이 규모에서 유일하게 믿을 만한 비교자다. `resolution`은 "에이전트가 틀렸다"와 "실은 아무것도 안 고장났다"를 분리한다 — 완전히 다른 행동을 요구하는 실패다.

**계산은 미룬다.** 라벨 `n ≥ 30` **그리고** 라벨률 > 50%(선택 편향이 유계) 전에는 어떤 숫자도 내지 않는다. 그리고 낼 때도 **상관계수가 아니라 캘리브레이션**이다 — 대상이 범주형이라 Pearson r은 범주 오류이고, 유일하게 가치 있는 것은 `confidence`별 정확도(스스로 말한 확신이 실제 적중을 따라가는가)다. 항상 건수와 함께 보고하고 맨 퍼센트는 내지 않는다.

**과거 장애 → 초기 가설.** 프롬프트 구멍은 이미 뚫려 있다 — `build_briefing`이 `[유사 이력]` 자리를 렌더하는데 프로덕션 조립이 `history_text`를 **아무것도 채우지 않는다**(모든 조사가 "없음"으로 frame한다). 벡터 없이 결정론 tier 워크로 채운다:

| tier | 조건 | 의미 |
|---|---|---|
| 1 | `fingerprint` 일치, 같은 사이트, `closed` | 이 점검이 이 대상에서 전에도 터졌다 |
| 2 | `target_locator` 일치, 같은 사이트 | 다른 점검이 같은 대상을 잡았다 |
| 3 | `target_locator` 일치, **다른** 사이트 | 같은 파이프라인 버그가 다른 공장에서 |
| 4 | 과거 locator ∈ `upstream_slice(이번 locator)` | 상류에서 전에 터진 적이 있다 |

tier 순서로 걸으며 최신순, K건(≈3)에서 멈추고, **행마다 왜 매칭됐는지(tier 사유)를 함께 렌더한다** — 없으면 리드가 tier 4를 tier 1처럼 과신하고, 스펙 §3.2가 이력을 사다리 최하위에 둔 이유가 무력화된다. `degraded` 판정과 `verdict_summary`가 없는 케이스는 제외한다(워커 실패로 닫힌 케이스의 판정은 순수 잡음이다).

**치명적 안전 제약**: **브리핑에 과거 케이스의 evidence id를 절대 렌더하지 않는다.** 과거 증거도 `ev-2` 형태고 이번 케이스에도 `ev-2`가 있다. 리드가 과거 id를 인용하면 `verify`의 인용 가능 우주가 `state.evidence`이므로 **결정론 가드레일을 그대로 통과한다.** component 이름·`verdict_type`·narrative만 렌더하고, 렌더러 단위 테스트로 고정한다.

배선 형태: `history_text`를 `EngineDeps`의 정적 필드로 채우는 것은 **틀린 모양이다** — `assemble_sites`는 사이트마다 기동 시 한 번 `EngineDeps`를 만들지만 이력은 **케이스마다, frame 시점에** 계산해야 한다. `EngineDeps.history_provider: Callable[[Case], str] | None`로 주입하고 frame 시점에 해석한다(엔진 층이 `repo`를 알게 되는 것을 피하고, 워커가 이미 쓰는 `deps_for_site` 콜백 관례와 일치한다). 검색 본체는 포트가 아니라 평범한 함수(`src/application/history.py`)다 — 두 기존 포트 위의 순수 조합이고, `upstream_slice`·`evidence_refs_for_case`가 이미 그 모양이다.

---

## 5. 분해와 순서

사람 파트너의 결정: **웹은 v2 초반.** 그래서 서비스화 핵 3개가 첫 무리로 올라온다.

```
Wave 1 — 바닥 (서비스가 서기 위한 전제)
  P1  내구성 큐 + 원자 claim + wall-clock 상한 + 전역 동시 상한
  P2  이벤트 스토어(seq · verdict_formed · open 호출부) + LedgerPort 3분할
  P3  ReportModel → md/html + 단계 체크리스트 + _fail 스냅샷 구제
      + VerdictSnapshot + HTML 메일 + 파일명 확장자화

Wave 2 — 능력 (새 감지·조사)
  P4  프로버 확장: 등재제 allowlist · query() · 인증 헤더 · 파라미터 해석기
      · pinned OpenAPI + 드리프트 점검 · boot 검증        ← P1 무관, 병렬 가능
  P5  concern 축 + rule 확장(all_zero · expected_state)   ← P4

Wave 3 — 서비스·집계
  P6  웹 서비스: API + SSE + 인증/requested_by + 사이트 해석 + 다중 RCA 후보
      + Timeline                                          ← P1, P2, P3
  P7  Fleet 집계: 시나리오 스키마 · 팬아웃 · MetricRollup · HTML 발행
                                                           ← P3, P4

Wave 4 — 학습 루프
  P8  관측성(Ticker · MetricsSink) + history provider + case label
      + chat 지문 정정                                     ← P2, P3
```

의존의 근거만 짚으면: **P3가 가장 많은 것을 막는다**(웹의 보고서 응답, 집계의 렌더러, 학습 루프의 스냅샷이 전부 `ReportModel`과 같은 데이터를 원한다). **P1·P2 없이 P6은 불가능하다** — 안 고치고 API를 얹으면 `api` 프로세스가 또 하나의 실행자가 되어 케이스 이중 실행·조사 유실·구독자 무응답이 동시에 나타난다. **P4는 아무것도 기다리지 않으므로** Wave 1과 병렬로 착수할 수 있다.

### 서비스화 핵 3개 (P1·P2의 실체)

지금 구조는 "단일 프로세스가 자기 메모리의 큐를 자기 세마포어로 소비한다"는 가정 위에 정확히 세워져 있고, 그 가정이 세 곳에 못 박혀 있다.

| 핀 | 현 상태 | 무엇이 터지나 | 최소 해결 |
|---|---|---|---|
| **인메모리 큐 + 1회성 재스캔** | `CaseQueue`는 `asyncio.Queue` 래퍼, `requeue_open`은 `PatrolDaemon.build()`에서 **딱 한 번** | `api`가 `open` 케이스를 써도 돌고 있는 데몬이 영원히 모른다. `_skip_unregistered_site`의 "다음 requeue_open이 집어 준다"도 거짓 — 그 "다음"이 없다 | `cases` 컬렉션을 큐로 쓴다(이미 `requeue_open`이 그렇게 한다). `find_one_and_update` 원자 claim + 주기 poll 잡 |
| **비원자적 lease** | `repo.save`가 `update_one({"id":...}, {"$set": doc}, upsert=True)` — owner/lease 술어 없음 | 지금 안전한 건 **우연이다**: `run_once`의 `repo.get`↔`repo.save` 사이에 `await`가 없어 협조적 스케줄링이 직렬화해 준다. 문서화되지 않은 불변식이고 `resume_once`의 버전 불일치 분기는 이미 그 사이에 `await`가 있다 | `claim(case_id, owner, now, ttl)` → 술어 있는 `find_one_and_update`. 못 잡으면 `None`=busy |
| **프로세스 내 이벤트 싱크** | `on_event`는 **동기** `Callable`, 영속화 0(전량 fire-and-forget) | 조사가 A에서 돌고 UI가 B에서 구독하면 아무것도 안 온다. 새로고침하면 재생할 것이 전혀 없다. `report_ready.data.path`는 로컬 파일 경로라 다른 노드에서 읽을 수 없다 | 싱크 경계에서 모든 이벤트를 `case_events(case_id, seq, ...)`에 append. 구독자는 `since_seq`로 tail. **sync store 호출이므로 시그니처 변경 0** |

메시지 브로커는 기각한다(§6) — 실제 요구는 "열린 케이스 하나를 원자적으로 claim하고 내가 죽어도 잃지 않는다"뿐이고 `cases` 컬렉션이 **이미** 내구성 큐다.

---

## 6. 범위 밖 / YAGNI

| 항목 | 이유 |
|---|---|
| **개발 시스템** (코드 수정·신규 기능·데이터 정합성 능동 교정) | v1과 동일하게 범위 밖. 이 시스템은 조사·판정·권고까지고 조치 실행은 사람의 몫이다. `recompute_verifier`의 재계산-대조 프리미티브만 재사용 가능하게 유지 |
| **대상 시스템 쓰기·자동 조치** | 읽기 전용 강제(§N1)를 전부 재설계해야 한다. 영구 기각 |
| **벡터 스토어 / 임베딩** | 세 게이트가 **전부** 통과할 때까지 아니다: ① 검색 범위당 종결 케이스 **≥500건**(현재 지문의 서로 다른 값은 3 사이트 × ~5 점검 = 수십 개) ② 사람이 라벨한 실제 원인을 구조 검색이 **놓친 비율 >20%** ③ 실재하는 산문 코퍼스 **≥200건**(현재 0건). 스펙 450행이 이미 기각한 것이므로 §4.5는 새 설계가 아니라 **미구현 스펙**이다. `langchain-mongodb`가 전이 의존으로 이미 설치돼 있어 import 한 줄 거리라 **명시적으로 막아둔다**. Atlas Vector Search는 Atlas를 요구 — `mongomock`으로 테스트 불가, 오프라인 규율 위반 |
| **AgentProvider 전면 추상화** | 모델 교체는 `llm.profiles` + `make_llm`으로 **이미 된다**. "외부 Agent 교체"는 지금 교체 대상이 하나도 없다. 추상화 대신 **규율로 지킨다**: LLM/에이전트 진입점은 `make_llm`과 `run_subagent` 둘뿐이고 새 호출 지점을 만들지 않는다 |
| **WebSocket을 1차 전송으로** | 클라이언트가 보내는 것은 "질문 제출"과 "질문에 답" 둘뿐이고, 둘 다 소켓이 아니라 **내구성과 멱등성**을 원한다. WS는 sticky session과 팬아웃 라우팅을 추가하는데 이벤트는 다른 워커에서 생기므로 pub/sub 팬아웃을 어차피 만들어야 하고 거기에 커넥션 어피니티까지 얹힌다. 저장된 이벤트 로그 위의 SSE로 시작하고, 진짜 양방향 필요가 **관측되면** 그때 |
| **Kafka/Redis 메시지 브로커** | 위 §5 |
| **테넌트별 DB·저장소 격리** | `(gbm, fct)` 축 + 접수 시점 술어가 요구의 전부다. 테넌트별 DB는 retention·인덱싱·boot 검증·백업을 전부 분기시키고 현재 이득이 0 |
| **워커 수평 확장 기계장치**(노드 등록·work stealing·샤드) | 스펙 §6이 이미 기각. 원자 claim만 고쳐 **나중에 가능하게** 두고, 워커 1 + api 1로 시작한다 |
| **이벤트 소싱으로 케이스 상태 재구성** | Timeline·재생을 위해 이벤트를 저장하는 것은 맞지만 `CaseRecord`가 status·lease·thread 매핑의 권위로 남아야 한다. 이벤트에서 수명주기를 유도하면 규율 4와 전이표(현재 불법 상태를 막는 메커니즘)를 통째로 버리는 것이다 |
| **범용 집계 DSL / 표현식 언어** | `reduce` 6종 Literal + 점 경로로 시작. 규율 6 — 사용자 입력 표현식은 감사 불가능 |
| **집계 결과의 LLM 서술** | Alarm Trend는 숫자와 커버리지다. LLM을 넣으면 `verify` 같은 결정론 가드레일을 새로 발명해야 하는데 집계용 verify는 `state.evidence`를 쓸 수 없다 |
| **read/write 분류 휴리스틱**(경로 규약·tags·operationId 정규식·LLM 분류) | fail-open이고 감사 불가하고 **비대칭적으로 실패한다** — 거짓음성(쓰기 호출)이 파국적이면서 동시에 비가시적이다 |
| **OpenAPI 문서에서 allowlist 자동 갱신** | 대상이 새 POST를 배포하면 우리 allowlist가 자동으로 넓어진다 = 교과서적 fail-open. 권한 결정이 대상 쪽으로 넘어간다. **"문서는 증거, config는 권한"을 코드 주석으로 박는다** |
| **OpenAPI 문서를 `StrictModel`로 파싱** | 대상이 `x-*` 확장 키를 붙이는 순간 `extra="forbid"`가 검증 오류로 죽는다. **이 리포에서 그 규율을 적용하지 않아야 하는 유일한 예외 지점**으로 명시 |
| **대상 API 스코프를 능동 프로브로 검증** | 쓰기 끝점에 403을 기대하는 호출이 필요하고 **그것 자체가 쓰기 시도다.** 영구 기각 |
| **OpenTelemetry / Prometheus / LangSmith** | p50/p99에 붙는 행동이 오늘 없다. 전 테스트가 오프라인인 프로세스에 네트워크 의존을 추가한다. LangSmith는 주입·감사가 안 되는 앰비언트 메커니즘이고, 공장 운영 데이터(프롬프트+증거)를 외부로 보내는 결정을 관측성이라는 이름으로 통과시키는 셈이다 |
| **토큰 비용을 통화로 환산** | 모델별 가격표가 반드시 낡고 조용히 틀린다. 토큰만 남기고 곱셈은 밖에서 |
| **프롬프트 A/B 프레임워크 · 메트릭 기반 `max_rounds` 자동 조정** | 자동 조정은 통제 knob을 코드 밖 피드백 루프로 옮기는 것 — 규율 6 위반. 사람이 지표를 읽고 config를 바꾼다 |
| **피드백용 웹 회신 메일 파싱** | 아웃바운드·읽기 전용 시스템에 **인바운드 메일 경로**를 여는 것. CLI 한 줄 대비 추가 가치 0에 새 공격면 |
| **`closed → reopened` 전이** | `ALLOWED`에 엣지를 추가하면 워커·close·retention·큐의 lease 상호작용이 전부 다시 열린다. 피드백은 주석이지 재실행이 아니다. 재조사가 필요하면 옛 케이스를 참조하는 **새 케이스**다 |
| **GraphQL / 범용 쿼리 API** | UI가 필요한 읽기는 §4.4의 열 개다. 움직이는 스키마 위의 범용 쿼리 층은 UI를 store 내부에 즉시 결합시킨다 — 이벤트 봉투가 막으려던 그것이다 |
| **HTML 템플릿 엔진(jinja2 등)** | 내장 템플릿 하나 + f-string으로 시작. `output.template`은 필드만 예약하고 v2에서는 `None`만 지원 |
| **사이트 간 클럭 스큐 보정** | 스펙 §6이 이미 기각. `window_from`/`window_to`로 스큐를 **드러낸다** — 보정이 아니라 표시가 이 시스템의 방식이다 |
| **집계 전용 새 이벤트 종류** | 집계는 엔진 산출물이 아니므로 `EngineEvent`를 내지 않는다(레저 + stdout로 관측). 규율 7 |
| **`LogReader` / `MetricsReader`** | 스펙이 이름만 예약했다. 지금 아니다 |

---

## 7. 선행 조건 (완료)

v2 착수 전에 갚은 v1 결함 5건 — 전부 "문서·규율은 그렇게 적혀 있는데 코드는 다르다" 계열이었다.

| 커밋 | 내용 |
|---|---|
| `5e94f81` | `endpoint_allowed`의 `?`·`#`·`;` 우회로. `{자리표시자}`가 `[^/]+`라 `/lines/L1?_method=DELETE&/oee`가 통과하고 httpx는 등록 안 된 끝점으로 나갔다 — POST를 열기 전에 이 allowlist가 유일한 가드가 되므로 반드시 선행 |
| `2e2200a` | `resume_once`에 `interaction_policy` 부재 → 스레드 재시작이 조용히 `autonomous`로 강등. 정책을 `CaseRecord`에 박제해 재개 주체가 케이스를 연 주체와 달라도 유지되게 했다(P6의 전제) |
| `a8daf41` | `_run_patrol`이 `on_event`를 안 넘겨 프로덕션에서 엔진 이벤트가 전무. `_stream_and_collect`이 아예 안 돌고 `report_ready`도 없었다 — P2의 착지점이 비어 있던 것 |
| `18fa8bf` | `find_open_by_fingerprint`가 인덱스 없이 풀스캔. `(status, fingerprint)` 복합 인덱스가 P8의 이력 검색까지 받친다 |
| `c98fcdf` | `architecture.md`가 없는 기능("데몬이 파킹 케이스를 자동 재개")을 서술 → 문서를 사실에 맞췄다. **다만 이건 문서가 발명한 것이 아니다** — 스펙 §F2가 lease의 방어 대상으로 "데몬 자동 재개"를 명시적으로 나열하므로 스펙은 그 기능의 존재를 전제하고 있다. 즉 **v1의 미구현이고, P1(주기 재스캔)과 P6(명령 채널)이 갚아야 하는 스펙 부채**다 |

이 웨이브가 남긴 규율 하나를 CLAUDE.md에 추가해야 한다: **문서·주석이 어떤 배선을 주장하면 호출부를 grep으로 확인하기 전까지 사실로 취급하지 않는다.** 세 건 중 둘이 문서화 커밋(`47502cd`)에서 검증 없이 쓴 문장이었고, `grep -rn "resume_once" src/` 한 번이면 드러났다.

같은 이유로 §4.5의 이력 검색도 **새 설계가 아니라 미구현 스펙**이다 — 스펙 §3.4가 "시맨틱 검색 없이 지문·스코프·서비스 일치 + 최신순 K건, **종결 케이스만**"을 이미 규정해 뒀다. 마지막 제약(종결 케이스만)의 이유까지 스펙에 있다: 진행 중 케이스의 미검증 가설이 새 조사를 오염시키지 않도록.
