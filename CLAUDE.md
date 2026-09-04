# AI 작업 가이드

이 파일은 이 리포지토리에서 코드를 작성·수정하는 AI 에이전트(Claude Sonnet
등급을 기준으로 작성됐다)를 위한 규율이다. 사람 개발자에게도 그대로
유효하다. 여기 적힌 것들은 전부 30건 넘는 독립 리뷰 라운드를 거치며 실제로
깨졌던 지점들에서 나왔다 — "이렇게 하면 더 안전하다"는 추상적 권고가 아니라,
"이걸 안 지켜서 실제로 버그가 났었다"는 기록이다.

먼저 읽어야 할 것: [docs/architecture.md](docs/architecture.md)(시스템이
어떻게 배선돼 있는지)와
[docs/superpowers/specs/2026-09-02-ops-monitoring-design.md](docs/superpowers/specs/2026-09-02-ops-monitoring-design.md)
(왜 이렇게 설계됐는지, 단일 진실 소스).

## 절대 규율

### 1. 무raise(no-raise) — 어댑터부터 발행까지

어댑터·프로브·rule 판정기·게이트·서브에이전트·워커·순찰·발행(mail) 전
층은 **예외를 던지지 않는다**. 실패는 항상 반환값의 상태로 흡수한다
(`ProbeResult.status="error"`, `CheckOutcome.status="error"`,
`PlanTask.status="error"`, `AdmitResult.action="rejected"` 등). 각 층의
최외곽 `try/except Exception`이 "예상 밖" 예외까지 마지막 방어선으로 잡는다.

이유: 노드 하나가 raise하면 LangGraph superstep 전체가 죽는다. 순찰 잡
하나가 raise하면 스케줄러가 그 잡을 조용히 스케줄에서 빼버릴 수 있어, 순찰이
스스로 죽어도 아무도 모르는 상황이 된다.

**허용된 예외는 딱 셋뿐이다** — 새 예외를 추가하고 싶다면 이 셋과 같은
수준의 계약인지 먼저 의심하라:

- `CaseStorePort.get_evidence`의 `KeyError` — "그 id의 증거가 없다"는 계약 자체.
- `KnownRuleError`(`src/patrol/rules.py`) — rule **설정** 오류 전용
  (미지의 rule 이름, 필수 params 부재, 비수치 bound 등). 데이터 자체의
  이상(필드 부재, NaN 등)은 이게 아니라 `finding`으로 처리한다 — 값이
  이상한 것과 설정이 잘못된 것은 다른 문제이고, 후자를 조용히 finding으로
  삼키면 설정 실수가 매 순찰마다 "이상 탐지"로 둔갑한다.
- 워커의 `record_send`/`pending_sends`(레저 계층 계약).

새 코드를 작성할 때: 실패할 수 있는 지점을 만들었다면 그 결과를 함수의
반환 타입에 상태로 넣어라. `try/except`로 감싸는 것은 방어가 아니라
**계약**이다 — 무엇을 잡고 무엇으로 바꿔 돌려줄지 명시적으로 정하라.

### 2. 시계 주입 — `datetime.now()` 직접 호출 금지

`src/__main__.py`(CLI 경계) 밖에서는 `datetime.now()`를 직접 부르지 않는다.
모든 함수는 `clock: Callable[[], datetime]`을 받아서 쓴다. 테스트가 시간을
고정값으로 통제해 결정론을 유지하기 위해서다. 새 함수를 작성할 때 "지금
시각"이 필요하면 인자로 `clock`을 받게 하라 — 전역이나 기본 인자로
`datetime.now`를 박아 넣지 마라.

### 3. LLM이 인용한 evidence id를 신뢰하지 않는다

서브에이전트나 리드가 응답에 적은 evidence id는 **환각일 수 있다**. execute
노드는 서브에이전트가 실제로 도구를 통해 `store.put_evidence`로 만든 id를
`store.get_evidence_record`로 다시 조회해서만 `EvidenceRef`를 만든다
(`src/application/nodes.py`의 `execute`). "LLM이 말한 것"과 "실제로 일어난
일"을 항상 분리하고, State에 올라가는 것은 후자여야 한다.

