# 디지털 트윈 운영 모니터링 시스템 설계

- **날짜**: 2026-09-02
- **상태**: 사용자 검토 대기
- **전작**: [langgraph-template](../../../../langgraph-template/) — 리포팅용 LangGraph 골격. 이 문서의 "전작 계승"은 그 저장소의 규약을 가리킨다.
- **검토 이력**: 섹션별 초안 → 독립 리뷰 에이전트의 비판 검토 5회(총 32건 지적) → 전건 반영. 각 지적은 본문에 녹아 있다.

## 0. 배경과 목표

**대상 시스템**: 디지털 트윈. edge에서 데이터를 수집해 여러 서비스가 Redis/MongoDB/Kafka를 경유해 가공하고, 최종적으로 REST API로 서빙한다. GBM(사업부)/FCT(공장)마다 배포가 존재한다.

**만드는 것**: 그 시스템의 **운영 모니터링 에이전트**. 두 개의 문이 있다.

| 모드 | 트리거 | 흐름 |
|---|---|---|
| ① 문의 대응 | 사람이 "(gbm, fct)의 X가 이상하다" 제기 | 대화형 접수 → 조사 → 원인 판정 → 조치 권고 보고서 |
| ② 자체 순찰 | 스케줄된 점검이 이상 탐지 | 케이스 자동 생성 → 같은 조사 엔진 → 보고서 발송 |

문 안쪽은 하나의 조사 엔진이다. 조치의 **실행**은 언제나 사람 몫 — 이 시스템은 대상 시스템에 대해 완전한 읽기 전용이다(§4.1).

**설계 철학**: LangGraph의 "개발자가 control flow를 지정한다"와 DeepAgent의 "태스크 분할·컨텍스트 영속·서브에이전트 위임"을 결합한다. 구체적으로:

- 조사 루프의 **구조**(라운드 상한, 병렬 폭, 허용 행동, interrupt 지점)는 코드가 정한다.
- 루프 **안에서** 무엇을 왜 조사할지는 LLM이 정한다.
- DeepAgent 3요소는 명시적 상태로 내린다: 플래닝 툴 → State 안의 태스크 리스트, 가상 파일시스템 → 케이스 파일(Store), 서브에이전트 → 역할 고정 서브그래프.

**스코프**: 이 문서는 운영 모니터링 v1이다. 개발 시스템(코드 수정 반영, 신규 기능 개발, 코드 리뷰, 정합성 체크)은 별도 설계로 미룬다. 단, 재계산-대조 프리미티브(§2.4)는 개발 시스템의 "데이터 정합성 체크"와 같은 부품이 되도록 재사용 가능한 능력 단위로 자른다.

### 0.1 검토했다 버린 대안

| 대안 | 버린 이유 |
|---|---|
| `deepagents` 라이브러리 단일 에이전트 | control flow가 LLM 루프에서 창발 — 라운드·비용 상한, 결정론 테스트가 어렵고 LangGraph 쪽 장점을 버림. 단, 그 라이브러리의 미들웨어를 부품 단위로 빌리는 것은 열어둠 |
| 전작식 고정 그래프 (자율성 최소) | 원인 조사는 열린 문제 — 고정 프로브 나열로는 새 장애 유형마다 코드 수정이 필요하고 딥에이전트 장점이 죽음 |

## 1. 전체 아키텍처

### 1.1 중심 개념: 케이스(Case)

조사 대상 사건 하나 = 케이스 하나. `(gbm, fct)` 스코프, 증상, 증거, 조사 계획, 판정, 보고서를 담는 단위.

- **수명주기**: `open → investigating → awaiting_human → closed`. `awaiting_human`은 타임아웃(config, 기본 72h)이 있고, 초과 시 미해결 명시 종결 + 스레드 폐기를 한 동작으로 수행한다.
- **식별자 분리**: `case_id`는 도메인 식별자, 체크포인터 `thread_id`는 인프라 식별자. 1:N을 허용한다 — 재조사, 재개 실패 후 새 스레드 재시작(§5.3-F3)이 이 매핑 위에서 동작한다.
- **중복 억제**: Finding에서 결정론적 지문(site, check, 대상 식별자)을 만들고, 같은 지문의 열린 케이스가 있으면 새 케이스를 열지 않고 기존 케이스에 Finding을 첨부한다. 지속되는 이상이 케이스를 양산하는 것을 막는 게이트.

### 1.2 4계층 (전작 계승, 의존은 안쪽으로만)

```
 presentation                     application                      infrastructure
┌────────────────┐      ┌───────────────────────────┐      ┌─────────────────────┐
│ CLI 챗 세션     │──┐   │  유스케이스                 │      │ 대상 시스템 어댑터    │
│ (개발·검증용)    │  │   │   investigate_case ────────┼──→   │  Redis/Mongo/Kafka  │
│ 웹 UI (최종형)  │──┼─→ │   patrol_all               │      │  REST (전부 읽기전용) │
├────────────────┤  │   │      │                     │      │  코드 저장소 리더     │
│ 보고서 렌더러    │←─┘   │      ▼                     │──→   ├─────────────────────┤
│ (md 템플릿)     │      │  조사 엔진 그래프 (§2)       │      │ 체크포인터·케이스Store │
│ 메일 발송(옵션)  │      │  순찰 그래프 (§1.4)         │      │ 실행 레저 / 스케줄러  │
└────────────────┘      │      ▼                     │      │ LLM                 │
                        │   domain: Case·Plan·       │      └─────────────────────┘
                        │   Evidence·Verdict·ports   │
                        └───────────────────────────┘
```

전작과 다른 점:

