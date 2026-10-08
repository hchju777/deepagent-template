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

## 범위 밖 — 12b·13으로

- 판정이 사람에게 닿는 경로(보고서·이벤트·메일)는 12b. 지금은 CLI 출력과 `--trace`의 `summary.md`뿐이다.
- `ask`(사람에게 묻기)는 13. `Decision`에 자리를 안 만들었다 — 만들면 route가 END로 흘려보내는 구멍이 생긴다.
- 판정이 케이스 저장소에 남지 않는다(12b가 보고서와 함께 정한다).
