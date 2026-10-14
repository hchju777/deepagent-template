# 12a단계 — conclude + verify (인용 검증된 판정)

> **목적**: 조사가 끝나면 **판정이 항상 생기고**, 그 판정의 인용은 **코드가 검사한다.** 10a·10b의
> 울타리는 안 바꿨다 — integrate 뒤에 두 노드(`conclude`·`verify`)를 붙였고, 판정자는 10b의
> frame·integrate처럼 **주입받는 함수**다(`EngineDeps.conclude`).
> 상태: **됐다(10-06)** — 리뷰 대기. 보고서·이벤트는 12b.

```bash
python -m src case investigate c-1 --stub-seeds examples/stub-seeds.json
python -m src case dryrun --plan examples/case-ladder.json --stub-seeds examples/stub-seeds.json
```

```
  라운드 5 — 끝난 이유: no_runnable
  판정 data_loss (medium) — 끝점은 컬렉션의 alarm 수를 세어 배지를 만드는데, 원천 재집계는 2이고 배지는 0이다. …
    원인 sink — 원천에는 alarm 문서가 둘인데 배지는 0 — …  (t-3.e1 t-4.e1)
    후보 api [low] — 끝점의 집계가 다른 것을 셀 가능성 — …  (t-2.e1)
    권고 sink의 쓰기 이후 배지 집계가 갱신되는 경로를 확인한다
    검증 통과
```

## 종료 판단 (시작할 때 적음, 10-06)

1. 조사가 **어떻게 끝나든** State에 `Verdict`가 생긴다 — 리드 conclude·상한·no_runnable·llm_error 전부.
   llm_error와 증거 0건은 LLM을 묻지 않고 코드가 `degraded`를 찍는다.
2. verify는 LLM 없이 인용을 검사한다. 인용 우주는 `state.evidence`, 불완전 증거로 주장하면 caveat 요구,
   문제가 있으면 한 번 되묻고 그래도 안 되면 걷어내고 낮은 확신으로 통과.
3. `case investigate`·`case dryrun` 출력에 판정이 보이고, 측정판에서 "sink 하나, 인용 전부 실재"가 나온다.
4. `verdict_type`은 코드가 쥔 닫힌 집합이고 `degraded`는 LLM이 못 낸다. 프롬프트에 사내 어휘 없음.

넷 다 됐다. 3의 측정판 결과는 아래 "측정판 확인".

## 무엇을 LLM이 정하고 무엇을 코드가 쥐는가

| | |
|---|---|
| **LLM** | 판정의 서술 · 어느 부품이 원인인가 · 후보와 기여 요인 · 권고 · `verdict_type` 여섯 중 하나 · 확신 |
| **코드** | 판정이 **반드시 생기는 것** · `degraded` 낙인 · 인용 우주(`state.evidence`) · 불완전 증거의 caveat 요구 · `component`가 실재하는 이름인가 · 후보 상한 3 · relation 길이 · 최상위·기여 요인의 confidence 제거 · 되묻기 한 번 · 강등 규칙 |

### `degraded`는 코드만 찍는다

"조사했는데 못 가렸다"(`inconclusive`)와 "조사가 안 돌았다"(`degraded`)는 다른 사실이다 — 전자는
운영이 읽을 판정이고 후자는 우리 시스템의 고장이다. 리드가 `degraded`를 고를 수 있으면 둘이 섞인다.
그래서 `Verdict.verdict_type`에는 있고(코드가 만든다) 리드가 내는 `lead.ConcludeReply`의 어휘에는 없다 —
리드가 내면 `validate`가 거부하고 수리 재시도로 간다. `tests/domain/test_verdict_model.py`가 두 집합의 차이를
단정한다.

코드가 `degraded`를 찍는 자리 넷(`nodes.make_verdict_nodes.conclude`):

| 언제 | 왜 LLM을 안 묻나 |
|---|---|
| `stopped_by == "llm_error"` | 죽은 LLM을 한 번 더 부르는 것이고, 성공하면 **안 돈 조사에 판정이 생긴다** |
| 증거 0건 | 인용할 것이 없어 어떤 판정도 근거가 없다. 실패한 태스크의 사유가 caveat에 남는다 |
| 판정자가 없다(`EngineDeps.conclude is None`) | 배선 누락 — 조용히 None이 아니라 "판정자 없음"이 남는다 |
| 판정을 못 받았다(JSON 실패·어휘 위반·예외) | 사유가 caveat와 `llm_errors`에 남는다 — 프롬프트가 안 먹히는 것이 보여야 한다 |

frame이 죽어도 **END가 아니라 conclude로 간다**(`route_after_frame`) — 그래야 죽은 조사에도 판정이 남는다.
참조 템플릿은 frame 실패를 END로 보내 verify를 안 거쳤고, 보고서 쪽이 그 구멍을 따로 메우고 있었다.

### verify — 규칙 셋, 되묻기 한 번, 강등

```
conclude → verify → (문제 없음 → END)
                  → (문제 있고 첫 시도 → conclude 재작성: 프롬프트에 `## 재작성` 블록)
                  → (문제 있고 두 번째 → 걷어내고 강등 → END)