1. **어댑터는 전부 읽기 전용.** 대상 시스템에 쓰는 포트 자체를 만들지 않고, 읽기 전용을 메커니즘으로 강제한다(§4.1). 에이전트 자신의 기록은 자기 저장소에만 쓴다.
2. **멀티사이트 런타임.** config deep-merge 계층은 유지하되, 한 프로세스가 사이트 레지스트리로 전체 (gbm, fct)를 관장한다. 모드 ①은 접수 시 확정된 한 사이트로 스코프 고정, 순찰은 레지스트리 전체를 fan-out.
3. **조사는 대화가 끼어드는 장기 실행.** `interrupt()`로 사람에게 묻고 기다리는 동안 체크포인터가 상태를 유지한다.

### 1.3 케이스 큐와 동시 조사 상한

순찰 잡은 케이스를 **큐에 넣는 것까지만** 하고 반환한다. 조사는 별도 워커가 전역 동시 조사 상한(`investigations.max_concurrent`) 하에 큐를 소비한다.

근거: 공통 인프라 장애(예: Kafka 클러스터 다운)는 전 사이트에서 동시에 Finding을 낸다. 상한 없이는 N개 조사가 동시에 아픈 대상 시스템에 프로브를 퍼붓고(모니터링이 장애를 악화시키는 고전 패턴), LLM 비용이 라운드 상한 × N으로 사실상 무상한이 된다.

### 1.4 순찰: 점검(Check) = 프로브 + 판정기

점검 하나는 "무슨 데이터를 뜨나(프로브)"와 "어떻게 이상을 가리나(판정기)"의 조합이다.

| 판정기 | 동작 | 비용 | 잡는 것 |
|---|---|---|---|
| `rule` | config 임계값·룰로 결정론 판정 | 무료 | 죽은 서비스, lag 초과, 데이터 끊김 등 셀 수 있는 이상 |
| `llm` | 프로브가 뜬 스냅샷을 LLM이 판정 | 유료 | 룰로 못 적는 의미적 이상 — 파생값-원천 불일치, 패턴 이상 |
| `rule+llm` | 룰 1차 필터 → 경계선만 LLM 2차 판정 | 절충 | 룰만으론 오탐/과탐 많은 항목 |

구현 노트: config 표기는 3종을 유지하되, 내부는 "llm 판정기 + 선택적 rule 프리필터"의 합성으로 구현해 인터페이스를 2종으로 줄인다.

**공통 계약**:

- 세 판정기 모두 같은 산출물 **`Finding`**을 낸다. 케이스 생성 로직은 판정기 종류를 모른다.
- **증거 스냅샷 박제**: 프로브가 뜬 데이터는 그 자리에서 케이스 Store에 id 부여 저장한다. Finding은 스냅샷 id를 인용하고, 케이스를 열기 전 결정론적 가드레일이 **저장된 스냅샷 대비** 인용을 검증한다(라이브 재조회 금지 — 증거는 휘발성이라 재조회 검증은 "진짜 이상일수록 검증 실패"하는 역설을 만든다). 이 스냅샷이 조사 엔진의 T0 증거로 상속된다.
- **점검 결과는 3상**: `ok | finding | error`. 프로브 실패(타임아웃, 접속 거부)는 Finding이 아니라 error다 — 네트워크 블립이 유령 케이스를 열지 않는다. 단 error는 버리지 않는다: 실행 레저에 기록되고, "연속 error N회"는 내장 rule 점검으로 케이스화할 수 있다(감시자의 자기 감시, §5.4).
- **실행 레저**: (site, check)별 최종 실행 시각·결과 3상을 자기 저장소에 기록. "미점검 명시"(예산 소진, 프로브 실패)가 사는 곳. 스케줄 틱마다 하트비트도 여기 기록한다(§5.4-F5).

**스케줄**: 점검별로 `{"interval": "5m"}` 또는 `{"cron": "0 8,20 * * *"}` 중 하나(둘 다 선언 시 config 로드 거부). APScheduler의 IntervalTrigger/CronTrigger. `max_instances=1` + coalesce로 간격 초과 시 중복 실행을 방지하고, 프로브에는 타임아웃을 건다.

**LLM 예산**: `patrol.llm_budget.max_calls_per_hour` — 순찰 LLM 판정 호출의 시간 창당 상한(전역, §4.5-①). 초과 시 해당 점검은 건너뛰되 레저에 "미점검"으로 명시한다. `rule+llm` 점검은 1차 룰이 이미 "경계선"이라 판정한 상태이므로, 점검별 `on_budget_exhausted: "skip"(미점검 기록) | "escalate"(룰 결과만으로 Finding)`로 방향을 선언한다.

**점검 ≠ 조사**: LLM 판정기의 역할은 "이상하다/아니다 + 근거"까지다. 원인 파기는 케이스를 열어 조사 엔진이 한다. 이 경계는 코드가 강제한다 — 순찰 LLM이 조사를 시작하면 비용 통제가 무너진다.

## 2. 조사 엔진 그래프

### 2.1 구조

```
frame ──→ select ──Send──→ [서브에이전트 ×N 병렬] ──→ integrate ──┬─→ select   (다음 라운드)
  │                                                            ├─→ ask_human (interrupt)
  └ 계획·가설 초기화                                              ├─→ conclude ─→ verify ─┬─→ END(보고)
    (모드 ① 접수 보완은                                           │              └─ 재작성 1회
     분리된 노드에서)                                             └─→ park      (awaiting_human)
```

루프 단위는 **라운드**(select → 실행 → integrate 한 바퀴).

### 2.2 통제 경계

