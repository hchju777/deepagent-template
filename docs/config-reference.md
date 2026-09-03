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
| `report.mail.recipients` | list[str] | `[]` | 수신자 목록 |
| `report.mail.username` / `.password` | str \| null / SecretStr \| null | null | SMTP 인증(선택) |
| `report.mail.use_tls` | bool | `false` | TLS 사용 여부 |
| `timezone` | str | `"Asia/Seoul"` | 보고서·스케줄 표시에 쓰는 IANA 타임존 |

**기동 검증이 추가로 강제하는 것**(§4.6, `src/boot.py`): 활성 사이트 중
`judge`가 `"llm"`/`"rule+llm"`인 점검이 하나라도 있으면 `llm.profiles.judge`가
비어 있으면 안 되고(검사 10), 활성 사이트가 있고 `llm.profiles`(judge/
subagent/lead 중 하나라도)가 값을 갖고 있으면 env `LLM_API_KEY`가 반드시
있어야 한다(검사 11) — `LlmProfiles`의 세 필드가 전부 필수라 사실상 항상
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
| `target` | str \| null | null | 토폴로지 locator(예: `"rest:/api/v1/lines/{line}/oee"`). 기동 검증이 토폴로지에서 해석 가능한지 확인 |
| `probe` | str \| null | null | 프로브 레지스트리 이름을 명시. 없으면 `target`의 kind 접두사로 기본 선택(`rest→rest_get`, `redis→redis_get`, `mongo→mongo_recent`, `kafka→kafka_lag`) |
| `params` | dict | `{}` | 프로브·rule 판정에 넘길 파라미터(아래 "rule 판정 4종" 참고) |
| `sample` | int \| null | null | 조회 건수 상한(예: `mongo_recent`의 `limit`) |
| `on_budget_exhausted` | `"skip"` \| `"escalate"` | `"skip"` | llm/rule+llm 판정인데 `patrol.llm_budget`이 소진됐을 때 동작 |

**rule 판정 4종**(`src/patrol/rules.py`, `params.rule`로 선택):

| rule | 필수 params | 동작 |
|---|---|---|
| `range` | `field`, (`min`과 `max` 중 하나 이상) | 값이 `[min, max]` 밖이면 finding |
| `exists` | (`field` 선택 — 없으면 데이터 전체를 봄) | 값이 비었으면(`None`/빈 컨테이너) finding |
| `freshness` | `field`, `max_age_s` | `field`의 타임스탬프가 `max_age_s`보다 오래됐으면 finding |
| `max` | `field`, `max` | 값이 `max`를 넘으면 finding |

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

기동 검증(검사 7)이 이 커밋이 `target.code.repos`가 가리키는 로컬 체크아웃에
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

## 기동 검증 11개 항목 (`src/boot.py`)

`python -m src knowledge validate`(`--live` 옵션 포함)가 도는 전체 목록. 하나만
잘못돼도 죽지 않고 **전부 모아서** 보고한다.

1. `app.json` 파싱·스키마
2. `registry.json` 파싱·스키마
3. 활성 사이트별 config 3계층 병합 + env 참조 해석
4. 토폴로지 내부 정합성(`topology_problems`)
5. 각 점검의 `target`이 해석되는가 — `rest:/path`·`redis:`·`mongo:`·`kafka:`는 토폴로지 locator로, `rest:<이름>`은 `target.rest.entries`로 해석하고, 등재 항목이면 `params.body`가 그 항목의 닫힌 스키마를 통과하는지까지 본다
6. 토폴로지가 참조하는 서비스 `code.repo`가 사이트 config의 `target.code.repos`에 있는가
7. `deployment.yaml`의 `(repo, commit)`이 로컬 체크아웃에 실재하는가(정적, deployment 없으면 건너뜀)
8. Mongo 계정이 readonly 롤인가 — `--live` 지정 시에만, `adapters="real"` + 계정 있는 사이트만
9. 각 점검의 프로브가 레지스트리에서 해석 가능한가
10. llm/rule+llm 판정 점검이 있으면 `llm.profiles.judge` 필수
11. `llm.profiles`를 쓰는 활성 사이트가 있으면 env `LLM_API_KEY` 필수

검사 8만 `--live`(실제 접속) 필요, 나머지는 전부 정적 — "죽은 사이트가 기동을
막으면 역효과"라는 원칙과 양립하기 위해 기본은 정적 검사만 돈다.