같은 원칙이 verify 노드에도 있다: 판정이 인용할 수 있는 "우주"는
`state.evidence`(리드가 실제로 본 것)로 한정한다 — Store 전체(`has_evidence`)를
기준으로 삼으면, 리드가 본 적도 없는 id를 판정이 인용해도 통과해버린다.

### 4. 수명주기 전이는 코드만 쥔다

`PlanTask.status`, `Case`/`CaseRecord`의 상태 전이 같은 것들은 LLM 출력에서
그대로 받아 쓰지 않는다. `_sanitize_new_task`(`nodes.py`)처럼, LLM이 만든
객체는 수명주기 필드를 코드가 강제로 초기화한 뒤에만 State에 들인다 — LLM이
`"status": "running"`을 실어 select 게이트를 우회하는 식의 구멍을 막기
위해서다. 새로운 "LLM이 만드는 객체"를 State에 들일 때는 항상 이 소독
과정이 필요한지 먼저 검토하라.

### 5. StrictModel — 모든 모델은 `extra="forbid"`

`src/config/schema_app.py`의 `StrictModel`을 상속한다. 알 수 없는 키가
config나 도메인 객체에 조용히 섞여 들어가는 것을 pydantic이 즉시 검증
오류로 잡게 한다. 새 pydantic 모델을 추가할 때 `BaseModel`을 직접
상속하지 말고 `StrictModel`을 상속하라.

### 6. 통제 경계 — 코드가 정하는 것 vs LLM이 정하는 것

- **코드가 정한다**: 그래프 구조, 라운드 상한(`max_rounds`), 병렬 폭
  (`parallel_width`), select 게이트 조건, interrupt 위치, verify 규칙,
  이벤트 어휘(규율 7의 성질 시험), 발행 배선.
- **LLM이 정한다**: 가설 내용, 태스크가 무엇을 조사할지, 라운드마다
  continue/ask/conclude 중 무엇을 고를지, 최종 판정의 서술.

새 기능을 추가할 때 "이걸 LLM이 결정하게 할까, 코드가 고정할까"를 판단하는
기준: **재현 가능해야 하고, 상한이 있어야 하고, 감사(audit) 가능해야 하는
것**은 코드가 쥔다. 그 안에서 "무엇이 맞는 판단인가"를 요구하는 것만 LLM에게
맡긴다.

### 7. 이벤트 어휘는 좁게 유지한다 (현재 6종)

`EngineEvent.event`는 `case_status_changed`/`round_started`/`task_finished`/
`question_raised`/`report_ready`/`verdict_formed` 여섯 개다(`src/domain/events.py`).
그래프 내부 노드명이나 State 키가 이 봉투 밖으로 새 나가면 안 된다 —
구독자(CLI, 향후 웹 UI)가 엔진 내부 구현에 결합되기 때문이다.

새 종류를 더할지 판단하는 시험은 **개수가 아니라 성질**이다:

> **"이 이름이 그래프를 다시 배선해도 그대로 유효한가?"**

`verdict_formed`는 도메인 사실(Verdict가 생겼다)을 가리키므로 conclude/verify를
합치든 쪼개든 유효하다. `node_entered`·`state_patch`·`select_gate_evaluated`·
`stream_mode` 패스스루는 무효다 — 그래프 모양이 바뀌면 뜻이 사라진다.
먼저 기존 어휘로 표현할 수 없는지 검토하고, 정말 필요하면 **스펙 문서를 먼저
갱신한 뒤에** 늘려라. 기각한 예: `round_finished`(경계로 유도 가능),
`evidence_added`(`task_finished.evidence_ids`에 이미 있다), `hypothesis_updated`.

### 8. 케이스 종결의 세 경로는 반드시 같은 발행 배선을 쓴다

데몬 자동 진행, `chat`, `case resume` — 케이스가 닫히는 세 경로 전부
"보고서 파일을 먼저 쓰고, `report_ready` 이벤트를 내고, 메일이 켜져 있으면
발송한다"는 계약을 지켜야 한다. 각자 이 조립을 따로 베끼면 언젠가 하나가
빠뜨린다(실제로 `case resume`이 한동안 그랬다). `src/__main__.py`의
`_build_publisher`가 조립하는 `(on_event, on_closed)` 쌍을 그대로 재사용하라
— 새 종결 경로를 추가한다면 반드시 이 헬퍼를 거쳐야 한다.

