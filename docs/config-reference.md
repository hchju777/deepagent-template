# Config 레퍼런스

모든 config 값은 파일 → deep-merge → `${ENV_KEY}` 해석 → pydantic 강타입
검증(`extra="forbid"`) 순서로 처리된다(`src/config/loader.py`). 문제는 첫
건에서 멈추지 않고 전부 모아 보고한다 — `python -m src knowledge validate`로
실제로 적용되기 전에 확인할 수 있다.

## 디렉터리 배치

```
<config-root>/
  app.json                              전역 config
  registry.json                         사이트 목록
  gbm/{gbm}.json                        사업부 계층
  factories/{fct}/common.json           시설 공통 계층
  factories/{fct}/{gbm}.json            시설×사업부 계층(선택)

<repo-root>/<knowledge.root>/           기본 "knowledge" — 사이트 config의 knowledge.root로 재지정 가능
  topology/common.yaml                  전역 토폴로지
  topology/{gbm}/{fct}.yaml             사이트별 토폴로지(선택, common과 deep-merge)
  deployment/{gbm}/{fct}.yaml           사이트별 배포 커밋 매핑(선택)
```

`--config-root`(기본 `config`)와 `--repo-root`(기본 `.`)는 모든 CLI
서브커맨드의 공통 옵션이다. 리포에는 실제로 동작이 검증된 예시 트리가
`config.example/` + `knowledge.example/`로 들어 있다.

## 사이트 config 계층 병합

`load_site_config(config_root, gbm, fct, env=...)`가 다음 3개 파일을 이
순서로 deep-merge한다(뒤가 이김, `src/config/loader.py`의 `_SITE_LAYERS`):

1. `gbm/{gbm}.json`
2. `factories/{fct}/common.json`
3. `factories/{fct}/{gbm}.json`

`python -m src config show --gbm <gbm> --fct <fct>`로 병합 결과와 **각 키가
어느 계층에서 왔는지**(provenance)를 함께 확인할 수 있다.

## `app.json` — 전역 config (`AppConfig`, `src/config/schema_app.py`)