| 코드가 정한다 (config·상수) | LLM이 정한다 |
|---|---|
| 그래프 구조, 라운드 상한, 라운드당 병렬 폭 | 가설 목록과 우선순위 |
| 서브에이전트 역할 목록과 각 호출 예산 | 태스크 생성/취소 |
| interrupt 지점 위치와 autonomous 정책 | 태스크의 서브에이전트 배정 |
| 계획 스키마·전이 검증, 증거 인용 규칙 | 증거 해석, 가설 지지/반박, 판정문 |

### 2.3 State — 세 축 (Pydantic, 체크포인트 대상)

1. **계획(Plan)**: 태스크 리스트. LLM 갱신은 append/cancel만 허용, 스키마·개수 상한은 리듀서가 검증.
   - 태스크 스키마: 목표, 담당 역할, **입력 증거 id 목록**(정식 필드), 우선순위, `status: pending | running | ok | error`(+error 요약).
2. **케이스 파일**: 증거 본문은 케이스 Store에, State에는 id + 요약(digest)만 — 스냅샷이 커도 State가 비대해지지 않는다.
   - 불변 증거 엔트리: id, 출처, as_of, 결과 봉투 메타(§4.2), 본문 ref. **code_tracer의 로직 명세도 id를 가진 증거 엔트리다** — 재계산 태스크가 참조하고 Verdict가 인용한다.
   - 가설 보드: 가설별 `open | supported | refuted` + 지지/반박 증거 id 링크.
   - 태스크 결과, autonomous 모드에서 기록된 질문.
3. **판정(Verdict)**: 구조화 필드 — 원인 컴포넌트 id(토폴로지 노드 참조), 판정 유형(초기 집합: `logic_bug | data_loss | config_error | stale_data | external | inconclusive | degraded` — 구현 계획에서 확정), 신뢰도, 조치 권고, **기계 검증 가능한 caveat 목록**(예: "배포 버전 미검증", "지식 digest 불일치"). 모든 주장에 증거 id 인용 필수. 구조화 필드는 E2E 채점 술어이기도 하다(§5.5-F7).

### 2.4 노드

**frame** — 케이스(증상 + T0 스냅샷)와 지식 브리핑(§3.6)으로 초기 가설들과 1차 계획 생성.
모드별 T0: 순찰 케이스의 T0는 Finding 시각이고 프로브 스냅샷이 이미 박제되어 있다. **모드 ① 케이스의 T0는 접수 시각이고 박제된 스냅샷이 없으므로, frame의 1차 계획은 휘발성 소스(Redis 등 현재값만 있는 것) 프로브를 최우선 순위로 배치한다** — 조사가 늦어질수록 증발하는 증거를 먼저 확보한다.
모드 ①의 접수 보완은 **세 노드로 분리**한다: 질문 생성 → interrupt 전용 노드 → 가설 생성. LangGraph의 interrupt는 resume 시 노드 선두부터 재실행되므로, interrupt 앞에 LLM 호출이 있으면 질문이 재생성되어 답이 엉뚱한 질문에 매칭될 수 있다. **설계 원칙: interrupt는 노드 최상단에만 둔다.**

**select** — 결정론. **실행 가능 = 태스크가 참조한 입력 증거 id가 케이스 파일에 전부 존재.** 실행 가능 태스크를 우선순위(동률이면 FIFO)로 병렬 폭만큼 꺼내 `Send` 발사. 실행 가능 0건이면 integrate로 폴백. 이 게이트가 없으면 frame의 1차 계획(증거가 없는 시점에 만든 전체 체인)이 라운드 1에 전부 발사되어 재계산 태스크가 입력 없이 실패한다.

**서브에이전트** — 역할 고정 서브그래프. 각자 호출 예산을 가진 닫힌 그래프로, 태스크 목표 + 필요한 증거 id만 받고 구조화 결과만 반환한다(리드 컨텍스트 격리).

| 역할 | 하는 일 |
|---|---|
| `data_prober` | 스코프 사이트의 Redis/Mongo/Kafka/REST를 as_of 기준 조회 → 증거 엔트리 |
| `code_tracer` | 대상 서비스 코드를 읽고 변환 로직 규명 → 로직 명세(증거 엔트리) |
| `recompute_verifier` | 단계 입력 스냅샷 + 로직 명세로 기대값 도출, 실제값 대조 → 샘플별 일치/불일치 |

- **브랜치는 절대 raise하지 않는다.** LangGraph에서 Send 브랜치 하나의 예외는 superstep 전체를 실패시켜 성공한 브랜치의 쓰기까지 증발시킨다. 서브그래프 최외곽 catch-all이 예외를 `status: error` + 원인 요약·스택으로 변환해 반환한다.
- integrate는 부분 실패를 소비한다 — "조회했더니 비어 있음"(그 자체가 증거)과 "조회 실패"(아무것도 모름)를 구분한다.
- 새 역할(LogReader 기반 로그 분석 등)은 역할 목록 추가로 확장한다.
- 세 역할의 조합이 파이프라인 이분탐색의 실행 부품이다: 단계 경계마다 probe(실제값) → trace(로직) → recompute(기대값 대조), 처음 어긋나는 경계가 범인 단계.

**integrate** — 리드 LLM이 새 증거로 가설 보드 갱신 → `계속 | 질문 | 결론` 선택. 라운드 상한 도달 시 코드가 강제 conclude — **"미확정" 판정 허용, 억지 결론 금지.** 단, 증거 수집이 사실상 전멸한 조사(태스크 에러율 기준)는 "미확정"이 아니라 **"조사 실패(degraded)"**로 낙인한다(§5.3-F4) — 시스템 고장이 정상적 미확정으로 위장하는 것을 막는다.