```

1. 다리(`root_cause`·`alternates`·`contributing`)마다 인용이 있어야 하고, 인용한 id는 **`state.evidence`**에
   있어야 한다. Store 전체가 아니다 — Store에는 error 태스크가 남긴 고아 본문도 있어, 그걸 기준으로 삼으면
   리드가 본 적 없는 id를 인용해도 통과한다(규율 3).
2. 잘린 표본(`complete=False`)으로 주장했으면 caveat에 **그 id**가 있어야 한다. 경계는 ASCII로 본다 — 정규식의
   단어 문자는 한글도 포함해서 "t-1.e1은 잘렸다"의 조사(은)가 id에 붙어 "명시하지 않았다"가 됐다(테스트가
   먼저 잡았다).
3. `component`는 **토폴로지의 서비스 이름**(`EngineDeps.components`)이거나 **리드가 본 증거·그래프 이름**에
   있어야 한다(`_universe` — 태스크의 "찾지 않고 이름을 댔다"와 같은 우주). 없는 부품을 가리키는 판정은
   보고서가 없는 것을 고치라고 적는다. 대본 경로(`check_discovery=False`)는 사람이 이름을 알고 적은 것이라
   안 본다 — 태스크 검사와 같은 스위치다.

**강등**(두 번째도 실패): 없는 인용을 **걷어내고** 확신을 `low`로, caveat에 문제 목록. 근거가 전부 사라진
다리는 뺀다 — 최상위가 그러면 `inconclusive`가 된다. `_accept_hypotheses`가 근거를 잃은 supported를 open으로
되돌리는 것과 같은 규칙이다. 참조 템플릿은 확신만 낮추고 환각 id를 그대로 뒀는데, 그러면 State에 "실제로
없는 것"이 남는다(규율 3의 "State에 올라가는 것은 실제로 일어난 일").

### 인과 사슬의 형태는 코드가 정한다 (`sanitize_verdict`)

후보 상한 3·중복·빈 부품, relation 300자, 최상위·기여 요인의 confidence는 None(최상위의 신뢰도는
`Verdict.confidence` 하나다). **거부가 아니라 소독**이다 — validator로 거부하면 후보 하나가 중복됐다고 판정
전체가 degraded로 떨어진다. 버린 후보는 caveat에 남긴다.

## 배선

| 무엇 | 어디 |
|---|---|
| `Verdict`·`CauseLink`·`VerdictType`(7)·`Confidence` | `src/domain/case.py` |
| State: `verdict`·`verify_problems`·`verify_attempts` | `src/application/state.py` |
| `conclude`·`verify` 노드, `sanitize_verdict`·`verify_verdict`·`demote_verdict`, 라우터 | `src/application/nodes.py` |
| 그래프: frame/integrate → conclude → verify → (conclude \| END) | `src/application/graph.py` |
| 리드의 판정 턴(`ConcludeReply` — degraded 없음), `make_lead`가 **셋**을 돌려준다 | `src/application/lead.py` |
| 판정 턴의 재료(`conclude_fields` — `ended`·`rewrite`·`components`·`example`) | `src/application/briefing.py` |
| 프롬프트 | `config/prompts/investigate-conclude.md`(`investigation.conclude_prompt`) |
| 대본의 `verdict`, 없으면 `note`(LLM 오류가 아니다) | `src/application/dryrun.py`, `examples/case-ladder.json` |
| 출력·진단(`verdict_lines`·`verdict_summary`)·트레이스 요약의 `판정 :` 줄 | `src/__main__.py`, `diagnose.py`, `trace_digest.py` |

판정 프롬프트는 `{actions}`가 없다 — 판정 턴에는 부를 읽기가 없고, 요구하면 운영이 빈 목록을 넣어 통과시킨다.
대신 `{evidence}`가 필수다(`briefing.CONCLUDE_REQUIRED`). `component` 후보 줄은 토폴로지에서 **생성**한다
(decisions ⑮ — 손으로 적으면 안 된다). 예시의 `verdict_type` 자리는 값이 아니라 `<… 중 하나>`다 — 값을 보여
주면 베끼고(10b가 `conclude`로 겪은 것), 그대로 두면 검증이 거부해 수리 재시도가 "그중 하나"를 전한다.

## 테스트

- `tests/domain/test_verdict_model.py` — validator·StrictModel·두 어휘의 차이.
- `tests/application/test_verdict.py` — degraded 넷, 소독, verify 규칙 셋, component 우주, 되묻기→강등, 라우터,
  그래프 경로 넷(판정까지·frame 사망·재작성 뒤 통과·상한).
- `test_lead.py` — 판정 턴(프롬프트에 본 것만·접속 정보 없음·트레이스), degraded 거부, 프롬프트 없음, 재작성 블록,
  곁다리 키, `_load_lead_prompt(required=)`. CLI 셋에 판정 줄.
- `test_briefing.py`·`test_dryrun.py`·`test_diagnose.py`·`test_trace_digest.py`·`test_schema_app.py` 각각의 몫.
- RED 스윕 +35(310 → 345): 위 규칙 하나씩 지워 짝 테스트가 빨개지는지.

구현 전 RED: 63 failed + 수집 오류 2(새 이름 import). 구현 뒤 셋이 남았고 셋 다 테스트가 먼저 잡은 것이었다 —
`\w` 경계(한글), 되묻기가 판정 답을 먼저 먹는 대본 순서, "대본에 판정 없음"을 LLM 오류로 센 것.

## 실제 소비자로 확인한 것

- `case dryrun examples/case-ladder.json` — 판정 블록이 찍히고 `검증 통과`. 같은 대본에 없는 id(`t-9.e1`)·
  인용 없는 기여 요인을 심은 적대적 사본 — `검증 미통과 → 강등`, `근거 없는 다리 제외: 기여 요인 processor,
  최상위 sink`, 판정이 `inconclusive (low)`로 바뀌어 출력됐다(대본 경로는 되물어도 같은 답이라 두 번째에서 강등).
- 측정판(`tools/local_case.py`, sink-stopped, haiku 대역): 아래.

## 측정판 확인

`tools/local_case.py`(sink-stopped, 심볼 인덱스 켬), 리드 자리는 턴마다 새 haiku 대역(11b·11d와 같은 하네스), 한 판.

- 리드가 **r5에 스스로 `conclude`**를 냈다(11d 측정 8판은 r3~r6 conclude·상한이 섞였다 — 같은 모양).
- 판정 턴(`07-r5-conclude.md`, 프롬프트 11,707자): 대역이 `data_loss (high)`, 원인 `sink`, 인용 `t-11.e1`(하트비트
  멈춤)·`t-10.e1`(컨슈머 그룹 오프셋 1830 밀림), 권고 셋을 냈다. **verify 한 번에 통과** — 없는 id 0, component는
  토폴로지 이름, 잘린 증거 없음. 되묻기 없이 끝났고 `검증 통과`가 출력·`summary.md`·`case trace`(`판정 : data_loss
  high · 원인 sink(인용 2)`) 세 소비자에 다 보인다.
- 계약 위반은 integrate의 `찾지 않고 이름을 댔다`(컨슈머 그룹 1건)뿐 — 판정 턴은 0.
- 되묻기·강등 경로는 이 판에서는 안 밟혔다(대역이 바로 맞게 냈다). 그 경로는 적대적 대본(위)과 노드·그래프 테스트가
  본다. n=1, haiku 대역 — 상한이지 예측이 아니다(11a).

## 사내 실측(sevt, 10-07) — 리뷰 4번과 그 뒤

실제 대상 + 실제 LLM + 11e 그래프로 두 판. **둘 다 r1 integrate에서 LLM 시간 초과 → degraded**라 판정은 비교하지
못했다. 사내 AI가 트레이스를 읽고 진단한 열 가지를 코드로 대조한 결과(사실은 코드에서, 판단은 사람이):

| # | 진단 | 코드 대조 | 처리 |
|---|---|---|---|
| 1 | 60초 × SDK 재시도 3 × 리드 재시도 2 = 한 호출 6분 | 기본 `timeout_s=60`·`max_retries=2`·`lead.RETRIES=1`, 트레이스에 걸린 초 없음 | **R1** |
| 2 | frame이 예시를 베낌, `code.trace`를 안 씀 | frame 예시는 접수 프로브 읽기 + 탐색 읽기(설계), `code.trace`는 integrate 예시의 칸 — 거기까지 못 갔다 | 1의 결과. R1 뒤 재측정 |
| 3 | `code.trace`가 `POST /path` 거부 | 문자열 그대로 노드 id | **R1** |
| 4 | `code.grep`이 README만 400줄 | 트리 순서로 자른다 | R2 — 코드 줄 먼저, 문서 줄 뒤 |
| 5 | `code.config` 통째 덤프 | 병합 전체를 돌려준다 | R2 — `key` 인자·키 지도 |
| 6 | `rest.query` 첫 항목에서 잘림 | 목록 응답을 들여쓴 JSON으로 | R2 — 항목당 한 줄, 대상 단어 든 항목 먼저 |
| 7 | `<데이터 흐름>` 대상 키 탈락·접수 끝점 줄 소실 | `_MAX_NAMES=8`, `budget=800`, 접수 끝점은 씨앗일 뿐 | R2 — 접수 끝점 고정, 증상 단어 겹치는 이름 먼저 |
| 8 | 그래프 방향(kafka 명령 target_resource가 reads)·동적 키 과대 귀속 | 동사는 옆 줄 어휘, 동적 키는 템플릿 매칭 | R3 — 사내 코드 줄 모양 두 개 받은 뒤 |
| 9 | degraded에 `검증 통과` | `verify_note`가 되묻기 0회를 통과로 | **R1** |
| 10 | NO_PROXY 밖이면 `RemoteProtocolError`만 | REST 프로버가 예외를 문자열로만 | **R1** |

### R1 (10-07) ✅ — 재측정을 막는 넷

- 기본값 `timeout_s` 60 → **300**, `max_retries` 2 → **0**(리드가 전송 오류를 한 번 되묻으니 SDK 재시도는 중복). 리드는
  **시간 초과면 되묻지 않는다**(`_is_timeout` — `APITimeoutError`·`ReadTimeout`·`TimeoutError`가 다 이름에 담는다) — 같은
  상한을 또 기다릴 이유가 없다. `llm_errors` 사유에 `시간 초과 (N초)`가 남고 "N회 시도 실패"는 실제 횟수다.
- 호출마다 걸린 초가 트레이스로 간다: `ask_json`의 `on_exchange`에 여섯째 인자 `latency_s`, 트레이스 파일 머리에
  `응답: N초`, `case trace` 머리줄에 `· 응답 N초`(옛 파일은 그대로 읽힌다).
- `flow.endpoint_path`: `POST /path`·`get /path`의 메서드 접두를 뗀다 — `code.trace`(리드)와 `code trace`(CLI) 둘 다.
- degraded 판정은 `검증 해당 없음`(코드가 찍고 verify를 안 거친다).
- REST 프로버: 전송 계층 오류에 프록시 env가 있고 호스트가 NO_PROXY 밖이면 `프록시 경유 의심: … NO_PROXY에 <host>를
  넣어라`를 덧붙인다(`urllib`의 bypass 규칙 그대로). doctor·patrol·조사가 같은 문자열을 본다.
- 테스트 먼저(RED 8 → GREEN): `test_schema_llm` 1, `test_lead` 2, `test_cli` 1(트레이서), `test_trace_digest` 1,
  `test_deployed_code` 1(기존 확장), `test_verdict` 1, `test_rest_prober` 2. 스윕 +11.
- 사내 확인: `app.json`의 `llm.timeout_s`·`max_retries`가 **명시돼 있으면 그 값이 이긴다** — 60이면 300, 재시도는 0으로
  맞춘 뒤 같은 판을 한 번 더([review-12a-4.md](review-12a-4.md) 그대로). 통과 기준: ④의 `끝난 이유`가 `llm_error`가
  아니고 트레이스 머리의 `응답 N초`가 보인다.

### 두 번째 실측(sevt, 10-07)과 R2 계획

R1 뒤 두 판: c-1 3라운드 6분 20초, c-2 6라운드 18분 51초, 둘 다 degraded. 직접 원인은 **게이트웨이가 ~180초에 끊는
것**(`500 Send timeout`·upstream reset) — integrate 프롬프트가 라운드마다 5K → 26K로 커졌고 생각하는 모델의 한 답이
180초를 넘었다. `timeout_s=300`은 의미가 없었다. 실제 원인은 사람이 찾았다(c-1: 해당 요약 파이프라인이 config에서
꺼져 있어 요약 키가 안 만들어짐 → `config_error`, 원인 배치; c-2: 원천 키의 두 컬럼이 전행 비어 요약 필터를 거치면
0건 → 상류). 리드가 놓친 이유: 접수 증거가 첫 행에서 잘려 대상 행을 못 봤고, 예시 액션을 그대로 따랐고, 읽는 쪽만
보고 쓰는 쪽을 안 봤고, `code.flow/uses/callers`를 0회 썼고, r2에 쥔 "키가 없다" 증거의 차이를 계산하지 않았다.

사내 Claude와 만든 수정 지시 열 묶음을 리뷰해 순서를 정했다(대화 기록). 요지: 강한 모델이 스스로 하는 일(중복 차단·
열린 질문·정체 감지·도구 경계 요약·예산)을 **코드가 먼저** 하고, 벽의 정체(유휴 상한인가)는 스트리밍으로 먼저 잰다.

- **R2-1 LLM 층 ✅(10-07)** — 아래.
- R2-2a 엔진 P0: llm_error여도 증거가 있으면 짧은 conclude 1회, degraded 서술에 라운드·읽기·증거 수, 미실행 태스크
  보고, 중복 질의 거부 사유에 기존 증거 id, 열린 질문 블록.
- R2-2b 증거 모양: 접수 증거는 identity 행만, 선언됐는데 없는 키, `code.read` 범위·`redis.get` 경로·`mongo.find` 좁히기
  안내, 예시 자리표시자, grep 코드 줄 먼저, `code.config key`, `rest.query` 목록 한 줄, `<데이터 흐름>` 접수 끝점 고정.
- R2-2c 운영·구조: Windows 종료 트레이스백, README NO_PROXY, system/user 메시지 분리.
- 그다음 사내 재측정 → R3(결정적 triage) → R4(ReAct 루프·도구 경계 요약).

### R2-1 (10-07) ✅ — 역할별 LLM·토큰 상한·스트리밍

- `app.json`: `llm`은 기본, `llm_roles: {lead, conclude, report}`에 역할마다 **덮어쓸 것만**(부분, 중첩도 부분 —
  `headers`·`tls`). `AppConfig.llm_for(role)`이 병합해 다시 검증한 설정을 주고, 덮어쓰기가 없으면 기본 객체 그대로(`is`)
  라 호출부가 어댑터를 두 벌 만들지 않는다. 역할의 모르는 키·역할 이름은 config 로드에서 막힌다(decisions ⑳).
- `LlmConfig.max_tokens`(chat_model은 langchain이 `max_completion_tokens`로, http는 `max_tokens`로 보낸다)와
  `stream`(chat_model만 — `astream`으로 조각을 모아 같은 `LlmReply`, `first_token_s` 기록). `describe()`가 실효
  `상한 Ns · 재시도 N · 토큰 N · 스트리밍`을 찍는다(7-4).
- `make_lead(..., conclude_llm=)`: 판정 턴만 다른 LLM. `case investigate`가 `lead`·`conclude` 역할로 둘을 만들고 출력에
  `판정: <describe>`를 남긴다. 보고서 서술은 `report`. `llm describe`는 기본과 **다른 역할만** 더 찍고, `llm ask/check
  --role`.
- 테스트 먼저(RED 8 → GREEN): `test_schema_app` 2, `test_schema_llm` 1, `test_llm_adapters` 2(가짜 게이트웨이에 SSE 추가),
  `test_lead` 2(판정 LLM 분리, CLI 배선 — 대본을 어댑터마다 따로 줘 판정이 첫째로 가면 소진된다), `test_cli` 1. 스윕 +11.
- 사내 확인: `llm_roles.lead`에 빠른 모델(+`max_tokens` 400), `conclude`에 생각하는 모델, 기본에 `stream: true`로 같은
  판을 한 번 더. 받을 것은 브리프의 일곱 줄 + 트레이스 머리의 `응답 N초`·첫 조각 초.

### R2-2a (10-07) ✅ — 엔진 P0

- **llm_error 뒤 판정 한 번**(1-3): `stopped_by == llm_error`여도 `state.evidence`가 있으면 `deps.conclude`를 한 번 묻는다
  — 액션 턴이 죽었다고 판정 턴까지 죽은 것은 아니다(R2-1로 다른 모델일 수 있다). 그것도 실패하면 degraded. 증거가
  0건이면 전처럼 묻지 않는다. 판정 프롬프트는 평소와 같다(예산 절반으로 줄이는 것은 R4-1의 프롬프트 상한에서).
- **degraded 서술은 사실로**(1-4): `nodes.stopped_summary` — `조사 중단 — <이유>. N라운드 · 읽기 M회(성공 K) · 증거 E건 ·
  가설 h-1[open] …`. "조사가 돌지 않았다"는 사내 실측에서 거짓이었다.
- **미실행 태스크**(7-1): `diagnose`가 끝날 때 `pending`인 태스크를 `미실행 N개 — 끝날 때까지 안 돌았다(우선순위에 밀림): …`
  로 센다.
- **중복 질의 거부 사유에 기존 증거 id**(C): `_accept_tasks(evidence_of=)` — `이미 한 읽기를 또 냈다 … — 그 결과는 t-1.e1`.
  거부만 하면 리드는 "왜"를 모른 채 또 낸다(사내 실측에서 세 번).
- **`<열린 질문>` 블록**(D): `briefing.open_questions_block` — 잘린 증거(좁혀 다시 읽어라), 실패한 읽기(사유 그대로 —
  프록시 의심 안내가 거기 있다), 거부된 중복(결과는 이미 증거에 있다). integrate·conclude 프롬프트에 `{open}` 자리.
  R2-2b가 "선언됐는데 없는 키"를 더한다.
- 테스트 먼저(RED 6 → GREEN): `test_verdict` 2, `test_diagnose` 1, `test_nodes` 1, `test_briefing` 2(자리와 재료가 짝인지까지).
  기존 `test_조사가_안_돌았으면…`은 새 서술에 맞췄다. 스윕 +10, 옛 conclude 분기의 케이스 3건 재지정.

### R2-2b-1 (10-07) ✅ — 도구: 잘라 보지 말고 골라서 전부

사내 실측의 넷(라우터 파일이 400줄에서 잘려 핸들러를 못 봄 · 요약 키 값이 첫 항목에서 잘림 · config 통째 덤프가 첫 키에서
잘림 · 끝점 path grep이 README만 걸림)은 전부 같은 모양이다 — **큰 것을 잘라 보여 주니 리드가 조각을 전부로 알거나 같은
읽기를 또 냈다.** 고치는 방향도 하나다: 리드가 **좁혀 낼 수 있는 인자**를 주고, 좁힌 결과는 자르지 않는다.

- **`redis.get(key, path?)`**(3-3): `domain/jsonpath.select` — `record[0].data`처럼 점과 `[n]`. string 값은 JSON으로 풀고,
  hash·list는 그 구조대로. 실패하면 **있는 키·목록 길이**를 적는다 — "없다"만 돌려주면 리드는 경로를 지어내 또 낸다.
  실제 리더와 스텁이 같은 계약(자리 없음 error, 키 없음 None — 전처럼).
- **`code.read(service, path, offset?, limit?)`**(3-2): 범위를 주면 통째로 받아 그 줄들을 자르지 않는다. source에
  `L120-L220 / 전체 410줄`. `offset`만이면 기본 400줄이고 봉투가 더 있다고 말한다. `offset`은 `code.grep`이 준 줄 번호에서.
- **`code.config(service, key?)`**(5): `key`면 그 자리 통째. 없이 부르고 합친 설정이 2400자(증거 한 건의 기본 예산)를 넘으면
  **키 지도**(`경로(두 단계) → 무엇이 있나`)만 주고 `complete=False`에 "key=로 읽어라". `rules.r0…r299` 같은 큰 부채꼴은
  안 내려간다 — 지도가 덤프만큼 커지면 지도가 아니다.
- **`code.grep` 코드 줄 먼저**(4): 리더에서 4000줄로 받아 코드 줄을 앞에, 문서(`.md/.rst/.txt/.adoc`) 줄은 구분 줄 밑에
  뒤로 보낸 뒤 400줄에서 자른다. 리더가 먼저 자르면 트리 순서상 앞서는 README가 전부를 먹고 config 줄은 영영 안 온다.
- **좁힌 읽기는 증거 예산에서 안 잘린다**: `actions.narrowed` — 그 action의 **선택** 인자 중 `path`·`key`·`offset`이 왔을 때.
  실행기는 그 결과를 증거 한 건 예산(`evidence_chars`)이 아니라 전체 예산(`evidence_total_chars`)까지 싣는다(`narrowed_chars=`).
  `redis.get`의 `key`는 키 이름이지 좁히는 자리가 아니라 안 센다 — 처음엔 이름만 보고 셌고 테스트가 잡았다.
- **integrate 규칙 한 줄**(3-4) "큰 것은 잘라 보지 말고 골라서 전부 봐라"와 **예시 읽기 하나**(2-4): 사내 실측에서 리드는
  예시의 action을 그대로 베꼈다. 한 칸뿐이라 순서가 곧 선택이다 — 안 써 본 action 먼저(컨슈머 lag가 보이게), 좁혀 다시 낼
  수 있는 `mongo.find{filter}`는 그 뒤.
- 테스트 먼저(RED 8 → GREEN). 스윕 +19(438) — 전부 RED 확인.

### R2-2b-2 (10-07) ✅ — 증거·브리핑: 대상 행 먼저, 코드가 쥐는 사실

- **대상 행 먼저, 항목당 한 줄**(1-5/6): 문서 목록은 압축 JSON 한 줄씩이고, 케이스 `target`(`L1/Alarm` — 순찰 판정이 식별 값을
  `/`로 이은 것)의 값이 전부 든 행은 **앞에 통째로**(`[n]`은 원래 자리, 머리줄에 `대상 행 N건 먼저`). `rest.query`의
  `{request, status, response}`에서 `response`가 문서 목록이면 한 줄로 눕히지 않고 같은 모양으로 편다 — 사내 실측에서
  19개 항목이 첫 항목에서 잘려 리드가 대상 행을 못 봤다.
- **선언됐는데 없는 키**(5-4): 발견 읽기(`redis.scan`·`mongo.list_collections`·`kafka.list_topics`)의 이름을
  `TaskOutcome.found`에 구조로 남기고, `execute`가 `EngineDeps.declared`와 대조해 **State `facts`**에 적는다
  (`application/facts.py`). 선언은 흐름 그래프에서 — 접수 끝점이 읽는 키 템플릿(`flow.declared_keys`), 추적이 없으면 config의
  전부. 잘린 scan으로는 말하지 않고, `hb:*`는 `alarm:stats:{line}`에 대해 아무 말도 못 한다(패턴의 글자 접두와 템플릿의 `{`
  앞부분이 겹칠 때만). `<열린 질문>` 맨 위에 실리고(같은 이름은 한 줄) 태스크 요약 뒤에도 붙는다. c-1에서 r2의 리드가
  알아낸 "요약 키가 없다"를 r3에서 잊은 자리다 — 이제 하네스가 쥔다.
- **흐름 블록**(7): 접수 끝점 줄은 맨 앞에 예산 밖으로(`find_seeds`는 서비스를 먼저 두므로 작은 예산에서 끝점 줄이 먼저
  떨어졌다), 관계당 여덟 이름은 증상·target 단어가 든 것 먼저(`briefing.case_words` → `flow_text(prefer=)`).
- **측정판**: 변형 `cache-missing`(요약 키가 아예 없음), 배지 응답은 사내 모양의 행 목록(대상 행은 18개 중 16번째).
  실제 소비자로 확인 — 파일 턴 리드로 r1에 `rest.query`+`redis.scan alarm:*`+`redis.scan *`를 내니 r1 integrate 프롬프트의
  증거에 `[16] {"group":"L1","title":"Alarm",…}`가 `response:` 바로 아래 첫 줄로, `<열린 질문>`에 `선언됐는데 없는 키 1개
  (scan alarm:*, t-2): alarm:stats:{line}`가 실렸다. 프롬프트 크기 frame 3.4K · integrate 7.2K · conclude 5.6K.
- 테스트 먼저(RED 10 → GREEN): `test_runner_probe` 3, `test_graph` 2, `test_briefing` 3, `test_flow` 3, `test_local_case` 1.
  기존 둘은 압축 JSON 따옴표에 맞췄고 측정판 테스트 넷은 행 목록 접근으로. 스윕 +17(455). `__main__`의 `declared` 배선은
  단위 테스트가 없다 — 측정판 실행이 그 배선을 통째로 지난다.

### R2-2c (10-07) ✅ — 운영: 닫기와 종료 소음, NO_PROXY

- **LLM 어댑터를 닫는다**(7-2): `LlmPort.close()`(기본은 할 일 없음 — 포트 표면 테스트는 수명주기 메서드로 허용),
  `ChatModelAdapter`는 자기가 만든 httpx 풀 둘을 들고 있다가 닫는다. `case investigate`는 리드·판정 어댑터 둘 다(같은 객체면
  한 번), `llm ask/check`와 리포트 서술도 끝나면 닫는다. 사내 Windows의 `ConnectionResetError(10054)` 트레이스백은 안 닫은
  풀을 proactor가 종료 중 치우며 낸 것이다 — 원인부터.
- **종료 소음 거름망**: 모든 명령이 `_run`을 지난다(`asyncio.run`은 한 곳 — 테스트가 센다). 루프 예외 처리기는 **transport
  층**의 `ConnectionResetError`·`Event loop is closed`만 거르고 태스크의 예외와 다른 예외는 기본 처리기로 — 거름망이 진짜
  오류를 삼키면 조사가 왜 죽었는지 아무도 모른다. Windows에서는 `sys.unraisablehook`도 proactor transport의 `__del__`만.
  여기서는 재현이 안 되므로 합성 context로 단위 검증했다 — 사내 재측정에서 "종료 때 트레이스백 유무"를 받는다.
- **README "프록시와 NO_PROXY"**(7-3): 왜 대상 REST·LLM 게이트웨이 호스트를 넣는지, "프록시 경유 의심" 줄이 코드가 env를
  보고 붙이는 것임을, `trust_env_proxy`의 자리.
- **H(system/user 분리)는 미뤘다** — 재측정이 R2-1·R2-2의 효과를 재는 자리라 프롬프트 구조를 같이 바꾸면 효과가 섞인다.
  R4-1과 함께([plan-12a-r2-r4.md](plan-12a-r2-r4.md)).
- 테스트 먼저(RED 5 → GREEN): `test_llm_adapters` 1, `test_cli` 4, 역할 배선 CLI 테스트에 `close` 횟수. 스윕 +5(460).

### 재측정(sevt, 10-08)과 R2-3

역할 지정(lead 빠른 모델 · conclude 생각 모델 · `stream`)으로 리드 턴 4~14초, 판정 턴은 226초가 걸려도 성공 — **180초 벽은 유휴
끊김이었고 stream이 풀었다.** 판정이 나오기 시작했고(1-3), 종료 트레이스백 없음(R2-2c), 대상 행 먼저(1-5) 확인. 새 문제 둘:
빠른 모델의 JSON 문법 오류(태스크 `goal`의 여는 따옴표 누락, 4번 중 1번 이상)와 분당 할당량 429(모델을 가리지 않고 공유).
**지난 리뷰의 c-1 원인("요약 파이프라인 use:false")은 오판이었다** — 이름이 비슷한 다른 배지였고, 리드 둘 다 같은 오판을 했고
verify가 통과시켰다. 실제 c-1: 요약 키 data 0건 ← 도메인 키 0건 ← 입력 5개 중 원천 키 하나 부재(TTL -2). c-2: 원천 키 554행
전원 문제 컬럼 둘이 `-`. 항목과 순서는 [plan-12a-r2-r4.md](plan-12a-r2-r4.md) R2-3·R3.

#### R2-3 ① (10-08) ✅ — 회귀 둘과 kafka tail

- **`llm check`**(2-5): 질문마다 `_close_llms`를 불러(R2-2c) 둘째 질문부터 닫힌 클라이언트로 나갔다 — 모든 모델이 고장난
  것처럼 보였다. 질문 셋을 한 루프에서 묻고 끝에서 한 번만 닫는다. CLI 테스트가 `asked == 3 · closed == 1`을 본다.
- **stderr 머리줄**(2-6): `diagnose.llm_error_line` — `⚠ LLM 오류 N건 — 조사 중단(llm_error) · 판정 <종류>` 또는
  `리드가 계약을 어겼다(조사는 끝까지 돌았다)`. "이 조사는 안 돌았다"는 llm_error 뒤에도 판정을 묻는 지금 거짓이었다.
- **`kafka tail`**(지시 5): `getmany`를 한 번만 불러 첫 배치(2건)만 받았다 — aiokafka는 레코드가 조금이라도 오면 바로 돌아온다.
  끝에서 뒤로 간 자리부터 받을 수 있는 수(`wanted`)를 채우거나, 더 안 오거나, 15초가 다할 때까지 돈다. 모자라면 봉투가
  "N초 안에 M건만"이라 말한다. 가짜 `aiokafka`로 단위 검증(`test_kafka_inspector.py` 새 파일 — 실물은 live).
- 테스트 먼저(RED 4 → GREEN). 스윕 +3(463).

#### R2-3 ② (10-08) ✅ — 429는 기다렸다 한 번 더, 호출 간격은 게이트웨이가 쥔다

- **`llm_pacing.py`**(2-2): 429 본문의 `nextAccessTime`(ISO·naive는 now의 시간대·epoch 초·밀리초)까지 기다렸다 **한 번만**
  다시 묻고 실패로 안 센다. 없으면 `Retry-After`, 그것도 없으면 60초 — 전부 `llm.rate_wait_max_s`(기본 120) 안. 두 번째도
  429면 오류(더 안 기다림). 할당량은 모델을 가리지 않으므로 `llm.min_interval_s`는 **base_url 단위 `Pacer`**가 지킨다 — 리드와
  판정 어댑터가 하나를 나눠 쓴다(둘째 어댑터의 첫 호출도 간격을 기다린다). 어댑터 둘이 같은 `ask → _once` 모양.
- **`LlmReply.waited_s`·`rate_limited`** → `ask_json` → 트레이스 훅 일곱째 인자 → 파일 머리 `대기: N초(429)` → 요약 머리줄
  `· 대기 N초`와 끝줄 `429 대기: 합계 N초 · 호출 M회`(브리프의 새 보고 항목). 안 기다렸으면 안 적는다.
- 가짜 게이트웨이에 429 본문 큐(`recorder.rate_limit`)와 `Retry-After`. 테스트 먼저(RED 9 → GREEN): pacing 2, 어댑터 4×2,
  schema 1, tracer 1, digest 1. 스윕 +13(476).

#### R2-3 ③ (10-08) ✅ — 답 스키마를 서버가 강제한다

- **`LlmPort.ask(prompt, *, schema=None)`**(2-1): `ask_json`이 답 모델의 JSON 스키마(`lead.response_schema`)를 넘기고 어댑터
  둘이 OpenAI 규약 `response_format`으로 보낸다(`llm_format`). **`strict`는 스키마가 닫힐 때만**(`domain/llm_schema.is_closed`):
  액션 턴은 태스크 `params`·`filter`가 자유형이라 닫을 수 없어 `strict: false`(문법과 윗단 모양만 강제 — 깨진 것은 따옴표였다),
  판정 턴은 pydantic 스키마를 `strictify`(전부 required·`additionalProperties: false`·default 제거, Optional은 null)해 `strict: true`.
  지시의 "strict: true"를 그대로는 못 하는 이유가 이것이다.
- `llm.response_format: json_schema(기본) | json_object | none` — 게이트웨이가 `json_schema`를 거부하는 배치는 `json_object`.
  자유 질문(`llm ask`)에는 안 보낸다. chat_model은 호출 kwargs로 넘긴다(langchain-openai가 요청 본문에 합친다 — 모델 객체에
  박으면 자유 질문까지 JSON을 강요한다). 스트리밍에서도 나간다(가짜 게이트웨이로 확인).
- 가짜 어댑터 넷이 `schema=`를 받고(`ScriptedAdapter.schemas`에 기록), 테스트의 지역 가짜들은 `**kw`로.
- 테스트: 스키마 닫힘 2, 어댑터 2(×2), 리드 2, 설정 1. 스윕 +10(486).

#### R2-3 ④ (10-08) ✅ — stream 기본 켬, 역할 설정 확정

- `llm.stream`은 `None`이면 chat_model 켬·http 끔(명시가 이김, http에 켜 달라면 전처럼 거부). 재측정에서 판정 턴 226초도
  stream이면 살았다 — 180초 벽은 유휴 끊김이었다. 가짜 게이트웨이의 마지막 조각에 `finish_reason: "stop"` — langchain-openai는
  그 조각에서만 `model`을 올리므로 없으면 스트리밍 답의 모델 확인이 죽는다(테스트가 잡았다).
- 역할 설정은 브리프에 JSON 그대로(lead `max_tokens 1500`, conclude `timeout_s 600`). 리포의 `config/app.json`에는 안 넣는다 —
  CLI 테스트가 그 파일을 복사해 쓴다(넣었더니 둘이 깨졌다). README에 사내 실측으로 정해진 `llm` 설정 요약. 스윕 +1(487).
- **작업 방식**: 이 스텝부터 전체 스윕과 기계적 커밋·푸시는 haiku 서브에이전트가 `sweep_commit.sh`로 돈다.

#### R2-3 ⑤ (10-08) ✅ — 프롬프트 상한

- `investigation.integrate_prompt_chars 8000`·`conclude_prompt_chars 10000`(2-4): `lead.fit_prompt`가 상한을 넘으면 넘친 만큼
  증거 예산을 줄여 다시 만든다(오래된 증거부터 `id | 질의 | 한 줄`로 접히고 최신은 통째로 — `evidence_block`의 규칙 그대로),
  바닥(1500 — 최신 한 건은 통째로)까지 접어도 넘으면 끝난 태스크 줄을 접는다(`tasks_block(fold=True)`: id·상태·goal 앞머리,
  실패한 것의 질의는 남긴다). 그래도 넘으면 그대로 보낸다(자르는 것보다 낫고 크기는 트레이스 머리에 남는다). frame은 상한 없음.
  좁혀 읽은 증거도 같은 규칙이다. 측정판 r2 integrate는 7.2K라 안 접힌다 — 사내 22.8K가 접힐 자리다. 스윕 +5(492).

#### R2-3 ⑥ (10-08) ✅ — verify의 component는 서비스만, 규칙 두 줄

- **component는 토폴로지 서비스 이름만**(3-3(a)): c-2에서 원인 칸에 Redis 키 이름이 들어갔는데 "증거에 나온 이름"이라 통과했다.
  `_component_problem` — 토폴로지를 아는 조사(`components`가 비어 있지 않음)에서는 서비스만, 사유에 되는 이름을 같이 적는다.
  `external` 판정만 예외(바깥 시스템은 토폴로지에 없다 — 증거에 나온 이름이면 된다). 토폴로지가 없는 조사는 옛 규칙. (b)(c)는
  입구 매핑(R3)이 있어야 한다.
- integrate 규칙 두 줄: "값이 비었으면 다음 홉은 그 키를 쓰는 쪽(`code.uses`)과 그 파이프라인의 입력 키"(3-5), "인자는 그
  읽기의 것만 — 다른 읽기의 인자를 옮겨 붙이면 버려진다"(3-4의 싼 절반). 예시의 자유 칸을 자리표시자로 바꾸는 나머지 절반은
  R3-0으로 — 입구 매핑이 결정적 읽기를 frame 앞으로 옮기면 frame 예시 자체가 줄어서 그때 한 번에 바꾼다.
- 스윕 +4(496). **R2-3 끝 — 사내 측정 #3**([review-12a-4.md](review-12a-4.md) "R2-3 뒤 측정 #3에서 더 볼 것").

### 측정 #3(sevt, 10-08)과 R2-4

판정 턴(생각 모델)이 `json_schema`를 걸면 4/4 JSON이 깨졌다 — 같은 프롬프트를 스키마 없이 보내면 121초에 정상이었다. 리드 턴(빠른
모델)은 JSON 실패 0. 429는 매번 정확히 60.0초(기본값) — nextAccessTime이 `2026-Oct-08 02:09:00+0000 UTC`라 못 읽었다. integrate
고정부가 7.1~7.9K라 증거가 1.6~4.5K로 깎였다. 10054 트레이스백 재발, kafka tail은 파티션 1개에 0건/168건. 항목·순서는
[plan-12a-r2-r4.md](plan-12a-r2-r4.md) R2-4.

#### R2-4 ① (10-09) ✅ — 10054는 콜백 실패로 온다

R2-2c의 거름망은 context에 `transport`·`protocol` 키가 있을 때만 걸렀다. 그런데 proactor가 끊긴 소켓을 치우는
`_call_connection_lost`가 실패하면 asyncio `Handle._run`이 `message`·`exception`·`handle`만 실어 보낸다 — `transport` 키가 없다
(CPython 소스로 확인). 그래서 그대로 찍혔다. 콜백의 qualname이 `_ProactorBasePipeTransport._call_connection_lost`이고 예외가
연결 리셋일 때만 거른다 — 다른 콜백의 같은 예외, 같은 콜백의 다른 예외는 기본 처리기로. 테스트는 실제 `asyncio.Handle`로 그
context를 만든다(Windows 재현은 여기서 안 된다 — 측정 #4에서 트레이스백 유무를 받는다). 스윕 +2(498).

#### R2-4 ② (10-09) ✅ — 429는 게이트웨이의 시각까지, 근거를 남긴다

- 사내 게이트웨이의 `nextAccessTime`은 429 본문 최상위에 `2026-Oct-08 02:09:00+0000 UTC` 모양이다(월 영문 약어, 오프셋 뒤 `UTC`).
  ISO로 못 읽어 매번 기본값 60초였다. `_gateway_form`이 월 약어를 **표로** 숫자로 바꾸고(`strptime("%b")`는 로캘을 따르고 사내
  Windows는 한국어다) 끝의 `UTC`/`GMT`를 뗀다. 오프셋 없이 `UTC`만 있으면 UTC로 본다(KST 시계로 테스트). 모르는 약어는 그대로
  둬서 파싱이 실패하게 한다 — 지어낸 달로 기다리지 않는다.
- openai SDK는 429 본문에서 `error` 안쪽만 예외에 남긴다(`body.get("error", body)`) — 시각이 `error`의 형제여도 찾게 응답 원문도
  같이 본다(chat_model). 사내 본문은 `error` 키가 없어 이번 원인은 아니었다.
- `quota_wait`가 `(초, 근거)` — `nextAccessTime`·`Retry-After`·`기본값`. `LlmReply.rate_source` → 트레이스 `대기: N초(429·근거)` →
  요약 끝줄 `· 근거 nextAccessTime 1 · 기본값 1`. 브리프에 측정 #4 항목과 `min_interval_s: 15`. 스윕 +7(505), 옮겨진 앵커 셋.

#### R2-4 ③ (10-09) ✅ — kafka tail은 빈 배치 한 번에 멈추지 않는다

사내 원천 토픽은 파티션 하나인데 `--limit 300`이 0건(8초)/168건(44초)였다. R2-3 ①의 루프는 빈 배치(1초 기다림) **한 번**에
멈췄고, 새 컨슈머의 첫 fetch는 1초를 넘기기 쉽다. 이제 받을 수 있는 것(`wanted` — 끝에서 뒤로 간 자리부터 끝 오프셋까지)을 다
받거나, 빈 배치가 **연속** 세 번이거나(받을 때마다 센 것을 지운다), 15초가 다할 때까지 돈다. 모자라면 봉투가 `빈 응답 3번 뒤
N건만(받을 수 있던 것 M건)`처럼 말한다. 168건은 그 토픽에 남아 있던 전부였을 수 있다(보존 기간) — 그때는 완전한 결과다. 44초는
루프 상한 밖이라 접속·메타데이터 시간이다(측정 #4에서 다시 본다). 가짜 aiokafka에 첫 응답 지연·끝없는 빈 응답·드문드문 빈 응답을
더했다. 스윕 +3(508).

#### R2-4 ④ (10-09) ✅ — 판정 턴은 스키마 없이, 깨진 답의 재시도도 스키마 없이

- 측정 #3: 판정 턴(생각 모델)에 `json_schema`를 걸면 4/4 깨졌다(`{verdict{"verdict_type":…` 모양, 237~285초). 같은 프롬프트를 스키마
  없이 보내면 121초에 정상. 리드 턴(빠른 모델)은 JSON 실패 0 — 스키마가 따옴표 누락을 막았다.
- **역할 기본값** `_ROLE_DEFAULTS = {"conclude": {"response_format": "none"}}` — 기본 `llm`과 역할 덮어쓰기 사이에 깔린다(덮어쓰기가
  이긴다). 역할별 설정은 원래 됐다(`llm_roles.conclude.response_format`) — 바뀐 것은 기본값이다. 결과가 기본과 같을 때만 같은 객체를
  돌려주므로, 이제 판정 어댑터는 따로 하나 생긴다(`llm describe`에 판정 줄이 늘 보인다). CLI 테스트 셋은 `build_llm` 자리에 대본을
  어댑터마다 새로 줬다 — 둘이 각자 처음부터 읽어 판정 턴이 액션 턴의 답을 받았다. 대본 하나를 나눠 쓰게(`_one_script`) 고쳤다.
- **깨진 답의 재시도는 스키마를 뗀다**(모든 턴): 스키마를 건 답이 파싱에 실패했다면 모델이 그 강제에 약한 것이고, 같은 스키마로
  다시 물으면 같은 모양으로 또 깨진다(4분짜리 턴을 두 번). 호출 자체가 실패한 재시도는 스키마를 그대로 둔다.
- 테스트 먼저(RED 3 → GREEN). 스윕 +5(513), 옮겨진 앵커 둘.

**핫픽스(10-09, `b4e4c37`)**: 사내 `app.json`에 `llm_roles`(report·lead 339, conclude 581)가 들어가자 `test_lead.py`의 CLI 테스트 둘이
깨졌다 — 복사한 config 때문에 판정 어댑터가 따로 생기고, `build_llm` 자리의 대본이 어댑터마다 처음부터 읽혔다. 테스트 헬퍼가 복사한
`app.json`에서 `llm_roles`를 걷어낸다. 사내와 같은 역할 블록을 넣어 재현(RED 2) → 고친 뒤 통과. ④는 그 위로 올렸다(`a7093ac`).

#### R2-4 ⑤ (10-09) ✅ — 고정부를 줄이고, 증거는 4K 밑으로 깎지 않는다

측정 #3: integrate 10.4~14.5K 중 고정부만 7.1~7.9K, 8K 상한을 맞추려다 증거가 1.6~4.5K로 깎였다. 상한의 이유였던 지연은 빠른 모델이
13~17K를 4~14초에 처리해 약해졌다 — 증거를 굶기는 쪽이 더 큰 손해다.

- **서비스 설명은 첫 구절 40자**(`briefing._role_brief`): 읽기 목록의 `service 자리에 쓸 이름` 줄. 잘랐으면 `설명 전문은
  code.services`를 붙인다(안 적으면 리드는 잘린 것을 전부로 안다). 사내 고정부의 큰 몫이 긴 문단 열 개였다.
- **해당 없는 규칙 줄은 뺀다**(`lead._conditional`): 템플릿의 규칙 줄 머리에 `{?블록}` 표지 — 그 블록이 비었으면(`(없음)`·`(아직
  없다)`) 그 줄과 이어지는 들여쓴 줄까지 뺀다. 무엇을 뺄지는 템플릿(운영이 고치는 파일)이 정하고 코드는 블록이 비었는지만 본다.
  integrate는 잘린 증거·실패한 읽기·열린 질문 규칙(`{?open}`)과 버려진 태스크 규칙(`{?rejected}`), conclude는 앞의 둘.
- **끝점 줄도 흐름 예산에 센다**: R2-2b-2에서 예산 밖에 두었더니 흐름 블록이 그만큼 커졌다. 맨 앞에 늘 실리는 것은 그대로.
- **증거 바닥 4K**(`lead._EVIDENCE_FLOOR`): 상한보다 이긴다. 그래도 넘으면 끝난 태스크 줄을 접고, 그래도 넘으면 그대로 보낸다.
  예산이 원래 4K보다 작으면 그대로다(루프 조건이 이미 거짓 — 처음 넣은 `min`은 스윕이 초록으로 잡아 뺐다).
- **요약이 고정부를 쪼갠다**: `프롬프트 N자 = … + 읽기 목록 · 흐름 · 케이스 · 열린 질문 · 예시 · 나머지`(나머지는 템플릿 자체).
- 실제 소비자로: 측정판 `cache-missing` r1 integrate는 7,277자 = 증거 1,924 + 태스크 489 + 읽기 목록 928 + 흐름 833 + 케이스 227 +
  열린 질문 55 + 예시 742 + 나머지 1,982 — **고정부 약 4.0K**. 측정판의 서비스 설명은 원래 짧아 줄 것이 없고, 이 판은 열린 질문이
  있어 그 규칙은 남고 버려진 태스크 규칙만 빠졌다. 사내에서 줄어드는 몫(서비스 설명)은 측정 #4의 새 분해 줄로 본다.
- 테스트 먼저(RED 6 → GREEN). 흐름 테스트 둘은 "예산 밖"에서 "맨 앞, 예산에 센다"로. 스윕 +8(521), 옮겨진 앵커 넷.

#### R2-4 ⑥ (10-09) ✅ — 태스크 id 인용은 코드가 고치고, caveats는 문장으로

- **`nodes.resolve_task_citations`**(지시 7): 판정이 태스크 id(`t-8`)를 증거로 인용했고 그 태스크가 만든 증거가 **하나뿐**이면 그 증거
  id로 바꾸고 caveat에 `인용 정리: … t-8→t-8.e1`을 남긴다. 지금까지는 verify가 "없는 id"로 판정 턴을 다시 물었다 — 사내 판정 턴은
  한 번에 4분이다. 바꾼 id는 State에 실재하는 증거라 규율 3 안이다. 둘 이상이면 고르지 않는다(코드가 고르면 판정이 인용하지 않은
  것을 인용한 것이 된다) — 그대로 두고 verify가 전처럼 되묻는다. 판정이 나오는 두 길(평소·llm_error 뒤) 모두 `sanitize_verdict` 앞에서.
- **caveats는 문장으로**(지시 6): 판정 예시의 caveats 자리가 "증거 id를 적는다"였고, 사내에서 id 나열만 나왔다. "무엇이 잘리거나
  실패해서 무엇을 단정 못 하는가, 그 증거 id와 함께"로. verify의 검사(잘린 증거로 주장했으면 그 id가 caveat에 있어야 한다)는 그대로.
- 대안 component 칸의 키 이름(지시 6의 앞부분)은 `llm ask`로 손으로 보낸 답이라 verify를 안 거쳤다 — 엔진의 검사는 R2-3 ⑥부터
  대안까지 본다(사내 답 4).
- 테스트 먼저(RED 2 → GREEN). 스윕 +4(525), 옮겨진 앵커 하나. **R2-4 끝 — 측정 #4**([review-12a-4.md](review-12a-4.md) "R2-4 뒤
  측정 #4에서 더 볼 것").

### 측정 #4(sevt, 10-09)와 R2-5

R2-4가 겨눈 것은 다 들어왔다: 판정 턴 JSON 실패 0(30~76초 — 전엔 237~285초에 실패), 리드 JSON 실패 0, 두 케이스 3분 27초·4분 26초,
429는 c-2만 2회 합계 30초(근거 `nextAccessTime` — 시각을 읽었다), 10054 없음, 고정부 5.6~6.0K, kafka tail 300에 183/250/258건(15초
상한을 봉투가 말함; 명령당 29~55초 중 접속이 약 40초). 남은 것: c-1 r1에서 스트림이 조각 520개 뒤 멈춰 1라운드 판정(그래서 c-1의
어긋난 판정은 정확도의 재료가 못 된다), c-2 강등에 서비스 아닌 component가 남음, recompute 기대값이 첫 행, 흐름 블록 0.7K → 1.5K.
정확도(대상 행 → 키)는 R3. 항목·순서는 [plan-12a-r2-r4.md](plan-12a-r2-r4.md) R2-5.

#### R2-5 ① (10-09) ✅ — 멈춘 스트림: 받은 글을 남기고, 온전하면 쓰고, 아니면 한 번 다시

- 원인(코드로 확인): `StreamChunkTimeoutError`는 langchain-openai의 `stream_chunk_timeout`(기본 120초, 조각 사이 침묵 — 첫 조각 전도
  센다)이 낸다. 그 문구의 "timeout" 때문에 `lead._is_timeout`이 시간 초과로 분류해 재시도 없이 `llm_error`, 어댑터는 모은 조각을
  버려 트레이스엔 "(응답 없음)". 조각 520개는 리드 답 한 벌 크기라 **끝 표시만 빠진 답**이었을 가능성이 크다.
- (c) `LlmReply.partial_text` — 끊기기 전까지 받은 글(실패한 답에만; 성공한 답은 `text`). 트레이스 날것에 실린다.
- (a) `ask_json`: 받은 글이 있으면 시간 초과가 아니다. 답 모델로 검증되면 그대로 쓰고(`스트림: 끝 표시 없이 끊김 — 받은 글 N자로
  읽었다 · …`), 아니면 전송 실패처럼 같은 질문·같은 형식으로 한 번 더(`스트림: 답 도중 끊김 — …다시 묻는다`). JSON 객체는 닫는
  괄호까지 와야 읽히므로 잘린 답이 검증을 지날 수 없다. 조각 0개 멈춤은 전처럼 시간 초과. 오류 문구는 첫 문장만(라이브러리의 설정
  안내까지 400자 가까이라 손으로 옮기는 머리줄이 그것으로 찼다 — 첫 문장에 상한·모델·조각 수가 다 있다).
- (b) `llm.stream_idle_s` → `stream_chunk_timeout`(그 필드가 있는 langchain-openai일 때만 — 없으면 기본 상한 그대로). 코드 기본값은
  두지 않는다: 리드는 역할 설정(브리프 `llm_roles.lead.stream_idle_s: 30`), 판정의 생각 모델은 첫 조각까지 오래 걸릴 수 있어 기본.
- (d) `_run`이 그 WARNING 레코드(`source == "stream_chunk_timeout"`)만 거른다 — 같은 로거의 다른 경고는 그대로.
- P1 5(400 `filter server request failed` 뒤 재시도로 성공): 동작은 이미 그랬다(시간 초과 아닌 전송 실패는 2초 뒤 같은 질문으로
  한 번). 요약 끝에 `전송 실패: N회 — 사유`와 `스트림 끊김: N회 — 받은 글로 읽음 K · 다시 물음 M`을 더했다.
- 실제 소비자로: 진짜 HTTP 가짜 게이트웨이가 조각 둘을 보내고 끝 표시 없이 멈추게 해 `ChatModelAdapter`(유휴 0.3초) → `ask_json` →
  `_make_tracer` → `digest`를 이었다. 온전한 답은 요청 1번에 읽혔고, 깨진 답은 같은 메시지로 한 번 더 물어 성공했으며, 두 트레이스
  머리에 `스트림:` 줄과 받은 글이, 요약 끝에 `스트림 끊김: 2회 — 받은 글로 읽음 1 · 다시 물음 1`이 찍혔다. 거름망을 단 뒤 stderr의
  경고 줄이 사라진 것도 같은 판에서 봤다.
- 테스트 먼저(RED 8 → GREEN). 요약 테스트는 두 수가 같아(1·1) 둘을 뒤바꾼 변이가 초록이었다 — 2·1로. 스윕 +16(541), 옮겨진 앵커 하나.

#### R2-5 ② (10-09) ✅ — 강등은 검사와 같은 규칙으로 걷어낸다

- 원인(제 버그): R2-3 ⑥에서 verify에 규칙 3(component는 토폴로지 서비스)을 넣으면서 `demote_verdict`는 그대로 **인용만** 걸렀다.
  그래서 c-2의 `후보 external`(인용은 멀쩡)이 두 번 되물음 뒤 강등 출력에 그대로 남았다. 이제 verify가 쓴 그 검사(`component_problem`)를
  강등에 넘겨, 서비스 아닌 component의 다리도 뺀다. 최상위가 빠지면 `inconclusive`(근거 잃은 최상위와 같은 규칙), caveat에
  `서비스 아닌 component 제외: 후보 external, 기여 요인 …`.
- 판정 프롬프트 한 줄: 원인이 데이터(키·컬렉션·토픽)면 그 데이터를 **만드는** 서비스, 데이터 이름이나 판정 종류(`external` 등)는
  component가 아니고, 만드는 쪽이 목록 밖일 때만 `verdict_type: external`. c-2는 판정 종류 값을 component 칸에 넣었다 — 두 칸을
  헷갈린 것. 사내 서비스 이름 꼴(collector/processor)은 안 넣는다(⑮) — 목록은 `{components}`가 준다.
- 한계: 판정 모델에는 다리마다 종류가 없어 "최상위는 안, 후보 하나는 바깥"을 적을 수 없다 — 이번엔 걷어내고 caveat에 남긴다.
- 테스트 먼저(RED 2 → GREEN). 프롬프트 테스트는 "만드는" 한 단어만 봐서 같은 문단의 "만드는 쪽이…"로 초록이었다 — 구절로. 스윕 +4(545),
  옮겨진 앵커 하나(같은 줄을 지키던 새 케이스 하나는 겹쳐서 뺐다).

#### R2-5 ③ (10-09) ✅ — recompute 기대값은 대상 행

- 원인은 **우리 예시**였다: recompute 칸의 `expect.path`가 `"response.items[0].alarm 같은 모양"`이라 리드가 `[0]`(첫 행 — 다른 배지)을
  옮겼다. 그리고 하나 더 — 증거의 행 번호는 `[i + 1]`(1부터)인데 경로 문법(`redis.get`의 path·`expect.path`)은 0부터라, 리드가
  증거의 `[16]`을 그대로 옮겨도 옆 행이었다. R2-2b-2의 "그대로 옮겨 적을 수 있다"는 주석이 사실이 아니었다.
- 행 번호를 **0부터**(경로 문법과 같다). 증거 머리줄은 건수 대신 대상 행의 **경로**: `response: 18건 · 필드: … · 대상 행 response[15]
  먼저`(dict 안 목록이면 키를 붙인다). `runner_probe.target_rows`가 그 줄을 읽고(같은 모듈이 쓰고 읽는다), 브리핑이 케이스 블록에
  `대상 행: t-1.e1 response[15]`, recompute 예시에 `response[15].화면이 보여 준 그 숫자의 필드`를 싣는다. 모르면 `response[대상 행
  번호]` — 지어낸 번호를 보여 주지 않는다.
- 실행 전 검사(`runner_probe.target_row_problem`, 규율 3의 연장): `expect.path`가 문서 목록의 행을 지나면 그 행이 케이스 target 값을
  다 담는지 본다(증거에서 대상 행을 앞세운 것과 같은 기준). 아니면 `expect.path의 response[0]는 케이스 대상(L1/Alarm)의 행이 아니다 —
  대상 행은 response[15]. 그 경로로 다시 내라`로 거부 — 엉뚱한 "불일치"가 사실로 남지 않는다. 그 목록에 대상 값이 든 행이 없거나
  target이 없으면 가릴 수 없어 막지 않는다. 경로 걷기는 `domain/jsonpath.steps`(같은 문법).
- 실제 소비자로: 측정판 `cache-missing`(r25b3b, 그래프를 만든 뒤)에서 r1 integrate 프롬프트의 케이스 블록에 `대상 행: t-1.e1
  response[15]`, 증거에 `대상 행 response[15] 먼저`와 `[15] {"group":"L1","title":"Alarm",…}`, 예시의 recompute에 `"path":
  "response[15].화면이 보여 준 그 숫자의 필드"`가 실렸고, 일부러 낸 `response[0].alarm`은 r2 태스크 줄에 위 거부 사유로 남았다.
  (첫 판은 측정판이 대상 코드를 다시 만들어 그래프가 낡아 `code.trace`가 실패했다 — 케이스를 만든 뒤 그래프를 만든다.)
- 테스트 먼저(RED 5 → GREEN). 기존 둘은 0부터의 번호와 경로 머리줄로 고쳤다. 스윕 +7(552).

#### R2-5 ④ (10-09) ✅ — 흐름 블록의 고정 줄은 케이스 자신의 끝점만

- 재현 먼저(측정판 밖, 실제 `briefing.flow_block`): 끝점 넷(각자 읽는 컬렉션 10개)인 그래프에 접수 경로 `/summary/badge`, 증거로
  다른 끝점 셋이 든 grep 결과 하나. frame 917자 → integrate **1,457자**. 가설대로였다 — 끝점 줄은 예산 앞에 고정인데 씨앗을 증거
  본문에서도 찾아, 증거에 나온 다른 끝점 둘이 고정 줄이 됐고(줄당 ~450자) 고정 줄만으로 예산 800을 넘겨 **접수 끝점의 이웃(서빙
  서비스·읽는 자원)이 전부 밀려났다**(`… +31줄`). 하나 더: 끝점 씨앗은 자원 씨앗 셋 상한 안에서 이름순이라, 접수 끝점 이름이 뒤쪽이면
  증거의 끝점들에 밀려 씨앗에서 빠진다.
- `flow_block`이 케이스 자신의 끝점(증상·접수 경로에서 찾은 것)을 따로 구해 씨앗 맨 앞에 두고(상한과 무관) `flow_text(pin=)`으로
  그것만 고정한다. 다른 끝점 씨앗은 이웃을 펼치지 않고 **맨 뒤에** 예산 안에서. `pin`을 안 주면 전처럼 끝점 씨앗 전부가 고정.
- 같은 재현이 frame 917자 · integrate 917자 — 접수 끝점의 이웃이 남고 다른 끝점은 뒤에서 예산에 잘린다. 사내 방향("증거가 늘면 흐름은
  대상 관련 줄만")과 같다.
- 테스트 먼저(RED 2 → GREEN) — 브리핑 쪽은 접수 끝점 이름을 뒤쪽(`/summary/zeta`)에 둬 씨앗에서 빠지는 것까지 잡는다. 스윕 +4(556),
  옮겨진 앵커 하나. **R2-5 끝 — 측정 #5**([review-12a-4.md](review-12a-4.md) "R2-5 뒤 측정 #5에서 더 볼 것").

### 측정 #5(sevt, 10-13)와 R3

R2가 겨눈 운영 지표는 전부 목표 안이다 — 리드 턴 3.8~14.4초, 판정 31~38초, 케이스 1분 41초·3분 4초, 계약 위반 0, 스트림 끊김 0,
10054 없음, 전송 실패 1(403, 재시도로 성공), 429는 c-2만 2회 합계 32초(근거 nextAccessTime), 흐름 블록 730→586이고 첫 줄은 접수
끝점, 행 번호 0부터 OK. **R2 끝.** 남은 것은 정확도이고 이번 c-1이 가장 나쁜 종류였다 — 빠르고(1분 41초, 리드가 r2에서 스스로
conclude), `data_loss(high)`로 **틀렸다**. 흐름 줄의 키 순서를 `prefer=case_words`(증상·target 단어의 부분 문자열, R2-2b-2)가 정해
이름이 비슷한 다른 배지의 키가 첫 자리에 왔고, 리드가 그 키를 읽어 `None`을 유실의 증거로 삼았다. 이 계획 R3의 "이름 유사성은 쓰지
않는다"를 내가 어긴 자리다. 항목·순서는 [plan-12a-r2-r4.md](plan-12a-r2-r4.md) R3.

#### R3-0 ① (10-13) ✅ — 대상 행의 링크 토큰 → 대상 키, 문장으로

- 토폴로지 `intake: {field, token}`(`knowledge/schema.py` `IntakeLink`) — 접수 끝점 응답의 대상 행에서 링크가 든 필드와, 그 값에서
  키의 이름 토큰을 뽑는 정규식(그룹 하나, 기동 검증). 리포 값은 자리표시자(`link`, `/summary/badge/([^/]+)/`) — 실제 필드와 모양은
  사내가 채운다(⑮). 측정판 배지 행에 같은 모양의 `link`(Alarm만 `stats`, 나머지는 어떤 키에도 안 드는 토큰).
- `application/intake_map.py`: 증거가 짚은 대상 행(R2-5 ③ `target_rows`)의 `[n] {…}` 줄을 다시 읽어 링크 값 → 토큰 → 접수 끝점이
  읽는 선언 키(`flow.declared_keys`) 중 토큰이 든 것. **하나일 때만 고른다.** 0개·2개 이상·토큰 못 뽑음·링크 필드 없음·행이 잘림은
  전부 그 사실 그대로(코드가 하나를 집으면 판정이 인용하지 않은 것을 코드가 인용한 것이 된다 — R2-4 ⑥과 같은 규칙).
- 케이스 블록에 **문장으로**: `대상 키: alarm:stats:{line} (대상 행 t-1.e1 response[15]의 링크 토큰 'stats'에서)` / `대상 키: 못 골랐다 —
  …`. 순서는 리드가 못 읽는 신호다. 흐름 줄의 `prefer`는 그 토큰뿐이고 `case_words`는 뗐다 — 토큰이 없으면 아무것도 앞세우지 않는다
  (알파벳순은 아무 주장도 아니다). frame은 접수 응답을 아직 안 봐 줄이 없다 — R3-1이 frame 전에 읽는다.
- 배선: `make_lead(intake=)` → 세 턴의 fields → `briefing.target_key`(한 함수 — ②의 verify도 이것을 쓴다). `DeployedCode.intake()`.
- 솔직한 한계: "토큰이 키 이름에 든다"도 부분 문자열 대조다. 다만 상대가 증상 문장이 아니라 토폴로지가 선언한 링크의 식별자다.
  R3-1의 선언된 사슬(토큰 → api config 경로 → 키 설정 → 접두+키)이 이것을 대체한다. 대소문자는 그대로 대조한다.
- 실제 소비자로: 측정판 `cache-missing`(r30b1, 그래프 생성 뒤) r1 integrate 프롬프트의 케이스 블록에 위 `대상 키:` 줄이 실렸고 끝점
  줄의 `reads(config키)`가 `alarm:stats:{line}`로 시작했다. `cmd_case_investigate`의 `intake=code.intake() if code else None` 한 줄은
  스윕 짝이 없다(코드 트리로 조사를 돌리는 테스트가 없다) — 이 판이 그 줄의 확인이다.
- 테스트 먼저(RED 7 + 새 모듈 3 → GREEN). 스윕 +12(568), 옮겨진 앵커 넷(그중 `case_words` 케이스는 `prefer=target.prefer`로).

#### R3-0 ② (10-13) ✅ — 대상과 연결 안 된 키로 high 금지

- `nodes.cap_unlinked_key`(verify 안, 코드만): 최상위 원인이 인용한 증거 중 **키 읽기**(`redis.get` — 증거 `source`의 `describe` 문자열을
  되읽는다, `intake_map.cited_keys`)가 있고 그 키가 ①의 대상 키(템플릿 — `facts.present`로 대조)가 아니면 confidence 상한 medium +
  caveat `대상과 키의 연결이 확인되지 않았다 — 최상위가 인용한 키 …; 대상 키 …`. 되묻지 않는다 — 한도다(R3 verify (b)의 싼 절반).
  대상 키를 모를 때(선언 없음·못 고름)도 건다 — 연결이 확인되지 않은 것은 같다. 키를 인용하지 않은 최상위는 이 규칙 밖(R3-2),
  scan 패턴은 키 읽기가 아니다, 대본 경로(`check_discovery=False`)는 안 건다(`_component_problem`과 같은 스위치). 이 규칙은 판정을
  맞게 만들지 않는다 — 거짓 확신을 막을 뿐이다.
- `EngineDeps.intake` — verify가 브리핑과 같은 재료(`declared["rediskey"]` + 토폴로지 `intake`)로 같은 대상 키를 본다. `__main__`이
  `code.intake()`를 넘긴다(①과 같이 스윕 짝 없음 — 아래 판이 확인).
- **곁다리(종료 판단에 필요)**: 측정판이 판정 턴에서 멈췄다. `FileTurnAdapter`가 번호를 메모리에 세서, R2-4 ④부터 따로인 판정 어댑터가
  리드의 `001-ask.md`를 덮어쓰고 이미 소비된 답을 기다렸다(R2-5 ③·R3-0 ①의 판에서 "004-ask 대기 시간 초과"가 그것이었다). 번호는
  폴더가 센다(`_next_turn`). 테스트는 어댑터 둘이 같은 폴더에서 001·002를 이어 쓰는지.
- 실제 소비자로: 측정판 `cache-missing`(r30b2b)에서 대본 리드가 `redis.get key=hb:sink`(대상 키가 아니다)를 읽고 그것만 인용해
  `data_loss(high)`를 내자 출력이 `판정 data_loss (medium)` · `주의 대상과 키의 연결이 확인되지 않았다 — 최상위가 인용한 키 hb:sink; 대상 키
  alarm:stats:{line}` · `검증 통과`였다.
- 테스트 먼저(RED 2 + 파일 턴 1 → GREEN). 스윕 +7(575).

#### R3-0 ③ (10-13) ✅ — 키 지도의 경로는 이름이 아니다 · `code.flow` 서비스 요약 · `code.uses` 서비스 안내

- P1 3: 키 지도(`code.config`가 상한을 넘겨 지도만 줄 때)의 첫 키가 `_주의` — "아래 경로는 설정 안의 위치이지 컬렉션·키 이름이 아니다
  — 그 자리의 값은 code.config key=<경로>로". 그리고 실행기(`ProbeRunner`)가 앞서 돌려준 키 지도의 경로를 기억해 두고, `collection`·`key`
  인자가 그 경로와 **정확히 같으면** 소켓 전에 거부한다(`… 설정의 경로(앞선 키 지도)이지 이름이 아니다 — code.config key='…'로`). 모양으로는
  못 거른다 — Mongo 컬렉션 이름에 점이 올 수 있다. 사내 c-2가 `mongodb_collection.<이름>`을 컬렉션으로 냈던 것.
- P1 4: `code.flow name=<서비스>`는 엣지 나열(40줄에서 잘림 — 사내 684줄) 대신 `flow.service_summary` — `api [service · dt-api]: 관계 N개`,
  관계별 `declares: 컬렉션 12 — c00, … 외 4개`, 끝에 "자원 하나의 배선은 code.flow name=<그 자원 이름>으로". 자원 이름은 전처럼 엣지.
- P1 5: `code.uses name=<서비스>`는 "서비스 이름이다 — 배선은 code.flow name='…', 자원을 쓰는 함수는 그 자원 이름으로"로 실패 사유.
- 실제 소비자로: 측정판에서 대본 리드가 r1에 `code.flow name=api`·`code.uses name=sink`를 내자 r2 태스크 줄이 각각 관계별 요약과 서비스
  안내였다(아래 확인 줄). 키 지도 거부는 측정판 config가 상한(2400자) 아래라 판에선 안 나온다 — 실행기 테스트(가짜 키 지도)와
  `deployed_code` 테스트(300키 config)가 각각 지킨다.
- 테스트 먼저(RED 4 → GREEN). 스윕 +6(581). **R3-0 끝 — 측정 #6**([review-12a-4.md](review-12a-4.md) "R3-0 뒤 측정 #6에서 더 볼 것").

#### R3-0 ④ (10-13) ✅ — 선언을 뺀다: 대상 키는 대상 행 자신의 값에서

- 결정(사람, 10-13): ①의 토폴로지 `intake: {field, token}` 선언은 끝점마다 사람이 적어야 한다 — 단기엔 특정 케이스를 다루지만 장기
  유지보수를 어렵게 하는 요소이고, 측정하고 정 안 되면 그때 더하는 것이 맞는 순서다. **선언 배선 전부를 걷어냈다**: `IntakeLink`·
  `Topology.intake`·`knowledge/topology/mx.json`의 `intake`·`DeployedCode.intake()`·`make_lead(intake=)`·`EngineDeps.intake`·`__main__`의
  두 줄. 사내가 토폴로지에 적을 것이 없다.
- 대신 `intake_map.map_target(state, declared_keys=)`가 **대상 행 자신의 값**으로 고른다: 행의 문자열 필드에서 영숫자 토큰(3자 이상,
  숫자만인 것 제외, 대소문자 그대로)을 뽑아 접수 끝점이 읽는 선언 키 이름과 맞추고, **정확히 하나의 키에만** 들 때 그 키(가장 긴 토큰이
  근거). 0개·둘 이상·행 잘림은 그 사실 그대로 `못 골랐다`. 케이스 블록 줄은 `대상 키: alarm:stats:{line} (대상 행 t-1.e1 response[15]의
  link 값 토큰 'stats'가 이 키에만 든다)` — 무엇을 근거로 골랐는지가 줄에 있다. 못 골랐으면 흐름 줄도 아무것도 앞세우지 않는다.
- ②의 상한은 그대로 — 못 골랐을 때 더 자주 걸리고 그게 맞다(`대상 키를 못 골랐다 — 고를 재료가 없다(접수 응답이나 선언 키)` / `… 값이
  선언 키 2개에 든다 — …`). ③·파일 턴 수정·측정판의 링크 필드(선언이 아니라 사내와 같은 데이터 모양)는 그대로.
- R3 계획의 전제도 바꿨다: "토폴로지 config가 사슬을 선언하고 코드가 걷는다" → "코드·config에서 뽑은 그래프와 실제 데이터에서 코드가
  걷는다, 새 선언 없음". 토폴로지의 기존 `flow.sources`(대상 config의 모양을 한 번 적는 것)는 케이스·끝점마다 늘지 않으므로 그대로.
- 정직한 대가(사람이 확인함): 추정은 선언보다 자주 실패한다. 그때 틀린 키를 집지 않고 "못 골랐다" + medium 상한으로 멈춘다. R3-1이
  그래프 사슬과 frame 전 읽기로 빈틈을 좁힌다.
- 실제 소비자로: 측정판 `cache-missing`(r30b4, 토폴로지에 `intake` 0건)에서 r1 케이스 블록에 위 줄이 실렸고, 대본 리드가 `hb:sink`만
  인용한 high 판정이 `data_loss (medium)` + caveat로 나왔다. 테스트 먼저(RED 5 + 모듈 함수 부재 → GREEN). 스윕 581 → 578(선언 케이스
  넷 제거, 추정 케이스 추가, 옮겨진 앵커 둘).

### 측정 #6(sevt, 10-13)과 R3-1

②(확신 상한)·③(키 지도 거부)은 작동했고, ④의 토큰 규칙은 두 케이스 다 틀렸다 — 유일성을 키 쪽에서만 봐서 무관한 라벨 필드의 조각이
한 키에만 들어 "유일"이 됐고 c-2는 틀린 키를 케이스 블록에 사실처럼 박았다. 영숫자·3자·부분 문자열은 config의 손 규칙을 코드로 옮긴
것일 뿐이었다. **정정(사내 10-13 재확인)**: c-1은 키 없는 케이스가 아니다 — 대상 요약 키는 있고 data가 빈 배열, 값의
`record[0].summary_info`에 대상 행의 identity가 그대로 있다(c-2도). 없는 키는 이름이 비슷한 다른 배지의 키였다. 측정판 `cache-missing`은
c-1형이 아니다. 항목·순서·B를 #7 뒤로 미루는 이유는 [plan-12a-r2-r4.md](plan-12a-r2-r4.md) R3-1.

#### R3-1 ①-a (10-13) ✅ — frame 전 입구 노드: 값 안의 identity로 대상 키

- 그래프에 `intake` 노드(START → intake → frame). 재료는 `EngineDeps.intake`(`intake_map.intake_spec` — 점검이 본 프로브의 읽기와
  `identity` 필드, 전부 점검 config에서). 사람이 연 케이스·대본(`check_discovery=False`)·이미 고른 State는 안 돈다. 던지지 않는다.
- `intake_map.run_intake`: `t-0` 접수 읽기 → 응답 원본(`runner.raw` — 포트에 새로 둔 "원본" 조회; 렌더한 본문은 잘리고 모양이 바뀐다)에서
  identity 필드가 전부 같은 대상 행 → 후보 키(그래프의 리터럴 선언 키 + 템플릿은 머리로 `scan`, 상한 50) → 하나씩 `redis.get`(`t-0-N`) →
  값의 **같은 객체**에 identity 값 전부가 잎으로 **완전 일치**하는 자리(`holds_identity`, 필드 이름 안 봄) → 정확히 하나면 그 키. 결과는
  `State.target`(`domain.TargetPick` — key·basis·candidates·problem). 읽은 것은 전부 태스크·증거로 State에 남아 리드가 같은 읽기를 다시
  낼 필요가 없고 판정이 인용할 수 있다.
- 케이스 블록 줄 `대상 키: alarm:stats:L1 — 키 값의 record[0].summary_info에 대상 identity(L1/Alarm)가 그대로 있다 (t-0-2.e1)` /
  `대상 키: 못 골랐다 — 후보 3개 중 값에 대상 identity(L1/Alarm)가 든 키가 없다`. 브리핑은 고르지 않고 적기만 한다(`state.target`). 흐름
  줄은 고른 키만 앞세운다. ②의 상한은 `state.target`으로(못 골랐으면 그 이유가 caveat에).
- 토큰 규칙(`row_tokens`)과 행 본문 되읽기(`target_row` 옛 것)는 뺐다. `start_read`·`check_of`는 `intake_map`으로 옮겼다(브리핑과 노드가
  같이 쓴다).
- 실제 소비자로: 측정판(`cache-stale`, 아직 옛 값 모양)에서 r1 프롬프트에 `t-0`(접수)·`t-0-1`(scan `alarm:stats:*`)·`t-0-2~4`(get)가
  실렸고 케이스 블록은 `못 골랐다 — 후보 3개 중 … 든 키가 없다`(값에 identity가 없는 옛 모양이라 맞는 답), 대본 판정이 `hb:sink`를 인용하자
  medium + 그 이유가 caveat에. "골랐다"는 ①-b의 측정판(값에 identity)에서 본다.
- 테스트 먼저(RED → GREEN). 스윕 578 → 583(토큰 케이스 9 제거, 입구 케이스 14 추가, 옮겨진 앵커 둘). 초록으로 잡힌 변이 둘은 테스트를
  날카롭게 했다(부분 문자열 대조 / 모든 키를 hit로).

#### R3-1 ①-b (10-13) ✅ — 측정판: 요약 키 값에 identity, c-1형, 미끼

- 요약 키 값을 사내 모양으로: `{"record": [{"summary_info": {"group", "title", "source"}, "data": [...]}], "count_1h", "updated_at"}`.
  변형 `data-empty`(**c-1형** — 키 있음, `data: []`, 컬렉션엔 최근 문서 있음). `cache-missing`의 "c-1형" 라벨을 뗐다(키가 아예 없는 변형).
- 미끼 `alarm:scan:{line}` — 이름이 비슷하고 라벨(`source: MES`)은 같지만 identity가 다른 배지(Caution). api 핸들러가 둘 다 읽어 둘 다
  후보가 된다(측정 #6 c-2의 모양). `test_index_trace`의 측정판 핸들러 테스트에 그 읽기와 밀린 gap 줄 번호를 반영했다.
- 실제 소비자로(`data-empty`, 그래프 재생성 뒤): 입구 노드가 `t-0`(접수) → `t-0-1`·`t-0-2`(scan `alarm:scan:*`·`alarm:stats:*`) → `t-0-3~8`
  (get 여섯) 뒤 케이스 블록에 `대상 키: alarm:stats:L1 — 키 값의 record[0].summary_info에 대상 identity(L1/Alarm)가 그대로 있다 (t-0-6.e1)`.
  미끼 셋은 읽혔지만 안 골렸다. 대본 판정이 `hb:sink`를 인용하자 medium + `대상 키 alarm:stats:L1`이 caveat에.
- **`code graph` 재생성 규칙(사람 요청 10-13)**: 측정 안내마다 "필요/불필요"를 명시한다. 기준은 ① 번들을 만드는 코드(`graph_build`·`index`·
  `index_trace`·`trace`, `flow.extract/add_endpoints/add_trace/apply_site`)가 바뀌었나 ② 배포 커밋이 바뀌어 `code status`가 낡음을 말하나.
  측정 #3 이후 ①에 해당하는 변경은 없다 — 측정 #7은 ②일 때만 재생성.
- 테스트 먼저(RED 2 → GREEN). 스윕 +3(586).

#### R3-1 ② (10-13) ✅ — 태스크 id 입력 · 막힘 되묻기 · 미실행 사유

- 사내 c-2: 리드가 t-7의 입력을 `["t-6"]`(태스크 id, 그 태스크는 실패)로 내고 t-8이 t-7을 기다려 둘 다 영영 안 돌았고, 라운드는
  `no_runnable`로 끝났다(리드는 continue). 진단은 t-7을 "우선순위에 밀림"이라 적었다 — 틀린 말.
- (a) `_accept_tasks`가 받은 뒤 입력의 태스크 id를 증거 id로 바꾼다(`_resolve_inputs`, 판정 인용 정리 R2-4 ⑥과 같은 규칙): 끝난
  태스크면 그 증거 **전부**(입력은 "그 결과를 다 본 뒤"라 고를 것이 없다), 실패했거나 증거가 없으면 **의존을 떼고** 기록(셋째 묶음 —
  받았다는 기록이지 거부가 아니다), 아직 안 돈(같은 답에 같이 낸) 태스크면 첫 증거 id `t-7.e1` — `ProbeRunner`가 ok 태스크마다 증거를
  정확히 하나 남기므로 게이트가 그 태스크가 끝날 때 열린다. `expect.evidence`도 같은 규칙(하나일 때만).
- 같은 답에서 **거부되거나 접힌 id**도 입력으로 받는다 — 입구 노드가 접수를 먼저 읽으므로 리드의 "증상 재현" 읽기는 으레 중복으로
  거부되는데(보드에서 그랬다) 리드는 그 id를 다음 태스크의 입력으로 댄다. 거부된 읽기는 그 결과의 증거(`t-0.e1`)로, 대기 중인 같은
  읽기의 갱신으로 접힌 id는 그 태스크의 첫 증거로. 앞 라운드에서 거부된 id는 그 기록(`t-1: 이미 한 읽기를 또 냈다 … — 그 결과는 t-0.e1`)에서
  되읽는다(`_rejected_reads` — 기록이 이미 그 사실을 담고 있어 State에 따로 두지 않는다; 조각 `DUP_KIND`·`DUP_WHERE`를 바꾸면 같이 바꾼다).
- (c) 대기 태스크는 있는데 돌릴 것이 없으면 거부 되묻기와 같은 길로 **케이스당 한 번** 되묻는다(`BLOCKED_MARK`가 기록에 남아 표식 —
  모양은 다른 기록과 같은 `t-5: 막힘 — 대기 태스크가 못 돈다 — t-5(입력 t-9.e1 없음) — …`라 진단이 종류 `막힘`으로 센다).
  되물어도 막혔으면 `no_runnable`로 끝내되 무엇이 어느 입력에 막혔는지를 기록에 남긴다. (a)가 입력을 고치므로 드물지만 안전망이다.
- (b) 진단의 미실행 줄이 태스크마다 사유를 단다 — `t-7(막힘 — 입력 t-6 실패)`, `t-8(막힘 — 입력 t-7.e1 없음)`, `t-9(우선순위에 밀림)`
  (`nodes.unrun_reason`, 되묻기 줄과 같은 함수).
- 실제 소비자로(측정판 `data-empty`, 대본 리드): r1에 t-1(접수와 같은 읽기 → 거부)·t-2(없는 끝점 → 실패), r1 답에 t-3(입력 `t-1`)·t-4(입력 `t-2`)
  → r2 프롬프트에 t-3 `[ok]`(입력이 frame 기록의 `그 결과는 t-0.e1`로 — 처음 돌렸을 땐 `t-1.e1`을 기다리다 막혀 그 보완이 들어갔다), t-4 `[ok]` +
  `t-4: 입력 t-2은 태스크 id이고 그 태스크는 실패했거나 증거가 없다 — 의존을 뗐다`.
  r2 답에 t-5(입력 `t-9`, 없는 태스크) → 되묻기 프롬프트에 `방금 낸 답의 t-5: 막힘 — 대기 태스크가 못 돈다 — t-5(입력 t-9.e1 없음) — 입력
  없는 읽기를 내거나 conclude`, 대본이 conclude → 진단 `1× 막힘`·`미실행 1개 — 끝날 때까지 안 돌았다: t-5(막힘 — 입력 t-9.e1 없음)`.
- 테스트 먼저(RED 4 → GREEN, 보드 뒤 +2). 스윕 +13(599). 대본 예제(`case-dryrun.json`)의 t-9는 대본 실행기가 증거를 안 만들어
  막힌 채 끝나므로 그 기록이 남는다 — `test_dryrun`의 "이름 기록 없음" 단언을 이름 기록으로 좁혔다.

#### R3-1 ③ (10-13) ✅ — 받을 때 인자 검사 · 참고 재집계

- P1 3: 등재·인자 검사(`action_problem`)를 **받을 때**(`_accept_tasks` 맨 앞, `_accept_problem`) 한다 — 거부라 되묻기가 그 자리에서
  고쳐 받는다. 사내 측정 #6에서 `redis.scan limit`·`mongo.count expect`가 실행에서 죽어 라운드를 잃었다. 기록은
  `t-1: 받지 않는다 — redis.scan이 모르는 인자 — limit (인자: pattern, count?)` — 받는 인자를 같이 적는다("모르는 인자"만으로는 같은
  모양으로 다시 틀린다). action이 없는 태스크도 받지 않는다. 실행기의 같은 검사는 마지막 방어선으로 그대로.
- P1 4: 재집계 filter의 잎에 케이스 target 값이 **하나도** 없으면(`runner_probe.off_target`) 증거 요약·본문·태스크 줄 머리에
  `참고 — filter에 대상 identity(L1/Alarm)가 없다: 대상 행의 수가 아니라 그 filter 범위의 수다.`(`ASIDE_MARK`). 하나라도 있으면 그대로
  — 배지 이름은 문서 필드가 아닐 수 있어 전부를 요구하면 거의 모든 재집계가 참고가 된다. target 없는 케이스는 가릴 수 없다. 브리핑의
  사다리는 참고 재집계의 `match`를 사실로 쓰지 않고 다음 칸을 비운다(짐작하지 않는다). 사내 c-1 t-10(컬렉션 전체 987281 vs 화면 0)의
  모양이다.
- `case dryrun`은 받을 때 거부된 태스크를 `받지 않은 것·기록 N건` 아래 보여 준다 — 울타리를 보는 명령인데 거부된 것이 목록에서
  조용히 사라졌다(예제의 미등재 `mongo.aggregate`). README 표본·예제 설명도 그 모양으로.
- 실제 소비자로(측정판 `data-empty`, 대본 리드): frame 답의 `redis.scan pattern='hb:*' limit=5` → r1 프롬프트 `<버려진 태스크>`에
  `t-1: 받지 않는다 — redis.scan이 모르는 인자 — limit (인자: pattern)`. r1 답의 `recompute.count filter={'level': 'minor'}` → r2 프롬프트의
  증거 줄이 `참고 — filter에 대상 identity(L1/Alarm)가 없다: …`로 시작하고, `filter={'line': 'L1', …}`인 것은 그대로. 진단에 `1× 받지 않는다`.
- 테스트 먼저(RED 3 → GREEN; 받을 때 거부로 모양이 바뀐 기존 테스트 둘 — 소독 테스트의 태스크에 action, CLI 테스트는 거부 기록 —
  손질). 스윕 +7(606).

### 측정 #7(sevt, 10-13)과 R3-1b

A는 맞았다 — 두 케이스 다 `키 값의 record[0].summary_info에 대상 identity가 그대로 있다 (t-0-N.e1)`로 맞는 키를 골랐다. 그런데 판정은 둘 다
inconclusive(low): 입구 노드가 후보 29개 읽기를 전부 증거로 실어 integrate 프롬프트가 19.5K자(상한 8K)가 됐고, 증거 예산은 **뒤(최근)에서
부터** 채우므로 가장 오래된 입구 읽기부터 본문을 잃어 **대상 키 본문이 "예산에서 빠졌다"** — data 빈 배열을 아무도 못 봤다. 리드 첫 수가
입구 읽기와 같아 중복 거부 → 되물어도 중복 → `no_runnable`(c-1 r1, c-2 r2). 리드가 `t-0-19.e1`을 `t-0.19.e1`로 적어 "없는 증거" 7회.
세 실패는 한 뿌리다: 입구 노드가 자기 작업 과정을 결과처럼 리드 앞에 쏟았다. 항목과 순서는 [plan-12a-r2-r4.md](plan-12a-r2-r4.md) R3-1b.
사내가 낸 `t-0.N.e1` → `t-0-N.e1` 정규화는 안 한다 — 뿌리는 우리 id 모양이고, 점·대시 교정은 코드가 LLM의 뜻을 짐작하는 두 번째 자리이며,
접고 나면 돌 일이 없다. B 미루는 이유 2(조립식 두 갈래)는 철회 — 남은 확인은 "펼친 키 ↔ 항목 이름 대응을 인덱스가 보관하느냐" 하나.

#### R3-1b ① (10-13) ✅ — 입구 노드는 결과만 보인다

- State(`plan_tasks`·`evidence`)에는 `t-0`(접수 읽기)과 `t-1`(고른 키를 다시 읽은 것)만 남는다. 후보 scan·get은 내부 번호(`t-0-N`)로만
  돌고(실행기의 원본 보관용) `TargetPick`의 집계(`read`·`absent`·`failed`·`hits`)로 접힌다 — 케이스 블록 한 줄
  `후보 29개 읽음 — 있음 20 · 없음 9 · identity 일치 1 (대상 키 …)`(`fold_line`). 후보 읽기의 **본문**은 어디에도 안 남는다: State에 접은
  채 두려면 증거를 읽는 소비자마다 "접힘"을 배워야 하고, 값은 `peek redis`로 다시 볼 수 있다(사내 안의 "원문은 저장소에 두고 id로만"과
  다른 점 — 저장소가 곧 State라 같은 말이 안 된다).
- 고른 키는 `t-1`로 **다시 읽는다** — 통째로이되, 후보 읽기 본문이 상세 예산에서 잘렸으면(`complete=False`) identity가 든 객체의 부모
  (`record[0]` — identity와 data가 같이 있는 자리, `_parent`)로 좁힌다. 근거는 `t-1.e1`을 인용하고 자리는 다시 읽은 값에서 다시 찾는다.
  다시 읽기가 실패하면 고른 키는 남기고 근거에 `(다시 읽기 실패: …)`를 적는다.
- id는 리드와 같은 한 단계 모양이라 frame 예시는 `next_task_number`가 `t-2`부터 보여 준다(바꾼 것 없음 — 번호 규칙이 이미 그랬다).
- 실제 소비자로(측정판 `data-empty`): r0 프롬프트 케이스 블록에 `대상 키: alarm:stats:L1 — … (t-1.e1)`과 `후보 6개 읽음 — 있음 6 · 없음 0 ·
  identity 일치 1 (대상 키 alarm:stats:L1)`, 예시 id `t-2`·`t-3`·`t-4`. r1 프롬프트의 태스크 줄은 `t-0`·`t-1`·`t-2`(리드의 것) 셋, 증거 줄 셋,
  크기 9.8K자(①-b 보드에서는 후보 여섯이 실려 13.5K자).
- 테스트 먼저(RED 7 → GREEN). 스윕 +7(613), 옮겨진 앵커 둘.

#### R3-1b ② (10-14) ✅ — 쓰는 쪽 엣지: 명령으로 쓰는 자원 (R3-3 앞당김)

- 사내 10-07 진단표 #8: 배치 파이프라인이 요약 키 이름을 kafka 명령에 실어 보내는데 `code.uses`가 그 함수를 "읽기"에 뒀다 — 그 줄엔 동사가
  없고(또는 `config.get`의 `get`뿐이고) 동사 없는 줄은 보수적으로 읽기였다. ③의 다음 홉(`code.uses <대상 키>`)이 쓰는 쪽을 보여 주려면 먼저
  고쳐야 했다.
- 인덱스(`attach_resources`): 자원 줄에 동사가 없고 **같은 함수의 뒷줄이 토픽으로 보내면**(`flow.produces` — `send`·`produce`·`publish`·`emit`)
  그 자원은 `writes`, `Resource.how = "command"`. 읽기 동사가 있는 줄(`redis.get(key)`를 읽어 보낸다)은 읽기 그대로, 뒤에 보내는 것이
  없으면 전처럼 읽기. `renamed` 사본도 `how`를 지킨다.
- `flow.direction`: `cfg`·`config`·`settings`가 붙은 `.get(`은 설정 조회라 동사로 안 센다(`_CONFIG_GET`) — `redis.get(key)`는 그대로 읽기.
- `code.uses`: 명령으로 쓰는 함수는 쓰기 목록에 `(명령 — 메시지에 자원 이름을 실어 보낸다)`를 달고 나온다.
- 흐름 그래프: `flow.add_commands(overlay, index, topology)` — 인덱스의 명령 자원마다 서비스 → 자원 `writes`(origin `command`, INFERRED,
  file:line) 엣지. `code graph`가 끝점 사슬 뒤에 단다. grep 줄만으로는 동사가 없어 `mentions`로만 남던 것이다.
- 측정판: 토폴로지에 `batch`(dt-core, `SERVICE_ROLE=batch`), `batch/refresh.py`가 요약 키 이름을 명령에 실어 보낸다(직접 `set` 안 함).
  **번들을 만드는 코드가 바뀌었다 → 측정 #8은 `code graph` 재생성 필요.**
- 실제 소비자로(측정판 다시 만들고 `code graph`): `명령 쓰기 엣지 1개`, `code uses 'alarm:stats:{line}'` 쓰기 2 — `batch.refresh.run
  (batch/refresh.py:L6) [dt-core · batch] … (명령 — 메시지에 자원 이름을 실어 보낸다)`와 `sink.writer.run`, 읽기 1 `api.alarms.badge`.
  graph.json·overlay.json에 `service_batch → rediskey_alarm_stats_line writes INFERRED batch/refresh.py L6`. 처음 돌렸을 땐 표시가 없었다 —
  번들(symbols.json)의 자원 직렬화가 다섯 칸이라 `how`를 잃었다. 여섯 칸으로(옛 다섯 칸 번들도 그대로 읽힌다).
- 테스트 먼저(RED 6 → GREEN, 보드 뒤 +1). 스윕 +9(622). `__main__`의 배선(`add_commands` 호출)은 짝 테스트가 없다 — 보드가 본다.

#### R3-1b ③ (10-14) ✅ — 이미 읽었다 + 다음 홉

- 측정 #7의 "첫 수 중복 → 되물어도 중복 → `no_runnable`"의 뿌리는 코드에 있었다: 입구 노드가 접수를 읽었는데 **frame 예시의 첫 칸이 여전히
  접수 읽기**였다(`example_block`의 `first_read`). 예시가 곧 출력이라 리드는 그대로 베꼈다. 입구가 돌았으면 접수 읽기는 예시에 없고
  (`intake_done`), 첫 칸은 다음 홉이다.
- **다음 홉은 입구 결과로 코드가 정한다**(`intake_map.next_hop`): 고른 키가 있으면 `code.uses name=<선언 이름>` — 화면이 읽는 키이니
  값이 어디서 오는지가 다음 질문이다. 인덱스는 템플릿 이름으로 답하므로 `TargetPick.declared`(고른 키가 나온 선언)를 쓴다. 심볼 인덱스가
  없으면 없는 문이라 홉이 없다(옛 사다리 — 접수 끝점의 사슬 — 그대로).
- 케이스 블록 입구 줄(`intake_lines`): `입구 노드가 이미 읽었다: 접수 읽기(t-0.e1) · 대상 키 <키>(t-1.e1) — 같은 읽기를 다시 내지 마라` /
  `값: record[0].data가 비었다`(`TargetPick.empty` — identity 옆의 빈 목록, 사내 c-1의 모양을 코드가 사실로) / `다음 홉: …`.
- 사다리(`_ladder_step`): 입구가 키를 골랐으면 첫 칸 `code.uses <선언 이름>`(입력 `t-1.e1`), 그다음 그 증거의 첫 "쓰기" 함수로
  `code.callers`, 그 뒤는 접수 끝점의 옛 사다리.
- 중복 거부 기록: 입구 노드가 한 읽기(`t-0`·`t-1`의 증거)의 중복이면 `… — 입구 노드가 이미 읽었다. 다음 홉: …`를 붙인다(`_accept_tasks`의
  `hint`·`hint_for`, integrate·frame이 `EngineDeps.code_index`로 넘긴다). 리드 자신의 읽기 중복엔 안 붙는다.
- 되물어도 중복이면 코드가 그 홉을 직접 내는 안은 보류 — #8에서도 안 먹히면 그때.

## 범위 밖 — 12b·13으로

- 판정이 사람에게 닿는 경로(보고서·이벤트·메일)는 12b. 지금은 CLI 출력과 `--trace`의 `summary.md`뿐이다.
- `ask`(사람에게 묻기)는 13. `Decision`에 자리를 안 만들었다 — 만들면 route가 END로 흘려보내는 구멍이 생긴다.
- 판정이 케이스 저장소에 남지 않는다(12b가 보고서와 함께 정한다).