전역 키가 사이트 계층에 섞이면 예산·상한이 사이트 수만큼 곱해지므로 반드시
`app.json`에만 둔다. `SecretStr` 필드는 `config show` 출력에서 자동 마스킹된다.

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `engine.max_rounds` | int | 6 | 조사 라운드 상한. 도달하면 무조건 conclude로 강제 전환 |
| `engine.parallel_width` | int | 3 | select가 한 라운드에 fan-out하는 태스크 수 |
| `engine.subagent_budgets.data_prober` | int | 8 | data_prober의 `recursion_limit` |
| `engine.subagent_budgets.code_tracer` | int | 6 | code_tracer의 `recursion_limit` |
| `engine.subagent_budgets.recompute_verifier` | int | 4 | recompute_verifier의 `recursion_limit` |
| `engine.autonomous_question_policy` | `"default_and_log"` \| `"park"` | `"default_and_log"` | 순찰이 연 케이스(autonomous)에서 리드가 질문할 때: 보수적 기본값으로 자동 답하고 로그만 남길지, 사람에게 파킹할지 |
| `investigations.max_concurrent` | int | 2 | 워커가 동시에 붙잡을 수 있는 케이스 수 |
| `investigations.awaiting_human_timeout_h` | int | 72 | 파킹된 케이스가 이 시간(시간 단위) 넘게 답을 못 받으면 타임아웃 종결 |
| `investigations.lease_ttl_s` | float | 900 | 케이스 임차(lease) 유효 시간(초). 조사 한 라운드보다 충분히 길어야 함 |
| `investigations.max_wall_clock_s` | float | 1800 | 조사 한 건의 벽시계 상한(초). keepalive가 lease를 무한 갱신하므로 이 상한이 없으면 멈춘 LLM 호출이 슬롯을 영구 점유한다 |
| `investigations.requeue_interval_s` | float | 30 | 열린 케이스 재스캔 간격(초). 다른 프로세스가 연 케이스를 데몬이 보게 한다 |
| `llm.profiles.judge` | str | **필수** | rule+llm/llm 판정에 쓰는 모델 이름 |
| `llm.profiles.subagent` | str | **필수** | 서브에이전트(data_prober 등)가 쓰는 모델 이름 |
| `llm.profiles.lead` | str | **필수** | frame/integrate/conclude(리드)가 쓰는 모델 이름 |
| `patrol.llm_budget.max_calls_per_hour` | int | 30 | 순찰이 시간당 쓸 수 있는 LLM 호출 상한(llm/rule+llm 판정용) |
| `patrol.self_check_errors` | int | 3 | 같은 점검이 연속 이 횟수 이상 error를 내면 자기 감시가 이상으로 인지 |
| `store.backend` | `"memory"` \| `"mongo"` | `"memory"` | 케이스/레저/체크포인트 영속화 백엔드. `memory`는 프로세스 종료 시 전부 사라짐 |
| `store.mongo_url` | str \| null | null | `backend="mongo"`일 때 필수. 보통 `${AGENT_MONGO_URL}` 참조 |
| `store.mongo_db` | str | `"deepagent"` | Mongo 백엔드일 때 사용할 DB 이름 |
| `store.retention.closed_case_evidence_d` | int | 90 | 닫힌 케이스의 증거/판정을 이 일수 후 비움 |
| `store.retention.ledger_d` | int | 30 | 순찰 레저 보존 일수 |
| `store.retention.checkpoint_ttl_d` | int | 14 | LangGraph 체크포인트 보존 일수 |
| `store.retention.sends_d` | int | 30 | 메일 발송 레저(F6 멱등 기록) 보존 일수 |
| `store.retention.events_d` | int | 30 | 이벤트 로그(`case_events`) 보존 일수 |
| `store.retention.snapshots_d` | int | 730 | 종결 판정 스냅샷 보존 일수. 사람 라벨은 몇 달 뒤에 오므로 다른 것들보다 훨씬 길다 |
| `report.output_dir` | str | `"output"` | 렌더된 보고서(`{case_id}.{format}`)를 쓰는 디렉터리 |
| `report.format` | `"html"` \| `"md"` | `"html"` | 보고서 산출 포맷. 메일은 HTML일 때 평문(마크다운)과 HTML 두 파트를 함께 보낸다 |
| `report.mail.enabled` | bool | `false` | 메일 발송 여부. `true`면 `host`/`recipients` 필수(검증자) |
| `report.mail.host` | str | `""` | SMTP 호스트 |
| `report.mail.port` | int | 25 | SMTP 포트 |
| `report.mail.sender` | str | `""` | 발신자 주소 |
| `report.mail.recipients` | list[str] | `[]` | 기본 수신자 목록 |
| `report.mail.recipients_by_concern` | dict | `{}` | concern별 수신자(`{"operation": ["ops@x"]}`). 선언되지 않은 concern은 `recipients`로 폴백한다 — 전부 적으라고 강제하면 같은 목록을 두 번 쓰게 되고 한쪽만 고치는 순간 갈라진다. 알 수 없는 키(`"operations"` 같은 오타)는 config 검증이 거부한다. **빈 목록은 "보내지 마라"가 아니다** — 폴백으로 간다 |
| `report.mail.username` / `.password` | str \| null / SecretStr \| null | null | SMTP 인증(선택) |
| `report.mail.use_tls` | bool | `false` | TLS 사용 여부 |
| `timezone` | str | `"Asia/Seoul"` | 보고서·스케줄 표시, **그리고 `clock` 해석기의 날짜 경계**를 정하는 IANA 타임존. `today`가 어느 날인지가 이 값으로 갈린다 — 해석 실패면 기동을 거부한다 |

**기동 검증이 추가로 강제하는 것**(§4.6, `src/boot.py`): 활성 사이트 중
`judge`가 `"llm"`/`"rule+llm"`인 점검이 하나라도 있으면 `llm.profiles.judge`가
비어 있으면 안 되고(검사 17), 활성 사이트가 있고 `llm.profiles`(judge/
subagent/lead 중 하나라도)가 값을 갖고 있으면 env `LLM_API_KEY`가 반드시
있어야 한다(검사 18) — `LlmProfiles`의 세 필드가 전부 필수라 사실상 항상
해당된다.

## `registry.json` — 사이트 목록

```json
{ "sites": [ { "gbm": "mx", "fct": "gumi", "enabled": true } ] }
```

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `sites[].gbm` | str | — | 사업부 코드 |
| `sites[].fct` | str | — | 시설 코드 |
| `sites[].enabled` | bool | `true` | `false`면 기동 검증·순찰·`assemble_sites` 전부에서 건너뜀 |