**ask_human** — `interaction_policy`에 따라:
- `interactive`(모드 ①): interrupt로 질문, 답을 케이스 파일에 기록.
- `autonomous`(모드 ②): frame 접수 보완은 발생 불가(순찰 케이스는 구조화 입력). integrate발 질문은 기본 **"질문을 케이스 파일에 기록 + 보수적 기본값으로 진행"**, config(`engine.autonomous_question_policy: "park"`)로 파킹+알림 전환 가능.

**park** — interrupt로 스레드를 유지한 채 케이스를 `awaiting_human`으로. 재개 시 사람 입력을 케이스 파일에 기록 후 **integrate로 진입**(frame 재실행 금지 — 계획·가설 보드가 이미 있다). 타임아웃 시 미해결 명시 종결 + 스레드 폐기.

**verify** — 결정론 가드레일 (전작 근거 id 가드레일의 직계):
- 판정의 인용 증거 id가 케이스 파일에 실재하는가. 인용 없는 주장이 없는가.
- **incomplete 결과 봉투(§4.2)에서 나온 부정 증거("없음")로 결론을 내리지 않았는가.**
- 실패 시 재작성 1회, 재실패 시 신뢰도 강등 + "검증 미통과" 명시 보고.

### 2.5 as_of 규율 — 세 겹의 시간축 정렬

핵심 난제인 "원천은 정상인데 파생 결과값이 다르다"는 변환 이상은 **결과가 계산된 당시** 기준으로 비교해야 한다. 지금 원천과 비교하면 false positive(그새 원천이 바뀜)와 false negative(그새 복구됨)가 모두 생긴다.

1. **데이터의 as_of**: 조사 기준 시각은 케이스 T0. 재계산 검증의 입력은 Kafka 타임스탬프→오프셋 seek, Mongo 이력으로 계산 당시 것을 복원한다. 노드에서 `datetime.now()` 금지, 시각은 주입(전작 계승).
2. **코드의 as_of**: 코드 증거 엔트리에 commit hash 필수. 사이트별 배포 커밋은 `knowledge/deployment.yaml`(§3.3)이 진실이고, code_tracer의 사이트 조사는 워크트리가 아니라 **그 hash 지정 읽기**로 한다. 배포 버전을 확인할 수 없으면 코드 증거에 "배포 버전 미검증" 플래그를 강제하고 Verdict caveat에 실린다.
3. **지식의 as_of**: 케이스 T0에 토폴로지·룰·deployment의 content digest를 박제한다. park 등으로 며칠 걸친 케이스가 재개될 때 digest 불일치면 re-frame 플래그 또는 보고서 caveat — 조사 중 임계값·토폴로지가 바뀌어 순찰 Finding과 최종 보고가 모순되는 것을 막는다.

## 3. 지식 층

에이전트가 대상 시스템에 대해 아는 것의 저장처와 공급 규칙. **4개 소스 + 파생 캐시 1개.**

| 소스 | 형태 | 저장처 | 주 소비자 |
|---|---|---|---|
| 토폴로지 명세 | 기계가독 YAML | `knowledge/`, 사이트별 deep-merge | frame(이분탐색 계획), data_prober(어디를 찍나) |
| 자유 문서 | md | `knowledge/docs/`, 사이트 계층 동일 | frame 브리핑 |
| 룰·임계값 | config (순찰 점검과 공용) | config | 순찰 판정기 + 조사 시 정상 기준 |
| 과거 이력 | 종결 케이스의 (지문, 증상, 판정, 권고) | 케이스 Store | frame 브리핑 |
| (파생) 코드 지식 캐시 | code_tracer가 규명한 로직 명세 | 케이스 Store, `(service, commit_hash)` 키 | recompute_verifier, 후속 케이스 |

### 3.1 토폴로지 명세

파이프라인 이분탐색은 "누가 뭘 읽고 써서 뭐가 파생되는가"를 기계가 따라갈 수 있어야 성립한다.

```yaml
services:
  twin-aggregator:
    code: { repo: "twin-services", path: "services/aggregator" }   # repo는 config의 name 참조
    reads:  [ { kind: kafka, topic: "edge.raw.{line}" } ]
    writes: [ { kind: mongo, collection: "twin_state" } ]
derivations:            # output 식별자를 키로 하는 map — 사이트별 merge 의미가 services와 동일
  "rest:/api/v1/lines/{line}/oee":
    inputs: [ { kind: mongo, collection: "twin_state" } ]
    via: twin-api
    key: line           # per-key(라인별 1:1) | fan-in(집계) — probe 스코프를 결정
```

- `derivations`를 리스트가 아닌 **map**으로 두는 이유: 리스트 deep-merge는 통째 대체 아니면 append라 사이트별 그래프 편집에 둘 다 틀린 의미다. map이면 `services`와 같은 merge 의미가 되고, 사이트별 삭제는 null 마커로 한다.
- `key` 필드: "line 7 이상"의 상류를 line 7만 찍을지(per-key) 전 라인을 찍을지(fan-in) data_prober가 결정하는 근거.
- frame은 증상 끝점에서 derivation을 거슬러 올라가며 **유계 깊이의 슬라이스**만 브리핑에 넣는다. 전체 코퍼스 덤프 금지.

### 3.2 우선순위 사다리 — 지식이 서로 다른 말을 할 때

```
실측 데이터 = 코드  >  룰 config  >  토폴로지 명세·자유 문서  >  과거 이력
```

