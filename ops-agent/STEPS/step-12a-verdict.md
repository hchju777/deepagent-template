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
- 측정판: **자기 토폴로지 사본**에 `batch`(dt-core, `SERVICE_ROLE=batch`; `local_case._board_topology`), `batch/refresh.py`가 요약 키 이름을
  명령에 실어 보낸다(직접 `set` 안 함). 리포의 `knowledge/topology/mx.json`은 사내에서 실제 서비스 지도로 바뀌는 파일이라 건드리지 않는다 —
  883175e가 거기에 적고 테스트가 그 파일을 읽어 사내에서 깨졌다(핫픽스 10-14: 파일 되돌림, `test_portability`가 `__file__` 기준 `knowledge`
  경로를 잡는다).
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

### 측정 #8(sevt, 10-14)과 R3-1c

①·③은 사내에서 그대로 작동했다(케이스 블록 다섯 줄, 첫 칸 `code.uses`, r1 9.8K자, `no_runnable` 0, 없는 증거 0). 판정은 둘 다 "요약 키 data 빈
배열"을 첫 나쁜 홉으로 잡고 거기서 멈췄다 — 실제 원인은 그 파이프라인의 입력 쪽 한두 홉 위다. **②의 쓰는 쪽 엣지는 사내에서 안 잡혔다**:
사내 모양은 발행 명령 DTO에 자원 이름을 `f"{config.get(…)}:{config.get(…)}"`로 싣고 **다음 줄**에서 `storage.save(command)`한다 — 뒷줄 동사가
`save`라 produce 목록에 없고, `Publish`는 같은 문장의 **앞줄** 클래스 이름에 있다. 동사 목록이라는 손 규칙은 다음 모양에서 또 틀린다. 대신
데이터로 간다: 사내 요약 키 값의 `metadata.event_type`이 쓴 파이프라인 클래스 이름과 완전 일치한다(c-1·c-2 둘 다). 항목·순서는
[plan-12a-r2-r4.md](plan-12a-r2-r4.md) R3-1c.

#### R3-1c ①-a (10-14) ✅ — 쓴 쪽은 데이터로: 값이 이름을 대는 클래스, `code.resources`

- 입구 노드가 고른 키 값의 **문자열 잎** 중 심볼 인덱스의 **클래스 이름**과 완전 일치하는 것이 정확히 하나면 그 클래스가 쓴 쪽이다
  (`intake_map.writer_of` — `holds_identity`와 같은 종류: 닫힌 집합과 완전 일치, 대소문자 그대로). 둘 이상 다른 클래스면 `못 골랐다`, 없으면
  `모른다`(클래스 집합을 받았을 때만 — 인덱스가 없으면 말이 없다, `writer_searched`). `TargetPick.writer`·`writer_at`·`writer_problem`.
  엔진은 인덱스를 들지 않고 클래스 이름 집합만 받는다(`EngineDeps.classes` ← `DeployedCode.class_names()`).
