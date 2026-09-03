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
4종(`range`/`exists`/`freshness`/`max`)이면 그걸로 끝이지만, 판정을 LLM에게
맡기고 싶으면(`judge: "llm"` 또는 `"rule+llm"`) `app.json`의
`llm.profiles.judge`가 채워져 있어야 기동을 통과한다. → [tutorial.md](tutorial.md),
[config-reference.md의 "rule 판정 4종"](config-reference.md#사이트-config-siteconfig-srcconfigschema_sitepy)

## rule이 아니라 LLM이 판정하게 하고 싶다

`judge: "llm"`(항상 LLM에게 묻는다) 또는 `"rule+llm"`(먼저 rule로 걸러 rule이
finding을 내면 그때만 LLM에게 2차 확인을 시키고, `patrol.llm_budget`이
소진됐으면 `on_budget_exhausted`(`"skip"`|`"escalate"`)를 따른다)을 쓴다.
판정 로직은 `src/patrol/llm_judge.py`. LLM이 원시 데이터를 함부로 보지
않도록 프롬프트에 실리는 것은 코드가 골라준 값뿐이다.

## `chat`으로 직접 조사를 시작하고 싶다

```bash
python -m src chat --gbm mx --fct gumi --config-root config --repo-root .
```

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

exit 0이면 통과. `--live`를 추가하면 Mongo 계정이 실제로 readonly 롤인지까지
확인한다(실 접속 필요 — CI 파이프라인에 대상 시스템 접근이 있을 때만 켠다).

## 새 점검이 실제로 이상을 잡아 케이스를 여는지 실 시스템 없이 확인하고 싶다

[tutorial.md의 Part B](tutorial.md#part-b-오프라인으로-전체-조사를-재현하기) —
`StubSeeds` + `ScriptedLLM`으로 결정론 재현.

## 이 코드베이스에서 AI에게 작업을 시키고 싶다

[CLAUDE.md](../CLAUDE.md) — 이 리포에서 지켜야 할 규율(무raise, 시계 주입,
증거 인용 등)이 전부 정리돼 있다.