### 9. 대상 시스템 읽기 전용은 등재제로 강제한다

`RestProberPort`에 `post`/`put`/`patch`/`delete`를 **만들지 않는다**. v1에서는
`get` 하나뿐이라 쓰기가 물리적으로 불가능했고, POST가 필요해진 뒤에도 그 성질을
잃지 않으려면 "임의의 메서드로 임의의 경로를 호출하라"가 표현 불가능해야 한다.

호출자는 `query(entry, params)`로 **등재 항목 이름**만 대고, 어떤 HTTP 메서드로
나갈지는 어댑터가 그 항목의 선언(`target.rest.entries`)을 보고 정한다. body는
항목의 닫힌 스키마를 통과해야 소켓에 나간다 — 메서드 수준에서 잃은 메커니즘의
정직한 대체물이 body 수준의 닫힌 스키마다.

**config가 권한이고, 대상의 자기 서술(OpenAPI 등)은 증거일 뿐이다.** 런타임에
명세를 읽어 등재 목록을 넓히는 코드를 만들지 마라 — 대상이 새 POST를 배포하면
우리 허용 범위가 자동으로 넓어지는 fail-open이 된다.

`read_only: true` 같은 플래그는 두지 않는다. 목록에 없으면 문이 안 열리므로
플래그가 중복이고, 끝점 수십 개에 플래그를 적으라고 하면 사람은 기동 검증을
통과시키려고 전부 `true`로 적는다 — 아무도 생각하지 않는 체크박스의 안전 가치는
0이다.

`tests/domain/test_ports.py`가 포트 표면을 단정한다. 산문 규율은 읽지 않으면
무력하므로 테스트가 지킨다.

## 언어 관례

- **코드 주석·문서(이 파일 포함)**: 한국어. WHY(비직관적 제약, 숨은 불변식,
  특정 버그의 우회, 놀랄 만한 동작)만 적고 WHAT은 적지 않는다 — 식별자
  이름이 이미 WHAT을 말해주지 않을 때만 주석을 단다. 라이브러리 오류
  메시지 원문 인용 등은 예외로 허용된다.
- **git 커밋 메시지**: 영어. `git log`를 보면 알 수 있듯 이 리포의 커밋
  메시지는 전부 영어로 돼 있다 — 코드 주석과는 다른 관례이니 섞지 말 것.
- 변수/함수/클래스 이름: 영어.

## 코드 지도

| 찾는 것 | 위치 |
|---|---|
| 그래프 배선 | `src/application/graph.py` |
| 노드 로직(frame/select/execute/integrate/ask_human/conclude/verify) | `src/application/nodes.py` |
| State 정의 | `src/application/state.py` |
| 서브에이전트 실행 | `src/application/subagents.py` |
| 접수(intake, 턴 단위) | `src/application/intake.py` |
| 사이트 축 해석 | `src/application/scope.py` |
| 케이스 개설(사람 경로) | `src/application/open_case.py` |
| 답변 라우팅(접수 vs 조사) | `src/application/answer.py` |
| 케이스 큐·워커(lease, resume, keepalive) | `src/application/worker.py` |
| 케이스 종결·정리 | `src/application/close.py` |
| 이벤트 매핑(State 변화 → EngineEvent) | `src/application/events.py` |
| 도메인 모델(Case/Verdict/Envelope 등) | `src/domain/` |
| 대상 시스템 포트(ABC) | `src/domain/ports.py` |
| 실제/스텁 어댑터 | `src/infrastructure/{redis,mongo,kafka,rest}_reader.py`, `stubs.py` |
| 어댑터 조립(stub↔real 전환점) | `src/infrastructure/factory.py` |
| Mongo 케이스 저장소 | `src/infrastructure/mongo_store.py` |
| 프로브 레지스트리 | `src/patrol/probes.py` |
| 파라미터 값 해석기(전부-또는-전무) | `src/patrol/resolvers.py` |
| rule 판정 6종 | `src/patrol/rules.py` |
| concern 축(system/operation) | `src/domain/concern.py` |
| 읽기 전용 순수 판정(끝점·body·집계) | `src/infrastructure/query_rules.py` |
| 이벤트 로그 포트·인메모리 구현 | `src/domain/events.py` |
| 종결 판정 스냅샷(retention보다 오래 산다) | `src/domain/snapshot.py` |
| LLM 판정 | `src/patrol/llm_judge.py` |
| 순찰 게이트(케이스 개설/첨부/억제) | `src/patrol/gate.py` |
| 순찰 데몬 조립 | `src/patrol/daemon.py` |
| 보고서 데이터 유도(단계 체크리스트) | `src/domain/report_model.py` |
| 보고서 렌더링(마크다운) | `src/presentation/report.py` |
| 보고서 렌더링(HTML, 기본 포맷) | `src/presentation/report_html.py` |
| 메일 발송(2단계 멱등) | `src/presentation/mail.py` |
| 토폴로지·배포·pinned OpenAPI 로더 | `src/knowledge/` |
| 명세 대조 판정(등재 항목·응답 필드) | `src/knowledge/target_api.py` |
| config 스키마·로더·병합 | `src/config/` |
| 기동 검증 항목 | `src/boot.py` |
| CLI | `src/__main__.py` |

