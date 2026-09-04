# 스텁 → 실구현 전환 가이드

리포를 처음 받으면 모든 사이트가 `target.adapters: "stub"`로 동작한다 —
대상 시스템이 전혀 없어도 그래프·CLI·순찰 배선을 확인할 수 있게 하기 위해서다.
실제 디지털 트윈 시스템에 붙이려면 아래를 순서대로 따라간다.

## 1. 접속 정보 채우기

사이트 config(`config/gbm/{gbm}.json` 등)의 `target`에서 필요한 대상만
채운다(전부 필요한 게 아니다 — 그 사이트가 실제로 갖고 있는 미들웨어만):

```json
{
  "target": {
    "adapters": "real",
    "redis": { "url": "${MX_GUMI_REDIS_URL}" },
    "mongo": { "url": "${MX_GUMI_MONGO_URL}", "db": "twin" },
    "kafka": { "bootstrap": "${MX_GUMI_KAFKA_BOOTSTRAP}" },
    "rest":  {
      "base_url": "${MX_GUMI_API_BASE}",
      "auth": { "header": "x-dep-ticket", "value": "${MX_GUMI_API_TOKEN}" },
      "entries": {
        "summary_prod": {
          "method": "POST", "path": "/summary/prod",
          "body_schema": { "part_code": "list[str]", "line_code": "str" }
        },
        "mes_plan": { "method": "GET", "path": "/mes/plan", "query_schema": {"date": "str"} }
      }
    },
    "code":  { "repos": [ { "name": "twin-services", "path": "/opt/repos/twin-services" } ] }
  }
}
```

`target.adapters`를 `"real"`로 바꾸는 것이 스텁↔실구현 전환의 스위치다
(`src/infrastructure/factory.py`의 `build_adapters`). 예시 트리를 복사해
시작했다면 `target.stub_seeds`(스텁이 돌려줄 가짜 응답)도 같이 지워라 —
`adapters="real"`에서는 쓰이지 않으므로, 남겨 두면 기동 검증이 거부한다.
**점검 간격도 같이 올려라** — 예시의 `interval: "3s"`는 빠른 시작이 5초 안에
끝나라고 낮춰 둔 값이고, 그대로 실전환하면 운영 대상을 3초마다 두드린다. 비밀번호가
있는 법인만 `redis.password`/`mongo.username`+`mongo.password`를 추가한다
(둘 다 `${ENV_KEY}` 참조로).

`.env`(gitignore됨)에 실제 값을 채운다. 값이 비어 있거나 없으면 config
로드 시점에 바로 걸린다 — 운영 중 조용히 실패하는 게 아니라 기동이 거부된다.

```bash
MX_GUMI_REDIS_URL=redis://prod-redis.internal:6379/0
MX_GUMI_MONGO_URL=mongodb://prod-mongo.internal:27017
MX_GUMI_MONGO_USER=twin_reader
MX_GUMI_MONGO_PASSWORD=<실제 비밀번호>
MX_GUMI_KAFKA_BOOTSTRAP=prod-kafka.internal:9092
MX_GUMI_API_BASE=https://prod-twin-api.internal
MX_GUMI_API_TOKEN=<실제 토큰>          # rest.auth.value가 참조한다
```

`target.code.repos[].path`는 로컬에 체크아웃된 실제 git 레포 경로다 —
`code_tracer`가 여기서 `git show`/`git grep`으로 소스를 읽는다(유일한 sync
포트, git subprocess 기반). CI/배포 환경이라면 이 경로에 대상 서비스
레포들을 미리 `git fetch`해 둬야 한다.

## 2. Mongo 계정은 반드시 readonly로