- **토폴로지도 사다리에 있다.** 손으로 유지하는 명세는 반드시 낡는다. code_tracer·data_prober의 증거가 토폴로지 edge와 모순되면, 문서 불일치와 동일하게 **"토폴로지 갱신 권고 Finding"**을 기록하고 integrate가 해당 슬라이스를 재계획한다.
- 문서와 코드가 다르면 코드가 이기고, 불일치 자체가 Finding이 되어 보고서에 "문서 갱신 권고"로 실린다.
- 과거 이력이 최하위인 이유: 유사 증상 ≠ 동일 원인. 이력은 가설의 출발점이지 근거가 아니다 — 판정 인용은 이번 케이스의 증거 id로만(verify가 강제).

### 3.3 deployment.yaml — 사이트×서비스 → 배포 커밋

로컬 체크아웃의 hash는 "T0에 그 사이트에서 돌던 커밋"과 같다는 보장이 없다(사이트별 배포 시점이 다르다). 배포 진실은 사이트별 `knowledge/deployment.yaml`(service → repo name + commit hash)이 담당하고, 운영 절차로 수동 유지한다.

- 케이스 T0에 이 파일의 digest도 박제한다(§2.5-3과 같은 메커니즘).
- 기동 검증: 기재된 hash가 해당 레포에 실재하는지 확인.
- 유지가 안 되는 사이트는 코드 증거에 "배포 버전 미검증" 플래그가 강제된다.

### 3.4 자유 문서·과거 이력·코드 지식 캐시

- **자유 문서**: 코퍼스가 작으므로 벡터 DB 없이 문서 인덱스 + frame의 LLM 선별. 커지면 검색을 붙일 확장점만 남긴다.
- **과거 이력**: 시맨틱 검색 없이 지문·스코프·서비스 일치 + 최신순 K건. **종결 케이스만** 검색 대상 — 진행 중 케이스의 미검증 가설이 새 조사를 오염시키지 않도록.
- **코드 지식 캐시**: `(service, commit_hash)` 키. hash가 바뀌면 자동 무효. 사이트 간 재사용은 이득(같은 hash면 같은 로직).

### 3.5 bootstrap — 지식 온보딩 워크플로우 (코어 이후 구현)

`knowledge/`를 손으로만 채우는 온보딩 비용을 줄이는 유틸리티. 원칙: **AI는 초안, 사람은 승인.**

```
1 수집   서비스별 code_tracer fan-out → reads/writes/derivation 후보 (file:line 근거 첨부)
2 대조   읽기 전용 어댑터로 실물 검증 → verified/unverified 마킹
3 질문   빈 곳·모순만 표적 질문 (하이브리드 대화 층 재사용)
4 초안   topology.yaml + docs/ 초안 + TODO 마킹 → 사람 리뷰·커밋 → 기동 검증이 최종 게이트
```

새 부품이 거의 없다 — code_tracer, 어댑터, 대화 층, 기동 검증의 재조합. 운영 중 자동 지식 수정은 하지 않는다: 드리프트 Finding이 사람에게 가고, 사람이 bootstrap 재실행 또는 수동 수정한다.

### 3.6 frame 브리핑의 조립

frame이 케이스마다 지식 층에서 꺼내 조립하는 것: 토폴로지 슬라이스(증상 끝점 역추적, 유계 깊이) + 적용 룰 + 유사 이력 K건 + 선별된 자유 문서 + deployment 정보.

## 4. 어댑터와 config

### 4.1 대상 시스템 어댑터 — 읽기 전용 포트 5종

domain에 포트, infrastructure에 구현. 개발용 스텁(in-memory) ↔ 실제 구현을 config로 교체(전작 패턴).

| 포트 | 제공 | as_of |
|---|---|---|
| `RedisReader` | GET, 패턴 SCAN(상한). ⚠ 구현 전 결정 항목(§7) | ✗ 현재값만 — 순찰 스냅샷 박제가 Redis 증거의 유일한 과거형 |
| `MongoReader` | find/count/aggregate (허용 연산 제한) | ◯ 이력 컬렉션·타임스탬프 필터 |
| `KafkaInspector` | 오프셋·lag·컨슈머 그룹 메타, 보존 내 메시지 읽기 | ◯ 타임스탬프→오프셋 seek |
| `RestProber` | 토폴로지 등록 끝점만 GET | ✗ |
| `CodeRepoReader` | 파일 읽기, 심볼 검색, commit hash 조회 | ◯ hash 지정 읽기 |

**원칙 ① — LLM에 원시 쿼리 금지.** 어댑터는 타입 있는 쿼리 빌더만 노출한다("컬렉션 X에서 필터 Y로 최대 N건").

**원칙 ② — 읽기 전용을 선언이 아니라 메커니즘으로.**
- Mongo: aggregate allowlist에서 `$out`/`$merge`/`$function`/`$where` 명시 배제(앞 둘은 **쓰기 스테이지**다). 기동 시 계정 권한 검사(`connectionStatus`로 readonly 롤 확인) — "읽기 전용 계정 권장"을 "검증"으로 격상.
- Kafka: **consumer group 미참여 `assign()`** + 오프셋 커밋 금지 + admin 변경 API 미노출. 나이브 구현의 기본 동작(group 참여·커밋)이 브로커 상태를 변경하므로 스펙으로 못 박는다.
- Redis: `KEYS *` 금지, SCAN + 상한.
- REST: 토폴로지에 등록된 끝점 밖 호출 거부.
- 코드 레포: git 변경 명령 자체를 노출하지 않는다.

**원칙 ③ — 아픈 시스템을 더 아프게 하지 않는다.** 어댑터 공통 타임아웃·결과 크기 상한·사이트별 동시 요청 상한(`target.guards.*`). 조사가 몰리는 시점은 정의상 대상이 아픈 시점이다.

