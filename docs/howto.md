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

`--symptom`을 안 주면 stdin으로 증상을 묻는다. 질문에 답하며 접수(intake)가
끝나면 케이스가 열리고, 조사 중 리드가 사람에게 물을 게 있으면 그 자리에서
바로 되묻는다(`interaction_policy="interactive"`). 입력이 중간에 끊기면
케이스는 `awaiting_human`으로 파킹되고, 안내된 `case resume` 명령으로 나중에
이어서 답할 수 있다.

## 파킹된 케이스에 나중에 답하고 싶다

```bash
python -m src case resume <case-id> --answer "<답변>"
```

데몬 프로세스가 그 케이스의 lease를 쥐고 있으면(실행 중이면) "데몬이 실행
중 — 잠시 후 재시도"와 함께 exit 2로 끝난다 — v1에는 실행 중인 데몬과 통신할
명령 채널이 없어서, lease가 비어 있거나 만료된 경우에만 CLI가 인라인으로
직접 재개한다.

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

[CLAUDE.md](../CLAUDE.md) — 이 리포에서 지켜야 할 규율(무raise, 시계 주입,
증거 인용 등)이 전부 정리돼 있다.

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