`src/infrastructure/query_rules.py`가 읽기 전용을 "선언"이 아니라
"메커니즘"으로 강제한다: `aggregate` 파이프라인은 허용된 스테이지
(`$match`/`$project`/`$group`/`$sort`/`$limit`/`$skip`/`$count`/`$unwind`)만
통과하고, `$out`/`$merge`/`$function`/`$accumulator`/`$where`(쓰기 또는 JS
실행)는 중첩된 위치에 있어도 재귀적으로 걸러진다. filter도 허용 연산자
목록(`$eq`/`$ne`/`$gt`/`$gte`/`$lt`/`$lte`/`$in`/`$nin`/`$exists`/`$regex`/
`$options`/`$and`/`$or`)만 통과한다.

그래도 **DB 계정 자체를 `read`/`readAnyDatabase` 롤로 만드는 것을
강력히 권장한다** — 코드 레벨 필터는 방어선이지 신뢰의 근거가 아니다.
계정이 실제로 readonly인지는 기동 검증이 확인해 줄 수 있다:

```bash
python -m src knowledge validate --live --config-root config --repo-root .
```

`--live`는 실제 접속이 필요하므로 기본으로는 돌지 않는다(죽은 사이트가
기동 자체를 막지 않도록). CI에 대상 시스템 접근 권한이 있을 때만 켜라.

Kafka는 `assign()`으로 파티션에 직접 붙어 컨슈머 그룹에 참여하지 않는다 —
운영 중인 컨슈머 그룹의 오프셋에 영향을 주지 않는다.

### REST 등재 항목 승인 — 사람이 읽는 것이 안전의 근거다

REST는 두 경로로만 나간다:

1. **토폴로지에 등록된 끝점의 GET** — 서브에이전트가 조사 중 부른다. 쿼리
   파라미터를 붙일 수 없다.
2. **`target.rest.entries`에 등재된 항목** — 순찰 점검이 `target: "rest:<이름>"`으로
   부른다. **POST가 가능한 유일한 경로다.**

등재제가 "완전 읽기 전용"을 지키는 방식은 자동 판정이 아니라 **사람의 승인**이다.
목록이 짧아서 실제로 읽히는 것이 안전의 근거이므로, 실운영 전환 시 다음을
**한 항목씩 눈으로 확인**하라:

- [ ] 이 항목의 `path`가 **정말 읽기 전용 끝점인가.** 대상 팀에 확인했는가?
      HTTP 메서드는 그것을 말해주지 않는다 — `POST /summary/prod`는 읽기지만
      `POST /plan/update`는 쓰기이고, 코드는 둘을 구별할 수 없다.
- [ ] `body_schema`가 **실제로 보낼 키만** 담고 있는가. 여유분을 넣지 마라 —
      스키마에 있는 키는 나갈 수 있는 키다.
- [ ] `query_schema`도 마찬가지 — 키와 타입 둘 다. 목록에 없는 키는 소켓 전에 거부된다.
- [ ] 해석기(`resolve`)가 이 항목을 조회용으로 가리킨다면 그 항목도 **읽기 전용인가.**
      기동 검증이 GET임을 강제하지만, GET이라고 부수효과가 없다는 보장은 아니다.
- [ ] 이 항목을 **실제로 참조하는 점검이 있는가.** 아무도 안 쓰는 등재 항목은
      "아무도 생각하지 않는 체크박스"와 같아서, 승인의 의미를 희석시킨다.

`git diff`로 `entries` 변경을 리뷰하는 것이 이 승인의 형태다 — 등재 목록은
코드가 아니라 **결정의 기록**이다.

`path`는 `/`로 시작하는 상대 경로여야 하고 `?`·`#`·`%`·`..`·`;`를 쓸 수 없다.
config 검증에서 거부되므로 절대 URL로 `base_url`을 벗어나거나 쿼리를 경로에
숨기는 것은 불가능하다.

## 3. LLM 게이트웨이 연결

```bash
LLM_BASE_URL=https://your-gateway.internal/v1
LLM_API_KEY=<실제 키>
```