**확장점**: `LogReader`, `MetricsReader`는 포트 목록에 이름만 예약. 인터페이스 선정의는 도입 결정 때.

### 4.2 결과 봉투 — "요청한 것"과 "얻은 것"의 차이

5개 포트 공통으로 결과에 메타를 동봉한다:

- `complete: bool` + 잘린 사유 (SCAN 상한, 행수 상한 도달 등)
- `observed_at`: 실제 관측 시각
- `effective_as_of`: 요청 as_of와 실제 달성된 as_of가 다르면 명시 — **Kafka의 timestamp→offset seek은 T0가 보존 밖이면 조용히 earliest로 떨어져 "T0보다 나중" 데이터를 반환하는 것이 기본 동작**이므로, 이 필드가 없으면 오염된 증거가 T0 증거로 위장한다.

verify 규칙(§2.4)과 연동: incomplete 결과의 부정 증거로 결론 금지. 스냅샷 박제 시 이 메타를 본문과 함께 저장한다.

### 4.3 코드 레포 — 멀티 레포, 서브모듈 불가지론

```jsonc
"code": { "repos": [ { "name": "twin-edge", "path": "/repos/twin-edge" },
                     { "name": "twin-services", "path": "/repos/twin-services" } ] }
```

- 토폴로지가 `name`으로 참조. 기동 검증: 참조된 name 실재.
- 제공 방식은 독립 클론이든 부모 레포의 git 서브모듈이든 무관(시스템은 불가지론). 요구사항은 둘: **full history**(hash 지정 읽기가 과거 배포 커밋에 닿아야 함 — shallow clone 금지), **시스템은 레포를 변경하지 않음**(pull/submodule update는 운영 절차).
- code_tracer의 사이트 조사는 워크트리가 아니라 deployment.yaml의 hash로 읽는다(§2.5-2). 워크트리는 최신 참조용 기본값일 뿐이다.

### 4.4 에이전트 자신의 저장소

| 저장소 | 담는 것 | 백엔드 |
|---|---|---|
| 체크포인터 | 그래프 스레드 상태 (재개·Time Travel·park) | MongoDB(`langgraph-checkpoint-mongodb`) / 개발 시 memory |
| 케이스 Store | 케이스 레코드, 증거 스냅샷 본문, 코드 지식 캐시, 종결 이력 | 같은 인스턴스, 별도 DB |
| 실행 레저 | (site, check) 실행 이력 3상, 하트비트, 발송 기록 | 케이스 Store 내 컬렉션 |

- 대상 시스템의 Mongo와 **물리적으로 분리** — 감시 대상에 상태를 저장하면 대상 장애 = 자기 장애.
- **보존 정책**: `store.retention.{closed_case_evidence_d, ledger_d, checkpoint_ttl}` 3키 + 주기 정리 잡. 체크포인터는 스텝마다 상태를 쌓으므로 방치하면 수개월 뒤 에이전트가 자기 기록에 깔려 정지한다.

### 4.5 config

**① 전역/사이트 스코프 분리.** deep-merge 계층은 사이트별 config를 만든다. 전역이어야 할 키가 사이트별로 해석되면 예산·상한이 사이트 수만큼 곱해져 가드가 무력화된다.

- `config/app.json` (전역): `engine.*`, `investigations.*`, `llm.*`, `patrol.llm_budget`, `store.retention`, `timezone`
- 사이트 계층 (deep-merge: `gbm/{gbm}.json` → `factories/{fct}/common.json` → `factories/{fct}/{gbm}.json`): `target.*`, `patrol.checks`, `knowledge` 오버라이드
- 기동 검증: 사이트 계층에 전역 키 등장 시 거부.

**② 레지스트리.** `config/registry.json`: `{ "sites": [ { "gbm": "mx", "fct": "gumi", "enabled": true }, ... ] }`. `enabled: false`는 "등록됐지만 순찰 제외" — 공장 PM/셧다운 기간, 신규 사이트 사전 등록에 쓴다.

**③ 스키마 검증.** merge 후 config를 pydantic 강타입으로 검증, **unknown key 거부**. deep-merge는 오타 키를 조용히 수용해 "오버라이드가 무시된 채 기본값으로 순찰이 도는" 최악의 조용한 실패를 만들기 때문.

**④ 인증 — 있는 법인도, 없는 법인도.** config에는 주소와 env 키 참조만, 비밀값은 전부 `.env`.

```jsonc
// 인증 없는 법인:  "redis": { "url": "${MX_GUMI_REDIS_URL}" }
// 인증 있는 법인:  "redis": { "url": "${MX_SUWON_REDIS_URL}", "password": "${MX_SUWON_REDIS_PASSWORD}" }
//                "mongo": { "url": ..., "username": "${MX_SUWON_MONGO_USER}", "password": "${MX_SUWON_MONGO_PASSWORD}" }
```

- 인증 필드는 선택 — 어댑터가 유무를 보고 접속 조립. URL에 비밀번호 금지(로그 노출 방지), 로그·`config show`에서 마스킹.
- env 키는 사이트 접두사 컨벤션(`MX_GUMI_...`). config가 참조한 키가 `.env`에 없으면 기동 거부.
- `.env.example`을 커밋하고 `.env`는 gitignore. Mongo는 읽기 전용 권한 계정 발급 권장(+기동 시 롤 검증, §4.1).

**⑤ 주요 키 사전.**

