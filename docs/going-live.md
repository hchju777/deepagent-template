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
    "rest":  { "base_url": "${MX_GUMI_API_BASE}" },
    "code":  { "repos": [ { "name": "twin-services", "path": "/opt/repos/twin-services" } ] }
  }
}
```

`target.adapters`를 `"real"`로 바꾸는 것이 스텁↔실구현 전환의 **유일한
스위치**다(`src/infrastructure/factory.py`의 `build_adapters`). 비밀번호가
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
운영 중인 컨슈머 그룹의 오프셋에 영향을 주지 않는다. REST는 토폴로지에
등록된 끝점(allowlist)만, GET만 허용된다.

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

- [ ] 사이트마다 필요한 대상만 `target`에 채우고 `adapters: "real"`
- [ ] `.env`에 실제 값(URL·계정·비밀번호) — `config/*.json`에는 `${...}` 참조만
- [ ] Mongo 계정을 readonly 롤로 생성하고 `knowledge validate --live`로 확인
- [ ] `LLM_BASE_URL`/`LLM_API_KEY` + `app.json`의 `llm.profiles` 3종
- [ ] `store.backend: "mongo"` + `AGENT_MONGO_URL`(대상 시스템과 별도 DB)
- [ ] 필요하면 `report.mail` 켜기
- [ ] `knowledge validate`(정적) 후 `knowledge validate --live`(접속 확인) 둘 다 통과
- [ ] `patrol run`을 상시 프로세스로 배포