## 테스트

`pytest tests/`로 전체 실행(`pytest.ini`가 `asyncio_mode = auto`를 켜 둬서
`async def test_*`를 그대로 쓸 수 있다). 테스트 트리는 `src/`와 계층별로
미러링돼 있다. → [tests/README.md](tests/README.md)에 더 자세한 철학이
있다. 핵심만 요약하면:

- **실제 시스템도, 실제 LLM도 요구하지 않는다.** Redis/Mongo/Kafka/REST는
  `src/infrastructure/stubs.py`의 `Stub*`, Mongo 백엔드 계약 테스트만
  `mongomock`을 쓴다. LLM은 `ScriptedLLM`(예약된 응답을 순서대로 재생)이나
  `GenericFakeChatModel`로 대체한다.
- **결정론이 최우선이다.** 시계는 항상 고정값을 주입하고, LLM 응답은
  대본으로 미리 정한다.
- **벤치 시나리오**(`tests/test_bench_scenarios.py`)는 스펙 부록 A의
  간판 시나리오를 점검→finding→케이스 개설→조사→판정→검증→발행까지
  전 구간 회귀로 재현한다. 채점은 `Verdict`의 구조화 필드(`root_cause.component`,
  `verdict_type`)만 본다 — 보고서 텍스트 문자열 매칭은 하지 않는다(템플릿을
  고칠 때마다 깨지는 벤치는 즉시 썩는다).
- 새 기능을 추가했다면 RED(실패 재현) → GREEN(픽스 후 통과)을 실제로
  둘 다 확인하라 — "고쳤다고 보고했지만 실제로는 안 고쳐졌다"는 사례가
  이 리포 리뷰 과정에서 실제로 있었다(재리뷰가 렌더링 결과를 직접
  검증해서야 드러남).

## 기동 거부 철학

`src/boot.py`의 `validate_boot`는 문제를 발견 즉시 죽지 않고 **전부 모아서**
`list[BootError]`로 돌려준다 — "밤에 조용히 틀리는 것보다 배포 시점에
시끄럽게 죽는 게 낫다"는 원칙. 새 검증 항목을 추가한다면 이 패턴을 따르라:
하나 실패해도 계속 다음 검증을 진행하고, 마지막에 전부 보고한다.

## 개발 워크플로우(SDD)

이 리포의 구현 계획들은 superpowers:subagent-driven-development 스킬로
진행됐다 — task-brief 추출 → 구현자 에이전트 파견 → 리뷰어 에이전트 파견 →
픽스 라운드 → 최종 전체 브랜치 리뷰 → 픽스 웨이브 순서. 큰 기능을 새로
추가한다면 이 워크플로우를 참고하라(`docs/superpowers/plans/`에 실제
집행된 계획 6건이 전부 남아 있다 — 각 계획 문서 자체가 태스크 단위 TDD
코드를 포함한 실례다). 작은 수정에는 이 무게가 필요 없다 — 언제 이
워크플로우가 필요한지는 사람 파트너와 상의하라.

## 흔한 실수 (실제로 있었던 것들)