## 사이트 config (`SiteConfig`, `src/config/schema_site.py`)

### `target` — 대상 시스템 접속 정보 (`TargetConfig`)

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `target.adapters` | `"stub"` \| `"real"` | `"stub"` | **스텁↔실구현 전환의 유일한 스위치**(`build_adapters`) |
| `target.redis.url` | str | — | `redis.asyncio` 연결 URL |
| `target.redis.password` | SecretStr \| null | null | 비밀번호 있는 법인만 |
| `target.mongo.url` | str | — | Mongo 연결 URL |
| `target.mongo.username` / `.password` | str \| null / SecretStr \| null | null | 계정 있는 법인만(Mongo는 URL과 별개로 계정 ID가 필요할 수 있음) |
| `target.mongo.db` | str | `"twin"` | 대상 시스템의 DB 이름(`RealMongo` 필수 인자) |
| `target.kafka.bootstrap` | str | — | Kafka bootstrap 서버 |
| `target.rest.base_url` | str | — | REST API 베이스 URL |
| `target.rest.auth.header` | str | — | 대상 API가 요구하는 인증 헤더 이름(예: `x-dep-ticket`) |
| `target.rest.auth.value` | SecretStr | — | 그 헤더의 값. 반드시 `${ENV}` 참조로 준다 — 리터럴 금지 |
| `target.rest.entries.<이름>.method` | `"GET"` \| `"POST"` | `"GET"` | 이 항목을 호출할 HTTP 메서드. **쓰기 메서드는 등재할 수 없다** |
| `target.rest.entries.<이름>.path` | str | **필수** | base_url 기준 경로. `/`로 시작해야 하고 `?`·`#`·`%`·`..`·`;`·`\`를 쓸 수 없다(config 검증에서 거부) |
| `target.rest.entries.<이름>.body_schema` | dict[str, 타입] | `{}` | POST body의 닫힌 스키마. 타입은 `str`/`int`/`float`/`bool`/`list[str]`/`list[int]`. GET 항목에는 둘 수 없다 |
| `target.rest.openapi_path` | str | `"/openapi.json"` | `--live` 드리프트 점검이 명세를 받아 올 경로. 등재 항목 `path`와 같은 규칙(절대 경로·호스트 불가) — 아니면 인증 헤더가 다른 호스트로 나간다 |
| `target.rest.entries.<이름>.query_schema` | dict[str, 타입] | `{}` | GET 항목의 쿼리 파라미터 닫힌 스키마(키+타입). body_schema와 같은 타입 어휘. 목록 밖 키·타입 불일치는 소켓 전에 거부된다. POST 항목에는 둘 수 없다 |
| `target.code.repos[].name` / `.path` | str / str | — | `code_tracer`가 읽을 로컬 git 체크아웃들. `name`은 토폴로지·deployment.yaml이 참조하는 식별자 |
| `target.guards.timeout_s` | float | 10 | 어댑터 호출 타임아웃 |
| `target.guards.max_rows` | int | 1000 | 조회 결과 상한(넘으면 `complete=False`) |
| `target.guards.max_concurrent` | int | 4 | 이 사이트에 대한 **모든 어댑터가 공유하는** 동시 요청 상한(세마포어 하나) |

인증 필드는 전부 선택이다 — 인증 없는 법인은 `url`만 채우고, 있는 법인만
`username`/`password`를 추가한다.

### `patrol.checks.<name>` — 순찰 점검 (`CheckConfig`)

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `judge` | `"rule"` \| `"llm"` \| `"rule+llm"` | **필수** | 판정 방식 |
| `schedule.interval` | str(`^\d+[smh]$`) | — | `interval`/`cron` 중 정확히 하나. 예: `"30s"`, `"5m"`, `"1h"`. 0은 불가 |
| `schedule.cron` | str(5필드) | — | 표준 5필드 cron 표현식 |
| `target` | str \| null | null | 토폴로지 locator(예: `"rest:/api/v1/lines/{line}/oee"`) 또는 등재 항목 이름(`"rest:summary_prod"`). 기동 검증이 각자의 이름공간에서 해석 가능한지 확인. **`{자리표시자}`가 든 locator를 `rest_get` 점검의 target으로 쓰면 그 문자열이 그대로 전송된다** — 토폴로지 패턴은 "이 모양의 끝점이 허용된다"는 뜻이지 값을 채워 주지는 않는다. 실제 값이 필요하면 자리표시자 없는 구체 경로를 쓰거나, 등재 항목(`target.rest.entries`) + `resolve`로 표현한다 |
| `probe` | str \| null | null | 프로브 레지스트리 이름을 명시. 없으면 `target`의 kind 접두사로 기본 선택(`rest:/path→rest_get`, `rest:<이름>→rest_query`, `redis→redis_get`, `mongo→mongo_recent`, `kafka→kafka_lag`) |
| `params` | dict | `{}` | 프로브·rule 판정에 넘길 파라미터(아래 "rule 판정 6종" 참고) |
| `concern` | `"system"` \| `"operation"` | `"system"` | 무엇이 이상한가 — 메일 수신자·브리핑 방향·보고서 헤더가 이 값을 따른다. `system`은 파이프라인 고장(Kafka lag·TTL 만료·5xx), `operation`은 데이터는 흐르는데 현장이 이상한 경우(0/0/0·NO PLAN). **사람이 적는다**: 라우팅 근거는 재현·감사 가능해야 한다 |
| `sample` | int \| null | null | 조회 건수 상한(예: `mongo_recent`의 `limit`) |
| `on_budget_exhausted` | `"skip"` \| `"escalate"` | `"skip"` | llm/rule+llm 판정인데 `patrol.llm_budget`이 소진됐을 때 동작 |
| `resolve.<키>.from` | `"rest"` \| `"mongo"` \| `"redis"` \| `"clock"` \| `"unfiltered"` | **필수** | 값을 어디서 읽을지. 값 자체를 config에 적으면 즉시 썩는다(사업부/법인마다 다르고 매일 바뀐다). `params.body`와 키가 겹치면 기동 거부 |
| `resolve.<키>.entry` / `.field` | str / str | `from="rest"`일 때 필수 | 부를 등재 조회 항목(**GET이어야 한다** — 기동 검증이 강제)과 뽑을 필드 |
| `resolve.<키>.collection` / `.field` / `.filter` | str / str / dict | `from="mongo"`일 때 앞 둘 필수 | 조회할 컬렉션·필드·필터 |
| `resolve.<키>.pattern` | str | `from="redis"`일 때 필수 | scan 패턴. 값은 **키 목록**이다 |
| `resolve.<키>.expr` | `"today"` \| `"yesterday"` \| `"now_iso"` | `from="clock"`일 때 필수 | 주입된 시계로 만든다(`datetime.now()`를 직접 부르지 않는다) |
| `resolve.<키>.cardinality` | `"all"` \| `"first:N"` \| `"sample:N"` | `"all"` | 값이 많을 때 자를 방식. 자르면 증거가 `complete=False`로 나가 verify가 "불완전 증거의 부정 결론"을 막는다 |

`from="unfiltered"`는 **의도한 전체 조회**를 명시한다 — 그 키를 아예 보내지 않는다.
해석 실패로 우연히 전체 조회에 도달하는 경로와 구별하기 위한 어휘다.

**전부-또는-전무**: 해석기가 하나라도 값을 못 내면 대상을 **호출조차 하지 않고**
`CheckOutcome(status="error")`가 된다. finding이 아니다 — 우리 쪽 실패가 "현장 이상"으로
둔갑하면 매 순찰이 거짓 경보가 된다. 빈 필터로 나간 요청은 endpoint에 따라 `0/0/0`(거짓
경보)이 되기도 하고 전체 조회(거짓 안심)가 되기도 하는데, 어느 쪽인지 알 방법이 없다.


**rule 판정 6종**(`src/patrol/rules.py`, `params.rule`로 선택):

| rule | 필수 params | 동작 |
|---|---|---|
| `range` | `field`, (`min`과 `max` 중 하나 이상) | 값이 `[min, max]` 밖이면 finding |
| `exists` | (`field` 선택 — 없으면 데이터 전체를 봄) | 값이 비었으면(`None`/빈 컨테이너) finding |
| `freshness` | `field`, `max_age_s` | `field`의 타임스탬프가 `max_age_s`보다 오래됐으면 finding |
| `max` | `field`, `max` | 값이 `max`를 넘으면 finding |
| `all_zero` | `field`, (`min_count` 기본 1) | 값(리스트·dict·스칼라)이 **전부 0**이면 finding. 빈 표본과 `min_count` 미만은 **다른 사유**의 finding — "질문을 잘못했다"와 "현장이 멈췄다"를 섞지 않는다. bool·NaN은 수치가 아니므로 데이터 이상 |
| `expected_state` | `field`, `expect`(목록), (`when` 선택) | `field` 값이 `expect`에 없으면 finding("생산중이어야 하는데 NO PLAN"). `when`(`{field, equals}` 두 키 고정)이 성립할 때만 판정하고, `when.field`가 없으면 **판정 불가 finding** — ok로 삼키면 그 점검은 영영 초록으로 남는다. 상태 값은 **문자열을 전제한다**: 비교가 `==`이라 `expect: [0]`은 `false`와도 맞고, `null`은 "필드 없음"과 구별되지 않아 표현할 수 없다 |

앞 넷은 파이프라인 신호(`concern: "system"`)를, 뒤 둘은 현장 상태
(`concern: "operation"`)를 본다. `expect`는 **값 목록이지 표현식이 아니다** —
비교 연산자·정규식을 열면 rule이 작은 질의 언어가 되고 config가 코드가 된다.

`field`는 점 표기(`"body.oee"`, `"items.0.name"`)로 중첩 dict/list를 가리킨다.
`rule` 자체가 미지의 값이거나 필수 params가 없거나 타입이 안 맞으면(예:
`min`이 문자열) 이는 **데이터 이상이 아니라 config 오류**이므로 finding 대신
기동 후 실행 시점에 error로 레저에 남는다(`KnownRuleError`).

### `knowledge`

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `knowledge.root` | str | `"knowledge"` | `<repo-root>/<이 값>`이 토폴로지·deployment 루트가 된다 |

## `knowledge/` — 토폴로지·배포 지식

`topology/common.yaml` (+ 선택적 `topology/{gbm}/{fct}.yaml`, deep-merge)의
형태:

```yaml
services:
  <service-name>:
    code: { repo: <target.code.repos의 name>, path: <레포 내 경로> }   # 선택
    reads:  [ { kind: kafka|redis|mongo|rest, topic|key|collection|endpoint: ... } ]
    writes: [ { kind: kafka|redis|mongo|rest, ... } ]
