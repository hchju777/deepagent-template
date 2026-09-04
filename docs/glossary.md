# 용어집

이 리포 전반에서 쓰는 용어를 정의한다. 소스 위치를 함께 적었다 — 정확한
필드 목록은 코드가 진실이다.

## 도메인 모델

**Case** (`src/domain/case.py`) — 조사 사건 하나. `(gbm, fct)` 스코프 +
증상(`symptom`) + 최초 관찰 시각(`t0`) + 대상 locator로 구성된, 조사 엔진에
넘어가는 도메인 객체. 저장소에 영속화되는 형태는 `CaseRecord`(아래)이고, 이
둘은 다르다 — `CaseRecord.to_case()`가 재구성한다.

**CaseRecord** (`src/domain/cases.py`) — 저장소에 저장되는 케이스의 실제
행. 상태(`status`), 소유자·임차(`owner`/`lease_until`), 파킹된 질문
(`question`), 종결 사유(`closed_reason`) 등 Case에는 없는 수명주기 필드를
갖는다.

**케이스 수명주기** — `open → investigating → awaiting_human → closed`.
`OPEN_STATUSES = (open, investigating, awaiting_human)`. →
[architecture.md §5](architecture.md#5-케이스-수명주기와-lease)

**lease(임차)** — 동시에 한 워커만 케이스를 조사하도록 하는 잠금.
`owner` + `lease_until`(TTL, `investigations.lease_ttl_s`)로 구현되며,
조사 도중 `lease_ttl_s/3` 간격의 keepalive로 갱신된다.

**지문(fingerprint)** (`src/domain/patrol.py`) — `sha256(gbm|fct|check|target)`의
앞 16자. 같은 지문의 열린 케이스가 이미 있으면 새로 열지 않고 기존 케이스에
증거를 첨부한다(중복 케이스 억제).

**스크래치 케이스(scratch case)** — `patrol:{gbm}:{fct}:{check}` id로 관리되는
임시 케이스. 순찰이 finding을 내면 그 증거 스냅샷이 정식 케이스로 승격되기
**전에** 먼저 여기 박제된다 — 판정 결과와 무관하게 프로브가 성공했다는 사실
자체는 항상 남아야 한다는 원칙 때문이다.

**Finding** (`src/domain/patrol.py`) — 점검 하나가 "이상이 있다"고 판단한
결과. `evidence_ids`로 스크래치 케이스에 남긴 증거를 가리킨다.

**CheckOutcome** — 점검 실행 결과의 4상: `ok`(정상) / `finding`(이상) /
`error`(프로브·rule 설정 실패) / `skipped`(예산 소진 등으로 건너뜀).

**PlanTask** (`src/domain/case.py`) — 조사 계획의 태스크 하나.
`role`(`data_prober`/`code_tracer`/`recompute_verifier`), 실행 가능 조건인
`input_evidence_ids`, 수명주기(`pending → running → ok|error|cancelled`)를
갖는다. 리드(LLM)가 내용을 정하지만 수명주기 전이는 코드만 쥔다.

**Hypothesis** — 리드가 세운 가설. `status`(`open`/`supported`/`refuted`) +
그 판단의 근거인 증거 id 목록.

**Verdict** (`src/domain/case.py`) — 조사의 최종 판정. `verdict_type`(7종 —
`logic_bug`/`data_loss`/`config_error`/`stale_data`/`external`/`inconclusive`/
`degraded`), 인과 사슬(`root_cause` + `contributing[]`), `confidence`
(`high`/`medium`/`low`), 조치 권고(`recommendations`), 유보 사항(`caveats`).
`inconclusive`/`degraded`가 아니면 `root_cause`가 필수다.

**CauseLink** — 판정의 인과 사슬 한 마디. 토폴로지의 서비스/locator를
가리키는 `component` + 그 주장을 뒷받침하는 `evidence_ids`.

**EvidenceRef** — State(조사 엔진의 작업 메모리)에 남는 증거 참조. 본문은
케이스 Store에 있고, 여기엔 id/출처(`source`)/요약/`as_of`/`complete`
(완전성)/`effective_as_of`만 남는다.

**EvidenceRecord** — 케이스 Store가 실제로 들고 있는 증거의 저장 형태
(본문 + 봉투 메타). `store.get_evidence_record(case_id, evidence_id)`로
조회한다. execute 노드는 서브에이전트가 스스로 적은 id를 신뢰하지 않고,
이 실측 레코드로 다시 조회해 `EvidenceRef`를 만든다.

## 결과 봉투

**Envelope** (`src/domain/envelope.py`) — 대상 시스템 호출 하나의 메타
정보. "요청한 것"과 "실제로 얻은 것"의 차이를 표현한다. `complete=False`면
**우리가 본 것이 전부가 아니다**는 뜻이고, 그때는 `truncated_reason`이 필수다.
두 갈래가 있다 — 상한(`max_rows` 등)에 결과가 잘렸거나, 해석기가 카디널리티로
질문의 범위를 좁혀 물었거나(`line_code: 500개 중 50개만 사용`). 둘 다 "부정
증거로 결론 금지"라는 같은 규율에 걸린다. `effective_as_of`는 요청한 시점(`requested_as_of`)과 실제로 달성한
시점이 다를 때(예: Kafka 보존 밖이라 더 나중 데이터로 폴백) 명시된다.

**해석기(resolver)** (`src/patrol/resolvers.py`) — 등재 항목 호출의 파라미터
값을 실행 시점에 살아 있는 소스에서 읽어 채우는 것. config는 값이 아니라 값의
**출처**만 선언한다(`from`: `rest`/`mongo`/`redis`/`clock`/`unfiltered`). 값을
config에 적으면 사업부·법인마다 다르고 매일 바뀌어 즉시 썩기 때문이다.

**전부-또는-전무(all-or-nothing)** — 해석기가 하나라도 값을 못 내면 대상을
호출조차 하지 않는다. 빈 필터로 나간 요청은 endpoint에 따라 `0/0/0`(거짓 경보)이
되기도 하고 전체 조회(거짓 안심)가 되기도 하는데, 응답만 봐서는 어느 쪽인지
구별할 수 없다. 이 규율은 호출자 규율이 아니라 `ResolveResult`의 반환 타입이
지킨다(실패하면 `params`가 비어서 나온다).

**unfiltered** — "이 키를 일부러 안 보낸다"는 명시적 선언. 해석 실패로 우연히
전체 조회에 도달한 것과 구별하기 위해 존재하며, 증거의 `request.unfiltered`에
그 키 목록이 남는다.

**ProbeResult** — `Envelope` + 실제 데이터(`data`)를 담은, 프로브 호출의
전체 반환값. `status`(`ok`/`error`)가 `error`면 `error` 원인 문자열이 필수다.

**as_of 3겹** — 이 시스템에서 "언제 시점 기준인가"는 세 층에서 따로
따라다닌다: ① 데이터(Kafka 오프셋·Mongo 이력의 `as_of`), ② 코드
(deployment.yaml의 commit hash — 그 시점에 어떤 로직이 배포돼 있었는가),
③ 지식(토폴로지·룰·deployment의 digest — 조사 당시 지식 정의가 무엇이었는가).

## 판정 방식(judge)

**rule** — `src/patrol/rules.py`의 4종 결정론 규칙(`range`/`exists`/
`freshness`/`max`)으로만 판정. LLM 호출 없음.

**llm** — 매번 LLM에게 데이터를 보여주고 이상 여부를 묻는다(`llm_judge.py`).

**rule+llm** — 먼저 rule로 걸러 rule이 `ok`면 LLM을 부르지 않고 끝낸다
(예산 절약). rule이 finding을 내면 그때만 LLM에게 2차 확인을 시키고, 예산이
없으면 `on_budget_exhausted` 정책(`skip`/`escalate`)을 따른다.

## 조사 엔진

**리드(lead)** — frame/integrate/conclude 노드가 부르는 LLM(`llm.profiles.lead`).
가설·계획·다음 결정(continue/ask/conclude)·최종 판정을 만든다.

**서브에이전트(subagent)** — `data_prober`/`code_tracer`/`recompute_verifier`
3종. `llm.profiles.subagent`를 쓰는 유계 ReAct 루프(`create_agent` 기반).
→ [architecture.md §4](architecture.md#4-서브에이전트-3종)

**라운드(round)** — frame 이후 select→execute→integrate 한 바퀴.
`engine.max_rounds`에 도달하면 다음 integrate가 무조건 conclude로 강제
전환한다.

**select 게이트** — `status=pending`이고 `input_evidence_ids`가 전부
`state.evidence`에 실재하는 태스크만 그 라운드에 실행 가능하다.

**verify** — LLM 없이 결정론으로 판정의 증거 인용을 검사하는 노드. 문제가
있으면 conclude를 한 번 재작성시키고, 그래도 안 되면 확신도를 `low`로
낮춰 통과시킨다.

**interaction_policy** — `"interactive"`(chat — 질문이 생기면 그 자리에서
사람에게 묻는다) / `"autonomous"`(순찰이 연 케이스 — 질문을
`autonomous_question_policy`대로 자동 처리하거나 파킹한다).

**qa_log** — 조사 중 오간 문답(사람의 답, 자동 처리된 질문, 라운드 상한에
막혀 버려진 질문 등)을 시간순으로 쌓아 두는 State 필드. 보고서 §5(조사
경위)의 "QA 로그" 항목으로 나간다.

## 지식층

**토폴로지(topology)** (`src/knowledge/topology.py`) — 서비스가 무엇을
읽고 쓰는지(`services[].reads`/`writes`), 어떤 locator가 무엇으로부터
파생되는지(`derivations`)를 기술한 지식. `knowledge/topology/common.yaml` +
사이트별 오버레이.

**derivation(파생)** — `derivations`의 map 항목 하나. "이 locator 값은 이
입력들로부터 이 서비스를 거쳐 만들어진다"를 기술한다 — 이상 탐색 시
"파생 그래프를 거슬러 올라가는" 경로가 여기서 나온다.

**locator** — 데이터 한 조각을 가리키는 문자열. `"kind:나머지"` 형식
(`"rest:/api/v1/lines/{line}/oee"`, `"mongo:twin_state"`, `"redis:plan:6:today"`,
`"kafka:edge.raw.{line}"`).

점검의 `target`은 locator이거나 **등재 항목 이름**이다 — `rest:/path`는 토폴로지 locator, `rest:<이름>`은 `target.rest.entries`의 항목을 가리킨다(후자만 POST가 가능하다).

**deployment.yaml** (`src/knowledge/deployment.py`) — 사이트별로 "이
서비스가 지금 어떤 커밋으로 배포돼 있는가"를 기록한 매핑. 기동 검증이 그
커밋이 로컬 체크아웃에 실재하는지 확인한다.

**digest** (`src/knowledge/digest.py`) — 토폴로지·룰·deployment의 정규화된
해시. 케이스가 "조사 당시 지식 정의가 무엇이었는지"를 `Case.knowledge_digests`에
박제해 두는 데 쓴다.

## 이벤트·발행

**EngineEvent** (`src/domain/events.py`) — 엔진이 밖으로 내보내는 이벤트
봉투. `event`는 현재 6종(`case_status_changed`/`round_started`/
`task_finished`/`question_raised`/`report_ready`/`verdict_formed`). 스토어가
부여하는 `seq`로 전순서가 잡힌다 — 고정 시계 테스트에서 `at`이 같은 값을
가지므로 `at`만으로는 순서가 나오지 않는다.

**F1~F6** — v1 설계·구현 과정에서 명문화된 규율들의 줄임말(이월 항목 번호).
이벤트 봉투(F1), 단일 실행자 보장(F2), 배포를 가로지르는 재개(F3), 삼켜진
에러 보존(F4), 순찰 생존신호/하트비트(F5), 발송 멱등(F6 — pending→sent
2단계). 코드 주석·커밋 메시지에 이 번호로 언급되는 경우가 있다.

## 기타

**StrictModel** — `extra="forbid"`가 걸린 pydantic 베이스 모델. 모든 도메인·
config 모델이 상속한다. 알 수 없는 키는 조용히 무시되지 않고 검증 오류가 된다.

**무raise 규율** — 어댑터·프로브·rule·서브에이전트·워커·순찰·발행 전 층은
예외를 던지지 않고 실패를 상태(`status="error"` 등)로 흡수한다. 예외:
`CaseStorePort.get_evidence`의 `KeyError`(계약), `KnownRuleError`(rule
config 오류 전용), 워커의 레저 계층. → [CLAUDE.md](../CLAUDE.md)