| 키 | 의미 |
|---|---|
| `registry.sites[].{gbm,fct,enabled}` | 관장 사이트 목록과 순찰 활성화 |
| `target.{redis,mongo,kafka,rest}` | 대상 접속처. 인증 필드 선택 |
| `target.code.repos[].{name,path}` | 조사 코드 레포 |
| `target.guards.{timeout_s,max_rows,max_concurrent}` | 어댑터 호출 가드 |
| `knowledge.root` | 토폴로지·문서 디렉터리 |
| `patrol.checks.<name>.{judge,schedule,params,on_budget_exhausted}` | 점검 정의 |
| `patrol.llm_budget.max_calls_per_hour` | 순찰 LLM 판정 시간당 상한 (전역) |
| `engine.max_rounds` | 조사 라운드 상한 — 도달 시 강제 conclude |
| `engine.parallel_width` | 라운드당 병렬 서브에이전트 수 |
| `engine.subagent_budgets.<role>` | 파견 1회당 내부 LLM 호출 상한 |
| `engine.autonomous_question_policy` | `default_and_log` \| `park` |
| `investigations.max_concurrent` | 전역 동시 조사 워커 수 |
| `investigations.awaiting_human_timeout_h` | park 대기 시한 |
| `llm.profiles.{judge,subagent,lead}` | 역할별 모델 — 비용 통제의 세 번째 축 |
| `store.retention.*` | 보존 정책 3키 |
| `report.mail.*`, `report.output_dir` | 보고 채널 |
| `timezone` | 스케줄·보고서 시간대 |

### 4.6 기동 검증 목록 (한눈에)

하나라도 실패하면 기동 거부 — 밤에 조용히 틀리는 것보다 배포 시점에 시끄럽게 죽는다.

1. config 스키마(강타입, unknown key 거부), 사이트 계층의 전역 키 거부
2. 점검 schedule 형식(interval xor cron)
3. 참조된 env 키 실재
4. 토폴로지 내부 정합성(참조 서비스 실재, kind 유효, derivation 끊긴 참조 없음)
5. 순찰 룰 타깃이 토폴로지로 해석되는가
6. 토폴로지의 repo name이 config에 실재하는가
7. deployment.yaml의 hash가 해당 레포에 실재하는가
8. Mongo 계정 readonly 롤 (대상 시스템)

전 사이트 접속성 검증은 **하지 않는다** — 죽은 사이트가 기동을 막으면 역효과, 지연 연결이 옳다.

## 5. 보고, 에러 처리, 테스트

### 5.1 보고서

엔진은 도메인 객체(Verdict + 케이스 파일)를 내고, 렌더링은 프레젠테이션(md 템플릿). **보고서는 항상 파일 먼저**(`output/`) — 메일·웹은 그 파일의 전달자다.

```
1 요약        증상, 스코프, T0, 판정 한 줄, 신뢰도, 태스크 에러율
2 판정        근본 원인(컴포넌트 id), 근거 사슬(증거 id 인용), caveat 목록
3 조치 권고   사람이 실행할 것들, 우선순위 순
4 증거        id별: 출처, as_of, 완전성(결과 봉투 메타), 요지
5 조사 경위   라운드 요약, 기각된 가설과 이유, 미조사 항목·에러 명시
```

"무엇을 확인 안 했나"의 명시(5절)가 신뢰의 조건이다 — 조용한 생략 금지의 보고서판.

### 5.2 채널과 이벤트 계약 (F1)

세 채널은 같은 유스케이스 + **같은 이벤트 봉투** 위의 어댑터다.

- 이벤트 봉투: 안정 소어휘 — `case_status_changed | round_started | task_finished | question_raised | report_ready` + `schema_version` + `case_id`.
- 그래프 `stream_mode=updates` 원본(내부 노드명·State diff)은 계약이 아니다. updates→이벤트 변환은 유스케이스 층의 얇은 매퍼 하나로 두고, **CLI도 이 봉투만 소비한다** — v1 CLI가 계약의 첫 검증자가 되어, 웹 UI 착수 시 "그래프 내부에 결합된 스트림" 문제가 생기지 않는다.

| 채널 | 형태 | 단계 |
|---|---|---|
| CLI | 대화 세션(접수·interrupt 응답) + 이벤트 스트리밍 + 보고서 출력 | v1 |
| 웹 UI | 같은 유스케이스 API + 같은 이벤트 구독 | 후속 (계약은 v1 확정) |
| 메일 | 종결 보고서·순찰 알림 발송 | v1 옵션 |

**단일 실행자 규칙 (F2)**: 본질은 **케이스당 실행자 하나** — 케이스 문서의 owner/lease 필드를 잡은 프로세스만 그 케이스의 그래프를 실행한다. 평시 lease 보유자는 patrol 데몬(워커)이고, `chat` 세션은 **자신이 연 케이스에 한해** lease를 잡고 인라인 실행할 수 있다(데몬 없이도 모드 ① 개발·검증 가능). `case resume`은 실행자가 아니라 **클라이언트**다 — 재개 명령을 큐에 넣고 이벤트 스트림을 구독해 보여준다. lease가 이중 실행(수동 resume vs 데몬 자동 재개, 데몬 이중 기동, chat vs 데몬)을 모두 방어한다.

### 5.3 CLI

```bash
python -m src chat --gbm mx --fct gumi   # 모드 ①: 접수 대화 → 조사 → 보고서
python -m src patrol                     # 순찰 데몬 (스케줄러 + 케이스 큐 워커)
python -m src patrol status              # 하트비트·최근 실행 확인
python -m src case list|show|resume <id> # 케이스 조회, park 재개(질문에 답하기)
python -m src config show / registry     # 병합 config·출처, 사이트·점검 목록
python -m src knowledge validate         # 기동 검증 단독 실행 (CI용)
```