derivations:
  "<locator>":                    # 예: "rest:/api/v1/lines/{line}/oee", "mongo:twin_state"
    inputs: [ { kind: ..., ... } ]
    via: <service-name>
    key: <fan-in 시 사용하는 키 이름>   # 예: "line"
```

`derivations`는 **map**이다(리스트가 아니다) — key는 그 파생값의 locator
문자열. `CheckConfig.target`과 `code_tracer`가 추적하는 upstream 조각이 이
그래프를 따라간다.

`deployment/{gbm}/{fct}.yaml`(선택, 없으면 관련 검사를 건너뜀):

```yaml
services:
  <service-name>: { repo: <name>, commit: <git commit hash> }
```

기동 검증(검사 13)이 이 커밋이 `target.code.repos`가 가리키는 로컬 체크아웃에
실제로 존재하는지(`git cat-file`) 확인한다.

## `.env` — 비밀값

`config/*.json`에는 `${ENV_KEY}` 참조만 쓰고, 실제 값은 절대 커밋되지 않는
`.env`(`.gitignore`됨)에 둔다. 값이 없거나 빈 문자열이면 로드 시점에
`ConfigError`로 걸린다. 키 목록 문서는 `.env.example`:

| 키 | 용도 |
|---|---|
| `AGENT_MONGO_URL` | 에이전트 자신의 저장소(`store.backend="mongo"`일 때) |
| `LLM_BASE_URL` | OpenAI 호환 LLM 게이트웨이 |
| `LLM_API_KEY` | 위 게이트웨이 인증키 — `llm.profiles`를 쓰는 활성 사이트가 있으면 기동 검증이 필수로 요구 |
| `{GBM}_{FCT}_REDIS_URL` / `_REDIS_PASSWORD` | 사이트별 Redis. 인증 없으면 URL만 |
| `{GBM}_{FCT}_MONGO_URL` / `_MONGO_USER` / `_MONGO_PASSWORD` | 사이트별 Mongo. 읽기 전용 계정 권장 |
| `{GBM}_{FCT}_KAFKA_BOOTSTRAP` | 사이트별 Kafka |
| `{GBM}_{FCT}_API_BASE` | 사이트별 REST base URL |

명명 규약은 강제되는 스키마가 아니라 관례다 — 실제로 어떤 env 키를 참조하는지는
각 사이트 config의 `${...}` 값이 결정한다.

## 기동 검증 항목 (`src/boot.py`)

`python -m src knowledge validate`(`--live` 옵션 포함)가 도는 전체 목록. 하나만
잘못돼도 죽지 않고 **전부 모아서** 보고한다. (개수를 제목에 적지 않는 이유:
항목이 늘 때마다 이 문서가 조용히 낡는다 — 실제로 그랬다.)

1. `app.json` 파싱·스키마
2. `app.timezone`이 해석 가능한 IANA 타임존인가 — `clock` 해석기의 날짜 경계가 이 값으로 정해지므로 오타가 나면 매일 하루씩 어긋난 질문이 나간다
3. `registry.json` 파싱·스키마
4. 활성 사이트별 config 3계층 병합 + env 참조 해석
5. 토폴로지 내부 정합성(`topology_problems`)
6. 각 점검의 `target`이 해석되는가 — `rest:/path`·`redis:`·`mongo:`·`kafka:`는 토폴로지 locator로, `rest:<이름>`은 `target.rest.entries`로 해석하고, 등재 항목이면 `params.body`가 그 항목의 닫힌 스키마를 통과하는지까지 본다
7. `resolve`가 있으면 target이 등재 항목인가 — 다른 target에 달면 런타임이 조용히 무시한다
8. `resolve`의 각 키가 등재 항목 스키마에 있는가, 그리고 해석기 **모양**이 그 타입과 맞는가 — `clock`은 문자열 하나, 소스 해석기는 리스트다
9. `from: "rest"` 해석기가 가리키는 항목이 실재하고 GET인가 / `mongo`·`redis` 해석기의 어댑터가 설정돼 있는가 / `mongo` 해석기의 `filter` 연산자가 허용 목록 안인가
10. 등재 항목이 pinned 명세(`knowledge/target_api/{gbm}/{fct}.json`)와 맞는가 — 항목 실재·스키마 키·타입·명세가 필수라 한 키. **명세는 검증만 하고 넓히지 않는다**(명세에만 있는 키는 문제가 아니다). 명세가 없는 것은 오류가 아니고, 있는데 깨진 것이 오류다
11. rule 점검이 보는 `body.<키>`가 명세가 말한 응답에 있는가 — 명세가 응답 모양을 말하지 않았으면 아무 판정도 하지 않는다
12. 토폴로지가 참조하는 서비스 `code.repo`가 사이트 config의 `target.code.repos`에 있는가
13. `deployment.yaml`의 `(repo, commit)`이 로컬 체크아웃에 실재하는가(정적, deployment 없으면 건너뜀)
14. Mongo 계정이 readonly 롤인가 — `--live` 지정 시에만, `adapters="real"` + 계정 있는 사이트만
15. 지금 대상이 내놓는 명세가 pin과 같은가 — `--live` 지정 시에만, pin이 있는 사이트만. 우리 등재 항목에 영향을 주는 차이만 보고한다. **명세를 못 받는 것(연결 실패·4xx·비JSON 응답)도 기동을 막는다** — `--live`를 켠 사람은 "지금 실제와 맞는가"를 묻고 있고, 못 물어본 것을 조용히 통과시키면 확인 안 한 것이 "이상 없음"으로 둔갑한다. 검사 14(Mongo 롤)가 같은 형태다
16. 각 점검의 프로브가 레지스트리에서 해석 가능한가
17. llm/rule+llm 판정 점검이 있으면 `llm.profiles.judge` 필수
18. `llm.profiles`를 쓰는 활성 사이트가 있으면 env `LLM_API_KEY` 필수

검사 14·15만 `--live`(실제 접속) 필요, 나머지는 전부 정적 — "죽은 사이트가 기동을
막으면 역효과"라는 원칙과 양립하기 위해 기본은 정적 검사만 돈다.
