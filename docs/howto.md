# How-to 색인

"~하고 싶다"로 찾아가는 작업별 목록. 각 항목은 관련 문서·소스 파일로 바로
연결한다.

## 새 사이트(사업부×시설) 추가하고 싶다

1. `config/registry.json`의 `sites`에 `{ "gbm": ..., "fct": ..., "enabled": true }` 추가.
2. `config/gbm/{gbm}.json`(사업부 계층) 작성 — 최소 `target`(대상 시스템 접속 정보)이 필요.
   같은 사업부의 다른 시설과 공통인 설정은 `config/factories/{fct}/common.json`으로 뺀다.
3. `knowledge/topology/{gbm}/{fct}.yaml`(선택 — 없으면 `knowledge/topology/common.yaml`만 씀)로
   그 사이트만의 토폴로지 차이를 얹는다.
4. `python -m src knowledge validate`로 확인. → [config-reference.md](config-reference.md)

## 새 순찰 점검을 추가하고 싶다

`patrol.checks.<이름>`에 `judge`/`schedule`/`target`을 채운다. rule 판정
6종(`range`/`exists`/`freshness`/`max`/`all_zero`/`expected_state`)이면 그걸로
끝이지만, 판정을 LLM에게
맡기고 싶으면(`judge: "llm"` 또는 `"rule+llm"`) `app.json`의
`llm.profiles.judge`가 채워져 있어야 기동을 통과한다. → [tutorial.md](tutorial.md),
[config-reference.md의 "rule 판정 6종"](config-reference.md#사이트-config-siteconfig-srcconfigschema_sitepy)

## rule이 아니라 LLM이 판정하게 하고 싶다

`judge: "llm"`(항상 LLM에게 묻는다) 또는 `"rule+llm"`(먼저 rule로 걸러 rule이
finding을 내면 그때만 LLM에게 2차 확인을 시키고, `patrol.llm_budget`이
소진됐으면 `on_budget_exhausted`(`"skip"`|`"escalate"`)를 따른다)을 쓴다.
판정 로직은 `src/patrol/llm_judge.py`. LLM이 원시 데이터를 함부로 보지
않도록 프롬프트에 실리는 것은 코드가 골라준 값뿐이다.

## 0/0/0 같은 **운영 이상**을 잡고 싶다

"배관은 멀쩡한데 현장이 이상하다"는 기존 rule로 표현되지 않는다. `exists`는 값이
있으니 통과하고, `max`는 0이 임계를 안 넘으니 통과하고, `range(min=1)`은 "하나라도
0이면"이라 야간에 한 라인만 쉬어도 울린다.

```json
"prod.badge_all_zero": {
  "judge": "rule", "schedule": { "interval": "5m" },
  "target": "rest:summary_prod", "concern": "operation",
  "params": { "rule": "all_zero", "field": "body.badge", "min_count": 3 }
}
```

`min_count`는 **표본이 그만큼 안 되면 판정하지 않는다**는 뜻이다. 라인 30개 중
2개만 돌아온 응답으로 "현장이 멈췄다"를 단정하면 안 되고, 그때는 "전부 0"이
아니라 "표본 부족"이라는 다른 사유의 finding이 난다.

"생산중이어야 하는데 NO PLAN"처럼 **한 값이 다른 값에 비추어 말이 되는가**를 보려면:

```json
"prod.status_matches_plan": {
  "judge": "rule", "schedule": { "interval": "5m" },
  "target": "rest:prod_status", "concern": "operation",
  "params": {
    "rule": "expected_state", "field": "body.prod_status",
    "expect": ["생산중", "대기"],
    "when": { "field": "body.plan_status", "equals": "생산중" }
  }
}
```

`when`이 성립할 때만 판정한다 — 계획이 없는 라인이 NO PLAN인 것은 정상이다.
`when.field`가 응답에 없으면 **판정 불가 finding**이 난다: ok로 삼키면 그 점검은
영영 아무것도 안 보면서 초록으로 남는다.

`concern`은 **빼먹을 수 없다** — 이 두 rule은 축을 위해 만든 것이라 명시하지
않으면 config 검증이 거부한다. 다만 값은 우리가 정하지 않는다: 큐 깊이가 전부 0인
것은 파이프라인 신호이므로 `"system"`이라 적으면 통과한다. 기존 rule
(`range`/`max` 등)로 현장 이상을 쓸 때는 이 강제가 없으니 직접 적어야 한다.

## `chat`으로 직접 조사를 시작하고 싶다

```bash
python -m src chat --gbm mx --fct gumi --config-root config --repo-root .
```

예시 트리(`config.example`)로 시험할 때는 `--stub-seeds stub-seeds.example.json`을
붙여라 — 안 붙이면 스텁에 아무 응답도 없어 서브에이전트의 REST 프로브가 전부
`404: 스텁에 등록되지 않은 끝점`으로 끝난다. `case resume`도 같다. 가짜 응답이
config가 아니라 플래그인 이유는 실전환 시 **빼는 것을 잊을 수 없게** 하기 위해서다.

`--gbm/--fct`는 **선택**이다 — 안 주면 registry의 활성 사이트를 후보로 증상에서
해석한다(사이트가 하나면 LLM 없이 확정된다). 확정하지 못하면 케이스를 만들지 않고
후보를 보여주니, 그중 하나를 `--gbm/--fct`로 지정해 다시 실행하면 된다.

`app.json`의 `access.allow`가 비어 있지 않으면 `--requested-by`가 필수다 — 주체가
없으면 거부한다(익명 요청이 통과하면 그 테이블이 장식이 된다).

`--symptom`을 안 주면 stdin으로 증상을 묻는다. **케이스는 접수보다 먼저 열린다** —
증상과 사이트가 정해지는 즉시 열리고, 대상(`target_locator`)은 접수가 채운다.
그래서 접수 도중 입력이 끊겨도 케이스와 지금까지의 문답이 남고, 안내된
`case resume` 명령으로 나중에 이어서 답할 수 있다(그때 접수부터 다시 하지 않는다).
조사 중 리드가 사람에게 물을 게 있으면 그 자리에서 바로 되묻는다
(`interaction_policy="interactive"`).

`case resume`에도 `--requested-by`가 있다 — `awaiting_human`에 넣은 답변 텍스트는
리드 프롬프트에 직행하고 evidence로 박제되므로, 개설만 막고 답변을 안 막으면
반쪽이다.

## 웹에서 케이스를 열고 싶다 (`api`)

```bash
python -m src api --port 8080 --config-root config --repo-root .
```

`api`는 **케이스를 쓰고 이벤트를 읽는 클라이언트**다 — 조사는 `patrol run`(워커)이
한다. 그래서 두 프로세스가 저장소로 만나야 하고, **메모리 백엔드에서는 서로를 못
본다**(`api`가 켜지며 경고한다). 실운영은 `store.backend: "mongo"`가 전제다.

```bash
# 케이스를 연다 — 스코프를 안 주면 증상에서 해석한다. 되물을 게 있으면 응답에 질문이 실린다
curl -s -X POST localhost:8080/cases -H 'content-type: application/json' \
  -d '{"symptom": "OEE가 이상하다", "concern": "system"}'
# → 202 {"case_id": "c-1", "status": "open", "question": null,
#         "intake": {"status": "done", "problems": []}}
#   intake.status가 "error"면 접수가 포기한 것이다(LLM 실패 등) — 케이스는 대상 없이
#   조사에 들어간다. 조용히 "정상 개설"처럼 보이지 않게 응답에 싣는다

# 접수가 되물었으면 답한다(턴 하나 — 다음 질문 또는 완료가 응답에 온다)
# question_seq(GET /cases/c-1에 실려 온다)를 함께 보내면 그 사이 질문이 바뀌었을 때 409다
curl -s -X POST localhost:8080/cases/c-1/intake-answers -H 'content-type: application/json' \
  -d '{"answer": "라인 7", "question_seq": 1}'

# 조사 중 그래프가 되물었으면(GET /cases/c-1의 question) 답을 **싣는다** — 실행은 워커가
# question_seq를 실으면 그 사이 조사가 다음 질문으로 넘어갔을 때 409로 거절한다
curl -s -X POST localhost:8080/cases/c-1/answers -H 'content-type: application/json' \
  -d '{"answer": "계획 변경 없음", "key": "2026-09-04T09:00-c-1", "question_seq": 1}'
# → 202 {"result": "accepted"}. 같은 key로 다시 보내면 duplicate — 재시도가 안전하다

# Fleet 집계 — 선언을 보고, 지금 한 번 돌린다(스케줄은 데몬이 시나리오당 1회 등록)
python -m src scenario list
python -m src scenario run alarm_trend        # 커버리지 수·digest와 리포트 경로를 낸다
curl -s localhost:8080/digests/alarm_trend    # 실행 기록(추세 비교의 재료)

# 리포트를 읽는 법: **커버리지가 먼저다.** "27/30 사이트"를 확인하기 전의 숫자는
# 아직 주장이 아니다. 불완전한 지표에는 ⚠와 사유가 붙고, 값이 없으면 —(0이 아니다).

# 조사가 끝난 뒤 실제 원인을 되먹인다(학습 루프 — 보고서 푸터가 이 명령을 안내한다)
python -m src case label c-1 --agreement wrong --actual-component plan-sync \
    --resolution false_positive --saw-report --by "$USER"
python -m src case label --stats            # 게이트가 닫혀 있으면 건수만, 열리면 confidence별 적중
# 적중 줄은 "모름 N건 분모 제외"와 "보고서 본 뒤 라벨 N건"을 함께 낸다 — 적중률만 읽지 않게.

python -m src patrol status                # 하트비트 + 조사 지표(건수·실패·소요 중앙값) + 점검별 최근 실행
curl -s -X POST localhost:8080/cases/c-1/label -H 'content-type: application/json' \
    -d '{"agreement": "correct", "resolution": "fixed"}'

curl -s localhost:8080/cases/c-1                                  # CaseDetail: 상태·질문·판정·candidates·단계·timeline(+timeline_source/error)
curl -s localhost:8080/cases/c-1 | python -c 'import json,sys; [print(c["rank"], c["component"], c["confidence"]) for c in json.load(sys.stdin)["candidates"]]'
curl -s -N localhost:8080/cases/c-1/events -H 'accept: text/event-stream'   # 진행 스트림
curl -s "localhost:8080/cases?gbm=mx&fct=gumi"                    # 목록 — 스코프 필수
curl -s localhost:8080/cases/c-1/report                           # 보고서(HTML)
```

`app.json`의 `access.subjects`가 비어 있지 않으면 `Authorization: Bearer <토큰>`을
보낸다. 틀린 토큰은 401이고, 볼 수 없는 케이스는 **없는 케이스와 같은 404**다 —
403으로 구별하면 케이스 존재가 새어 나간다.

## 파킹된 케이스에 나중에 답하고 싶다

```bash
python -m src case resume <case-id> --answer "<답변>"
```

데몬 프로세스가 그 케이스의 lease를 쥐고 있으면(실행 중이면) "데몬이 실행
중 — 잠시 후 재시도"와 함께 exit 2로 끝난다. CLI는 lease가 비어 있거나 만료된
경우에만 인라인으로 직접 재개한다. 실행 중인 데몬에 답을 **넘기려면** HTTP
(`POST /cases/{id}/answers`)를 쓴다 — 그쪽은 답을 레코드에 싣기만 하고 데몬의
워커가 집어 간다.

## 케이스 상태를 들여다보고 싶다

```bash
python -m src case list [--status open|investigating|awaiting_human|closed]
python -m src case show <case-id>              # 요약(상태/판정/증거 수)
python -m src case show <case-id> --report      # 저장된 보고서 전문(없으면 즉석 재렌더)
python -m src patrol status                     # 하트비트 + 점검별 최근 실행 (메모리 백엔드는 안내만)
```

## 스텁을 실제 시스템에 연결하고 싶다

`target.adapters: "real"`로 바꾸고 실제 접속 정보를 채운다 →
[docs/going-live.md](going-live.md).

## 케이스를 프로세스 재시작 후에도 남기고 싶다(Mongo 백엔드)

`app.json`의 `store.backend: "mongo"` + `store.mongo_url: "${AGENT_MONGO_URL}"`,
`.env`에 `AGENT_MONGO_URL` 채우기. → [going-live.md](going-live.md)

## 보고서를 메일로도 받고 싶다

`app.json`의 `report.mail.enabled: true` + `host`/`recipients`(그리고 필요하면
`username`/`password`/`use_tls`). 켜져 있는데 host나 recipients가 비어 있으면
기동 검증이 막는다(조용히 실패해 발송 대기열만 계속 쌓이는 걸 막기 위해).

## 예산이나 라운드 상한을 조정하고 싶다

`app.json`의 `engine.max_rounds`(조사 라운드 상한), `engine.parallel_width`
(라운드당 병렬 태스크 수), `engine.subagent_budgets.*`(역할별 서브에이전트
`recursion_limit`), `patrol.llm_budget.max_calls_per_hour`(순찰의 LLM 호출
예산)를 조정한다. → [config-reference.md](config-reference.md)

## CI에서 config·토폴로지가 어긋나지 않았는지 확인하고 싶다

```bash
python -m src knowledge validate --config-root config --repo-root .
```

exit 0이면 통과. `--live`를 추가하면 대상에 실제로 접속해 **Mongo 계정 롤**과
**pinned 명세 드리프트**까지 확인한다(CI 파이프라인에 대상 시스템 접근이 있을 때만
켠다). `--live`는 명세를 **못 받는 것도 기동을 막는다** — 못 물어본 것을 조용히
통과시키면 확인 안 한 것이 "이상 없음"으로 둔갑하기 때문이다. 명세를 받을 수 없는
환경이라면 `--live` 없이 돌려라(pin과의 정적 대조는 그때도 돈다).

실 접속 없이 드리프트 판정을 예행하려면 `--stub-seeds` 파일의 `rest_openapi`에
"지금 대상의 명세"를 심으면 된다.

## 새 점검이 실제로 이상을 잡아 케이스를 여는지 실 시스템 없이 확인하고 싶다

[tutorial.md의 Part B](tutorial.md#part-b-오프라인으로-전체-조사를-재현하기) —
`StubSeeds` + `ScriptedLLM`으로 결정론 재현.

## 이 코드베이스에서 AI에게 작업을 시키고 싶다

세 문서를 이 순서로 준다.

1. [CLAUDE.md](../CLAUDE.md) — 지켜야 할 규율(무raise, 시계 주입, 증거 인용 등).
   **하지 말 것**이 여기 있다.
2. [docs/file-map.md](file-map.md) — 파일별 역할과 데이터 흐름. **어느 파일을 열어야
   하는가**가 여기 있다.
3. [docs/for-implementers.md](for-implementers.md) — "X를 추가하려면" 레시피.
   **무엇을 어떤 순서로 만지고, 빠뜨리면 무엇이 조용히 깨지는가**가 여기 있다.

## 특정 컬렉션에 특정 질의를 날리고 싶다

`mongo_recent`는 항상 `filter={}`라 "최근 N건"뿐이다. `probe: "mongo_find"`를 **명시하면**
필터·정렬을 config로 쓸 수 있다.

```json
"twin.stopped": {
  "judge": "rule", "schedule": { "interval": "5m" },
  "probe": "mongo_find", "target": "mongo:twin_state",
  "params": { "rule": "exists", "field": "0.line",
              "filter": { "state": "STOP" }, "sort": [["ts", -1]], "sample": 50 },
  "resolve": { "line": { "from": "rest", "entry": "list_lines", "field": "code" } }
}
```

읽기 전용은 여기서도 메커니즘이다 — 필터 연산자가 닫힌 허용 목록을 통과해야 하고
`$where`처럼 서버측 JS를 도는 연산자는 표현할 수 없다. 해석기 값은 필터에 합쳐진다
(리스트는 `$in`, 스칼라는 동등 비교). 정적 필터와 `resolve`의 키가 겹치면 기동이 거부한다.

**표현할 수 없는 것**: 시간 범위 질의(`{"ts": {"$gte": <어제>}}`). 해석기는 값 하나를 내고
정적 필터가 그 값을 참조할 문법이 없다 — 참조 문법을 만들면 그것이 곧 표현식 DSL이고
규율 6이 금지한 것이다.

## 조사 정확도를 재고 싶다

되먹임이 먼저다. 조사가 끝난 뒤 사람이 실제 원인을 알려준다.

```bash
python -m src case label c-1 --agreement wrong --actual-component plan-sync \
    --resolution false_positive --saw-report --by "$USER"
python -m src case label --stats
```

게이트는 **종결 라벨 30건 그리고 종결의 절반 초과**다. 그전에는 어떤 퍼센트도 안 낸다 —
12/40으로 낸 30%는 다음 주에 뒤집힐 숫자이고, 한 번 보고되면 사람이 그것을 기억한다.

열리면 `confidence`별 적중을 낸다. 분모 규칙은 **코드가 쥔다**(어느 라벨을 셀지 사람이
고르면 숫자가 원하는 대로 나온다): `unknown`은 분모에서 빼되 뺀 수를 같은 줄에 적고,
케이스당 마지막 라벨만 세고, `confidence`가 없는 판정은 버리지 않고 "미상" 버킷에 넣는다
(버리면 분모에 생존 편향이 생긴다 — confidence를 못 낸 판정이 곧 어려운 케이스다).

조사 소요는 `python -m src patrol status`가 요약한다 — 건수·실패 수·**중앙값**·최장.
평균이 아닌 이유는 파킹 한 건이 며칠 걸리면 통째로 왜곡하고, 그 한 건이 바로 사람이
따로 봐야 하는 것이기 때문이다.

## 값이 매일 바뀌는 파라미터로 점검하고 싶다

`part_code`·`line_code`처럼 사업부/법인마다 다르고 매일 바뀌는 값은 config에
적지 않는다 — 적는 순간 썩는다. `resolve`로 **어디서 읽을지**만 선언한다.

```json
"prod.badge_nonzero": {
  "judge": "rule",
  "schedule": { "interval": "5m" },
  "target": "rest:summary_prod",
  "params": { "rule": "exists", "field": "body.badge",
              "body": { "part_code": ["P001"] } },
  "resolve": {
    "line_code": { "from": "mongo", "collection": "lines", "field": "line_code",
                   "filter": { "active": true }, "cardinality": "first:50" },
    "date": { "from": "clock", "expr": "today" }
  }
}
```

정적 값은 `params.body`에, 해석할 값은 `resolve`에 둔다(같은 키를 양쪽에 두면
기동이 거부된다). 소스는 넷이다 — **형제 조회 항목**(`from: "rest"`: 대상 시스템
자신이 인정한 목록이라 값이 실재함을 보장한다), **Mongo/Redis 직접 조회**(대상이
그 목록을 API로 안 열어 줄 때), **시계**(`from: "clock"`: `app.timezone` 기준
날짜), **`unfiltered`**(일부러 생략).

`rest`가 값의 정당성 면에서는 가장 강하지만 **절단 탐지는 약하다**: 목록 API가
서버 쪽에서 페이지네이션하면 우리는 1페이지를 전체 목록으로 오해한다(요청에
필터나 페이지 파라미터를 실을 방법이 아직 없다). `mongo`/`redis`는 어댑터가
`guards.max_rows`에 닿았음을 `complete=False`로 알려 준다. 목록이 길어질 수 있는
축에는 그쪽을 쓰는 편이 정직하다.

해석기가 하나라도 값을 못 내면 **대상을 호출조차 하지 않고** `error`가 된다.
빈 값을 보내면 endpoint에 따라 `0/0/0`(거짓 경보)이 되기도 하고 전체 조회(거짓
안심)가 되기도 하는데 어느 쪽인지 알 방법이 없기 때문이다. 전체를 보려는
**의도**라면 `{"from": "unfiltered"}`로 명시하라 — 그러면 그 키를 아예 안 보낸다.