### 5.4 에러 처리 — 실패의 다섯 층

| 층 | 정책 |
|---|---|
| 어댑터 호출 | 결과 봉투(`ok\|error`, complete, effective_as_of) — 그래프 안으로 raise 금지 |
| 서브에이전트 | 최외곽 catch-all → `status: error` + **원인 요약·스택 보존**, integrate가 소비 |
| LLM 게이트웨이 | 유계 재시도(지수 백오프) 후 태스크/점검 error 강등 |
| 순찰 점검 | 3상 error → 레저 기록. "연속 error N회"는 내장 rule 점검으로 케이스화 |
| 프로세스 죽음 | 체크포인터 재개 + 아래 F3·F6 |

**F3 — 배포를 가로지르는 재개**: 체크포인트에 스키마 버전 스탬프. 재개 실패(역직렬화·노드 참조 오류)나 버전 불일치 시 해당 thread 폐기 + **같은 case에 새 thread로 케이스 파일 기반 재시작** + 레저 기록. 무가드 자동 재개의 크래시 루프를 막는다.

**F4 — 삼켜진 에러의 전파**: error 봉투의 원인·스택은 레저/케이스 파일에 보존. 보고서 1·5절에 태스크 에러율 명시. 증거 수집이 사실상 전멸한 조사는 "미확정"이 아니라 **"조사 실패(degraded)"** — 게이트웨이 설정 오류 같은 시스템 고장이 겉보기 정상인 미확정 보고서로 위장해 몇 주 뒤 발견되는 것을 막는다.

**F5 — 순찰 생존 신호**: 스케줄 틱마다 레저에 하트비트. `patrol status`로 노출. 프로세스 생존 감시는 외부(systemd/k8s)가 담당하고 하트비트에 외부 알람을 거는 것을 운영 요구사항으로 명문화한다. "연속 error N회" 자기 감시는 점검이 돌 때만 작동하므로, 데몬 자체가 죽는 경우는 이 층이 담당한다.

**F6 — 발송 멱등**: 발송 기록은 `pending → sent` 2상. "기록 먼저(pending) → 발송 → sent 갱신" 순서로 하고, 재개·다음 틱에 pending을 재시도한다(중복은 기록 id로 억제). at-most-once(기록 후 발송 전 죽으면 유실)도 at-least-once 폭주도 아닌 실용 절충.

### 5.5 테스트 전략

원칙: **LLM과 대상 시스템을 스텁으로 바꾸면 전체가 결정론이 된다.**

1. **domain 단위**: 리듀서(계획 append/cancel), 지문 생성, 수명주기 전이, 결과 봉투 규칙, 우선순위 사다리.
2. **그래프 구조** (이 시스템의 척추): 스크립트된 가짜 LLM으로 — select 게이트(입력 증거 없으면 미발사), 라운드 상한 강제 종결, verify의 인용 누락·incomplete 부정 증거 거부, park→resume이 integrate로 진입, autonomous 정책 분기, 브랜치 error가 라운드를 죽이지 않음. LangGraph 의미론(superstep, interrupt 재실행)에 기대는 지점이 많아 구조 테스트의 가치가 크다.
3. **기동 검증 네거티브**: §4.6의 각 항목이 실제로 기동을 거부하는지.
4. **간판 시나리오 E2E**: in-memory 대상 스텁에 장애를 심고(예: aggregator 스텁의 0-나눗셈 + 데이터) 케이스가 올바른 단계 경계·원인을 지목하는지.
   - **채점 술어는 Verdict의 구조화 필드**(원인 컴포넌트 id, 판정 유형)만 단언 — md 텍스트 매칭 금지(템플릿 수정마다 깨지는 벤치는 즉시 썩는다).
   - 시나리오마다 **회귀 모드(스텁 LLM — 각본 검증)**와 **평가 모드(실 LLM — 조사 품질)**를 구분 표기. 시나리오 저장소가 곧 품질 평가 벤치 자산으로 축적된다.

## 6. v1 스코프 경계

**포함하지 않는 것** (검토 후 의도적 제외):

- 웹 UI 구현 — 이벤트 계약(§5.2)만 v1에서 확정, 구현은 후속
- bootstrap 워크플로우(§3.5) — 코어 엔진 이후
- LogReader/MetricsReader — 이름 예약만
- 개발 시스템 — 별도 설계 (재계산-대조 프리미티브만 재사용 가능하게 잘라둠)
- 벡터 검색(문서·이력), 토폴로지 자동 생성/CI 검증, 시크릿 매니저 연동, 다중 워커 수평 확장, 추가 알림 채널(Slack 등), 케이스 큐 우선순위 고도화, 사이트 간 클럭 스큐 보정 — 각 리뷰에서 YAGNI 기각. 필요가 관측되면 그때.

## 7. 구현 시 결정 항목

1. **Redis 연산 폭**: GET+SCAN은 "값이 전부 문자열" 가정이다. 구현 전 실물 스키마와 대조해 확정하고, 해시·TTL이 쓰이면 TYPE 분기 읽기 + TTL 조회를 추가한다(여전히 읽기 전용).
2. **순찰 점검 구체 목록**: 사이트별 실제 점검 항목·임계값은 config 채우기 단계에서 운영자와 정의.
3. **LLM 게이트웨이 프로파일별 실제 모델명**: 게이트웨이 제공 목록 확인 후.
4. **awaiting_human 알림 경로**: park 시 사람에게 닿는 채널(메일 우선? 웹 대기함?)은 채널 구현 순서에 따라.