- **null 삭제 버그**: deep-merge에서 "앞 계층이 비어 있으면 지름길로
  건너뛴다"는 최적화가 null 마커(명시적 삭제 의도)를 못 지우고 지나친 적이
  있다. deep-merge는 항상 전체 경로를 타야 한다.
- **Kafka fresh consumer의 빈 메타데이터**: `assign()`으로 붙인 직후 바로
  읽으면 파티션 메타데이터가 비어 있어 "빈 ok"를 돌려주는데, 실제로는
  데이터가 있다. `await consumer.topics()`로 먼저 메타데이터를 채워야 한다.
- **Mongo `aggregate()`의 await 누락**: 실구현 코루틴은 명시적으로
  await해야 한다 — 빠뜨리면 TypeError가 아니라 조용히 다른 실패로 번질 수
  있다.
- **env 참조 미치환**: `app.json`의 `${AGENT_MONGO_URL}` 같은 참조를 실제로
  치환하는 코드 경로를 빠뜨리면, Mongo 백엔드가 "설정했는데 안 켜진다"는
  형태로 조용히 고장 난다. `load_app_config`/`load_site_config` 양쪽 다
  `env`를 반드시 넘겨 받아야 한다.
- **round_hint가 매 호출마다 0으로 리셋**: resume 이후 라운드 번호를 이벤트에
  실으려고 새 변수를 스레딩했는데, 그 변수 자체가 매 `investigate_case`/
  `resume_case` 호출마다 새로 시작해 버렸다. 재개 시의 "진짜 현재 상태"는
  `graph.aget_state(config)`의 체크포인트에서 읽어야 한다 — 호출부가 별도로
  들고 다니는 카운터를 믿지 마라.
- **판정 로직보다 먼저 실행되는 가드**: "LLM이 없으면 건너뛴다" 같은 가드를
  "스냅샷은 항상 남아야 한다"는 원칙보다 먼저 배치하면, 그 원칙이 조용히
  깨진다. 가드는 실제로 막아야 하는 지점 바로 앞에 둬라 — 그보다 먼저
  두면 그 사이의 "항상 일어나야 하는 일"이 스킵된다.
- **고아 상태**: 그래프 밖에서 실패하면 케이스가 `investigating`으로 영원히
  남을 수 있다. 워커의 예외 처리 경로가 실패 시에도 상태를 정리하는지
  항상 확인하라.
- **"고쳤다"는 보고를 그대로 믿지 않는다**: 표 렌더링 픽스가 "됐다"고
  보고됐지만 실제로 mistune으로 렌더해 보니 안 됐던 사례가 있다. 텍스트를
  고쳤다는 자체 판단이 아니라, 실제 소비자(렌더러, 파서, 다른 프로세스)로
  직접 검증하라.
- **문서·주석이 주장하는 배선을 그대로 믿지 않는다**: 위 항목의 형제다.
  `docs/architecture.md`가 "데몬이 파킹 케이스를 자동 재개한다"고 적어 뒀지만
  데몬은 `resume_once`를 한 번도 부르지 않았고, `__main__.py`의 주석은
  "데몬과 같은 발행 배선"이라고 했지만 데몬에는 `on_event`가 안 넘어가
  프로덕션에서 엔진 이벤트가 전무했다. 둘 다 문서화 커밋에서 호출부를 확인하지
  않고 쓴 문장이고, `grep -rn "resume_once" src/` 한 번이면 드러났다. 문서가
  어떤 함수를 부른다고 하면 **호출부를 grep으로 확인하기 전까지 사실로 취급하지
  마라** — 특히 그 문서를 근거로 새 코드를 얹으려 할 때.

## 무엇이 범위 밖인가

코드를 수정하거나, 새 기능을 만들거나, 데이터 정합성을 능동적으로
바로잡는 **개발 시스템**(대상 시스템에 쓰기를 가하는 모든 것)은 v1 범위
밖이다. 이 시스템은 대상 시스템에 대해 완전 읽기 전용이며, 조사·판정·조치
"권고"까지만 한다 — 조치 실행은 사람의 몫이다. `recompute_verifier`의
재계산-대조 프리미티브만 향후 개발 시스템에서 재사용 가능하도록 설계돼
있다.