- 케이스 블록 줄 `쓴 쪽: SummaryRefresh — 값의 metadata.event_type과 인덱스의 클래스 이름이 완전 일치`(`writer_line`), 그다음 홉.
- 다음 홉은 쓴 쪽을 알면 **그 클래스가 읽는 자원**: 새 action `code.resources name=<클래스>`(`DeployedCodePort.resources`,
  `query.resources_lines` — 그 클래스 메서드들의 자원을 읽기·쓰기로 나눠 메서드·줄과 함께, 자원이 없으면 "메서드 N개, 키를 조립식으로
  만들면 텍스트로는 못 찾는다"; 이름이 여럿이면 `callers`처럼 후보만). 모르면 `code.uses <선언 키>` 그대로(인덱스 데이터라 남긴다). 인덱스가
  없으면 목록에서 숨긴다(`_hidden`). 이름이 `inputs`가 아닌 이유: 등재 목록 검사가 `put` 부분 문자열을 막는다(규율 9) — 검사를 느슨하게 하지
  않고 이름을 바꿨다. 등재 목록의 메서드 이름이 포트에 있는지 보는 테스트를 하나 더 뒀다(이름을 바꾸다 실제로 어긋났다).
- 사다리: `code.uses` 홉 뒤의 `code.callers`(쓰는 함수)는 그대로, `code.resources` 뒤(입력 키마다 읽기 한 칸)는 ③에서.
- 측정판: 배치를 사내 모양대로 `_run`을 가진 클래스(`SummaryRefresh`)로, 요약 키 값에 `metadata.event_type`으로 그 이름, 원천 키
  `alarm:source:{line}`을 읽는다. 미끼 키의 `event_type`은 클래스 이름이 아닌 문자열(`refresh-job`) — "모른다"가 뜨는 쪽.
- 실제 소비자로(측정판 재생성 + `code graph`): r0 케이스 블록 `쓴 쪽: SummaryRefresh — …`·`다음 홉: code.resources name='SummaryRefresh'`,
  예시 첫 칸 `code.resources`. 대본 리드가 그대로 내자 r1 증거 `t-2.e1 | code.resources name='SummaryRefresh' | batch.refresh.SummaryRefresh
  (batch/refresh.py:L4) [dt-core · batch] / 읽기 1: alarm:source:{line} [rediskey] — …_run (…)`. r1 10.8K자. CLI `code resources <이름>`도 같은 출력.
- CLI `python -m src code resources <이름>`도 같은 조립 — 사내에서 손으로 확인하는 자리(`code uses`로 B를 보던 그 자리).
- 테스트 먼저(RED 7 → GREEN, 이름 바꾼 뒤 +1, CLI +1). 스윕 +13(650; 옮긴 앵커 둘).

#### R3-1c ①-b (10-14) ✅ — 동사 목록 걷어내기

- R3-1b ②의 "자원 줄에 동사가 없고 같은 함수의 뒷줄이 토픽으로 보내면 쓰라는 명령"(`PRODUCE_VERBS`·`produces`·`Resource.how`)을 걷어냈다 —
  사내 모양(뒷줄 `save`, 앞줄 클래스 이름)에서 틀렸고, 목록을 늘려도 다음 모양에서 또 틀린다. 쓴 쪽은 ①-a의 데이터 링크로 안다.
- 같이 걷어낸 것: 흐름 그래프의 명령 엣지(`flow.add_commands`와 `code graph`의 호출), `code.uses`의 "(명령 — …)" 표시, 인덱스 자원 튜플의
  여섯째 칸. 남긴 것: `config.get`·`cfg.get`·`settings.get`의 `get`을 읽기 동사로 안 세는 것(`_CONFIG_GET`) — 규칙이 아니라 오분류 수정이다.
- 옛 번들 호환: ②로 만든 번들(사내 측정 #8의 것)은 자원 튜플이 여섯 칸이다 — `Index.from_dict`가 다섯 칸만 받는다. 못 읽으면 `code graph`를
  다시 만들기 전까지 `code uses`·`code resources`가 전부 막힌다.
- 동사 없는 줄은 다시 "읽기(보수적)"다. 그래서 측정판 `code resources SummaryRefresh`의 쓰기 목록에서 `alarm:stats:{line}`이 빠지고 읽기에
  든다 — 틀린 분류지만 규칙으로 맞추지 않는다. 쓴 쪽은 키 값이 말한다.
- 테스트: ②의 테스트 넷을 걷어내고 번들 호환 테스트 하나(RED → GREEN). 스윕 -8 +1(643).

#### R3-1c ② (10-14) ✅ — 코드가 보라고 한 증거는 예산에서 빼지 않는다

- 측정 #8 c-1 r2: 다음 홉(`code.uses`)의 결과 `t-2.e1`이 "예산에서 빠졌다". 증거 예산은 뒤(최근)에서부터 채우는데 r2에는 더 새 증거가 있어
  홉의 결과가 밀렸다 — 코드가 보라고 한 것을 코드가 안 보여 주는 모순.
- 받을 때 코드가 지정한 홉(`next_hop`)과 action·params가 **정확히 같은** 태스크에 `PlanTask.pinned`를 단다(`_accept_tasks(hop=)`, frame·
  integrate 둘 다). 리드가 실어 보낸 `pinned`는 수명주기 필드처럼 `_sanitize_task`가 지운다(규율 4).
- 브리핑(`pinned_evidence`·`evidence_block`): 입구 노드의 읽기(`t-0`·`t-1`, 입구가 돌았을 때만)와 pinned 태스크의 증거 본문은 예산과 무관하게
  남는다. 고정 증거가 먼저 예산을 쓰고 나머지는 전처럼 최근 것부터 — 고정만으로 넘으면 나머지는 최근 하나만 본문이 남는다. 둘 다 테스트가 고정한다 — 첫 스윕에서 `continue`를 빼는 변이가 초록이라(고정은 이미 `detailed`에 있어 두 번 세기만 했다) 변이를 둘로 갈랐다. 줄(id·출처·요약)은
  전처럼 전부 남는다. `fit_prompt`의 상한 맞추기는 그대로 — 고정 본문은 줄이지 않으므로 상한을 조금 넘을 수 있고, 그건 자르는 것보다 낫다.
- 테스트 먼저(RED 3 → GREEN). 스윕 +7(650).

#### R3-1c ③ (10-14) ✅ — 예시는 사다리 칸 하나, `code.resources` 뒤는 입력 자원 읽기, 중복 기록에 늘 홉, `code.read` 안내

- **integrate 예시**(P1 3): 입구가 홉을 정한 케이스에서 사다리 칸이 있으면 예시는 **그 칸 하나**다 — 측정 #8에서 둘째 칸 `mongo.find`를 리드가
  그대로 베껴 무관한 컬렉션으로 샜다. 홉 칸이 다 끝나 사다리가 없으면 전처럼(빈 예시는 형식을 못 보여 준다).
- **사다리 — `code.resources` 뒤**: 그 클래스가 **읽는** 자원마다 읽기 한 칸(`_input_step`). 증거 본문의 읽기 구간만 파싱한다(쓰기 구간은 입력이
  아니다). 키 템플릿은 대상 키에서 안 자리를 채운다(`alarm:source:{line}` ← 선언 `alarm:stats:{line}`·실재 `alarm:stats:L1` → `alarm:source:L1`),
  못 채우는 자리가 있으면 글자 앞부분으로 `redis.scan`, 컬렉션은 `mongo.find filter={} limit=5`, 토픽은 `kafka.tail`. 이미 낸 것(이름만 같으면 —
  `limit`을 바꿔 낸 것까지 새 칸으로 치면 같은 칸이 매 라운드 돌아온다)과 이 사이트에 없는 시스템은 건너뛴다. 대상 키 자신은 입구가 읽어 같은
  규칙에 걸린다(동사 없는 줄은 읽기로 분류돼 쓴 쪽의 출력도 입력 목록에 든다 — ①-b). 다 읽었으면 옛 사다리(접수 끝점의 사슬)로. 그다음 "있나·
  비었나·값 분포"는 R3-3.
- **중복 거부 기록**(P1 4): 리드 자신의 읽기 중복에도 홉 줄을 붙인다(입구 읽기 중복은 전처럼 `입구 노드가 이미 읽었다.`까지). 홉 **자체**를 또 냈으면 홉 줄은 제자리걸음이라(보드에서 그렇게 나왔다) `그 홉은 이미 돌았다. 예시의 첫 칸(…)을 내라`. 같이 잡은 것: 거부
  기록 되읽기(`_rejected_reads`)가 `그 결과는 ` 뒤를 전부 증거 id로 봐서 홉 안내가 붙은 줄에서는 마지막 id에 안내문이 섞였다(R3-1b ③ 이후 입구
  중복에서 이미 그랬고, 이제 모든 중복에 붙으니 늘 그렇게 된다) — id는 다음 ` — ` 앞까지.
- **`code.read`**(P1 5): git이 실패했을 때 심볼 인덱스가 그 경로를 **다른 레포**의 모듈로 알면 꼬리에 `이 경로는 <서비스>(<레포>)에 있다 —
  service='<서비스>'로 다시 내라`. 같은 레포에서 없는 경로는 git 말 그대로(인덱스와 배포 커밋이 다를 수 있어 꾸미지 않는다). 측정판은 서비스
  넷이 한 레포라 이 꼬리가 안 나온다 — 단위 테스트로만.
- 측정판(`data-empty`, `code graph` 재생성): r1 예시가 `redis.get key='alarm:source:L1'` 한 칸(입력 `t-2.e1`), 리드가 그대로 내면 r2 증거에 원천
  키 값이 실린다. 중복 거부 기록 `… — 그 결과는 t-2.e1 — 다음 홉: code.resources …`.
- 테스트 먼저(RED 4 → GREEN). 스윕 +11(661). **R3-1c 끝.**

### 측정 #9(sevt, 10-14)와 R3-3 — 코드가 상류를 걷는다

쓴 쪽 링크·pinned·예시 한 칸은 작동했다. 그러나 c-1은 원천 키까지 세 홉이 걸렸고, c-2는 r1에 첫 나쁜 값(입력 키 554행, 두 필드 전부 `'-'`)을
읽고도 못 봤다 — 본문이 접혀 같은 읽기를 8회 냈다. 리드가 한 홉씩 걷는 구조의 한계다. 입구 노드의 고리(키 값의 잎 = 클래스 → `code.resources` →
입력)를 **코드가 키마다 반복**한다(R3-3, 옛 triage의 걷기를 앞당긴 것). 항목과 사람 결정은 [plan-12a-r2-r4.md](plan-12a-r2-r4.md) R3-3.

#### R3-3 ① (10-14) ✅ — 행 목록 요약 · frame 예시 한 칸 · `code.uses <클래스>` 안내

- **행 목록 값은 코드가 요약한다**(P0 2): 문서 목록의 `profile` = `행 N · 필드 M · 모든 행이 같은 값: f='-'`(행이 둘 이상일 때만 — 한 행이면
  모든 필드가 그렇다; 필드 여섯까지, 나머지는 "외 k개"). 본문 머리줄(`N건 · 필드: …`)에 붙고, 요약 줄 머리에 `[행 N · …]`로 실린다 —
  요약 줄은 접혀도 남는 자리라 "554행 두 필드 전부 `'-'`"가 거기 있어야 리드가 본다. Redis 값은 JSON **문자열**이라 그대로 눕히면 안의 목록이
  첫 행에서 잘렸다 — 한 겹 풀어(`value: (JSON 문자열) 키 2개: record, metadata` / `  record: 554건 · 필드: … · 모든 행이 같은 값: …`) 목록과 같은
  모양으로, 요약 머리에는 `[value.record: 행 554 · …]`. JSON이 아닌 문자열은 전처럼. 값 분포 액션(`data.value_counts`)은 안 만든다 — 이것이
  그 자리다.
- **frame 예시도 홉이 있으면 그 한 칸**(P1 5): 넷 중 셋(`code.grep`·`code.config`·`mongo.list_collections`)을 리드가 그대로 내 라운드를
  낭비했고 그 잘린 증거가 끝까지 예산을 먹었다.
- **`code.uses name=<클래스>`**(P1 6): "클래스 이름이다 — `code.resources name=…`"(서비스 이름 때와 같은 꼴). 원인 하나가 프롬프트에
  있었다 — integrate 규칙 문장이 ①-a 이후에도 "값이 비었으면 `code.uses(name)`"이었다. 쓴 쪽 기준으로 고쳤다(`code.resources(name)`,
  쓴 쪽을 모르면 `code.uses(<키 이름>)`).
- 측정판: r0 예시 태스크 1개, r2 증거 줄 `[value: 행 4 · 필드 2] {'type': 'string', …}`, 본문 `value: (JSON 문자열) 4건 · 필드: …` + 행.
- 테스트 먼저(RED 4 → GREEN). 스윕 +8(669).

#### R3-3 ②-a (10-14) ✅ — 걷기 본체: 코드가 상류를 걷는다

- 노드 `intake → walk → frame`(`application/walk.py`). 입구가 키와 쓴 쪽을 골랐고 인덱스가 있으면 돈다. 입구의 고리를 키마다 반복한다: 쓴
  클래스의 `code.resources` → 읽는 자원마다 읽기(키 템플릿은 대상 키로 채움, 못 채우면 scan 뒤 다섯 개까지; 컬렉션 `mongo.find limit=5`, 토픽
  `kafka.tail limit=5`) → 상태 — **없음·0행·빈 배열만 나쁘다**(사람 결정: 손 규칙 없음; 정상이면 행 요약을 같이) → 값이 그 클래스를 대는 키는
  **출력**(표에 남기되 입력에서 뺀다, P0 3) → 나쁜 입력의 쓴 쪽(값의 잎)을 알면 한 홉 더, 모르면 거기서 멈춘다(다음 홉 `code.uses <그 키 선언>`),
  입력이 다 정상이면 멈춘다(다음 홉 없음 — ④가 채운다). 상한 깊이 4·읽기 15·30초(주입 시계) — 상한에서 멈추면 다음 홉은 그 자리의
  `code.resources`(이미 읽은 것이면 ③의 입력 칸 사다리가 이어 받는다). 대상 키 자신(선언 이름이 읽기 목록에 든다 — ①-b)은 다시 안 읽는다.
- State: 읽기마다 태스크 `t-1-N`과 증거(판정이 인용한다), `Walk`(홉 표·멈춘 이유·다음 홉·그 입력 증거). 케이스 블록 **홉 표**
  (`키 | 쓴 쪽 [서비스] | 상태 | 증거`), `입구 노드가 이미 읽었다` 줄에 `걷기 읽기 N건`, `다음 홉:`은 걷기가 멈춘 자리. 태스크 블록은 걸은 읽기를
  한 줄로, 증거 블록은 나쁜 홉만 본문(pinned)이고 나머지 걸은 읽기는 접는다(예산도 안 쓴다 — 홉 표가 상태를 말한다). 걸은 읽기는 중복 집합에
  들어 리드가 또 내면 "입구 노드가 이미 읽었다 + 걷기의 다음 홉"을 받는다. 받을 때의 pinned 홉도 걷기의 것.
- 클래스 → 서비스(`DeployedCode.class_services`, `EngineDeps.class_services`)는 표의 `[서비스]`용 — 같은 이름이 두 레포면 둘 다 적힌다. 가르기는 ②-b.
- 측정판 변형 `source-missing`(원천 키 없음 → `없음`)·`source-blank`(빈 배열 → `0행`): r0 케이스 블록에 홉 표 두 행과 `다음 홉: code.uses
  name='alarm:source:{line}' — 걷기가 멈춘 자리(쓴 쪽을 모른다 …)`, 예시 첫 칸 `code.uses`. `data-empty`는 `멈춤: 입력이 다 정상 — SummaryRefresh`
  이고 다음 홉이 없어 예시가 옛 모양(넷)으로 돌아간다 — ④의 자리.
- 테스트 먼저(RED 6 → GREEN; 걷기 4·노드 2·브리핑 1·인덱스 1·측정판 1). 스윕 +25(694), 옮긴 앵커 7.

#### R3-3 ②-b (10-14) ✅ — 같은 클래스 이름이 두 레포

- 측정 #9 P1 4: 같은 클래스 이름이 수집기·처리기 두 레포에 있고 `파일:qualname`도 같아 "그걸로 골라라"는 안내가 제자리였다. 사람 결정: 대상
  키를 config로 선언한 서비스로 가른다 — 후보끼리 가를 때만, 표에 근거; 둘 다 선언이면 둘 다.
- 걷기(`walk.branches`): 클래스가 한 곳이면 그대로(`service` 인자도 안 단다 — 리드의 중복이 같은 질의여야 잡힌다). 두 레포면 **그 키를 선언한
  서비스**(흐름 그래프 `declares` 엣지 → `flow.declaring_map`, `EngineDeps.declared_by`)로 가르고 홉 표 아래 `주:`에 근거를 적는다. 둘 다
  선언이거나 선언이 없으면 둘 다 걷는다(가지마다 `code.resources service=`, 같은 입력은 한 번만; 판정의 component 후보도 둘 다). 깊은 홉은
  대상 키가 아니라 **그 입력 키의 선언 이름**으로 가른다.
- `code.resources name=… service=…`(등재 목록의 선택 인자, 포트·스텁·실제): 서비스의 레포로 좁혀 찾는다. 서비스 없이 물어 여럿이면 후보 뒤에
  `같은 이름이 서비스 N곳에 있다 — service= 인자로 고른다: a, b`. CLI `code resources <이름> --service <서비스>`도 같은 조립(없는 서비스는 그 자리에서).
- 테스트 먼저(RED 5 → GREEN). 스윕 +10(704), 옮긴 앵커 3.

#### R3-3 ③ (10-14) ✅ — 단일값 필드의 나쁨은 코드 줄이 정한다

- 측정 #9 c-2: 입력 키 554행의 두 필드가 전부 `'-'`였고 읽는 쪽 코드에 `["<필드>"] != "-"` 줄이 있었다 — 코드가 그 값을 걸러 아무것도 안 남는다.
  사람 결정: `'-'` 같은 자리표시자 목록은 두지 않는다(손 규칙). 입력을 읽는 쪽(걷고 있는 클래스)의 레포에서 **필드 이름과 그 값이 한 줄에 둘 다
  따옴표 리터럴로** 있을 때만 나쁨이고 그 줄이 근거다(`walk.literal_lines`; 변수 이름으로 쓰인 필드는 안 센다).
- 걷기: 정상인 입력의 단일값 문자열 필드(행 요약의 `모든 행이 같은 값`)마다 `code.grep patterns=[필드] service=<읽는 쪽>`(가지의 서비스, 없으면
  클래스가 한 곳에만 있을 때 그곳, 그래도 없으면 전체; 한 홉에 셋까지, 읽기 상한에 센다). 리터럴 줄이 있으면 `나쁨 — 모든 행이 f='-', 코드가
  거른다: 파일:L줄 \`본문\` (grep 증거)`. 나쁨이 잡히면 그 `(필드, 값)`을 위로 들고 가 입력에 같은 필드=같은 값이 있는 동안 grep 없이 나쁨
  (`같은 단일값 … (아래 홉과 같다)`). 나머지 단일값은 표시만(행 요약에 남는다).
- 멈춘 자리: 리터럴 줄이 있었으면 멈춘 이유가 무엇이든("입력이 다 정상"이든 "쓴 쪽을 모른다"든) 다음 홉은 그 줄 둘레 `code.read service= path=
  offset= limit=40`(사람 결정 "리터럴 줄이 있으면 그 함수" — 지금은 줄 둘레, 함수 전체는 ④). 읽는 쪽 서비스를 모르면 `code.read`를 못 만들어
  쓴 쪽을 모르는 입력의 `code.uses`가 남는다.
- 측정판에서 둘을 잡았다. ① grep 줄의 머리가 커밋 SHA가 아니라 `main:`(선언 없는 서비스는 `default_ref`로 읽는다)이라 리터럴 줄을 못 읽었다 —
  머리는 "경로 앞의 무엇이든"으로. ② 케이스 블록의 다음 홉 줄이 홉 인자를 `name` 하나로 가정해 `code.read` 홉에서 **frame 노드가 KeyError로
  죽었다**(규율 1 위반) — `describe`로 그린다. 둘 다 단위 테스트로는 안 보였다.
- 측정판 변형 `source-filtered`(c-2형: 원천 행은 있고 모든 행의 `state`가 `'-'`, batch 코드에 `r.get("state") != "-"`): r0 홉 표
  `alarm:source:L1 | — | 나쁨 — 모든 행이 state='-', 코드가 거른다: batch/refresh.py:L11 …`, `다음 홉: code.read … path='batch/refresh.py'
  service='batch'`, 예시 첫 칸 `code.read`.
- 테스트 먼저(RED 3 → GREEN). 스윕 +8(712), 옮긴 앵커 1.

#### R3-3 ⑤ (10-14) ✅ — 클래스 이름 component는 서비스로

- 측정 #9 P1 7(c-2): 리드가 쓴 쪽 **클래스 이름**을 원인 칸에 적었고 verify가 "서비스 아닌 component"로 걷어냈다. 클래스는 서비스 안에 있으니
  걷어낼 것이 아니라 번역할 것이다 — `translate_classes`: 검사 전에 `EngineDeps.class_services`로 서비스로 바꾸고 caveat `component <클래스>는
  클래스 이름 — 인덱스로 서비스 <서비스>로 바꿨다`. 두 레포면 걷기가 가른 서비스(홉 표의 쓴 쪽), 못 갈랐으면 첫 것 + "둘 다 후보". 바뀐 판정은
  State에 둔다(통과·상한·강등 어느 길이든). R2-5 ②의 "서비스만" 규칙은 그대로 — 넓힌 것이 아니라 번역이다.
- 테스트 먼저(RED 1 → GREEN). 스윕 +5(717).

#### R3-3 ④-a (10-14) ✅ — 멈춘 자리의 다음 홉 (1): 리터럴 줄을 품은 함수 전체

- 사람 결정 "리터럴 줄이 있으면 그 함수". `code.read`에 `line=`(등재 목록의 선택 인자) — 인덱스의 심볼 줄 범위(시작·끝)로 그 줄을 품은 **가장
  안쪽** 함수·메서드·클래스를 통째로(옛 계획의 "함수 전체 모드"); 출처에 `L5-L7 / 전체 N줄 — <qualname> 전체`. 줄 둘레 40줄은 함수가 길면
  잘리고 짧으면 옆 함수를 섞는다. 인덱스에 그 줄을 품은 심볼이 없으면 줄 둘레(`— … 줄 둘레`). 걷기의 리터럴 홉은 `code.read service= path=
  line=`을 가리킨다.
- ④-b(리터럴 줄이 없을 때 `_run` + `get("X")`↔`register(component="X")`로 등록된 클래스 본문; 원천에서 멈췄으면 토픽 lag/tail + 같은 이름의
  수집기 클래스)는 "같은 이름"의 뜻(값의 잎 = 수집기 클래스 이름인가, 토픽 문자열 = 등록 문자열인가)을 답 받은 뒤.
- 테스트 먼저(RED 1 → GREEN). 스윕 +5(722).

#### R3-4 ① (10-14) ✅ — 레포 단위 가르기·가지 격리·`code.uses service=`·`code.resources repo=`

- 측정 #10 P0 2: 처리기 레포는 서비스 5개가 `path` 없이 한 레포·한 config를 공유한다 — `flow.owner`가 못 가르고(`AMBIGUOUS`) `class_services`가
  그 파일을 **버려서** 같은 이름의 클래스가 "수집기 한 곳"으로 보였다 → 원천 키 셋이 수집기로 붙고, `code.resources`에 인자 없이 물어 "여럿"으로
  실패하고, 그 실패 하나가 걷기 전체를 끊었다. 사람 결정: 레포 단위로 가르고 표에 `<레포>(서비스 N개 공유)`, component 후보는 그 전부, 가른
  레포를 `code.resources`에 넘긴다.
- `class_services`는 못 가른 파일을 그 레포의 서비스 전부로 잇고, 어댑터가 `service_repos()`(서비스 → 레포)를 내며 엔진(`EngineDeps.service_repos`)이
  걷기에 넘긴다. 걷기는 클래스의 서비스들을 레포로 묶어 **레포끼리** 선언 서비스로 가른다 — 한 서비스 레포는 `service=`, 공유 레포는 `repo=`(새
  선택 인자 — 서비스 하나를 대표로 대면 표와 기록이 거짓이 된다), 한 레포뿐이면 인자 없이. 홉에 `candidates`(판정의 component 후보)를 두고
  verify 번역(⑤)은 그 후보로(공유 레포면 첫 것 + caveat에 전부; 표의 `service` 글자에서 되읽지 않는다). 주의 모양: `W는 레포 2곳(a, dt-proc(서비스
  3개 공유))에 있다 — <키>을 선언한 dt-proc(서비스 3개 공유)로 골랐다`.
- **가지 격리**: 한 가지의 `code.resources` 실패는 여기로 오게 한 행에 `· W[꼬리표]의 자원 못 읽음 — 사유`로 적고 다른 가지는 계속 걷는다. 모르는
  쓴 쪽이 없으면 멈춘 이유는 그 실패(다음 홉 없음) — 멈춘 자리의 재정의(가장 깊은 나쁜 홉)는 ②.
- `code.uses service=`(등재 목록·포트·스텁·어댑터·CLI `--service`)를 받아 그 서비스의 레포 안으로 좁힌다(P1 7). CLI `code resources <이름>`의 안내는
  레포 단위(`query.repo_hint` — 어댑터·CLI 공용): `같은 이름이 레포 N곳에 있다 — --repo(또는 그 레포의 서비스로 --service)로 고른다: <레포>(<서비스들>)`
  (P1 6). `--repo`도 받는다.
- 측정판: 변형 `palette-empty` — batch가 읽는 공용 팔레트 키의 쓴 클래스(`PaletteLoader`)가 서비스 디렉터리 밖(`common/`, 세 서비스 공유)과 dt-api에
  같은 이름으로 있다. 보드: CLI `code resources PaletteLoader`가 `같은 이름이 레포 2곳에 있다 — --repo(또는 그 레포의 서비스로 --service)로 고른다: dt-api(api), dt-core(batch, processor, sink)`를 내고 `--repo dt-core`로 읽기 목록이 나오며 `code uses alarm:palette --service batch`가 레포 안으로 좁힌다. r0 홉 표 `alarm:palette | PaletteLoader [dt-core(서비스 3개 공유)] | 빈 배열 (colors)` + 주 `PaletteLoader는 레포 2곳(api, dt-core(서비스 3개 공유))에 있다 — alarm:palette을 선언한 dt-core(서비스 3개 공유)로 골랐다`, 걷기가 `palette:source`까지 이어 `입력이 다 정상 — PaletteLoader`에서 멈춘다(다음 홉 없음 → 예시 네 칸 — ②의 몫). source-filtered는 팔레트 행이 `정상`으로 끼고 전처럼 리터럴 줄과 `code.read line=`을 가리킨다. r0 6.4K.
- 테스트 먼저(RED → GREEN). 스윕 +26(748).

#### R3-4 ② (10-14) ✅ — 멈춘 자리 = 가장 깊은 나쁜 홉, 다음 칸은 늘, pinned는 사슬만, 걷기 id 한 단계

- 측정 #10 P0 3: 멈춘 이유가 "마지막 실패"(다른 가지의 일)였고, 입력이 다 정상에서 멈추면 다음 홉이 없어 frame 예시가 네 칸으로 돌아갔다. P0 4:
  나쁜 홉 전부를 고정하니 r1이 22~28K. P1 5: `t-1-N` 때문에 리드가 `t-1`을 다시 냈다.
- 멈춘 자리는 **가장 깊은 나쁜 홉**(`Walk.stop_at`; 같은 깊이면 먼저 읽은 것)이고 사유와 다음 칸은 그 홉의 일로 만든다 — 쓴 쪽 모름 → `code.uses
  <선언 이름>`, 자원 못 읽음 → `code.resources`(같은 인자), 걸었는데 입력이 다 정상 → **읽는 메서드 전체**(`code.read line=`, 자원 줄의 메서드 자리),
  입력이 0 → `입력을 모른다 — W(인덱스 자원 0 — 메시지 입력일 수 있다)`와 클래스 본문(머리 줄). 리터럴 줄이 있으면 그 함수가 첫 칸, 멈춘 자리의
  질의가 둘째 칸 — `Walk.rungs`(차례)로 두고 사다리(`_ladder_step`)가 **아직 안 낸 첫 칸**을 밟는다(낸 칸은 실패했어도 또 안 낸다; 다 밟았으면
  마지막 칸의 결과로 옛 사다리). 받을 때의 pinned도 그 칸(`nodes._hop` — 돈 태스크만 "낸 것"으로 센다: 되묻기의 base에는 첫 답의 태스크가 대기로
  실려 있다). 칸을 못 만들면(읽는 쪽 서비스 모름) 입구 홉으로 돌아가지 않는다. 이은 컴포넌트 함수 칸은 ③의 조회가 생긴 뒤 그 뒤 칸으로.
- pinned 본문은 **멈춘 홉까지의 사슬**(`Hop.parent`로 되짚는 `Walk.chain_ids`)만 — 다른 가지의 나쁜 홉은 표의 상태로 족하고 본문은 접는다.
- 걷기 읽기의 id는 리드와 같은 한 단계(입구 뒤 다음 빈 번호부터 `t-2`…)이고 `PlanTask.walked`(코드만 단다 — `_sanitize_task`가 지운다)로 식별한다.
  `WALK_PREFIX`는 없앴다. `next_task_number`가 그 뒤부터 보여 준다(보드: 걷기 5건 뒤 예시 `t-7`).
- 보드: palette-empty r0 `멈춤: 입력이 다 정상 — PaletteLoader`, `다음 홉: code.read line=9 path='common/palette.py' service='batch'`, 예시 한 칸 `t-7`.
  source-filtered r0 첫 칸 `code.read line=12`(리터럴 함수, `t-6`), 리드가 그대로 내자 r1 예시 첫 줄은 둘째 칸 `code.uses`(멈춘 자리 — 쓴 쪽 모름),
  태스크 블록 `- 걷기 읽기 4건 (t-2…t-5)`, 접힌 증거 3(사슬 밖의 자원·팔레트·grep), r1 14.0K.
- 테스트 먼저(RED → GREEN). 스윕 +20(768).

#### R3-4 ③ (10-14) ✅ — 단일값 나쁨 좁히기: 어댑터 안의 조회(범위·짝)

- 측정 #10 P0 1: 단일값 검사가 레포 전체 `code.grep`(400줄 상한, 첫 리터럴에서 멈춤)이라 사이트 상수 config 줄과 다른 처리기의 dict 줄을 가짜로
  잡고 진짜 `["<필드>"] != "-"` 줄은 놓쳤다. grep 5번이 읽기 상한 15를 먹고 증거로 실려 r1이 14~19K였다(P0 4).
- 사람 결정: **범위** = 그 입력을 읽는 클래스의 파일 + 거기서 `get("X")` 리터럴로 부르는 컴포넌트의 등록 파일(`register(component="X")`, `.py`만 —
  config 제외), **짝** = 필드와 값이 한 비교식의 양쪽(`x["f"] != "v"`·`r.get("f") == "v"`·뒤집힌 순서; 사전 리터럴·주석·변수는 아님). X가 변수면
  "못 이음 — 변수"(새 데이터 링크는 만들지 않는다). 통과한 필드는 전부 적는다.
- `DeployedCodePort.filter_lines(name, field=, value=, service=|repo=) → FilterScan`(등재 항목이 아니다 — 리드가 못 내고 걷기 노드만 부른다;
  `nodes.EngineDeps.filter_lines`, `__main__`이 어댑터의 메서드를 꽂는다). 어댑터는 인덱스로 클래스 파일을 고르고(가지의 `service=`/`repo=`로 같은
  이름 둘 중 그 레포), 배포 커밋 **스냅샷**(이미 받아 둔 것)에서 등록 줄과 비교식 줄을 찾는다 — runner를 안 거치므로 읽기 상한·증거 밖. 던지지 않는다
  (`problem` — 인덱스·클래스·스냅샷). 순수 조각 `comparison_lines`·`component_names`.
- 걷기: 첫 거르는 줄에서 나쁨(`… 코드가 거른다: <경로>:L<줄> \`<본문>\` (이은 컴포넌트 파일)`), 리터럴 칸의 입력 증거는 그 입력 키의 읽기. 통과하면
  `· 단일값 f='v' — 거르는 줄 없음(N파일, 이은 컴포넌트 X)` / `(1파일 — 못 이음: 변수 name)` / `— 검사 못 함(<문제>)`. 이은 컴포넌트의 등록 줄은 ②의
  사다리에서 `_run` 전체 다음 칸들(`code.read line=` — 등록 줄을 품은 심볼 전체). 조회가 없으면(인덱스 없음·대본) 표시만.
- 측정판: `batch/refresh.py`의 `_run`은 조립뿐(`self.components.get("state_filter")`), 거르는 줄은 `batch/components.py`의 `@register(component=
  "state_filter")` 클래스에; 미끼 둘(사이트 config `badge.state: "-"`, processor의 `EMPTY = {"state": "-", …}`). 보드 source-filtered r0: 입력 행
  `나쁨 — 모든 행이 state='-', 코드가 거른다: batch/components.py:L7 \`return [r for r in rows if r.get("state") != "-"]\` (이은 컴포넌트 파일)`, 미끼는
  안 잡혔고 걷기 읽기 3건(grep 없음), 첫 칸 `code.read line=7 path='batch/components.py'` → r1 증거가 `apply` 함수 전체, r1 13.1K(②의 14.0K).
- 테스트 먼저(RED → GREEN). 스윕 +17(785).

#### R3-5 ① (10-15) ✅ — 핫픽스: 중첩 함수 이름 가림, 던진 걷기도 보인다

- 측정 #11: 두 케이스 다 걷기 없이 돌았는데 아무도 몰랐다. 원인은 R3-4 ③의 단일값 분기가 문자열 `where = …`를 두어 R3-4 ①의 중첩 함수 `where()`를
  덮은 것 — 파이썬은 함수 안의 대입을 그 함수 전체의 지역 이름으로 잡으므로, "단일값 통과 뒤 두 레포 가르기" 순서에서 `branches()`가 문자열을 불러
  TypeError. 걷기 노드의 마지막 방어선이 `Walk(stopped="걷기 노드가 던졌다…")`로 삼켰지만 홉이 비어 `Walk.lines()`가 빈 목록을 돌려줘 케이스 블록·진단
  어디에도 안 떴다. 보드(palette-empty)는 가르기가 통과보다 **먼저**라 못 잡았다 — 순서를 테스트에 넣었다.
- 고침: 중첩 함수를 `params_for`로, 분기의 문자열을 `linked_note`·`scope_note`로 갈랐다(이름 충돌 회귀 테스트: 통과 뒤 가르기, 거르는 줄 뒤 가르기).
  `Walk.lines()`는 홉 0이어도 멈춤 사유가 있으면 머리줄 한 줄을 낸다(걷기 자체가 없으면 전처럼 빈 목록). 진단(`diagnose` — CLI 출력과 trace의
  `summary.md`)에 `걷기 홉 N · 읽기 M — 멈춤: …` 줄. 아무것도 못 읽은 걷기의 다음 홉은 입구 홉 그대로(`code.resources <쓴 쪽>`) — 맞는 동작이라 그대로.
- 엔진 수준 테스트: 조회가 던지는 걷기 → `walk.stopped == "걷기 노드가 던졌다 — RuntimeError: boom"`, 케이스 블록에 그 줄, 진단에 그 줄.
- 테스트 먼저(RED 3 → GREEN). 스윕 +3(788). 보드 두 변형은 전과 같다(회귀 순서는 ④의 측정판 모양으로).

#### R3-5 ② (10-15) ✅ — 측정 도구 `case walk`: LLM 없이 입구 + 걷기만, 케이스 블록만, 읽기 전용

- 측정 #12는 걷기의 홉 표를 정답 표와 대조한다 — LLM 조사(한 케이스 3~4분, 판정이 끼어든다) 없이 걷기만 봐야 한 판에 배지 19행을 돌 수 있다.
- `src/application/walk_only.py`: 그래프 대신 두 노드(`intake`·`walk`)를 직접 부르고 패치를 **그래프의 reducer와 같은 규칙**(`plan_tasks`·`evidence`·
  `hypotheses`는 id 병합, `llm_errors`·`facts`는 이어 붙이기, 나머지 교체)으로 합친다 — 여기서 어긋나면 `case walk`의 표와 조사의 표가 다른 걷기를 말한다.
  리드는 안 부른다(`_no_lead`는 불리면 던진다). 엔진 기능이 아니다.
- CLI `case walk <id>` / `case walk --target "<identity 문자열>" --check <점검> --gbm --fct [--stub-seeds]`: 조사(`cmd_case_investigate`)와 같은 재료
  (`_code_if_ready`·`build_adapters`·`ProbeRunner`·`EngineDeps`)에 리드만 뺀다. `--check`는 기본값 없이 필수(틀리면 점검 목록), `--target`은 순찰이
  만드는 문자열 그대로(쪼개지 않는다 — 제목에 `/`가 든 행이 있다). 출력은 리드가 받는 케이스 블록 그대로 + 꼬리 한 줄(걷기 읽기 수·초, 그래프 상태,
  읽기 전용). 저장소·보고서·트레이스에 아무것도 남기지 않는다(테스트가 디렉터리 상태로 확인). 코루틴은 CLI의 단일 `_run`을 지난다(`asyncio.run` 한 곳 규칙).
- 보드(palette-empty 번들): `--target "L1/Alarm"` → 조사 r0와 같은 홉 표·주·다음 홉, 꼬리 `걷기 읽기 5건`; `--target "L9/Nope"` → `대상 키: 못 골랐다 — 접수
  응답에 대상 행이 없다`와 `걷기 없음(…)`; `--check nope` → 점검 목록; 저장된 케이스 id로도 같은 표; 전후 `cases.json`·출력 디렉터리 변화 없음.
- 테스트 먼저(RED → GREEN: 응용 2 + CLI 2). 스윕 +7(795).

#### R3-5 ③ (10-15) ✅ — 단일값 검사 재설계: AST 등록·클래스 파일 범위·상한 없음·일부만 검사·행 0·사실만

- 측정 #11 P0 3·4: c-2의 진짜 줄(`<처리기B 파일>:L50`)을 못 잡은 것은 R3-4 ③의 범위가 등록 **줄**의 파일(등록 모음 파일)이었기 때문이고, 필드 상한 6이
  7번째 필드를 가렸고, 못 본 것을 "정상"이라 적어 판정이 그걸 믿었다. 빈 배열은 요약 자리에 안 보였다.
- **등록은 AST로**(`deployed_code.registrations`): `register` 호출의 `component` 상수 + 바깥 호출 인자(Name, 또는 Attribute의 끝 이름 — 별칭은 import한
  모듈), 데코레이터 형도 같은 모양. 네 모양((a) 한 줄, (b) 사이트 인자, (c) 점 표기, (d) 여러 줄)을 읽고 문서 문자열 속 예시는 안 센다. 레포 스냅샷마다
  한 번 캐시. 사이트 선택: 명시한 사이트 인자(`gbm`·`factory`·`fct`)가 전부 이 사이트 값과 같은 등록만 후보, 그중 명시 인자가 많은 것, 수가 같으면 여럿.
- **범위** = 읽는 클래스의 파일 + 등록이 가리키는 **클래스를 인덱스로 푼 파일**(`qy.find("<레포>:<클래스>")`, 별칭이면 그 모듈의 것). 등록이 아예 없는
  이름(사전 `cfg.get("kind")`)은 컴포넌트가 아니라 못 이음도 아니고, 등록은 있는데 이 사이트 것이 없거나 인덱스에 없거나 여럿이면 못 이음. 변수
  `get(<변수>)`는 **같은 수신자**에 리터럴 get이 있을 때만 "변수 x"(사전 get까지 세면 어느 파일이나 못 이음이 된다).
- **비교식을 먼저 뽑아**(`comparison_pairs`) 입력의 문자열 단일값 **전부**와 맞춘다(상한 없음; 문자열 아닌 단일값은 `대조 안 함 K개(문자열 아님)`). 링크는
  단일값과 무관하게 클래스마다 한 번 묻는다(`fields={}` — 자원 0인 클래스도) → 멈춘 자리의 뒤 칸 = 이은 컴포넌트 **클래스 본문**(`code.read line=<클래스
  정의 줄>`, 나쁜 필드 이름이 리터럴로 든 파일 먼저, 최대 3).
- **문구는 사실만**: `나쁨 — {행 수}행 전부 f='-' g='-' · 이 필드 값이 든 비교식 <파일>:L<줄> '<줄 원문>'`(원인 말 없음). 통과: `· 단일값 … — 거르는 줄
  없음(N/M 대조, P파일, 이은 컴포넌트 X)`. 못 이음이 하나라도 있으면 `미확인 — … · 단일값 … — 일부만 검사(N/M, 못 이음: …)`, 조회 실패는 `일부만 검사(0/M):
  <문제>` — 둘 다 멈춤이 `입력을 다 못 봤다 — <클래스>(일부만 검사)`. 빈 배열은 요약 머리 `[value.record: 행 0]`(`profile([]) == "행 0"`, JSON `"[]"`도).
- 보드: source-filtered r0 입력 행 `나쁨 — 4행 전부 state='-' · 이 필드 값이 든 비교식 batch/components.py:L7 'return [r for r in rows if r.get("state") != "-"]'`(미끼 둘은 안 잡힘), 멈춤 꼬리 `(단일값이 든 비교식 batch/components.py:L7)`, 첫 칸 `code.read line=7`; palette-empty는 전과 같다. 측정판의 등록은 아직 데코레이터 형(호출형·사이트 인자·점 표기·여러 줄은 ④에서).
- 테스트 먼저(RED → GREEN). 스윕 +11(806).

#### R3-5 ④ (10-15) ✅ — 측정판 모양 + 정답 홉 표 테스트 (+ 표가 드러낸 고침 둘, `case walk`의 칸 목록)

- 측정판(`tools/local_case.py`)에 사내 c-1·c-2의 사슬을 세웠다: 요약 키 ← 도메인 키(`alarm:domain:{line}`, `DomainBuilder`) ← 원천 묶음
  (`alarm:raw:{line}`). 변형 `chain-empty`(c-1형 — 셋 다 빈 배열; 원천의 쓴 쪽 `SourceCollector`는 메시지 입력이라 인덱스 자원 0이고 같은 이름이
  dt-api에도 있다)와 `chain-filtered`(c-2형 — 도메인·원천 행 전부 `state='-' wg='-'`, 같은 값인 문자열 일곱 필드 뒤; 원천의 쓴 쪽 `SourceMerger`의
  입력 셋 `feed:*`은 정상). 다른 변형의 도메인·원천 묶음은 정상이라 그 측정은 행이 하나 늘 뿐이다.
- **등록 모음 파일**(`common/registry.py`) 하나에 호출형 네 모양 — (a) 한 줄, (b) 사이트 인자(기본 `StateFilter`는 거르지 않고 이 사이트 전용
  `GumiStateFilter`가 비교한다; 다른 사이트 전용은 다른 파일의 미끼), (c) 점 표기 별칭, (d) 여러 줄 — 과 문서 문자열 속 `@register` 예시(세면 이
  사이트의 state_filter가 둘이 돼 못 이음이 된다). 컴포넌트 파일엔 데코레이터가 없다. 원천 키 행에 `unit='ea'`를 넣어 "단일값 통과 뒤 두 레포
  가르기" 순서를 측정판이 탄다 — 이제 측정판이 P0 1(중첩 함수 가림)을 잡는다(스윕으로 확인).
- **정답 홉 표 테스트**(`tests/test_board_walk.py`): 트리는 테스트가 세우고(리포 `config/`·`knowledge/`를 베끼지 않는다 — 사내에서 실제 값으로 바뀌는
  칸) 대상 레포 둘은 측정판 파일 그대로, `code graph`(graphify 없음) 한 번 뒤 `case walk --target L1/Alarm`을 c-1형·c-2형·정상 시드로. 행(증거 칸
  뺌)·주·멈춤·칸을 통째로 대조한다.
  - c-1형: `alarm:raw:L1 | SourceCollector [dt-core(서비스 3개 공유)] | 빈 배열 (rows)`, 주 `… alarm:raw:{line}을 선언한 dt-core(서비스 3개 공유)로
    골랐다`, 멈춤 `입력을 모른다 — SourceCollector(인덱스 자원 0 — 메시지 입력일 수 있다)`, 칸 `SourceCollector` 본문 → `RowParser` 본문(그 안의
    `row.get("kind") == "event"`). 다른 입력은 정상(가짜 나쁨 0).
  - c-2형: `alarm:domain:L1 | DomainBuilder [batch] | 나쁨 — 5행 전부 state='-' wg='-' · 이 필드 값이 든 비교식 batch/components.py:L11 '…' · 대조
    안 함 1개(문자열 아님)`, `alarm:raw:L1 | SourceMerger [dt-core(서비스 3개 공유)] | 나쁨 — 같은 단일값 state='-' (아래 홉과 같다)`, 입력 셋 정상,
    멈춤 `입력이 다 정상 — SourceMerger (단일값이 든 비교식 batch/components.py:L11)`, 칸 그 줄의 함수 → `SourceMerger._run` → `RowFiller` 본문 →
    `RefJoiner` 본문.
  - 정상 시드: 가짜 나쁨 0(단일값 검사는 돌고 다른 사이트 전용 클래스의 `unit != "ea"`에 안 걸린다).
- 표가 드러낸 것(고침):
  - **같은 줄의 짝 둘을 "외 1줄"로 셌다** — `r.get("state") != "-" and r.get("wg") != "-"`는 한 줄인데 짝 수로 세어 없는 줄을 말했다. `(경로, 줄)`로 센다.
  - **뒤 칸 정렬이 걷기에선 안 먹었다** — ③ 항목은 "나쁜 필드 이름이 리터럴로 든 파일 먼저"라 적었지만 걷기는 링크를 `fields={}`로 물어 어댑터가
    정렬하지 않았다(코드 차례 그대로 — 뒤 칸 셋에서 잘릴 수 있다). 이어 온 단일값이 있으면 그 필드로 묻는다. c-2형은 빈 칸을 채우는 컴포넌트
    (`RowFiller`)가 코드 차례로 뒤인데 칸에서 먼저 온다.
  - `case walk`가 **칸 전부**를 찍는다(`칸(멈춘 자리부터 차례대로):` 번호 목록) — 케이스 블록은 첫 칸(다음 홉)만 보여 측정 #12가 "다음 칸"의 차례를
    대조할 수 없었다. 측정 도구의 출력이지 리드가 받는 것은 아니다.
- 남긴 것(측정 #12가 볼 것): 범위는 사람 결정대로 등록이 가리키는 클래스의 **파일**이다 — 다른 사이트 전용 클래스가 **같은 파일**에 있으면 그
  비교식도 범위에 든다(측정판은 다른 파일에 두었다). 채우는 자리에 필드 이름이 리터럴로 안 나오면 정렬이 안 먹어 코드 차례(최대 3)다.
- 보드: chain-empty·chain-filtered의 `case walk`가 테스트의 표 그대로(행·주·멈춤·칸), source-filtered·palette-empty는 멈춤·첫 칸 그대로(행
  `alarm:domain:L1 … 정상`이 하나 늘었다), 다른 배지 행 `L1/Caution`은 `걷기 없음`, 넷 다 읽기 전용(저장소·출력 변화 없음).
- 테스트 먼저(RED → GREEN: 측정판 2·걷기 1, 측정판 걷기 3은 엔진 수정을 빼고 실패 확인). 스윕 +10(816), 앵커 재지정 4.

#### R3-5b ① (10-16) ✅ — 입구: 대상 행은 순찰과 같은 함수로 통째로, 쓴 쪽은 후보 읽기의 전체 값에서

- 측정 #12: 19행 중 걷기가 c-1·c-2에서만 돌았다. **행 9**(제목에 '/')는 입구가 대상 문자열을 '/'로 쪼개 필드 수와 맞춰 보다 `접수 응답에 대상 행이
  없다`였다 — R3-5 ②의 CLI 테스트는 입구 줄만 보고 주석에 "골랐다"고 적었다(단정이 약했다; 이번에 대상 행 줄까지 단정한다). **16행**은 대상 키를 골랐는데
  `쓴 쪽: 모른다`였다 — data가 커서 후보 본문이 잘린 키는 identity의 부모(`record[0]`)로 좁혀 다시 읽고, 쓴 쪽을 **그 좁힌 값**에서 찾아 형제 자리
  (`metadata.event_type`)를 못 봤다. c-1·c-2는 data가 빈 키라 안 잘려 피했다.
- 대상 문자열은 **한 함수**(`domain.patrol.target_of` — identity 값을 `/`로 이은 것)를 순찰(`rules`)과 입구가 같이 쓴다. 입구는 행마다 이어 케이스 대상과
  **통째로** 비교하고(identity 필드가 없는 행은 대상이 아니다), identity는 그 행의 값이다. 입구는 `focus_of`를 더 안 쓴다 — 나머지 호출부(증거 머리의
  대상 행, recompute 검사)는 부분 문자열 비교라 '/' 제목에서도 행을 찾으므로 동결 그대로.
- 쓴 쪽은 **후보 읽기의 전체 값**에서 찾는다(실행기가 원본을 잘리지 않은 채 들고 있다). 다시 읽기가 실패해도 쓴 쪽은 안다. 안 잘린 키는 전에도 전체 값이라
  "데이터 행 안의 클래스 이름이 둘 이상 → 못 좁힌다"는 새 위험이 아니다.
- 측정판: 제목에 '/'가 든 배지(`Up/Down Rate`)와 그 요약 키 `alarm:rate:{line}` — data 150행이라 후보 본문(기본 2400자)이 잘리고, 쓴 쪽 이름(`RateRefresh`,
  배치 클래스)은 data 밖 `metadata`에 있다. api 핸들러가 그 키를 읽어 입구 후보가 된다(지식 테스트의 getattr 줄이 한 줄 밀렸다). 정답 홉 표 테스트에
  `--target "L1/Up/Down Rate"` 한 판 — 대상 키 줄이 `summary_info`(좁힌 값 안의 자리)라 잘린 키임이 보이고, 쓴 쪽 줄과 걷기 표가 나온다. 쓴 쪽만 옛 방식으로
  되돌리면 측정판이 16행의 `쓴 쪽: 모른다`를 재현한다(변이로 확인).
- 보드: `case walk --target "L1/Up/Down Rate"` → `대상 행: t-0.e1 response[18]`, `대상 키: alarm:rate:L1 — 키 값의 summary_info에 …`, `쓴 쪽: RateRefresh`, 걷기
  홉 2 · 멈춤 `입력이 다 정상 — RateRefresh`.
- 테스트 먼저(RED 7 → GREEN). 스윕 +6(822), 앵커 재지정 4.

#### R3-5b ② (10-16) ✅ — "비었다"는 한 판정, `입력이 다 정상`은 전부 읽고 전부 정상일 때만

- 측정 #12의 걷기 틀린 사실 둘: `<입력 키>`의 `record {}`를 `정상`이라 했다 — 걷기의 상태(`status_of`)는 빈 목록만, 대상 키 홉(`_empty_lists`)도 빈 목록만
  보며 따로 놀았다. `<클래스>`는 인덱스 자원이 자기 출력뿐인데 `입력이 다 정상`이었다 — 입력 수를 자원 줄 수로 셌다. 같은 자리에서 읽기 실패도 나쁨이
  아니라서 `입력이 다 정상`에 섞였다(리뷰에서 찾았고 사람이 넓히기로 했다, 10-16).
- **판정 하나**(`intake_map.empties` — 사람 결정 10-16): 객체 바로 아래에 행이 든 목록이 하나라도 있으면 정상, 없을 때만 빈 목록·빈 객체 자리를 적는다.
  대상 키 홉은 identity의 부모에(`empties_at`, 후보 읽기의 전체 값 기준 자리), 다른 홉은 값의 맨 위에(`status_of`) 쓴다. 문구도 하나(`empty_text`):
  `빈 배열 (a) · 빈 객체 (b)`; 맨 위가 `{}`면 `빈 객체`. `TargetPick.empty_objects`를 더했다(빈 목록 `empty`는 그대로 — c-1 대상 키의 문구가 안 바뀐다).
- **`입력이 다 정상`의 조건**(사람 결정 10-16): 나열된 입력을 전부 실제로 읽었고, 전부 정상이고, 단일값 검사도 완전할 때만. 걸은 클래스마다 실제로 읽은
  입력 수와 못 본 입력을 센다 — 읽기 실패(`읽기 실패 <키>`), 못 채우는 템플릿의 scan 실패(`읽기 실패 <템플릿>(scan)`)·상한·0개(`<템플릿> scan N개 중
  M개`). 멈춤은 못 본 입력이나 일부만 검사가 있으면 `입력을 다 못 봤다 — <클래스>(<그것들>)`, 실제로 읽은 입력이 0이면 `입력을 모른다 — <클래스>(인덱스
  자원 0 — 메시지 입력일 수 있다 | 인덱스 자원이 자기 출력뿐)`, 아니면 `입력이 다 정상`. 키 없음(None)은 읽은 것이다 — 그 행의 사실 `없음`(나쁨)이고 읽기
  실패와 안 섞인다.
- 자기 출력의 판정: 값이 그 클래스를 대면 입력이 아니다 — 대상 키는 입구가 읽었으니 처음부터 `(읽음, 대상의 쓴 쪽)`으로 둔다. 이미 읽은 키를 다른 클래스가
  또 나열하면 다시 안 읽고 그 읽기 결과(정상·실패)를 따른다(안 따르면 두 번째 클래스가 "자기 출력뿐"이 되거나 실패를 놓친다).
- 다음 칸: 읽기가 있었으면(실패한 읽기도) 읽는 메서드, 읽은 입력이 없으면("모른다") 클래스 본문 — 처음엔 실패도 클래스 머리로 갔고 보드에서 보고 고쳤다.
- 측정판: `chain-objects`(도메인 키 `record {}` ← 원천 묶음 빈 배열, 그 쓴 쪽 `RawAppender`는 그 묶음만 읽어 덧붙여 다시 쓴다)·`palette-unread`(palette-empty에
  원천 색 키 읽기 실패). stub Redis는 시드 값이 `{"__error__": …}` 하나면 읽기 실패로 돌려준다(실서버의 WRONGTYPE 자리 — 키 없음과 다른 사실). 엔진 수정을
  빼고 돌리면 측정판이 `record {}`를 `정상`으로 재현한다. 기존 테스트 하나가 읽기 실패 뒤의 `입력이 다 정상`을 단정하고 있어 기대값을 바꿨다.
- 보드: chain-objects `alarm:domain:L1 | DomainBuilder [batch] | 빈 객체 (record)` → `alarm:raw:L1 | RawAppender […] | 빈 배열 (rows)` → 멈춤 `입력을 모른다 —
  RawAppender(인덱스 자원이 자기 출력뿐)`, 칸 클래스 본문. palette-unread `palette:source | — | 읽기 실패 — redis.get key='palette:source' — WRONGTYPE …` → 멈춤
  `입력을 다 못 봤다 — PaletteLoader(읽기 실패 palette:source)`, 칸 그 읽기가 든 메서드. chain-empty·chain-filtered는 그대로.
- 테스트 먼저(RED 7 + 측정판 2 → GREEN). 스윕 +18(840), 앵커 재지정 9.

#### R3-5b ③ (10-16) ✅ — 측정 도구 `case walk --key <키>`

- 측정 #12: 배지 밖 끝점은 입구에서 막힌다 — 라우트가 리터럴 → config로 키를 정해 접수 응답의 행과 키 값의 identity로 고르는 입구가 할 일이 없다(B는
  아직 안 만든다). 사람이 키를 대면 그 키부터 걷는다. 엔진 기능이 아니라 측정 도구다(읽기 전용 — 저장소·보고서·트레이스 없음).
- `case walk --key <키> --gbm --fct [--stub-seeds]`(`walk_only.walk_from_key`): 그 키를 `t-1`로 읽어 대상 키로 삼는다 — 쓴 쪽은 값에서(`writer_of`), 빈 자리는
  걷기와 같은 판정(`empties`, 값의 맨 위), 선언 이름은 흐름 그래프의 선언 키와 자리가 맞는 **하나**(`declared_for`; 없거나 여럿이면 키 그대로 — 입력 채우기는
  scan으로). 키가 없거나 읽기가 실패하면 그 사실만 적고 걷지 않는다. 대상 키 줄은 `대상 키: <키> — --key로 댄 키 (t-1.e1)`. 케이스 id·`--target`·`--key`는
  셋 중 하나만이고 `--key`는 점검을 안 쓴다.
- 보드: chain-objects 시드로 `case walk --key alarm:domain:L1` → `alarm:domain:L1 | DomainBuilder [batch] | 빈 객체 (record)` → `alarm:raw:L1 | RawAppender
  […] | 빈 배열 (rows)` → 멈춤 `입력을 모른다 — RawAppender(인덱스 자원이 자기 출력뿐)`.
- 테스트 먼저(RED → GREEN). 스윕 +7(847).

#### R3-5c ① (10-16) ✅ — 쓴 쪽은 문서 목록(행) 밖의 잎에서만, identity는 개수까지

- 측정 #12b P0 1: 쓴 쪽 링크가 행 안의 값까지 봐 문서 속 화면 라벨이 인덱스의 스키마 클래스로 붙었다(38회). `<알람 전체 키>`는 metadata의 쓴 쪽 + 행 안
  이름 둘로 "3개 — 못 고른다". R3-5b ①에서 쓴 쪽을 후보 읽기의 전체 값에서 찾게 바꿀 때 "행 안의 클래스 이름은 새 위험이 아니다"라고 적었는데, 걷기가
  모든 행에서 돌자 그 위험이 드러났다. 쓴 쪽은 **문서 목록(dict들의 목록) 밖의 잎**에서만 찾는다 — 측정한 사슬은 전부 그 밖(`metadata.event_type`)이었다.
  입구·걷기의 입력·`--key`가 같은 함수라 한 번에 고쳐진다. 쓴 쪽 이름이 행 안에만 있는 키는 "모른다"가 된다(틀린 사실보다 모름).
- P0 2: identity를 집합으로 비교해 그룹=제목 배지가 그 값이 한 번 든 키 셋과 맞았다. 값마다 **개수**까지 센다.
- 측정판: 증감 배지 키의 행 안 화면 라벨 `Overview`와 같은 이름의 dt-api 스키마 클래스, 그룹=제목 배지 `ALL/ALL`과 그 값이 한 번 든 미끼(Caution 키의
  `scope`). 입구만 되돌리면 측정판이 둘 다 재현한다(쓴 쪽 "2개 — 못 고른다", `ALL/ALL` "3개 — 못 고른다").
- 보드: `--target "ALL/ALL"` → `대상 키: 못 골랐다 — 후보 9개 중 값에 대상 identity(ALL/ALL)가 든 키가 없다`, `--target "L1/Up/Down Rate"` → `쓴 쪽: RateRefresh`.
- 테스트 먼저(RED 5 → GREEN). 스윕 +5(852), 앵커 재지정 3.

#### R3-5c ② (10-16) ✅ — 판정 턴: 조각 뒤 멈춤·빈 응답·유휴 상한, 판정 프롬프트의 사슬 중간 홉 접기

- 측정 #12b P0 3: 판정 턴이 664조각 뒤 120초 멈춤을 시간 초과로 끝냈다(재시도 없음). 판정 턴도 리드와 같은 `ask_json`이라 받은 글이 있으면 읽어 보고
  끊긴 답이면 한 번 더 묻게 돼 있었다 — 시간 초과로 끝났다는 것은 받은 글이 비었다는 뜻이다(본문 없는 조각 — 생각 모델의 추론 조각으로 보인다). 빈
  응답 2회(220초씩)는 토큰 상한 탓인지 알 수 없었다.
- 답(`LlmReply`)에 **조각 수**(라이브러리가 넘긴 조각, 끝 조각 포함)와 **종료 사유**(`finish_reason`)를 — 실패한 답에도.
- **조각이 온 뒤의 멈춤**은 받은 글이 없어도 시간 초과가 아니다 — 같은 질문으로 한 번 더(트레이스 메모 `조각 N개 뒤 끊김(받은 글 없음)`). 조각도 없이
  났으면 지금처럼 시간 초과(같은 상한을 또 기다리지 않는다). 받은 글이 있으면 전처럼 읽어 보고 온전하면 쓴다.
- **빈 응답**은 형식 오류가 아니다 — 사유를 붙인 재질문이 아니라 같은 질문·같은 형식으로 한 번 더. 종료 사유가 토큰 상한(`length`)이면 같은 질문도 같은
  상한에 걸리니 다시 안 묻고 `빈 응답(조각 N개, 종료 사유 length)`을 적는다.
- **판정 역할의 유휴 상한** 기본 170초 — 게이트웨이 유휴 끊김(~180초, 10-08) 바로 아래(라이브러리 기본 120초에 끊겼다). 역할 설정에 적으면 그 값이 이긴다.
- **판정 프롬프트의 사슬 중간 홉 접기**(사람 결정 10-16): 고정 증거(사슬)는 예산 축소 밖이라 걷기가 모든 행에서 돌며 판정 프롬프트가 16.7~24K였다.
  판정 턴에서는 대상 키(`t-1`)와 멈춘 홉을 뺀 사슬의 본문을 접는다 — 행 상태·단일값 비교식 줄은 걷기 표에 있고, `- id | 출처 | 요약` 줄이 남아 인용할 수
  있다. 리드 턴은 그대로. 실패는 크기와 무관했으니(사람 확인) 이건 곁고침이다. t-0 배지 응답을 대상 행만 남기는 것, 멈춘 홉을 요약 머리 + 몇 행으로
  줄이는 것은 이번에 안 했다(본문 모양을 바꾼다).
- 테스트 먼저(RED 5 → GREEN; 조각 수는 라이브러리가 끝 조각까지 넘겨 SSE 조각 수보다 하나 많다 — 단정을 그 뜻으로). 스윕 +9(861), 앵커 재지정 1.

#### R3-5c ③ (10-16) ✅ — 인용한 코드 증거의 레포가 component의 레포와 다르면 caveat

- 측정 #12b P1-4: 서술은 배치 레포의 `<요약 처리기>`인데 component가 processor였고, 서비스 이름이라 검증을 지났다. c-9가 근거로 인용한 t-11은
  `code.read service=<배치 서비스>`였다(사람 확인: 둘은 다른 레포).
- verify가 클래스 이름 번역(R3-3 ⑤) 뒤에 `caveat_code_repos`를 건다: component(서비스)의 근거로 인용한 **코드 증거**(`code.*`)를 만든 태스크의 `repo`·
  `service` 인자로 그 레포를 정하고, component 서비스의 레포와 다르면 `component <서비스>(레포 <r1>)의 근거로 인용한 <증거>은 레포 <r2>의 코드다 — 그 레포의
  서비스 <…>도 후보`. component는 안 바꾼다(코드는 사실만 적는다), 검증 문제도 아니다. 인자가 없는 코드 증거(레포 하나)와 코드 아닌 증거는 말하지 않는다.
- 측정판: 이 판정은 LLM 판정 턴 뒤라 `case walk`로는 안 보인다 — 단위 테스트(verify 노드 포함)로 잰다.
- 테스트 먼저(RED → GREEN). 스윕 +4(865).

#### R3-5d ①-a (10-19) ✅ — 레포 가르기는 모든 홉에, 값 없는 홉의 멈춤 문구

- 측정 #13 고침 1의 원인은 걷기 쪽이다: 키 선언으로 레포를 가르는 일(`branches`)이 **나쁜 홉에서만** 돌았다. 정상 홉의 쓴 쪽은 두 레포의 서비스 전부가
  후보로 실렸고, 판정 번역(`translate_classes`)은 홉의 후보를 "가른 결과"로 믿어 첫 것(`<수집기>`)을 고르고 "레포를 …가 공유"라고 적었다(거짓).
- `split(writer, template, walking)`이 가르기 하나를 모든 홉에 적용한다 — 정상 홉의 쓴 쪽 칸·후보·주 줄이 같은 근거를 갖는다(걷지는 않는다). 못 가르면
  주 줄 꼬리가 걸을 때 `둘 다 걷는다`, 정상 홉이면 `둘 다 후보`. 같은 주 줄은 한 번만. 번역의 "공유 vs 못 가름" 구분은 ①-b.
- 추가분 4: 키가 없거나(`없음`) 컬렉션·값이 0행인 홉에서 멈추면 `키가 없다 — K` / `0행 — K(값이 비어 쓴 쪽을 알 수 없다)` — "값에 인덱스의 클래스 이름이
  없다"는 값이 있을 때의 말이다. 다음 칸은 셋 다 `code.uses`(그 키를 누가 쓰나).
- 보드: chain-empty의 `alarm:palette | PaletteLoader [dt-core(서비스 3개 공유)] | 정상 …`과 주 줄 `PaletteLoader는 레포 2곳(api, dt-core(서비스 3개 공유))에
  있다 — alarm:palette을 선언한 dt-core(서비스 3개 공유)로 골랐다`가 SourceCollector 주 줄 앞에 선다. 측정판 테스트의 기대값도 그렇게.
- 테스트 먼저(RED 5 → GREEN; 옛 문구를 단정하던 걷기 테스트 셋은 새 문구로). 스윕 +5(870), 앵커 재지정 5.

#### R3-5d ①-b (10-19) ✅ — 번역의 공유/못 가름, 연결 확인에 걷기 키, 걷기와 어긋남 caveat, 후보의 토픽

- 고침 1(번역): 후보의 레포가 하나면 "레포를 …가 공유 — 첫 것, 전부 후보", 둘 이상이면 "…에 다 있다 — 못 가른다, 전부 후보"(서비스→레포 지도로;
  지도가 없으면 전처럼 걷기의 후보만 공유로). ①-a가 정상 홉도 갈라 두니 c-9형은 가른 레포의 서비스가 후보로 오고, 못 가른 것은 그렇게 적힌다.
- 고침 2(연결 확인): 걷기 홉 표의 키는 걷기가 대상 키에서 이은 것이라 연결된 것으로 — 그 키를 인용한 최상위는 상한에 안 걸린다.
- 고침 4(걷기와 어긋남): 최상위 component(서비스)가 걷기가 멈춘 홉의 쓴 쪽 서비스가 아니면 caveat `판정의 component X는 걷기가 멈춘 자리(<키>, 쓴 쪽 Y
  [서비스들])의 서비스가 아니다 — <서비스들>도 후보`, 걷기가 정상으로 본 홉의 쓴 쪽이면 `(걷기는 <키>을 정상으로 봤다 — 그 쓴 쪽이 X)`. 걷기가 그 클래스의
  입력까지 본 멈춤(`입력…`)에서만 — 쓴 쪽을 모르거나 상한에 걸린 걷기, `external`은 밖. component는 안 바꾼다.
- 추가분 5(후보의 토픽, 사람 결정 10-19): 이 케이스에 나온 토픽(kafka 읽기 태스크의 `topic`)과 **완전 일치**하는 component는 허용(최상위도). 서비스도
  토픽도 아닌 **후보**는 되묻지 않고 그 후보만 빼고 `후보 제외(서비스도 이 케이스의 토픽도 아닌 component): …` — 최상위·기여 요인은 전처럼 재작성.
  옛 테스트 둘(후보의 외부 이름이 재작성·강등을 부르던 것)은 그 규칙대로 기대값을 바꿨다.
- 측정판: 판정 뒤의 검증이라 `case walk`로는 안 보인다 — 단위 테스트(verify 노드 포함).
- 테스트 먼저(RED 4 → GREEN). 스윕 +9(879).

#### R3-5d ② (10-19) ✅ — `length`도 1회 다시, usage, 트레이스 머리의 턴 줄

- 측정 #13 추가분 1: `length`는 고정 상한이 아니었다 — 같은 판정 질문 재전송 7번 전부 stop(출력 3.1K~22.5K 토큰), 판정 20번 중 2번만 length(생각 폭주).
  R3-5c ②의 "토큰 상한이면 다시 안 묻는다"를 되돌린다 — 빈 응답은 종료 사유와 무관하게 같은 질문으로 한 번 더, 사유는 오류 문구에 남는다.
- usage(사람 확인 10-19): 게이트웨이는 `stream_options` 없이도 마지막 조각 하나에 usage를 싣고 langchain-openai가 `usage_metadata`로 옮긴다 — 요청은
  그대로, 어댑터가 스트림 조각·비스트림 응답의 `usage_metadata`만 읽어 `LlmReply.input_tokens`·`output_tokens`에 둔다(0이면 모른다 — 끊긴 스트림엔 없다,
  생각 토큰 구분도 없다).
- 트레이스 머리에 턴 줄 — 성공한 턴도: `턴: 조각 N · 종료 <사유> · 토큰 입력 I · 출력 O`(토큰을 모르면 `토큰 모름`, 셋 다 모르면 줄 없음). 리드가 트레이서에
  넘기는 열째 위치 인자(`stats`)다. `case trace` 요약 머리줄에도 같은 글이 붙는다.
- 테스트 먼저(RED → GREEN; 가짜 게이트웨이가 마지막 조각·비스트림 응답에 `usage`를 싣는다). 스윕 +8(887), 재지정 1(length 사례는 "빈 응답을 다시
  안 묻는다"로).

#### R3-5d ③ (10-19) ✅ — 상속: 부모 클래스의 자원과 파일

- 측정 #13 추가분 3: `code.resources`가 부모 `_run`의 읽기를 하위 클래스에 안 붙여 `<BMS 배지>` 5종이 "인덱스 자원이 자기 출력뿐"이었고, 단일값 검사 범위에
  부모 믹스인 파일이 없었다(믹스인 셋, 상속 38파일).
- 인덱스는 처음부터(6a) `inherits` 엣지를 저장한다 — **`code graph` 재생성 불필요.** `query.ancestors`가 그 엣지를 끝까지(가까운 것부터, 순환은 한 번만)
  따라가고, `resources_lines`가 부모들의 멤버 자원까지 센다(자기 것이 앞; 줄의 메서드 자리가 부모의 것이라 어느 부모인지 보인다) + 둘째 줄
  `부모 클래스의 자원 포함: <부모 qualname들>`. 걷기의 읽는 메서드 자리(`reader_at`)도 그 줄을 따라 부모 파일로 간다.
- 단일값 검사(`filter_lines`)의 범위 = 클래스 파일 + **부모 클래스 파일들** + 이은 컴포넌트 클래스 파일. 부모의 `_run`이 부르는 `get("X")`도 링크다.
- 측정판: `DomainBuilder`가 읽기를 부모 `LineReader`(`common/readers.py`)의 `_run`에서 받고 쓰기만 자기 것이다 — 이 수정 없이는 chain-* 걷기가 거기서
  "인덱스 자원 0"으로 멈춘다(측정판 걷기 테스트 넷이 그걸 잰다).
- 테스트 먼저(RED → GREEN). 스윕 +6(893).

#### R3-5d ④-a (10-19) ✅ — Kafka 포인터 읽기: 포트·stub·실어댑터·등재·증거는 세기만

- 측정 #13 추가분 2(사람 결정 10-19): 처리기가 쓴 키 값의 `metadata.source`(토픽·파티션·오프셋)가 그 키를 만든 메시지 **한 건**을 가리킨다(법인 둘
  122/122키), 보존 범위 밖 2개가 둘 다 진짜 원인 자리. `tail`은 끝에서 몇 건이라 그 메시지가 아니다.
- 포트 `KafkaInspectorPort.message_at(topic, partition, offset)` — `assign` 뒤 `seek` 한 건, 그룹 미참여·커밋 없음. 범위 밖(앞은 지워진 것, 뒤는 아직
  없는 것)과 압축·삭제로 빈 오프셋(seek이 다음 레코드를 준다)은 `found=False`에 `note`(사실이지 실패가 아니다), 범위 안인데 빈 응답 셋이면 실패(모른다).
  이름이 `fetch`가 아닌 이유: `tests/domain/test_ports.py`의 WRITE_VERBS(레포 동사)가 막는다.
- stub: 같은 모양. 토픽 시드는 메시지 목록(오프셋 = 차례, 파티션 0 하나)이거나 `{"begin": n, "messages": […]}`(보존이 n부터) — 측정판이 "키를 만든
  메시지가 토픽에 없다"를 심는 자리.
- 등재 `kafka.message_at`(필수 `topic`·`partition`·`offset`) + `SUMMARY_ONLY`: 실행기가 원본은 들고(걷기가 행을 본다) 증거의 요약 줄·본문은
  `<토픽>[<파티션>]@<오프셋> — 있음 · [rows: 행 N · 필드 M · 모든 행이 같은 값: …] · K바이트 · 보존 b~e` / `… — 없음 · <note>` 한 줄뿐이다(메시지는
  10.4MB까지 — 세기만). 리드의 읽기 목록에는 없다(`briefing._WALK_READS`) — 오프셋을 옮겨 적게 하면 그 수의 전사 실수를 검증하게 된다(recompute의
  `expect`와 같은 이유); 걷기 홉 표와 그 증거로 받는다.
- 걷기·입구·발행 선언·측정판은 ④-b.
- 테스트 먼저(RED 8 → GREEN; 목록 테스트는 action이 없을 때 당연히 통과라 스윕 변이로 효력을 봤다). 스윕 +12(905).

#### R3-5d ④-b (10-19) ✅ — 걷기의 메시지 포인터 홉, 발행 선언, 측정판

- 걷기: 걸은 클래스를 댄 키 값의 포인터(`TargetPick.pointer` / 아래 홉은 그 키 값에서 `pointer_of`)가 있으면 `kafka.message_at` 한 건을 그 클래스의
  입력으로 읽는다 — 표 한 행(`<토픽>[<파티션>]@<오프셋>`, kind `message`, 쓴 쪽 칸은 `발행 선언: <서비스들>` 또는 `발행 선언 없음(우리 밖)`). 값 판정은
  키 값과 하나(`status_of` — 봉투 없는 목록은 맨 위 목록), 단일값 판정도 하나(`judged` — 키 값 블록을 함수로 뺐다). 정상이면 읽은 입력으로 세어
  `입력이 다 정상 — X`(메시지 홉 포함), 비었으면 `입력 메시지가 나쁘다 — X의 <포인터>(<발행 선언>)`, 보존 범위 밖·압축 구멍이면 `키를 만든 메시지가 토픽에
  없다 — X의 <포인터>(보존 범위 b~e · <발행 선언>)`(나쁨 — 키 없음과 같은 종류), 읽기 실패면 `입력을 다 못 봤다 — X(읽기 실패 <포인터>)`. 포인터가 없고
  읽은 입력도 없으면 `입력을 모른다 — X(… · 포인터 없음)` — 토픽을 짐작하지 않는다(사람 결정 10-19). 발행 쪽으로는 걷지 않는다.
- 다음 칸: 나쁨이면 그 메시지를 받는 클래스 본문(메시지만 읽은 클래스는 읽는 메서드 줄이 없어 본문) → 이은 컴포넌트 → `kafka.tail <토픽>`; 없음이면
  `kafka.tail`이 먼저. 단일값 비교식이 잡히면 그 줄의 함수가 맨 앞(③과 같다).
- 포인터의 필드 이름은 `topic`·`partition`·`offset`으로 **가정**(값 안 문서 목록 밖의 객체, 숫자 문자열 허용) — 사내 실제 이름이 다르면 "포인터 없음"이
  뜬다 → 측정 #15에서 확인할 것. 입구(후보 읽기의 전체 값)와 `--key`가 같은 함수로 적는다.
- 발행 쪽: `flow.producing_map`(토픽 → `produces` 엣지 중 config 출처의 서비스) → `EngineDeps.topic_producers` — `case walk`·`case investigate` 둘 다
  넘긴다. verify의 어긋남 caveat는 메시지 홉이 멈춘 자리면 `(<포인터>, 발행 선언: …)`로 적고 발행 서비스를 후보로.
- 측정판 변형 셋(`message-empty`·`message-gone`·`message-blank`): 원천 묶음 키 값에 `metadata.source`, stub 토픽 시드에 포인터 메시지(gone은 발행
  선언이 있는 가공 토픽의 `begin: 100` 앞). `RowParser`의 비교식은 `ws != '-'`로(메시지 행의 단일값 자리). 다른 변형엔 포인터가 없어 정답 표가 그대로이고,
  포인터 없는 "모른다" 멈춤 둘(chain-empty·chain-objects)에 ` · 포인터 없음`이 붙는다.
- 테스트 먼저(RED → GREEN). 스윕 +18(923).

#### R3-5d ⑤ (10-19) ✅ — 결함 아님(`not_a_defect`)은 걷기가 지킨다

- 측정 #13 추가분 6: 판정 유형에 "결함 아님"이 없었다 — 정상 공백(비어 있는 것이 그 코드의 의도된 결과)을 `inconclusive`나 상류 탓으로 적었다. 사람 결정
  10-19 (3): "증거 id 하나 이상"은 모든 판정이 지나 구분력이 없다 → (a)~(d).
- 어휘: `VerdictType`·`ConcludeReply`에 `not_a_defect`. 원인이 없는 판정이라 `root_cause`가 없고(있으면 모델이 거부 — 리드면 수리 재시도) 근거는 새 필드
  `basis`(증거 id 목록)에. 리드 어휘는 `basis`가 비면 거부한다.
- verify(`not_a_defect_problems`, 대본 경로는 밖): (a) `walk.stopped`가 `입력이 다 정상`(포인터 메시지 홉 포함) (b) 홉 표에 상태가 `나쁨`으로 시작하는 행 없음
  (단일값이 거르는 줄에 걸린 것 — `빈 배열`·`0행` 홉은 아니다: `<ATR 배지>`형이 (a)(b)를 지나는 것이 사람 결정의 전제) (c) `basis`가 멈춘 클래스의 코드 증거
  (`code.resources name=<클래스>` 또는 칸의 `code.read`)와 그 클래스의 비지 않은 입력 증거(멈춘 홉의 자식 중 `정상`)를 **함께** 인용 — 하나라도 아니면 되묻고,
  재작성 뒤에도면 `inconclusive`(low)로 강등(근거 없는 "정상"을 내보내지 않는다). `basis`의 없는 id는 인용 검사에, 태스크 id는 인용 정리에 같이 든다.
  (d) 지났으면 확신 상한 medium + caveat(걷기는 나열된 입력과 메시지 한 건만 봤다 — 조인으로 거르는 처리기는 못 본다, P1-5).
- 프롬프트: 유형 목록에 `not_a_defect`(결함 아님 — 정상 공백)와 조건 한 줄, 예시의 `basis`는 **빈 목록**(자리표시 문장을 두면 다른 판정도 베껴 "없는 id"가 된다).
- 보고서·벤치는 이 리포에 없다(12b 범위) — 진단 줄은 유형을 그대로 찍는다.
- 테스트 먼저(RED → GREEN). 스윕 +15(938).

### 측정 #14·#15(10-19)와 R3-5e — 고침 여덟

결과·사람 결정·구현자 제안(동의)은 [review-12a-4.md](review-12a-4.md)와 계획 문서 R3-5e.

#### R3-5e ① (10-19) ✅ — 사이트가 덮은 같은 토픽 값의 둘째 행도 그 관계로 config 엣지

- 측정 #15 P0-1: 발행 선언 16행 전부 "없음(우리 밖)". 사내 사이트 층은 소비 토픽과 발행 토픽을 **같은 값**으로 덮고 GBM 층엔 그 값이 없다 —
  `flow.apply_site`가 첫 행(소비 key_path)으로 노드를 만든 뒤 둘째 행(발행 key_path)은 `노드가 이미 있다 → continue`로 엣지 없이 넘어갔다. 측정판은 토픽을
  사이트 층이 안 덮어(GBM 층의 config `produces` 엣지로) 못 잡았다.
- 고침: 같은 값을 다른 key_path로 또 만나면 노드는 하나, 엣지는 **그 행의 관계로** 더 단다. 같은 (출발, 도착, 관계)의 config 엣지는 한 번 — 도착은 바뀐
  이름으로 견줘 GBM 노드를 이름만 바꾼 자리(그 엣지가 따라온다)에 또 안 붙고, 두 번 입혀도 같다.
- 측정판: gumi 층이 감사 토픽을 소비(`topic3`)·발행(`topic2`) 같은 값으로 덮고(GBM 층엔 없다 — `code graph`가 `gumi 덮은 값 3`), message-gone의 포인터가
  그 토픽의 보존 앞을 가리킨다 → `case walk`의 메시지 홉 행이 `발행 선언: batch, processor, sink`(사이트 행에서). 코드에 안 나오는 config 이름이라 흐름
  요약의 "config에만 있는 이름"이 하나 는다(테스트 기대값 1 → 2).
- 테스트 먼저(RED 3 → GREEN). 스윕 +3(941).

#### R3-5e ② (10-19) ✅ — 진입에서 호출 그래프로 닿는 부모 메서드의 자원만

- 측정 #15 P0-2: ③이 부모 멤버 자원을 전부 붙이니 다른 자식만 부르는 믹스인 메서드의 `<근무일 키>` 읽기가 홉 29/33에 실렸고(그 메서드를 부르는 파일은
  75개 중 6개) 한 법인의 정상 배지에 가짜 멈춤 `키가 없다 — <근무일 키>`가 났다.
- `query.reachable_members`: 진입 = 자기 메서드 전부 + 부모 메서드 중 `calls` 엣지가 하나도 안 들어오는 것(프레임워크가 부르는 `_run` — 사람 동의: 토폴로지에
  진입 이름을 적게 하지 않는다). 거기서 `calls`(exact·candidate — 템플릿 메서드의 `self.handle()`은 하위 클래스들로 candidate)로 닿는 부모 메서드만, 이
  클래스와 부모들의 메서드 안에서. `resources_lines`는 자기 메서드 + 닿은 부모 메서드의 자원만 세고 둘째 줄에 ` · 진입에서 안 닿은 부모 메서드 N개 뺌`.
  `filter_lines`의 범위도 닿은 부모 메서드의 파일만(P0-3의 절반 — 안 부르는 믹스인의 `get(<변수>)`가 "못 이음"으로 세어지던 것).
- 측정판: `DomainBuilder(LineReader)`는 그대로(부모 `_run`은 호출 없음 → 진입). 단위 픽스처에 다른 자식만 부르는 부모 메서드(`workday`)와 다른 자식만 부르는
  믹스인(`Audit`)을 더했다.
- 테스트 먼저(RED → GREEN). 스윕 +4(945), ③ 사례 셋 재지정.

#### R3-5e ③ (10-19) ✅ — `get(<변수>)`를 인자 기본값 리터럴로 잇는다

- 측정 #15 P0-3: 부모 파일의 `get(<변수>)` 둘이 "못 이음 — 변수"로 세어져 N/N 대조에도 `미확인` 59행, `입력을 다 못 봤다(일부만 검사)` 6건(정상 배지 3).
- `deployed_code.default_links`(AST): 변수가 그 호출을 품은 함수의 **인자**이고 기본값이 문자열 리터럴이면, 범위 파일 안의 같은 이름 호출들이 그 인자를
  안 넘기면 기본값, 리터럴을 넘기면 그 리터럴(전부 같은 하나일 때만), 넘긴 것이 또 그런 인자면 한 단계 더(깊이 3). 변수·식을 넘기거나 값이 갈리면 못
  잇는다(지금처럼). 실행이 아니라 AST 사실이다. `filter_lines`는 이은 리터럴을 컴포넌트 이름에 더하고(그 클래스 파일이 범위에 든다) 남은 변수만 못 이음.
- 테스트 먼저(RED → GREEN). 스윕 +4(949), 옛 `unlinked` 줄의 사례 하나 재지정.

#### R3-5e ④ (10-19) ✅ — 잘린 인용은 되묻지 않고 코드가 caveat

- 측정 #15 P0-4: verify 규칙 2("잘린 표본을 인용했으면 caveat에 그 id")로 여섯 번 되물었고, c-2는 맞는 첫 답(external, 원천 토픽)을 버린 뒤
  두 번이 length로 죽었다. 사실을 적는 일은 리드에게 되물을 일이 아니라 코드가 할 일이다.
- `nodes.caveat_truncated`: 다리·`basis`가 인용한 잘린 표본(`complete=False`) 중 caveat에 아직 없는 id마다 `잘린 표본 <id>을 인용했다 — 그
  표본으로는 '없다'를 단정할 수 없다`를 **코드가** 단다(토큰 경계 `_id_mentioned` 그대로). verify 노드가 검사 전에 거치므로 되묻지 않고 첫 답을
  쓰며, 강등돼도 caveat는 남는다. `verify_verdict`에서 규칙 2와 `incomplete` 인자를 뺐다.
- 테스트 먼저(RED → GREEN). 스윕 +3(952), 옛 규칙 2의 사례 둘 재지정.

#### R3-5e ⑤ (10-19) ✅ — 조각 메타데이터는 비지 않은 값만 덮는다

- 측정 #15 P1-6: 리드 턴 종료 사유 30/30 "모름" — `_once`가 조각마다 `response_metadata`를 통째로 덮어, 끝 표시 뒤에 오는 조각(usage만 실은 것
  등)의 비어 있지 않은 메타데이터(langchain-openai가 조각마다 붙이는 값)가 `finish_reason`을 지웠다. 리드는 max_tokens 1500이라 `length`를 알아야 한다.
- `llm_chat_model.merge_metadata`: 비지 않은 값만 덮는다(마지막 비지 않은 종료 사유가 남는다). 가짜 게이트웨이는 usage를 끝 표시 **뒤** 본문 없는
  뒷조각으로 보낸다 — 그 모양에서 옛 코드가 종료 사유·모델 이름을 잃는 것을 RED로 먼저 봤다. 사내 게이트웨이가 usage를 어느 조각에 싣는지는 사람
  확인(10-19 "마지막 조각")뿐이라 어느 모양이든 같은 결과가 되게 했다.
- 테스트 먼저(RED → GREEN). 스윕 +2(954).

#### R3-5e ⑥ (10-19) ✅ — 레포 이름 번역 · 못 본 멈춤 뒤의 확신 상한 · 메시지 읽기 시간

- P1-7: `translate_classes`가 **레포 이름**(레포 지도의 값 — 서비스가 곧 레포면 제외)과 홉 표의 쓴 쪽 글자 `<레포>(서비스 N개 공유)`(걷기 홉의
  `service`·`candidates`)도 그 레포의 서비스 전부 후보로 — 첫 것으로 바꾸고 caveat `component X는 서비스가 아니라 레포 — 그 레포의 서비스 Y로
  바꿨다 (레포를 …가 공유 — 첫 것, 전부 후보)`. 측정 #15 c-4(최상위 강등)·c-7(후보 제외)의 모양.
- P1-8: `cap_unseen_stop` — 걷기가 멈춘 자리를 못 본 멈춤(키가 없다·입력을 모른다(포인터 없음)·입력을 다 못 봤다(읽기 실패)·자원을 못 읽었다·
  깊이/읽기/초 상한) 뒤의 high → medium + caveat `걷기가 멈춘 자리를 못 봤다 — <멈춤>; 확신 상한 medium`. 연결 상한과 같은 모양(되묻지 않는다).
  본 멈춤(다 정상·메시지가 나쁘다·토픽에 없다·쓴 쪽을 모른다/0행)은 밖. 대본 경로는 안 건다.
- 메모: 걷기의 30초 상한에서 `kafka.message_at` 읽기에 쓴 시간을 뺀다(`aside`) — 읽기 수 상한과 `Walk.seconds`(실제 시간)는 그대로.
- 테스트 먼저(RED → GREEN). 스윕 +6(960), 옛 사례 둘(시간 상한·번역의 걷기 후보) 재지정. **R3-5e 끝** — 다음은 측정 #16 브리프(`code graph` 재생성 불필요).

### 측정 #16(10-20)과 R3-5f

측정 #16(걷기 36·LLM 15): ②·④·⑤·⑥ 작동, `not_a_defect` 1/15, length 2/18. 틀린 것 셋 — 발행 선언이 `<처리기>` 5개로 잘못(P0-b), `default_links`가 실코드
모양에서 `{}`(P0-3), 부모 메서드의 `self.<속성>` 키가 다른 하위 클래스 값으로(P0-c). 원인·결정은 리뷰 문서 "측정 #16 결과"와 계획 문서 R3-5f.

#### R3-5f ① (10-20) ✅ — 사이트 행의 정체는 (종류, key_path, 서비스): base 있는 행만 이름을 바꾼다

- 사람 답(10-20): 사내 사이트 행 17개는 전부 base 없음(GBM 그래프에 토픽 노드 0). `…producer.topic.default_topic`에 처리기·배치의 데이터 토픽 행과 수집기의
  원천 토픽 행이 차례로 와서, key_path로 노드를 찾던 `apply_site`가 데이터 토픽 노드의 이름을 원천 토픽으로 바꿨다 — 발행 선언 "처리기 5개", 수집기 없음,
  데이터 토픽 노드 사라짐. 결정: base 없는 행은 만들거나 붙이기만, 이름 바꾸기는 하지 않는다.
- `flow.apply_site`: `base`가 있는 행만 **그 값의 노드**를(key_path가 아니라) 이름 바꾸고(`by_label`), 사이트 값이 이미 다른 노드면 옛 노드를 빼고 엣지를
  옮긴다(id가 둘인 노드를 안 만든다). base 없는 행은 새 노드 또는 기존 노드에 config 엣지만. `flow.site_rows`(순수 함수, `site_overrides`가 쓴다): 서비스마다
  그 서비스의 GBM 값과 견주고 base가 갈리면 행을 나눈다 — key_path 하나에 GBM 값 하나로 견주면 같은 key_path를 다른 값으로 쓰는 두 레포 중 한쪽이 덮지
  않았는데도 덮은 행이 됐다.
- 측정판: dt-api의 gumi 층이 dt-core와 같은 key_path(`producer.topic.topic2`)로 사이트 전용 토픽 `gumi.api.audit`를 선언한다 — 옛 코드에서는 이 행이
  `gumi.alarm.audit` 노드 이름을 바꿔 보드의 message-gone 홉이 `발행 선언 없음(우리 밖)`이 됐다(RED). `code graph`의 gumi 덮은 값 3 → 4.
- 테스트 먼저(RED → GREEN). 스윕 +4(964), 옛 앵커 넷(rename 둘·`site_overrides` 둘) 재지정. **측정 #17 전에 `code graph` 재생성 필요**(`sites/<fct>.json`을
  다시 쓴다).

#### R3-5f ② (10-20) ✅ — `get(<변수>)`를 이름 단위로 잇는다

- 측정 #16 P0-3(실코드): `default_links`에 `<배치 레포>`의 base.py + 하위 클래스 파일을 넣으면 `{}` — `get(<변수>)`를 품은 부모 함수에는 기본값이 없고 리터럴
  기본값은 재정의(super()로 위치 인자 전달)에 있다. 함수 단위 `resolve`는 부모에서 "기본값 없음"으로 바로 None이었고, 호출을 이름만으로 맞춰 재정의 안의
  `super()._run(rows, shape)`를 재정의 자신의 호출로도 세어 자기 인자로 되돌았다. 못 이음 두 변수 50번씩, `미확인` 50행, `일부만 검사` 11건.
- `default_links.resolve(name, param)`: 같은 이름의 정의들과 호출들을 한 묶음으로 — 호출이 넘긴 값(리터럴, 호출한 쪽의 인자면 그 이름에서 한 단계 더),
  안 넘긴 호출이 쓸 기본값(같은 이름 정의들의 문자열 기본값, 하나일 때만), 호출이 없으면 그 기본값 — 전부 같은 하나여야 잇는다. 재정의가 자기 인자를
  `super()`로 넘기는 것은 호출이 아니라 전달이다. 정의마다 인자 자리가 다르면 못 잇는다. 수신자에 리터럴 `get("…")`이 하나도 없는 범위(뼈대의 `get(<변수>)`뿐)에서는
  `component_names`가 그 변수를 안 세므로(사전 get과 못 가른다), 이은 리터럴이 등록된 컴포넌트면 그 변수도 컴포넌트 변수로 센다.
- 측정판: 보드의 `SourceCollector`가 `MessageJob._run(message, parser)`(기본값 없음, `components.get(parser)`)를 잇고 재정의 `_run(message, parser="row_parser")`가
  super()로 넘긴다 — 옛 코드는 "못 이음 — 변수 parser"라 parsers.py의 `ws != '-'` 비교식을 못 봐 message-blank·source-blank 행이 깨졌다(RED). 단위 fixture에도
  `Based`/`Overrider`와 순수 함수 사례(부르는 쪽 없음·리터럴 넘김·재정의 둘의 기본값이 다름·기본값이 어디에도 없음).
- 테스트 먼저(RED → GREEN). 스윕 +4(968), 옛 앵커 둘 재지정. `code graph` 재생성과 무관(질의 때 스냅샷 AST).

#### R3-5f ③ (10-20) ✅ — 클래스 속성 리터럴은 그 클래스의 것

- 측정 #16 P0-c: 부모 메서드가 `config.get("redis_key", self.<속성>)`으로 키를 고르는데 인덱스가 다른 하위 클래스의 속성 값 키까지 이 클래스 입력으로
  붙였다 — `<배송 처리 클래스>`에 `<ATR 키>`가 붙어 c-10 걷기가 `키가 없다 — <ATR 키>`에서 멈추고 판정이 그걸 근거로 data_loss. 원인: `index._walk_body`가
  클래스 수준 `NAME = "리터럴"`을 모듈의 `string_consts`에 넣고 `attach_resources`가 그것을 모듈 전체의 별칭 표(식별자당 첫 모듈이 이긴다)로 만들어 그
  식별자가 보이는 모든 함수에 붙였다(`via=alias`).
- 인덱스: 클래스 속성 리터럴은 `_Class.attr_consts`(그 클래스의 것). `self.<속성>`을 읽는 메서드에는 그 속성을 정의한 **가족**(자기·조상·후손) 클래스마다
  그 리터럴의 이름을 `attr:<속성>:<클래스>`로 붙인다(`attr_literals`·`_self_reads`). 질의(`resources_lines`): 질의 클래스의 MRO에서 **가장 가까운 정의**의 것만
  남기고, MRO에 리터럴이 없으면 `self.<속성>: 이 클래스와 부모에 그 속성의 리터럴이 없다 — 못 이음(값을 가진 하위 클래스: …)` 한 줄(입력으로 안 센다). 걷기는
  `code.resources` 줄을 읽으므로 그대로 따라온다.
- 측정판: 보드 `batch/keyed.py` — `KeyedJob._run`이 `self.cfg.get("redis_key", self.redis_key)`, `PaletteSync`(alarm:palette)·`FeedRefSync`(feed:ref). 옛 인덱스는
  마지막 속성(feed:ref)을 부모에 박아 `code resources PaletteSync`가 feed:ref를 보였다(RED).
- 테스트 먼저(RED → GREEN). 스윕 +4(972). **측정 #17 전에 `code graph` 재생성 필요**(인덱스 자원이 바뀐다 — ①과 같은 이유로 한 번이면 된다).

#### R3-5f ④ (10-20) ✅ — length 뒤 재질문에 "짧게" 한 줄

- 측정 #16: 판정 호출 18번 중 length 2 + 유휴 1(c-14는 두 번 다 실패 → degraded), 성공 출력 3.1K~10.6K. 사람 결정 (2)는 답이 없어 구현자 제안을 기본값으로
  — 판정 역할 `max_tokens`는 동결(상한을 둬도 끊긴 답이기는 마찬가지), 대신 length 뒤 재질문에 답의 모양을 말한다.
- `lead.brief_prompt`: `## 짧게 — 앞의 답이 길이 상한에서 끊겼다. 같은 형식으로, 서술은 핵심 몇 문장으로 짧게, 후보(alternates)는 둘 이내로 내라`를 **맨 뒤에**.
  빈 긴 답(같은 질문 재전송)에도, 깨진 긴 답(사유 재질문 `## 다시`)에도 붙는다. `stop`·시간 초과 뒤에는 안 붙는다.
- 테스트 먼저(RED → GREEN). 스윕 +2(974), 옛 앵커 하나(호출 실패 재시도의 스키마) 재지정. **R3-5f 끝** — 다음은 측정 #17 브리프(`code graph` 재생성 **필요**).

### 측정 #17(10-20)과 R3-5g

측정 #17(걷기 36·LLM 15): R3-5f ①~④ 전부 작동, `not_a_defect` 1/15, length 5/20. 작은 결함 하나(자원 아닌 리터럴의 자기 속성이 못 이음으로)와
사람 결정 둘(판정 `max_tokens` 16K 시험은 사내 config, P1-5 조인 동결 해제 — 병합식은 pandas `merge`). 원인·결정은 리뷰 문서 "측정 #17 결과"와 계획 문서 R3-5g.

#### R3-5g ① (10-20) ✅ — 속성 리터럴은 자원이 아니어도 정의다

- 측정 #17 작은 결함: `<배송 클래스>` 조회에 `self.<역할 속성>: … 못 이음(값을 가진 하위 클래스: <ATR 클래스>)` — 그 클래스엔 `"material"` 리터럴이 있고
  `<ATR 클래스>`는 형제다. R3-5f ③이 config 이름과 같은 리터럴만 후보로 붙여 자원 아닌 정의가 "없음"으로 보였고, 가족의 후보를 "하위 클래스"라 적었다.
- 인덱스: 클래스 수준 문자열 리터럴 전부를 `Symbol.consts`에(빈 문자열은 자리표시자라 뺀다; JSON 왕복). 질의: MRO에서 그 속성을 **정의한** 가장 가까운
  클래스를 `consts`로 찾고, 그 정의가 자원이면 그 줄, 자원이 아니면 풀린 것(줄 없음), MRO에 정의가 없을 때만 `못 이음(자원 값을 가진 다른 클래스: …)`(자원 아닌 값으로 정의한 형제는 후보가 아니라 거기 안 보인다). `consts`가
  없는 옛 번들은 전처럼 후보 중 가장 가까운 것.
- 측정판: 보드 `KeyedJob._run`이 `self.mode`도 읽고 `PaletteSync.mode = "material"`, 형제 `FeedRefSync.mode = "feed:ref"`(키 이름) — 옛 코드는 PaletteSync 조회에
  `self.mode … 못 이음(값을 가진 하위 클래스: FeedRefSync)`를 냈다(RED).
- 테스트 먼저(RED → GREEN). 스윕 +3(977). **측정 #18 전에 `code graph` 재생성 필요**(심볼에 `consts`가 는다).

#### R3-5g ③ (10-20) ✅ — 메시지 읽기가 한 번 실패하면 그 걷기의 다음 메시지는 안 읽는다

- 측정 #17 메모: kafka 접속 실패는 메시지 한 건에 50~60초이고 걷기 시간 상한 밖(R3-5e ⑥)이라 c-9 걷기가 135초였다.
- `walk`: 한 걷기에서 메시지 읽기가 처음 실패하면 사유(첫 줄 80자)를 들고, 그 뒤 포인터 홉은 `읽기 실패 — 앞 메시지 읽기가 실패해 건너뜀(<사유>)`로만
  적는다 — 읽기 수·증거에 안 세고, 멈춤은 전처럼 `입력을 다 못 봤다`. 접속 실패만 가려내지 않는다(오류 문구 규칙을 두지 않는다) — 한 걷기의 메시지 읽기는
  많아야 몇 건이고, 두 번째도 실패할 확률이 높은 쪽에 베팅한다.
- 테스트 먼저(RED → GREEN). 스윕 +1(978). `code graph`와 무관.

#### R3-5g ④-a (10-20) ✅ — pandas 병합식을 어댑터가 뽑는다

- 사람 결정(10-20): P1-5 조인의 동결을 푼다, 사내 병합식은 pandas `merge`. 비교식(`comparison_pairs`)과 같은 자리에서 **코드가 무엇으로 잇나**를 AST로 —
  `deployed_code.merge_pairs`: `x.merge(y, on=…)`·`pd.merge(x, y, on=…)`, `left_on`/`right_on`(문자열·문자열 목록), `how`(없으면 inner). 키가 변수면 모른다.
- `FilterScan.merges`(`MergeHit`: 경로·줄·본문·왼쪽 필드들·오른쪽 필드들·how) — `filter_lines`가 단일값 범위(클래스 파일 + 닿는 부모 파일 + 이은 컴포넌트
  파일)에서 뽑는다. 걷기는 ④-b에서 쓴다.
- 테스트 먼저(RED → GREEN). 스윕 +3(981). `code graph`와 무관.

#### R3-5g ④-b (10-20) ✅ — 걷기의 조인 검사: 두 입력의 키 값이 겹치는가

- P1-5(동결 해제, 사람 결정 10-20): 코드 비교식이 아니라 양쪽 데이터의 키 차이로만 보이는 거름 — 사내 작업자 배지 5건(원천의 빈 작업대 코드가 기준표에
  없어 inner 병합이 0건, 입력 둘은 각각 정상). 손 규칙 없음: 병합식은 AST 사실(④-a), 겹침은 걷기가 읽은 두 입력의 값.
- `walk.join_check(merges, inputs)`: 병합식마다 왼쪽 키 필드를 가진 행이 있는 입력과 오른쪽 키 필드를 가진 다른 입력의 값 집합(여러 필드면 묶음)을 견줘
  하나도 안 겹치면 짝 없음 — 변수를 따라가지 않는다(필드 이름이 사실). 한쪽이 비었거나 하나라도 겹치면 아니다. 걷기는 한 클래스의 입력을 다 읽은 뒤
  `FilterScan.merges`로 세고, 짝 없는 두 홉을 `나쁨 — 병합 짝 없음: <A>.<f> {…} ↔ <B>.<g> {…} 겹침 0 (<how> 병합) (<파일>:L<줄>) · <원래 요약>`으로 바꾸며,
  읽을 때는 정상이라 안 낸 가지(아는 쓴 쪽)를 그때 낸다. 멈춘 자리가 그 홉이고 쓴 쪽으로 더 못 걸었으면(쓴 쪽 모름·메시지) 사유는 `병합이 0건 — <클래스>: …`,
  첫 칸은 병합 줄(그 클래스의 레포), 그다음은 전처럼 그 입력의 칸(메시지면 `kafka.tail`, 키면 `code.uses`); 쓴 쪽으로 걸었으면 멈춤은 전처럼 그쪽(`입력이 다 정상 —
  <쓴 쪽>` 등)이고 병합은 홉 상태에 남는다. 어느 쪽이 틀렸는지는 걷기가 정하지 않는다 — 두 홉 다 나쁨이고 판단은 리드의 것.
- 측정판: 변형 `join-empty` — `SourceJoiner`(`common/joiner_pd.py`, pandas `merge(left_on="alarm_code", right_on="code", how="inner")`)가 원천 묶음을 쓰고
  이벤트 행의 `alarm_code`가 전부 `''`(쓴 쪽 없음). 정답 표: 두 입력 홉이 `병합 짝 없음`, 멈춤 `병합이 0건 — SourceJoiner: …`, 첫 칸 병합 줄.
- 테스트 먼저(RED → GREEN). 스윕 +4(985). `code graph`와 무관. **R3-5g 끝** — 다음은 측정 #18 브리프(`code graph` 재생성 **필요** — ①).

### R3-5h — 측정 #18 뒤 (10-21)

#### R3-5h ① (10-21) ✅ — 병합 짝 없음은 inner만, 나머지는 주

- 측정 #18 P0-b: `병합 짝 없음 … (left 병합)` 6행이 가짜 나쁨 — left 병합은 겹침 0이어도 왼쪽 행이 남는다(c-1이 `키가 없다` 대신 읽기 15 상한까지 갔다).
  ④-b가 `how`를 안 가렸던 것. 나쁨은 inner(과 남는 쪽이 빈 경우 — 그건 이미 `0행` 홉)만.
- 걷기: inner 아닌 병합의 겹침 0은 사실이라 주(`주:` 줄)로만 — `<클래스>: <짝> 겹침 0 (<how> 병합) — <how> 병합이라 행은 남는다 (<파일>:L<줄>)`. 홉·멈춤은
  그대로. ②가 여기에 채움값 조회를 얹는다.
- 키 필드를 가진 입력 행이 어느 쪽에도 없는 병합식(`join_unmatched`)은 주 `<클래스>: 병합식 <파일>:L<줄>의 키 필드(<f> ↔ <g>)를 가진 입력 행이 <왼쪽·오른쪽·양쪽>에
  없다 — 겹침을 못 센다` — 측정 #18에서 작업자 배지의 병합식이 왜 안 세어졌는지(변환 뒤 이름이 다른가)를 다음 판이 보이게. 읽은 입력이 없으면 이 주는 없다
  ("입력을 모른다"가 말한다).
- 테스트 먼저(RED → GREEN). 스윕 +4(989). `code graph`와 무관.

#### R3-5h ② (10-21) ✅ — 이어진 호출의 채움값을 거르는 줄

- 측정 #18 P0-a: ④가 작업자 배지를 못 잡았다 — 핵심 병합이 outer이고 결과에 바로 이어진 호출이 짝 없는 칸을 `'-'`로 채우며 같은 파일의 비교식(L50)이 그
  값을 거른다. 결과가 비지 않으니 "빈 결과"가 아니라 "짝 없는 행의 그 열이 전부 채움값"이 사실이고, 그것이 비교식과 이어진다.
- 어댑터: 병합식 결과에 바로 붙은 `.<이름>(…)` 중 문자열 인자가 꼭 하나인 것을 `MergeHit.chained`(이름, 그 문자열)로 — 채움값 **후보**다. 호출 이름은
  정하지 않는다(사내 어휘를 안 쓰고, `.to_dict("records")` 같은 흔한 꼬리도 후보가 되지만 그 값이 비교식에 없으면 아무 일도 없다). 문자열 인자가 둘이면 모른다.
- 걷기: inner 아닌 병합의 겹침 0 + 후보가 있으면 채워지는 열(`fill_fields` — 보존 안 되는 쪽 입력의 열: left면 오른쪽, right면 왼쪽, outer면 양쪽; 양쪽 이름이
  같은 키는 합쳐지니 뺀다)을 그 값으로 단일값 조회(`lookup(fields={열: 값})` — 단일값 검사와 같은 조회, 읽기 상한 밖). 거르는 줄이 있을 때만 두 홉
  `나쁨 — 병합 뒤 채움값: <짝> 겹침 0 (<how> 병합) → <how> 병합이라 행은 남고 짝 없는 행의 <열>(<쪽> 열) 값이 <값>(<호출>(<값>)) · 이 값을 거르는 줄 <파일>:L<줄>`,
  멈춤(쓴 쪽으로 더 못 걸었을 때) `병합 뒤 채움값을 거른다 — <클래스>: …`, 칸은 거르는 줄 → 병합 줄 → 입력 칸. 거르는 줄이 없으면 ①의 주뿐.
- 측정판: 변형 `join-outer` — `SourceOuterJoiner`(`common/joiner_outer.py`: outer 병합 → `.pipe(fill_blanks, "-")` → `code`·`alarm_code`가 `'-'`인 행을 거른다).
  정답 표: 두 입력 홉 `병합 뒤 채움값`, 멈춤 `병합 뒤 채움값을 거른다 — SourceOuterJoiner: …`, 첫 칸 거르는 줄, 둘째 칸 병합 줄.
- 테스트 먼저(RED → GREEN). 스윕 +5(994). `code graph`와 무관(인덱스 심볼은 그대로 — 측정판의 새 파일은 그 보드의 `code graph`가 읽는다).

#### R3-5h ③ (10-21) ✅ — 결함 아님의 root_cause를 코드가 basis로

- 측정 #18 P0-c: c-10·c-12(정상 공백)가 두 번 다 `not_a_defect`에 root_cause를 같이 실어 실패 → degraded(`root_cause.component: Field required` →
  `root_cause 없이 basis에`). 지난번엔 둘 다 통과. 수리 재질문이 같은 모양을 또 낸다 — 뜻이 하나인 실수(결함 아님에는 원인이 없고, 거기 적힌 증거 id가
  곧 근거다)라 코드가 옮긴다.
- `ConcludeReply`의 사전 검증(`mode="before"`): `not_a_defect` + root_cause → root_cause의 증거 id(있으면)를 basis 뒤에 붙이고(중복 없이) root_cause를 비운다.
  component가 없는 반쪽 root_cause도 같다. 조용히 고치지 않는다 — caveat `CAUSE_MOVED` 한 줄(코드가 더하는 caveat, `caveat_truncated`와 같은 자리 —
  보고서의 주의 줄에 남는다). 증거 id가 어디에도 없으면 사후 검증이 전처럼 거르고 수리 재시도.
- 테스트 먼저(RED → GREEN). 스윕 +3(997). `code graph`와 무관. **R3-5h 끝** — 다음은 측정 #19 브리프(`code graph` 재생성 **불필요**).

### R3-5i — 측정 #19 뒤 (10-21)

#### R3-5i ① (10-21) ✅ — 양쪽 키 필드를 가진 행이 한 입력에만 있으면 못 센다

- 측정 #19 P0-a: ②가 작업자 배지를 못 셌다 — 오른쪽 키 필드를 왼쪽 입력(기준표)의 전 행이 None으로 가져 `_keyed`가 양쪽에 같은 입력을 대고, 짝은
  `a_ev == b_ev`로 건너뛰고, `join_unmatched`는 양쪽 다 "있다"라 주도 안 남겼다. 진짜 오른쪽은 원천 토픽 메시지인데 열 이름이 변환 함수(camel → snake)를
  거쳐 AST 사실로는 못 잇는다 — 사람 결정(10-21): 이름 규칙 금지, "못 센다 주"까지. 작업자 배지 판정의 정확도는 메시지 홉의 공백값 증거에 달린다.
- `join_unmatched`가 까닭 문장을 돌려준다: 한쪽에 없으면 전처럼 `키 필드(<f> ↔ <g>)를 가진 입력 행이 <쪽>에 없다`, 양쪽을 가진 입력이 하나뿐이면 `… 행이 한
  입력 <라벨>에만 있다(<쪽> 값 전부 None)` — 그 입력에서 한쪽 키 값이 전부 None이면 그 사실도(열은 있으나 비어 있다). 걷기의 주는 `<클래스>: 병합식 <파일>:L<줄>의
  <까닭> — 겹침을 못 센다`.
- 테스트 먼저(RED → GREEN). 스윕 +2(999, 기존 사례 하나 앵커 옮김). `code graph`와 무관.

#### R3-5i ② (10-21) ✅ — basis는 그 케이스 증거 id의 enum, 설명이 붙은 인용은 id만으로

- 측정 #19 P0-c: "없는 id" 되묻기 11회 — basis 원소가 `<id>: <설명>` 문장. 판정 턴 한 번에 분 단위라 뜻이 하나인 실수에 그 값을 치를 이유가 없다.
- 리드: `conclude_schema(증거 id들)` — 판정 턴 스키마의 `basis`와 다리의 `evidence_ids` 항목을 그 케이스 증거 id의 `enum`으로(서버가 스키마를 강제하면
  리드는 그 밖을 못 쓴다; 증거가 없으면 열린 문자열 그대로, 닫힘은 그대로). `ask_json`이 스키마를 인자로 받는다 — 판정 턴만 케이스마다 다르다.
- 노드: `resolve_described_citations` — 인용의 앞 토큰이 그 케이스 증거 id(또는 태스크 id — `resolve_task_citations`가 이어 받는다)이고 뒤에 설명이 붙었으면
  id만으로, caveat `인용 정리: 설명이 붙은 증거 id를 id만으로 바꿨다 — …`. 모르는 id는 그대로 두어 verify가 전처럼 거른다. 두 conclude 경로 다 태스크 id
  정리 앞에 둔다(스키마가 강제되지 않는 경로의 안전망).
- 테스트 먼저(RED → GREEN). 스윕 +5(1004). `code graph`와 무관.

#### R3-5i ③ (10-21) ✅ — config로 푼 이름은 쓰임새로 흐를 때만 자원

- 측정 #19 P0-b: `키가 없다 — <임시 알람 키>` 틀린 사실 2건(`<ATR 케이스>`, #18의 `<배송 케이스>`) — 부모 메서드가 `config.get`으로 이름만 풀고
  `if <명령> and <변수>:` 참거짓에만 쓴다. `key` 표식(`_key_at`: 토큰이 따옴표로 있는 줄)은 쓰임새를 안 봤다.
- 인덱스(`attach_resources`): `key` 표식의 자원은 `_key_flows`가 True일 때만 — 토큰 상수를 푸는 식(`cfg.get("tok")`·`cfg["tok"]`와 그 위에 이어진
  호출·첨자·f-string)의 바로 위가 다른 호출의 인자·return·yield면 자원; 지역 변수 대입이면 그 변수(와 대입·f-string·반복 변수로 번진 이름)가 호출 인자·
  return·self 속성·첨자 대입에 닿을 때만(`_tainted_reaches_sink`, 고정점); 조건식(`if`·`while`·`assert`·비교)뿐이면 아니다. 토큰을 못 찾거나 모르는 모양
  (사전 안 등)이면 자원으로 둔다 — 자원을 잃는 쪽이 더 나쁘다. `alias`·`literal`·`attr` 표식은 그대로.
- 테스트 먼저(RED → GREEN). 스윕 +3(1007). **`code graph` 재생성 필요**(자원 표식이 바뀐다). **R3-5i 끝** — 다음은 측정 #20 브리프.

### R3-5j — 측정 #20 뒤 (10-22)

#### R3-5j ① (10-22) ✅ — None 꼬리는 필드별

- 측정 #20: 작업자 배지의 `못 센다` 주 5/5에 None 꼬리가 없었다 — 키 튜플 전체가 None일 때만 달았는데 오른쪽 3필드 중 하나만 전 행 None이다.
- `join_unmatched`: 한 입력뿐일 때 쪽마다 필드별로 전 행 None인 필드를 모아 `(오른쪽 <f> 값 전부 None)`, 양쪽이면 `(왼쪽 <a>·오른쪽 <b> 값 전부 None)`.
- 테스트 먼저(RED → GREEN). 스윕 +1(1008, R3-5i ① 사례 하나 앵커 옮김). `code graph`와 무관.

#### R3-5j ② (10-22) ✅ — 길이 재질문은 스키마를 두고, 원소 안의 증거 id들로 나눈다

- 측정 #20: "없는 id" 11 → 1, 인용 정리 1 — 둘 다 판정 역할의 `## 짧게` 턴. 깨진 답 뒤 재질문은 스키마를 떼는데(측정 #3의 형식 깨짐) 길이 상한에서 끊긴
  답은 형식 탓이 아니다 — 떼면 R3-5i ②의 증거 id enum이 그 턴에 안 걸린다.
- 리드(`ask_json`): 직전 답이 `length`로 끊겼으면 재질문에 스키마를 그대로 둔다. 형식이 깨진 답 뒤는 전처럼 뗀다(기존 시험 그대로).
- 노드(`resolve_described_citations`): 원소가 id가 아니면 그 안에 **완전일치**로 든 케이스 증거 id들(앞뒤가 식별자 글자가 아니고 뒤의 점은 문장 끝일 때만 —
  `t-1.e10`은 `t-1.e1`이 아니다)로 나눈다; 앞 토큰만의 경우도 여기 든다. 하나도 없으면 태스크 id 머리 규칙, 그다음은 그대로(verify가 거른다). caveat은 전처럼.
- 테스트 먼저(RED → GREEN). 스윕 +2(1010). `code graph`와 무관.

#### R3-5j ③ (10-22) ✅ — 속성으로 푼 토큰도, 받는 함수의 매개변수까지

- 측정 #20: `키가 없다 — <임시 알람 키>` 그대로 — (a) 그 줄의 토큰이 리터럴이 아니라 `self.<속성>`(값은 하위 클래스, `attr:` 표식)이라 R3-5i ③의 검사 밖,
  (b) 리터럴이어도 람다 안 `self.<리셋>(…, <인자>=<변수>)` 키워드 인자라 "호출 인자 = 쓰임새"로 끝났는데 받는 함수 안은 `if <명령> and <인자>:`뿐.
- 인덱스: `_key_flows` → `_flows(fn, resolve, line·token | attr)` — 출발이 토큰 상수거나 그 함수의 `self.<속성>` 읽기 전부(`attr:` 표식도 검사, 하나라도
  흐르면 자원). 호출 인자로 가면 `_arg_sink`: 받는 함수를 `resolve`(자기·조상의 메서드, 같은 모듈 함수)로 풀어 그 매개변수(위치·키워드)의 쓰임새를 같은
  규칙으로 이어 본다 — 깊이 3을 넘거나 못 풀면(저장소 클라이언트·외부 함수·`*args`) 자원. 람다 인자는 싱크가 아니고 그 몸체의 호출을 따로 본다. 속성 출발을
  둘러싼 호출은 config 조회의 기본값 자리(`cfg.get("redis_key", self.<속성>)` — 첫 인자가 문자열 리터럴인 `.get`)면 푸는 호출이고, 속성이 첫 인자면 쓰임새다.
- 따라오는 변화: 부모가 역할 속성을 비교(`self.<역할 속성> == "material"`)에만 쓰면 그 속성은 자원이 아니다 — 부모 조회의 `self.<역할 속성>: … 못 이음(자원
  값을 가진 다른 클래스: …)` 줄(측정 #18 ①)도 사라진다. R3-5g ①의 질의 MRO 논리는 그대로이고, 그 시험 픽스처(`KEYED`)는 `mode`가 호출 인자로 흐르게 바꿨다;
  보드 시험은 부모 조회에 `self.mode` 줄이 없음을 단정한다.
- 테스트 먼저(RED → GREEN). 스윕 +4(1014, R3-5i ③ 사례 둘 앵커 옮김). **`code graph` 재생성 필요**. **R3-5j 끝** — 다음은 측정 #21 브리프.

### R3-5k — 측정 #21 뒤 (10-22)

#### R3-5k ① (10-22) ✅ — 키워드 인자의 값도 받는 함수로

- 측정 #21: 남은 부모 조회 줄의 속성 읽기가 `<매개변수>=self.<역할 속성>` 키워드 인자 — 부모 노드가 `ast.keyword`라 `_flows`가 호출 인자로 못 보고 끝의
  "모르는 모양 → 자원"으로 갔다(`_arg_sink`를 안 불렀다). 버그.
- `_flows`의 부모 표: 키워드 인자의 값 → 그 호출(키워드 노드를 건너뛴다). 그러면 받는 함수의 매개변수를 전처럼 따라간다(`_param_of`는 키워드를 이미 안다).
  `cfg.get("redis_key", default=self.<속성>)` 같은 키워드 기본값도 푸는 호출로 본다.
- **판단: 남은 줄은 남긴다.** 사내 모양은 받는 함수가 값을 `self.<레지스트리>.get("<처리기>").process(…)`로 넘겨 끝내 못 푸는 호출에 닿는다 — "못 풀면
  쓰임새"가 저장소 클라이언트 읽기를 지키는 규칙이라 그대로 둔다. 지우는 길은 레지스트리 풀기(P1-7, 보류).
- 테스트 먼저(RED → GREEN). 스윕 +1(1015). **`code graph` 재생성 필요**.

#### R3-5k ② (10-22) ✅ — 턴의 스키마를 트레이스에

- 측정 #21: `## 짧게` 턴에 json_schema가 갔는지 트레이스로 못 봤다.
- 리드(`ask_json`): 턴 통계에 그 턴에 보낸 답 스키마의 이름 — `schema_label`: 판정이면 `concludereply(증거 id N)`, 액션 턴은 `frame_reply`·`integrate_reply`,
  안 보냈으면 `없음`(형식이 깨진 답 뒤의 재질문).
- 트레이스(`_make_tracer`): `턴:` 줄 끝에 ` · 스키마 <이름>` — `case trace`가 그 줄을 그대로 머리줄로 옮긴다(요약 쪽 변경 없음). 스키마 칸만 있어도 줄이 선다.
- 테스트 먼저(RED → GREEN). 스윕 +3(1018). `code graph`와 무관.

#### R3-5k ③ (10-22) ✅ — 결함 아님의 문자열 basis·caveats

- 측정 #21: `<50V>` 첫 판정이 `root_cause.component/evidence_ids: Field required`로 못 읽혀 재질문 한 번. 그 오류를 내는 답 모양을 여기서 재현해 보니 둘뿐이다
  — basis나 caveats가 목록이 아니라 **문자열 하나**. R3-5h ③의 사전 검증이 그때 손대지 않고 넘겨 root_cause가 남았다.
- `ConcludeReply`의 사전 검증: not_a_defect면 문자열 basis·caveats를 한 원소 목록으로(원소 안의 id는 노드가 나눈다 — R3-5j ②), root_cause는 그래도 옮긴다.
  다른 모양(사전 등)이면 root_cause만 비우고 그 칸의 오류만 남겨 재질문이 그것만 말하게 한다. 실제 모양은 다음 판 트레이스의 날것 응답으로 확인한다.
- 테스트 먼저(RED → GREEN). 스윕 +2(1020). `code graph`와 무관.

#### R3-5k ④ (10-22) ✅ — 판정 턴 A/B 측정 명령 `llm ab`

- 측정 #21 요청: 측정판에서 병합 주 줄을 판정 프롬프트에 넣을 때·뺄 때 작업자 배지의 유형·component가 바뀌는가. 이 컨테이너엔 사내 모델이 없고 측정판의 LLM은
  파일 대역이라 여기서 돌리면 내가 답한 것이 된다 — 사내 모델의 반응이 아니다. 측정판엔 그 주의 모양(`… 한 입력에만 있다`)도 없다. 그래서 사람이 사내에서
  돌릴 명령을 만든다(계획의 `tools/` 대신 `llm check` 옆 — 역할 LLM 배선·config·시계를 같이 쓴다).
- `python -m src llm ab <판정 트레이스 파일> [--runs N] [--dry-run]`: `case investigate --trace`가 쓴 판정 턴 파일의 `## 물어본 것`을 꺼내(`conclude_ab.prompt_of`),
  걷기의 병합 주 두 꼴(`… 병합이라 행은 남는다 …`, `… — 겹침을 못 센다`)만 뺀 판을 만들고(`strip_join_notes` — 다른 주·홉 표·증거는 그대로), 판정 역할 LLM에
  A(그대로)·B(뺀 것)를 회차마다 번갈아 묻는다(`run_ab`; 판정 스키마는 실제 판정 턴과 같게 그 프롬프트에 보인 증거 id enum). 줄마다 `유형 · component · 확신`,
  못 읽은 답은 사유만(답의 발췌는 자른다 — 증거 본문이 섞일 수 있다), 끝에 판마다 `유형 · component`별 수. 병합 주가 없으면 묻지 않고 1, `--dry-run`은 뺄 줄만.
- 시험이 병합 주를 **실제 걷기 출력**으로 만든다 — 걷기 문구가 바뀌면 시험이 깨져 도구가 같이 움직인다. 여기서 echo 모델 config로 같은 명령을 셸에서 돌려 봤다
  (뺀 줄 1, A1·B1 `못 읽음 — … JSON으로 읽을 수 없다`, 요약 두 줄).
- 테스트 먼저(RED → GREEN). 스윕 +6(1026). `code graph`와 무관. **R3-5k 끝** — 다음은 측정 #22 브리프(`code graph` 재생성 **필요** — ①).

#### R3-5l ① (10-25) ✅ — 병합 주는 판정 프롬프트에서만 뺀다

- 측정 #22 (c) A/B(`llm ab`, 작업자 배지 5건 ×3회): 병합 주가 든 판(A)은 외부 상류 1/15·logic_bug 6·high 6, 뺀 판(B)은 4/15·2·0. 사람 결정(10-25): 병합
  주는 판정 프롬프트에서 뺀다 — 걷기 표·트레이스에는 남긴다.
- 표식은 하나다(`domain/case.JOIN_NOTE_MARKS` — 두 꼴 `… 병합이라 행은 남는다 …`·`… — 겹침을 못 센다`): 걷기가 그 표식으로 주를 쓰고(`run_walk`의 두 `remember`),
  `Walk.lines(join_notes=False)`가 그 주만 빼며, `intake_lines`·`case_block`이 그 선택을 받고 **`conclude_fields`만** `join_notes=False`로 부른다. frame·integrate
  (조사 중 어디를 볼지는 그 주가 돕는다)·`case walk`·보고서·레포를 가른 주는 그대로. `llm ab`는 같은 표식을 가져다 쓴다 — 이제 판정 턴 트레이스엔 그 줄이 없어
  (보낸 프롬프트 그대로다) 이 도구는 그 전의 트레이스에만 뺄 줄이 있다(없으면 전처럼 "병합 주 줄이 없다"로 1).
- 테스트 먼저(RED → GREEN) — 걷기 쪽은 **실제 걷기 출력**의 두 꼴로 댄다(문구가 표식 밖으로 바뀌면 깨진다). 스윕 +6(1032). `code graph`와 무관.

#### R3-5l ② (10-25) ✅ — 단일값 나쁨은 거르는 쪽(`!=`) 짝만

- 측정 #22 (a) 넓은 걷기 372의 새 틀린 사실 1종: `<기준 키> | 나쁨 — N행 전부 <f>='<v>' · 이 필드 값이 든 비교식 … '(df["<f>"] == "<v>")'` — 그 줄은 `==`로
  **남기는** 거름이라 그 값의 행은 전부 산다. 10-14·10-15 규칙은 "필드와 값이 한 비교식의 양쪽"까지였고 연산자를 안 봤다(실측이 전부 `!= "-"`). 사람 결정
  (10-25): 단일값 나쁨은 그 값을 비교식(연산자·리터럴)에 대어 거르는 쪽일 때만.
- 어댑터는 사실만 넓힌다 — `comparison_pairs`가 `(줄, 필드, 값, 연산자, 본문)`, `FilterHit.op`(컴포넌트 클래스 자리에는 없다). 걷기가 가른다(`dropping`): `!=`
  짝만 나쁨·위로 이어 볼 단일값·첫 칸(`단일값이 든 비교식`)이고 줄 수(`외 N줄`)도 `!=` 줄만; `==` 짝뿐이면 정상이고 상태에 `· == 비교식 <자리> '<원문>' — 그
  값을 남기는 쪽이라 안 센다` 한 토막(리드가 그 줄을 "코드가 거른다"로 읽지 않게). 채움값 검사(R3-5h ②)도 같은 조회라 같은 규칙. **한계**: `if r["f"] == "v":
  continue`처럼 `==`로 버리는 줄은 못 본다 — 결정이 연산자·리터럴까지라 거기서 멈춘다(다음 판에 그 모양이 나오면 그때).
- 측정판: 기본 `StateFilter`가 `== "ea"`로 남기는 선택을 한다(틀린 사실 모양) — 고치기 전엔 원천 행이 가짜 나쁨(`case walk`로 재현), 고친 뒤 정답 홉 표의
  원천 행에 그 사실 토막. 보드의 `!=` 변형(c-2·join-outer·message-blank)은 그대로다.
- 테스트 먼저(RED → GREEN). 스윕 +6(1038). `code graph`와 무관(인덱스·자원 표식 변경 없음). **R3-5l 끝** — 다음은 측정 #23 브리프(`code graph` 재생성 **불필요**; ③ 점 이름은 꼬리 원문을 받아 정한다).

#### R3-5m ① (10-26) ✅ — 리터럴 묶음을 도는 반복 변수로 잇는다

- 측정 #23 (3)의 꼬리 두 모양. `<이벤트 처리 클래스>`의 `get(<처리기>.<속성>)`은 처리기가 메시지의 이벤트 코드로 런타임에 만들어져 AST 사실이 없다 — `못 이음 —
  변수`가 맞는 말이라 **그대로**(P1-8: 메시지 내용으로 푸는 길뿐). `<지그 클래스>`의 유력한 줄 `<인덱스>.get(key) for key in ("<a>", "<b>")`는 사실이 있다 — 그
  변수의 값은 그 묶음의 원소뿐이다.
- `loop_links`: `get(<변수>)`의 변수가 **같은 함수의 for·컴프리헨션에서 문자열 리터럴만 든 묶음(튜플·목록·집합)을 도는 대상**이면 그 리터럴 전부. 이름 단위·
  전부-또는-전무(`default_links`와 같은 규칙) — 같은 이름의 묶음이 여럿이면 합치고, 하나라도 못 읽으면(변수 묶음·리터럴 아닌 원소) 못 잇는다. 호출이 그 반복
  안에 있어야 하고(반복 뒤의 변수는 마지막 값), 문자열 하나는 묶음이 아니다(글자를 돈다). `filter_lines`는 기본값 리터럴과 합쳐 "변수 → 리터럴들"로 보고 등록된
  것만 잇는다(리터럴 get과 같은 규칙).
- 그 줄은 "유력"이라 확인된 것이 아니다 — 다른 모양이면 꼬리가 그대로 남고 #24에서 안다.
- 테스트 먼저(RED → GREEN) — 어댑터 통합 시험이 그 모양의 클래스(`Looper`)로 `변수 key` 못 이음을 먼저 재현했다. 스윕 +5(1043). `code graph`와 무관.

#### R3-5m ② (10-26) ✅ — `## 짧게` 재질문은 별도의 한 번

- 측정 #23: `<PGM>` 1회차 degraded의 경로 — 조각 뒤 끊김(`StreamChunkTimeout` 239초) → 같은 질문으로 한 번 더(예산 1회를 씀) → length → `## 짧게`를 물을
  차례가 없다. "length도 1회 다시"(R3-5d ②·R3-5f ④)는 짧게라는 **다른 처방**인데 전송·형식 재시도와 한 예산(`RETRIES = 1`)을 나눠 쓰고 있었다.
- `ask_json`: 짧게 재질문은 한 턴에 한 번, 전송·형식 재시도와 별도 — 직전 답이 length이고 아직 짧게를 안 물었으면 한 번 더 허용한다(상한 3회). 짧게 뒤에 또
  length면 끝(전처럼 2회), 전송 실패 둘·형식 실패 둘도 전처럼 2회, 시간 초과(조각 없음)는 전처럼 안 되묻는다. 길이 재질문의 스키마 유지(R3-5j ②)는 그대로.
- 테스트 먼저(RED → GREEN). 스윕 +3(1046). `code graph`와 무관.

#### R3-5m ③ (10-26) ✅ — `llm ab --notes-from <frame 턴 파일>`

- 측정 #23: `<AGV 2건>`이 6회 중 3회 inconclusive(지난 판 6/6) — AGV에도 병합 주가 있었고 R3-5l ①이 뺐다. 뺀 탓인지는 지금 도구로 못 가린다: 판정 턴
  트레이스에 주가 없어 `llm ab`가 뺄 줄이 없다(`병합 주 줄이 없다`로 1).
- 반대 방향: `python -m src llm ab <판정 턴 파일> --notes-from <같은 케이스의 frame 턴 파일>`. frame 턴의 병합 주 줄(`join_notes_of` — 빼는 것과 같은 표식)을
  판정 프롬프트의 걷기 표 아래(행·있는 주 뒤, 다음 절 앞 — R3-5l ①이 뺀 자리, `add_join_notes`)에 끼운 판이 A(주 있음), 그대로가 B. 끼운 것을 빼면 원래
  그대로다. `--dry-run`은 끼울 줄만. 판정 턴에 주가 이미 있으면 전처럼 뺀다(`--notes-from`은 무시). frame 턴에도 주가 없거나 판정 프롬프트에 걷기 표가 없으면
  묻지 않고 1(사유 한 줄).
- 돌릴지는 사람 — AGV 2건의 판정 턴 파일과 frame 턴 파일로 `--runs 3`이면 측정 #22 (c)와 같은 요약 두 줄이 나온다. 여기서 echo 모델 config로 같은 명령을 셸에서
  돌려 봤다(끼운 줄 1, A1·B1 `못 읽음`, 요약 두 줄).
- 테스트 먼저(RED → GREEN) — 끼울 자리는 **실제 걷기 출력**으로 댄다. 스윕 +4(1050). `code graph`와 무관. **R3-5m 끝** — 다음은 측정 #24 브리프(`code graph`
  재생성 **불필요**).

#### R3-5n ① (10-27) ✅ — 재시도 예산은 실패의 종류마다 따로

- 측정 #24 degraded 6 중 셋: `<PGM>` `ReadError` → `ConnectionError`(전송 오류 둘이 재시도 1회를 다 씀), `<작업자 2>` 길이 → 짧게 → 형식 실패(수리 재질문 차례
  없음), `<작업자 1>` 길이 → 짧게 → 조각 끊김(같은 질문 차례 없음). R3-5m ②가 짧게만 떼어 냈는데 원칙은 **종류마다 처방이 다르면 예산도 따로**다.
- `ask_json`: 직전 실패의 종류로 처방과 예산을 고른다 — 호출 자체의 실패(`connection`: 예외·오류 상태·조각 뒤 끊김)는 같은 질문으로 **두 번**(`CONNECTION_RETRIES`,
  둘째는 세 배 쉰다 — `_pause`), 빈 응답(`empty`) 한 번(같은 질문), 길이 상한(`length`) 한 번(사유 재질문 + 짧게, 스키마 유지), 깨진 형식(`format`) 한 번(사유
  재질문, 스키마 뗌). 짧게로 바꾼 뒤(`briefed`)엔 어떤 재시도든 짧게를 유지한다 — 길이 상한은 그 질문의 성질이지 그 호출의 사고가 아니다. 조각 없는 시간 초과는
  전처럼 안 되묻는다. 최대 6회지만 실제 사슬은 셋을 넘기 어렵다. 사유의 횟수는 실제로 보낸 수다.
- 옛 `RETRIES`(형식 1)는 그대로, `transport`·`shorten` 깃발은 `kind`·`briefed`로 — 전송 오류를 되묻는 옛 시험은 호출 수가 3이 된다(`CONNECTION_RETRIES + 1`).
- 테스트 먼저(RED → GREEN) — 세 사슬을 그대로 재현했다. 스윕 +4(1054; 옛 "짧게를 몇 번이고" 사례는 새 구조에 자리가 없어 "잘린 긴 답 뒤에 짧게로 안
  바꾼다"가 대신한다). `code graph`와 무관.

#### R3-5o ① (10-27) ✅ — 빈 긴 답은 길이 상한의 실패, `## 짧게` 두 번

- 측정 #25: 종류별 예산이 degraded를 7 → 1로 줄였고, 남은 하나(`<작업자 5>` length → 짧게 → length)와 length의 성질이 같이 드러났다 — length 턴 10 중 9가 빈
  응답에 출력 토큰이 정확히 16,000. 답이 아니라 **생각**이 상한을 먹는다. 프롬프트 모양으로 생각을 못 줄이지만 짧게 한 줄이 8/9를 살린다(짧게 뒤 생각이 짧아진다).
- `ask_json`: 빈 응답이라도 종료 사유가 length면 종류는 `length`(예산 `BRIEF_RETRIES = 2`), 처방은 전처럼 같은 질문에 짧게(고칠 글이 없으니 사유 재질문은 아니다 —
  R3-5c ②). 종료 사유가 stop인 빈 응답은 전처럼 `empty` 한 번. 잘린 JSON(length)도 짧게 두 번(사유 재질문 + 짧게). 최대 7회지만 실제 사슬은 셋을 넘기 어렵다.
- 상한 자체(생각 포함 16K)는 사내 config의 `max_tokens`와 게이트웨이의 추론 설정이 쥔다 — 코드 밖, 사람 결정. 차례로 돌려도 30%라 동시 실행 탓이 아니었다.
- 테스트 먼저(RED → GREEN). 스윕 +3(1057). `code graph`와 무관. **R3-5o 끝** — 다음은 측정 #26 브리프(`code graph` 재생성 **불필요**).

#### R3-5p ①-a (10-28) ✅ — `kafka.offsets`: 토픽의 보존 범위만

- 평가자 분석(#22~#25): `키가 없다` 멈춤에서 그 키를 쓰는 클래스·구독 토픽·토픽의 보존 범위 끝 오프셋을 홉으로 적어 달라(메시지 한 건 읽기와 같은 읽기).
  읽기는 `message_at`이 머리에 주는 범위(`begin`·`end`)만 따로 — 포트 `KafkaInspectorPort.offsets(topic)`: 파티션마다 `begin`·`end`와 남은 메시지 수, 본문
  없음. `assign`만, 그룹 미참여·커밋 없음, seek·fetch 없음(브로커 왕복 둘). 파티션은 AdminClient(`_partitions_of`)로 — 없으면 토픽이 없는 것(실패).
- 스텁은 파티션 0 하나(시드의 `begin`·메시지 수). 등재 `kafka.offsets topic`. 리드 목록에는 없다(`_WALK_READS` — 걷기가 낸다; 리드에겐 `kafka.tail`).
- 테스트 먼저(RED → GREEN) — 포트 표면·등재·스텁·실어댑터(가짜 aiokafka, `getmany` 0회)·리드 목록. 스윕 +6(1063). `code graph`와 무관.

#### R3-5p ①-b·c (10-28) ✅ — 없는 키의 쓰는 클래스·구독 토픽·보존 범위 홉

- 평가자 분석: `키가 없다` 멈춤(2-a 전부)에서 그 키를 쓰는 클래스와 그 입력을 적지 않아 판정이 "쓰는 주체를 확정 못 함" → inconclusive. 쓴 쪽은 값 말고도
  있다 — 인덱스의 쓰기 자원(`code.uses`의 쓰기 줄, `walk.writers_in`)과 그 키를 선언한 서비스(`declared_by`)가 일치하는 클래스. 그 클래스의 메시지 입력은
  config의 구독 선언으로만 안다 — `flow.consuming_map`(서비스 → `consumes`·config 출처 토픽들, 발행 선언과 같은 길)이 엔진 deps `subscriptions`로 걷기에.
- 걷기: 입력 키가 `없음`이고 값에 쓴 쪽이 없으면 `code.uses <선언 이름>` 한 번 → 한 클래스로 좁혀지면 그 홉의 쓴 쪽·서비스·후보를 채우고(근거 줄에 그 증거),
  그 서비스가 구독하는 토픽마다(최대 3) `kafka.offsets` 한 번 → `topic` 종류 홉 `<토픽> | <클래스> [<서비스>] 구독 | 보존 범위 b~e · 남은 메시지 N`. 비었으면
  그 홉이 나쁨이고 가장 깊어 멈춤 `구독 토픽이 비었다 — <토픽>(<클래스> [<서비스>])`, 칸 `kafka.tail` → `code.resources`. 남아 있으면 멈춤은 `키가 없다 —
  <키>(쓰는 <클래스> [<서비스>] · 구독 <토픽> 범위 b~e, 남은 메시지 N …)`, 칸 `code.resources` → 토픽마다 `kafka.tail` — 쓴 쪽이 입력을 받고도 키를 안 만든
  것인지, 입력이 없는 것인지를 리드가 사실로 가른다. 쓰는 클래스로 더 걷지는 않는다(그 클래스의 다른 입력은 그 칸의 몫). 쓰는 클래스가 없거나 둘 이상(주 한 줄)
  이면 전처럼. 범위 읽기 실패는 나쁨이 아니고 멈춤 문구에 `범위 읽기 실패`.
- 측정판: 원천 키를 쓰는 `SourceSink`(processor, 원천 토픽 구독)를 두고 `source-missing`의 정답 홉 표(구독 토픽 둘 — 공유 config의 소비 선언이 둘이다 —
  둘 다 남은 메시지 5, 멈춤 `키가 없다 … 구독 …`)와 새 변형 `source-missing-quiet`(원천 토픽 비었음 → `구독 토픽이 비었다 — mx.alarm.raw(SourceSink
  [processor])`).
- 테스트 먼저(RED → GREEN) — 걷기 단위(쓰는 클래스 있음·토픽 비었음·범위 읽기 실패·구독 선언 없음·클래스 없음·여럿)와 측정판 정답 표. 스윕
  +9(1072). **`code graph`와 무관** — 인덱스·그래프 파일은 그대로고(`consumes` 엣지는 이미 있다), 걷기가 그것을 새로 읽을 뿐.

#### R3-5p ② (10-28) ✅ — 판정 블록의 걷기 표 뜻 줄 + `llm ab --legend`

- 평가자 분석: `<작업자 배지>` 60 판정 전부 판정 글에 원천 `<f>='-'`를 적었다(external 12·처리기 쪽 22·inconclusive 20·degraded 6) — 메시지 홉 `정상`, 멈춤
  `입력이 다 정상 — <처리기>`라 `'-'`를 쓴 마지막 쓴 쪽이 원인으로 읽힌다. 걷기 표의 `정상`은 "읽었고 거르는 비교식을 못 찾았다"는 뜻이고 값이 뜻대로인지는 보지
  않는다 — 그 뜻이 표에 없었다.
- `WALK_LEGEND`(`domain/case`) 한 줄: `뜻: '정상'은 읽었고 거르는 비교식을 못 찾았다는 뜻이다 — 값이 뜻대로인지(예: '-'가 정상 값인지)는 보지 않았다. '입력이 다
  정상'도 같다(…)`. `Walk.lines(legend=True)`가 머리 아래에 두고, 판정 블록(`conclude_fields` → `case_block(legend=True)`)만 켠다 — frame·integrate·`case walk`·
  보고서는 그대로(표가 없는 던진 걷기엔 없다).
- `llm ab --legend <판정 턴 파일>`: A는 지금 그대로, B는 같은 표식을 걷기 머리 아래에 끼운 판(`add_legend` — 이미 있으면 그대로, 표가 없으면 1). 요약 라벨
  `A(지금)`·`B(뜻 줄)`. 병합 주는 건드리지 않는다. `<작업자 배지>` 5건 × 3은 사내에서 — 여기서 echo 모델 config로 명령을 돌려 봤다.
- 테스트 먼저(RED → GREEN). 스윕 +6(1078). `code graph`와 무관. **R3-5p 끝** — 다음은 측정 #27 브리프(`code graph` 재생성 **불필요**).

#### R3-5q ① (10-29) ✅ — 공유 레포의 쓰기 줄도 받고, 쓴 쪽 0인 키는 주 줄로

- 측정 #27: 없는 키의 쓴 쪽·구독 토픽 홉이 사내 408행에서 0건 — (a) `_USES_ENTRY`가 `[<레포> · <서비스>]`만 받아 공유 레포의 쓰기 줄 `[<레포>]`(서비스 없음)이
  빠졌다(`<AGV 키>`), (b) 쓰기 0줄(`<PGM 키>`·`<충전 키>` — 인덱스가 쓴 쪽을 모른다)은 조용히 넘겼다. 둘 다 내 쪽의 빈자리다.
- 정규식의 서비스를 선택으로 — 서비스가 없으면 `split`이 인덱스의 클래스 → 서비스로 가른다(전과 같은 길; 구독 토픽도 그 서비스의 것). `code.uses` 실패·쓰기
  0줄·클래스 아닌 쓰기 줄은 주 한 줄로 사실을 남긴다(`<키>: 인덱스에 쓰는 함수가 없다(code.uses 쓰기 줄 0) — 쓴 쪽을 채우지 않는다` 등).
- 테스트 먼저(RED → GREEN) — 공유 레포 줄로 쓴 쪽이 채워지고 토픽 홉이 붙는 것, 세 가지 주 줄. 스윕 +4(1082). `code graph`와 무관.

#### R3-5q ② (10-29) ✅ — 뜻 줄을 판정 블록에서 뺀다

- 측정 #27: `llm ab --legend`(#25 트레이스 5건 × 3) A(지금) 맞음 7·틀림 2, B(뜻 줄) 맞음 5·틀림 4 — 뜻 줄이 처리기 쪽 틀림을 늘린다. 본 판 작업자 배지도
  맞음 3/15·틀림 7/15(#25: 4·3). 사람 결정: 판정 프롬프트에서 뺀다.
- `conclude_fields`가 `legend`를 안 켠다 — 뜻 줄은 어느 프롬프트에도 안 들어간다. `Walk.lines(legend=)`·`case_block(legend=)`·`llm ab --legend`는 측정용으로
  그대로(`WALK_LEGEND`의 주석이 그 사실을 적는다).
- 테스트 먼저(RED → GREEN). 스윕 +0(1082; 뜻 줄 사례 둘은 뒤집어 "켠다"를 잡는다). `code graph`와 무관. **R3-5q 끝** — 다음은 측정 #28 브리프(`code
  graph` 재생성 **불필요**).

#### R3-5r ① (10-30) ✅ — 동사 없는 자원 줄의 방향은 조립한 값이 처음 닿는 호출의 동사로

- 평가자 회신(10-30) 4(a): 사내 `<PGM 처리기>`의 `<대상 자원 속성>`이 키를 조립하고 명령 DTO로 `<싱크>`가 쓴다 — 그 줄엔 동사가 없어 인덱스가 읽기로 적었고
  (이 모양 43개) `code.uses` 쓰기 0줄이라 걷기가 쓴 쪽을 몰랐다(측정 #27 (b)). 요청: `<대상 자원 속성> → 명령 DTO` 흐름으로 쓴 쪽을 잡아라.
- `attach_resources`: 같은 줄에 동사가 없으면 `_handoff` — 그 줄의 출발(토큰 상수·리터럴·별칭·`self.<속성>`)에서 위로 올라가 조립한 값이 **처음 닿는 호출**의
  동사(`READ_VERBS`/`WRITE_VERBS`)로 방향을 정한다. 사전·목록·생성자·f-string은 값을 품고 가는 그릇이고, config 조회(`cfg.get`)는 푸는 자리라 지나며, 동사
  없는 호출은 받는 함수(가족 메서드·같은 모듈 함수)를 풀 수 있으면 그 매개변수로 이어 본다(깊이 3, `_flows`와 같은 해석기). 꼭대기가 대입이면 그 변수들이
  닿는 뒤 호출을 코드 순서로(`log.info(key)`는 지나간다), `return`이면 부르는 쪽(exact `calls`, 프로퍼티는 `.<이름>` 접근)에서 이어 본다 — 부르는 쪽이 읽기·쓰기로
  갈리면 읽기(보수). 그래도 없으면 전처럼 읽기. R3-1b ②의 "뒷줄 토픽 전송"(줄 위치 규칙, 측정 #8에서 틀림)과 다르다 — AST 흐름이고 같은 줄 동사가 있으면
  그대로다. 값에 적힌 쓴 쪽(R3-1c ①-a)은 그대로 먼저다.
- `Resource.handoff`(여섯째 칸): 넘긴 동사. 옛 번들의 여섯째 칸(R3-1b ②의 "command")은 동사표에 없으면 버린다. `code.resources`·`code.uses` 줄 끝에
  ` · 키를 조립해 send()로 넘긴다` — 걷기의 `writers_in`·`inputs_in`은 줄 머리만 보므로 그대로 읽는다.
- 측정판: `SummaryRefresh`(요약 키 이름을 명령에 실어 보내는 배치)가 `alarm:stats:{line}`의 쓰기가 됐다(`RateRefresh`의 증감 키도) — 보드 CLI(`code graph` →
  `code resources`·`code uses`)로 직접 확인. 흐름 그래프(grep + 같은 줄 동사)는 안 바뀐다 — 그래프의 `writes` 엣지는 여전히 줄 동사 기준(적어 둔다).
- 테스트 먼저(RED → GREEN) — 사전에 담아 send·`return` → 부르는 쪽·생성자 안 save·부르는 쪽의 get·갈린 부르는 쪽·부르는 쪽 없음·config 조회 뒤 set·동사 없는
  호출 지나기·가족 메서드 둘 건너 send·깊이 초과·모듈 함수·같은 줄 동사, 줄 표시, 옛 번들 여섯째 칸. 스윕 +11(1093). **`code graph` 재생성 필요**
  (인덱스의 방향이 바뀐다 — 측정 #29 브리프에 명시).

#### R3-5r ② (10-30) ✅ — 없는 키를 쓰는 클래스의 구독 토픽은 그 클래스 코드에서

- 평가자 회신(10-30) 4(b): 공유 config의 구독 선언이 법인마다 10~11개라 R3-5p의 "서비스 선언 합집합, 상한 3"은 고르는 규칙이 된다(P-5 기각). 입력 토픽은
  클래스의 `config.get("infra", "kafka", "consumer", "topic", "<이름>")`에서 — 인덱스가 그 줄을 이미 토픽 읽기로 센다(키 토큰 + 조상 키 `consumer`·`topic`).
- `keyed_writer`가 쓰는 클래스를 고른 뒤 그 클래스의 `code.resources`를 한 번 읽어(걷기 읽기 +1) 읽기 `[topic]`마다 `kafka.offsets` 홉(상한 없음, 근거에 그 증거).
  서비스 선언(`subscriptions`)은 읽지 않고 수만: 토픽이 0이면 주 `<클래스>: 클래스 코드에 구독 토픽이 없다(code.resources 읽기에 [topic] 0줄) — 서비스 선언 N개는 그
  클래스의 것인지 몰라 안 읽는다`, 멈춤 꼬리 `구독 토픽 없음(클래스 코드 · 서비스 선언 N개)`; `code.resources`가 실패하면 주와 꼬리 `구독 토픽 모름(code.resources
  실패)`. 첫 칸은 그 클래스 본문(`code.read` — 걷기가 읽은 자원 글의 머리 줄; 못 읽었으면 전처럼 `code.resources`), 빈 토픽 멈춤은 `kafka.tail` 뒤 본문.
  라우터 표(`<메시지 유형 열거>.X`·`<파이프라인 열거>.X`)로 토픽을 정하는 클래스는 줄 모양을 받아야 한다(측정 #29 질문).
- 측정판: `source-missing`은 `SourceSink`의 코드가 구독하는 `mx.alarm.raw` 한 줄(선언 셋 중 감사 토픽·main은 안 읽는다), 첫 칸 `code.read` 본문; 새 변형
  `palette-missing`(공용 팔레트 키 없음 — 쓰는 `PaletteLoader`의 코드엔 토픽이 없다)은 `구독 토픽 없음(클래스 코드 · 서비스 선언 3개)`. 보드 CLI로 확인.
- 테스트 먼저(RED → GREEN) — 걷기 단위(클래스 토픽 하나·빈 토픽·범위 실패·토픽 0·자원 실패·쓴 쪽 없음은 자원도 안 읽음), 보드 둘, 시드. 스윕 +7(1100).
  `code graph`와 무관(①이 이미 재생성을 요구한다).

#### R3-5r ③ (10-30) ✅ — 멈춘 홉의 입력을 못 읽었으면 확신 상한 low

- 평가자 회신(10-30) 2(P-7): 못 읽은 상류 위의 하류를 탓하지 못하게 — low 상한 + 고정 caveat. inconclusive 강제는 반대(코드가 판정을 정하게 된다). `<작업자 4>`
  (측정 PC가 그 법인 Kafka에 안 닿아 메시지 홉이 읽기 실패인데 data_loss/처리기 high ×3)가 대상.
- `cap_unread_upstream`(`nodes`): 멈춘 홉(`Walk.stop_at`)에서 나간 입력 홉 중 `읽기 실패`인 것(키·컬렉션·토픽 범위·포인터 메시지)이 있거나 멈춤이 `입력을 다
  못 봤다`(scan 일부 포함)면 확신 low + `상류를 못 읽었다 — <키들>; 읽은 범위 안의 판정이라 확신 상한 low`. 다른 가지의 읽기 실패는 아니다(멈춘 홉의 입력이
  아니다). 유형은 그대로. verify가 `cap_unseen_stop`(medium) 뒤에 건다 — 그보다 세다. 되묻지 않는다(한도다).
- 테스트 먼저(RED → GREEN) — 메시지 홉 읽기 실패·scan 일부·medium → low·이미 low면 그대로·다른 가지·걷기 없음·verify 노드. 스윕 +5(1105). `code graph`와
  무관.

#### R3-5r ④ (10-30) ✅ — 실패한 태스크는 인용 불가

- 평가자 회신(10-30) 5(P-3) "한다(작음)". 측정 #27: 없는 id 2가 실패한 태스크 id였다 — 되묻기 한 번에 ~108초.
- 태스크 줄(`tasks_block`, 접은 줄도)의 `[error]`이고 증거 없는 태스크 끝에 ` · 증거 없음(인용 불가)`, 열린 질문의 `- 실패한 읽기 N건(증거 없음 — 인용 불가): …`.
  그래도 인용하면 verify의 문제가 `실패한 태스크 id t-N 인용 (<자리>) — 증거가 없다(인용 불가)` — "없는 id"가 아니라 왜(`verify_verdict(failed=)`, verify 노드가
  State의 실패한 태스크 id를 넘긴다). 성공한 태스크 id는 전처럼 `resolve_task_citations`가 고친다.
- 테스트 먼저(RED → GREEN). 스윕 +4(1109). `code graph`와 무관. **R3-5r 끝** — 다음은 측정 #29 브리프(`code graph` 재생성 **필요** — ①).

#### R3-5s ① (10-31) ✅ — 결과를 품는 것은 생성자뿐, 상태 설정은 같은 수신자의 뒤 호출

- 측정 #29 중단(평가자): R3-5r ①의 "못 푸는 호출의 결과가 값을 품고 간다"가 틀렸다 — `<배치 읽기 헬퍼>`가 키를 `<수집기>.set_key_options(…)`로만 넘기고
  `collect()`는 키를 안 받는데, 읽은 데이터를 키처럼 따라가 출력의 `save`에 닿아 219줄 중 142줄이 틀린 쓰기(걷기는 홉 2에서 `입력이 다 정상`). 맞은 77줄은
  `<명령 DTO>(…=키) → save` — 생성자가 키를 필드로 담는 경우.
- `_carries`: 대입의 오른쪽이 값을 품는 것은 이름·그릇(사전·목록·튜플·f-string·연산)·값 자신의 메서드(`key.format(…)`)·리터럴의 문자열 메서드(`"{}".format(key)`)·
  **생성자 호출**(`is_ctor` — 인덱스의 클래스로 풀리거나, 아무것으로도 안 풀리는 대문자 이름)뿐이다. 소문자 함수·레지스트리 컴포넌트·`self.` 메서드의 결과는 품지
  않는다. 올라가기에서도 같다 — 생성자·문자열 메서드가 아니면 그 출발은 거기까지.
- `_next_receiver_verb`: 동사 없는 호출에 값을 넘겼으면 **같은 수신자의 뒤 호출**(AST 순서, `x.set(k).go()` 유창 사슬 포함) 중 처음 동사 있는 것이 방향 —
  `x.set_key(k)` 뒤 `x.save()` 쓰기, `x.set_key_options(k)` 뒤 `x.collect()` 읽기(`READ_VERBS`에 `collect`). 다른 수신자·동사 없는 호출은 지난다. 뒤 호출이
  없으면 모르는 것(읽기).
- 사내 모양 테스트: `set_key_options → collect`, `set_key → save`, 줄 나눈 유창 사슬, 레지스트리 결과, 함수 결과(변수·직접 인자), DTO(인덱스 클래스·인덱스 밖
  대문자), 리터럴 `format`, 뒤 호출 없음, 뒤 호출 중 다른 수신자 건너뜀.
- 아는 한계(측정 #29 재측정에서 본다): 키 토큰과 `set_key_options(…)`가 **같은 줄**이면 줄 동사 규칙(`_parts`가 `set_key_options`를 `set`으로 쪼갠다)이 먼저 쓰기로
  정한다 — R3-1의 조각 단위 매칭이고 이 단계에선 손대지 않았다. 사내 모양이 같은 줄인지 재측정의 쓰기 줄 대조가 말한다.
- 테스트 먼저(RED → GREEN). 스윕 +8(1121). **`code graph` 재생성 필요**(인덱스 방향).

#### R3-5s ② (10-31) ✅ — 재정의·프로퍼티 읽기의 부르는 쪽 잇기

- 측정 #29 중단: `<대상 자원 속성>` 43개가 여전히 쓰기 0 — 부모 `<기록 처리기>._return_wrapper`의 `self.<속성>`이 부모 property에 묶여 하위 override로 안
  이어졌다(속성 접근은 `calls` 엣지가 없고, 베이스의 `self.<이름>()` 엣지는 베이스 메서드로만 간다).
- `callers(fn)`: exact `calls`에 더해 (a) 재정의 사슬(`override_of`)의 베이스 메서드를 부르는 쪽 — 실행 때 재정의로 오는 호출, (b) 프로퍼티면 가족(자기·조상·후손,
  그 이름을 다시 정의한 후손은 뺌) 메서드 중 `self.<이름>`을 읽는 것. 그다음은 전과 같다 — 부모 `_wrap`이 `Command(key=self.target)`으로 `save`하면 하위 property의
  키가 쓰기(생성자가 품는다), `redis.get(self.target)`이면 읽기, 부모는 쓰고 손자는 읽으면 갈려서 읽기(보수). 레지스트리 `process()` 결과 언패킹을 거치는 모양은
  P1-7까지 안 잡힌다 — 재측정에서 43개가 어느 모양인지 본다.
- 테스트 먼저(RED → GREEN). 스윕 +3(1124). 다음은 측정 #29 재측정 브리프(`code graph` 재생성 **필요**).

#### R3-5s ③ (10-31) ✅ — config 경로 호출은 그 클래스의 토픽 읽기, 사이트 행이 key_path를 이름으로

- 측정 #29 재측정 1부: 인덱스에 `[topic]` 자원이 0 — `<AGV 클래스>`가 `kafka_collector.subscribe_topic(topic=self.config.get("infra", "kafka", "consumer", "topic",
  "<x>_topic"), …)`로 구독하는데 사내 토픽은 사이트 층에만 선언돼(측정 #16) GBM 이름으로 선 인덱스가 그 줄을 자원으로 못 봤다 → 토픽 행 0, 2-a 근거 소실.
- 인덱스(`_topic_path_calls`): 문자열 인자만이고 끝 셋이 `consumer`, `topic`, <이름>인 config 호출은 그 함수의 토픽 읽기(경로가 consumer라 구독 — 동사표
  불필요). GBM 이름에 그 key_path가 있으면 값(전처럼), 없으면 key_path가 이름이고 `via=path`. 발행 경로(producer)·이름 없는 경로는 아니다.
- `flow.site_renames`: 토픽 행마다 `("topic", key_path) → 값`도 준다 — `_code_if_ready`·`code resources`의 `Index.renamed`가 그 경로 이름을 사이트 토픽으로
  바꾼다. 같은 key_path를 서비스마다 다른 값으로 쓰면(측정 #16 P0-b) 안 바꾼다.
- 걷기: 값으로 안 풀린 경로(`…consumer.topic.<이름>`)는 토픽이 아니다 — 주 `<클래스>: 토픽 경로 <경로>의 값을 모른다(사이트 선언에서 못 바꿨다) — 읽지 않는다`,
  전부 그러면 꼬리 `구독 토픽 경로 N개의 값을 모름(사이트 선언에 없음)`.
- 측정판: `SourceSink`가 사이트 전용 감사 토픽(topic3)을 `self.cfg.get("infra", "kafka", "consumer", "topic", "topic3")`로 구독 — `source-missing`에 `gumi.alarm.audit`
  행(브로커에 없어 읽기 실패)이 `mx.alarm.raw` 앞에 선다. 보드 CLI로 확인. 흐름 그래프의 config에만 있는 이름이 3 → 2(그 줄이 코드 줄이다).
- 테스트 먼저(RED → GREEN). 스윕 +6(1130). **`code graph` 재생성 필요**(인덱스 자원이 는다).

#### R3-5s ④ (10-31) ✅ — 줄 동사 검사에서 상태 설정 호출 이름을 뺀다

- 측정 #29 재측정 1부 (c): `<수집기>.set_key(self.config.get("redis_key", "<토큰>"))` 한 줄이 줄 동사로 쓰기 — 틀린 쓰기 7줄(`_parts`가 `set_key`를 `set` 조각으로
  쪼갠다, R3-1의 조각 단위 매칭). 요청: `set_key`도 같은 수신자의 다음 동사(`collect`)로.
- `_without_setters`: 줄 동사 검사 전에 `set_<x>(`·`set<X>(` 호출 이름을 지운다 — 그 줄은 동사 없는 줄이 되어 넘김 규칙으로 가고, `_verb_direction`은 통째 이름
  (`set_key`)을 동사로 안 보므로 같은 수신자의 뒤 호출(`collect` 읽기·`save` 쓰기)이 정한다. 맨 `set(`(redis)은 그대로 쓰기. 흐름 그래프의 `direction`(grep 엣지)은
  안 바꿨다 — 인덱스 자원 줄에서만.
- 테스트 먼저(RED → GREEN) — `set_key(config.get(…))` 뒤 `collect`/`save`, camelCase `setKey`, 맨 `set(`. 스윕 +2(1132). 다음은 측정 #29 재측정 2부 브리프(`code graph` 재생성 **필요**).

#### R3-5s ⑤ (10-31) ✅ — config 조회의 인자가 품는 값은 그 결과가 품는다

- 측정 #29 재측정 2부: `<AGV 2건>`이 `키가 없다 — <AGV 키>`(쓴 쪽 칸 없음, `code.uses` 쓰기 줄 0)로 돌아갔다 — 쓴 쪽은 그동안 `set_key` 버그로만 잡혔고(그 줄은
  `_load_initial_state`의 읽기, ④로 맞게 읽기가 됐다), 진짜 쓰기는 `<AGV 클래스> L<줄>` `config_key="<토큰>"`(키워드 인자) → `<AGV 부모>._flush_runtime_snapshot`
  `target_key = self.config.get("redis_key", config_key)` → `_save_snapshot(storage, target_key, …)` → `save`. 받는 함수의 매개변수까지는 들어갔지만(R3-5j ③·
  R3-5s ①) config 조회 호출의 **결과**가 그 인자의 값을 품는다고 보지 않아 `target_key`에서 끊겼다.
- `_carries`: config 조회 호출(`_is_config_call`)은 인자가 값을 품으면 그 결과가 품는다 — 토큰을 키로 푸는 자리라서. 그래서 `_handoff_into`로 들어간 매개변수가
  `target_key`로 번지고 다음 호출(`_save_snapshot` → `save`)이 방향을 정한다. 인자와 무관한 config 조회 결과(`self.config.get("redis_key", "heartbeat")`)는 전처럼
  품지 않는다 — 매개변수가 로그로만 가면 모르는 것(읽기).
- 사내 모양 테스트: 키워드 리터럴 → 부모 매개변수 → 조회 → 헬퍼 → `save`, 같은 길의 읽기(`get`), 위치 인자·조회 결과를 바로 넘김, 같은 모듈 함수로 넘김, 무관한
  조회. 아는 한계: 하위 클래스의 호출 줄에 동사 조각이 있으면(`_read_snapshot(…)`의 `read`) 줄 동사 규칙이 먼저다(R3-1 조각 매칭) — 방향은 맞고 넘긴 동사 칸만 빈다.
- 측정판: `SourceSink`의 쓰기를 이 모양으로(`SnapshotSink` 부모 — 사내 `<AGV 부모>`): 쓰기 줄은 하위 클래스의 호출 줄 하나(`config_key="alarm_source"`)뿐이라
  인덱스가 못 이으면 `source-missing`의 쓴 쪽이 사라진다 — 보드 CLI로 확인(`code uses alarm:source:{line}` 쓰기 1 ` · 키를 조립해 set()로 넘긴다`, `source-missing`
  걷기 전과 같음).
- 테스트 먼저(RED → GREEN). 스윕 +2(1134). **`code graph` 재생성 필요**(인덱스 방향).

#### R3-5s ⑥ (10-31) ✅ — 걸은 클래스의 구독 토픽 입력은 보존 범위·발행 선언으로

- 측정 #29 재측정 2부: ③으로 인덱스에 `[topic]`이 돌아오자 걸은 클래스의 구독 토픽이 `kafka.tail` **입력 홉**이 되어 `<ATR 토픽> | — | 빈 배열 (messages)` →
  `쓴 쪽을 모른다 — <ATR 토픽>(값에 인덱스의 클래스 이름이 없다)`로 멈췄다(`<ATR 케이스> --key <ATR 키>`) — 토픽의 값에 쓴 클래스 이름이 있을 리 없다. 요청:
  토픽 홉은 값의 쓴 쪽이 아니라 보존 범위(남은 메시지)와 발행 선언으로, 비었으면 `구독 토픽이 비었다`(#28과 같은 멈춤).
- 걷기: `[topic]` 입력은 `kafka.offsets`(보존 범위 — 없는 키의 쓰는 클래스의 토픽 홉과 같은 `_topic_status`)로 읽고 쓴 쪽 칸은 발행 선언(`topic_producers`,
  없으면 `발행 선언 없음(우리 밖)` — 판정의 component 후보도 그 서비스들, 포인터 메시지 홉과 같다). 메시지가 남아 있으면 읽은 입력(정상), 비었으면 그 홉이 나쁨 —
  멈춤 `구독 토픽이 비었다 — <토픽>(<클래스> [<서비스>] · 발행 선언 …)`, 칸은 `kafka.tail` → 그 클래스 본문. 범위 읽기 실패와 값으로 안 풀린 config 경로(주 한 줄)는
  못 본 입력(`입력을 다 못 봤다` → `cap_unread_upstream` low). 발행 쪽으로 걷지는 않는다. `kafka.tail` 입력 읽기는 이제 없다(토픽 입력은 전부 이 길).
- 측정판 `source-quiet`: 원천 키 값이 `SourceSink`를 대고 rows가 빈 배열이라 걷기가 그 클래스로 내려가고, 그 코드의 구독 토픽 둘 — 사이트 전용 감사 토픽은 브로커에
  없어 `읽기 실패`(발행 선언 셋), 원천 토픽은 비어 `구독 토픽이 비었다 — mx.alarm.raw(SourceSink [processor] · 발행 선언 없음(우리 밖))`. 보드 CLI로 확인.
- 테스트 먼저(RED → GREEN). 스윕 +6(1140). **R3-5s 끝** — 다음은 측정 #29 재측정 3부 브리프(`code graph` 재생성 **필요** — ⑤가 인덱스 방향을 바꾼다).

#### R3-5t ① (10-31) ✅ — 구독 토픽 행의 쓴 쪽 칸은 발행 선언 하나로

- 측정 #29 재측정 3부(10.5/24, 2-b 통과): 없는 키의 쓰는 클래스가 구독하는 토픽 행(R3-5p ①-c)이 쓴 쪽 칸에 `<AGV 클래스> [<서비스>] 구독`을 적어 `<ACS 토픽>`에
  발행하는 레포가 없다는 사실이 안 보였다 — AGV 6회 중 4회가 external로 못 갔고 2회는 "생산자가 내부인지 외부인지 단정 못 한다". 걸은 클래스의 토픽 행(⑥)은
  `발행 선언 없음(우리 밖)`을 적어 `<ATR>`이 external로 갔다.
- `published(토픽)` 한 함수가 발행 선언 글자와 후보 서비스들을 만들고 세 홉(포인터 메시지·걸은 클래스의 구독 토픽·없는 키의 쓰는 클래스의 구독 토픽)의 쓴 쪽 칸이
  전부 그것이다. 구독 클래스는 상태 칸 꼬리 `· 구독 <클래스> [<서비스>]`(두 토픽 경로 다 — 행 모양 하나). 없는 키 경로의 빈 토픽 멈춤도 `구독 토픽이 비었다 —
  <토픽>(<클래스> [<서비스>] · 발행 선언 …)`로 ⑥과 같은 모양.
- 측정판: `source-missing`의 감사 토픽 행이 `발행 선언: batch, processor, sink`(사이트 행의 선언), 원천 토픽 행이 `발행 선언 없음(우리 밖)` — 보드 CLI로 확인.
- 테스트 먼저(RED → GREEN). 스윕 +4(1144). `code graph` 재생성 불필요(걷기만).

#### R3-5t ③ (10-31) ✅ — 비지 않은 토픽 행은 `정상 — …`

- 측정 #29 재측정 3부 (3): 메시지가 남은 토픽 행이 `<토픽> | 발행 선언 없음(우리 밖) | 보존 범위 a~b · 남은 메시지 n`이라 빈 행(`비었다 — …`)과 판정 낱말로 안
  갈렸다. `_topic_status`가 `정상 — 보존 범위 a~b · 남은 메시지 n`을 준다 — 키 행의 `정상 — …`과 같은 꼴. 멈춤 꼬리의 토막(`구독 <토픽> 범위 a~b, 남은 메시지 n`)은
  그대로.
- 테스트 먼저(RED → GREEN). 스윕 +1(1145). `code graph` 재생성 불필요.

#### R3-5t ② (10-31) ✅ — 인덱스가 모르는 쓴 쪽은 같은 GBM 다른 사이트의 같은 키 값에서

- 측정 #29 재측정 3부 (2): `<없는 키>: 인덱스에 쓰는 함수가 없다(code.uses 쓰기 줄 0)` 주를 리드가 "쓰는 코드 누락"으로 읽어 `<PGM 케이스>` 1회차 logic_bug
  `<배치 서비스>`(2-a의 유일한 틀림). 근본은 P1-7(레지스트리 경유 `<대상 자원 속성>` 쓰기)이라 그때까지의 메움 — 평가자 요청: 같은 키 꼬리가 다른 사이트에 있으면
  그 값의 `metadata.event_type`으로 쓴 쪽을 채우고 칸에 출처를 적어 걷기가 그 클래스의 입력까지 이어지게, 다른 사이트에도 없으면 지금 주 줄.
- 걷기(`keyed_writer` → `peer_writer`): `code.uses`에 인덱스 클래스의 쓰기 줄이 없을 때만 `peer(키, 템플릿)`을 묻고, 값이 대는 인덱스 클래스를 쓴 쪽으로 — 쓴 쪽 칸
  `<클래스> [<서비스> · <FCT> 값의 metadata.event_type]`, 근거 `쓴 쪽은 <FCT>의 같은 키 값(…)에서 — 인덱스 쓰기 줄 0(<code.uses 증거>)`, 주 `… 쓴 쪽은 <FCT>의 같은
  키 <읽은 키> 값의 …에서 채웠다: <클래스>`. 그다음은 전처럼 그 클래스의 구독 토픽(R3-5p ①-c). 없거나·못 읽거나·값이 인덱스 클래스를 안 대면 전처럼 주 줄 그대로.
  그 읽기는 runner 밖이라 읽기 수에 안 세고 증거도 아니다 — 표의 사실로만.
- CLI `_peer_reader(config_root, gbm, fct, env, clock, seeds)` → `(peer, close)`: registry의 같은 GBM·활성·다른 사이트를 차례로, 같은 키 → 없으면 같은 템플릿의 첫 키
  (`scan`) → 다음 사이트; 키마다 `PEER_SITES`(3)곳까지, 한 번 못 읽은 사이트(접속 실패·층 없음·Redis 없음)는 그 걷기에서 다시 안 묻는다. 읽기 전용(`get`·`scan`).
  stub은 시드 `peers.<fct>.redis`(없는 사이트는 못 읽는 사이트 — 다른 변형·다른 CLI 테스트는 전과 같다). `case walk`·`case investigate` 둘 다 `EngineDeps.peer`로
  넘기고 어댑터와 같이 닫는다. 사내: 측정 PC가 다른 법인 Redis에 닿는지가 효과를 정한다 — 안 닿으면 5초씩 3곳까지 쓰고 주 줄 그대로(브리프에 적음).
- 측정판 `palette-source-missing`: 원천 색 키가 없고 쓰는 `PaletteSeeder`(dt-core `common/`, 세 서비스 공유)는 레지스트리 결과(명령)를 저장해 인덱스 쓰기 줄 0;
  sevt 시드가 그 키 값에 `PaletteSeeder`를 댄다 → 쓴 쪽 칸 `PaletteSeeder [dt-core(서비스 3개 공유) · sevt 값의 metadata.event_type]`, 그 클래스가 구독하는
  `mx.alarm.main`의 보존 범위 홉, 멈춤 `키가 없다 — palette:source(쓰는 … · 구독 mx.alarm.main 범위 0~5, 남은 메시지 5)`. 보드 CLI로 확인(`code uses palette:source`
  쓰기 0).
- 테스트 먼저(RED → GREEN). 스윕 +8(1153). 다음은 측정 #30 브리프(`code graph` 재생성 **불필요**).

#### R3-5t ④ (11-01) ✅ — 다른 사이트에 물을 키는 그 사이트의 이름 목록으로

- 측정 #30(걷기만): ①③ 의도대로, ② 채움 0 — peer가 이 사이트의 키를 글자 그대로 다른 사이트에 물었는데(`<이 FCT>:<꼬리>`를 `<다른 FCT>` Redis에) 사이트마다 키에
  자기 접두어(`키 접두어 항목`)가 붙어 get도 scan도 못 찾았다. 평가자가 직접 재 보니 리더는 닿고(접속 7~10초) `<다른 FCT>` 접두어 키는 인덱스 클래스를 댄다.
- `_peer_reader(…, names_for)`: 이 사이트의 이름 목록(`code.names_for(fct)` — 이 사이트 키를 만든 것과 **같은 config 경로**)에서 그 템플릿의 key_path를 찾고, 그
  사이트의 이름 목록에서 같은 key_path의 값을 가져와 자리는 이 사이트 키에서 채운다(`fill_key`); scan 패턴도 그 값의 머리로. 문자열 바꿈 규칙이 아니다. 그 사이트가
  그 key_path를 선언하지 않으면 거기엔 없는 것(읽지 않고 묻는 수에도 안 센다); 이름 목록이 없거나(인덱스 없음) 이 사이트 이름에 그 템플릿이 없으면 전처럼 글자 그대로.
  읽은 키는 그 사이트의 이름 그대로 주에 간다(`… sevt의 같은 키 sevt:palette:source 값의 …`). 사이트마다 이름 목록은 한 번만 읽는다.
- 테스트: 사이트마다 다른 접두어(`gumi:k:{line}` 걷기, `sevt:k:L7`만 있는 peer), 선언 안 한 사이트는 글자 그대로의 키가 있어도 안 읽음, 이름 목록 없으면 글자
  그대로. 측정판: sevt 층에 `키 접두어 항목`(gumi는 그대로 — 키 이름 전부가 안 바뀌게), `palette-source-missing`의 sevt 시드 키가 `sevt:palette:source` — 보드 CLI로
  확인(주 줄에 sevt 이름).
- 아는 한계(평가자 주의): 값이 대는 클래스가 **지우는 쪽**(`<정리 파이프라인>`)일 수 있다 — 값의 `metadata.event_type`은 마지막에 그 키를 만진 클래스이지 쓰는 클래스라는
  보장이 아니다. 걷기는 그 클래스의 구독 토픽까지 가고, 리드가 그 클래스 본문을 보고 가른다. 3개(`<충전기 배터리 키>` 계열)는 어느 사이트에도 없어 주 줄 그대로다.
- 테스트 먼저(RED → GREEN). 스윕 +6(1159). **R3-5t 끝** — 다음은 측정 #30 재측정 브리프(`code graph` 재생성 **불필요**).

## 범위 밖 — 12b·13으로

- 판정이 사람에게 닿는 경로(보고서·이벤트·메일)는 12b. 지금은 CLI 출력과 `--trace`의 `summary.md`뿐이다.
- `ask`(사람에게 묻기)는 13. `Decision`에 자리를 안 만들었다 — 만들면 route가 END로 흘려보내는 구멍이 생긴다.
- 판정이 케이스 저장소에 남지 않는다(12b가 보고서와 함께 정한다).