`app.json`의 `llm.profiles.judge`/`subagent`/`lead`에 실제 모델 이름을
채운다(OpenAI 호환 게이트웨이 기준, `src/infrastructure/llm.py`의
`build_chat_model` → `ChatOpenAI`). 세 프로파일 다 필수이므로, 게이트웨이가
모델별로 다른 이름을 쓴다면 여기서 매핑한다.

## 4. 영속화를 메모리에서 Mongo로

`store.backend: "memory"`(기본)는 프로세스가 죽으면 케이스·레저·체크포인트가
전부 사라진다. 운영 배포에는 반드시 Mongo로 바꾼다:

```json
{ "store": { "backend": "mongo", "mongo_url": "${AGENT_MONGO_URL}", "mongo_db": "deepagent" } }
```

`.env`에 `AGENT_MONGO_URL`을 채운다. 이 Mongo는 **에이전트 자신의 저장소**이지
대상 시스템의 Mongo가 아니다 — 완전히 별도의 DB(가능하면 별도 서버)를 쓰는
것을 권장한다. `store.retention.*`으로 보존 기한(닫힌 케이스 증거/판정,
순찰 레저, 체크포인트, 발송 레저 각각)을 조정할 수 있다.

## 5. 메일 발송 켜기(선택)

```json
{
  "report": {
    "mail": {
      "enabled": true,
      "host": "smtp.internal",
      "port": 587,
      "sender": "ops-agent@internal",
      "recipients": ["oncall@internal"],
      "username": "${SMTP_USER}",
      "password": "${SMTP_PASSWORD}",
      "use_tls": true
    }
  }
}
```

`enabled: true`인데 `host`나 `recipients`가 비어 있으면 기동 검증이 막는다
— 조용히 매번 SMTP 연결에 실패해 발송 대기열(pending)만 계속 쌓이는 상황을
막기 위해서다. 발송은 2단계 멱등(pending 기록 → 실제 전송 → sent로 표시)이라
프로세스가 중간에 죽어도 같은 보고서가 중복 발송되지 않는다.

## 6. 순찰 데몬을 상시 프로세스로

```bash
python -m src patrol run --config-root config --repo-root .
```

`--for-seconds` 없이 실행하면 무한히 돈다(포그라운드). systemd/컨테이너
등으로 감독하는 프로세스로 배포하고, 재시작 시 다시 `knowledge validate`가
자동으로 먼저 도는지(기동 거부 철학) 확인하라 — `_run_patrol`이 데몬을
띄우기 전에 항상 기동 검증부터 돈다.

## 체크리스트

- [ ] 사이트마다 필요한 대상만 `target`에 채우고 `adapters: "real"`, `target.stub_seeds`는 삭제
- [ ] **점검 간격을 예시의 `3s`에서 운영 값(분 단위)으로 올렸는가** — 예시 트리를 복사해 왔다면 그대로 두면 대상을 3초마다 두드린다
- [ ] `.env`에 실제 값(URL·계정·비밀번호) — `config/*.json`에는 `${...}` 참조만
- [ ] Mongo 계정을 readonly 롤로 생성하고 `knowledge validate --live`로 확인
- [ ] **`target.rest.entries`의 항목을 한 개씩 사람이 읽고 승인** — 각각이 정말
      읽기 전용 끝점인지 대상 팀에 확인했는가(§2의 승인 절차)
- [ ] 인증이 필요하면 `rest.auth` + `.env`의 토큰 키
- [ ] `LLM_BASE_URL`/`LLM_API_KEY` + `app.json`의 `llm.profiles` 3종
- [ ] `store.backend: "mongo"` + `AGENT_MONGO_URL`(대상 시스템과 별도 DB)
- [ ] 필요하면 `report.mail` 켜기
- [ ] `knowledge validate`(정적) 후 `knowledge validate --live`(접속 확인) 둘 다 통과
- [ ] `patrol run`을 상시 프로세스로 배포
