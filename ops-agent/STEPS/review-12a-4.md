# 12a 리뷰 4번 — sevt에서 실제 조사 한 판 (사내 AI용 브리프)

> 이 문서는 **사내에서 대상 시스템에 붙을 수 있는 AI(또는 사람)**가 읽고 그대로 수행하기 위한 것이다. 수행자는
> 아래 명령만 돌리고 **진단만** 한다 — 파일을 고치지 않고, 커밋하지 않고, 대상 시스템에 쓰지 않는다(이 시스템은
> 대상에 대해 완전 읽기 전용이고 아래 명령도 전부 읽기다). 결과는 아래 "보고 형식"대로 짧게 적어 바깥(이 리포를
> 고치는 쪽)으로 옮긴다. 옮길 때 **실제 호스트명·계정·컬렉션·토픽·서비스 이름은 쓰지 않는다** — `<서비스A>`
> 같은 자리표시자로 바꾼다. 트레이스 파일(`output/traces/`)의 증거 본문은 옮기지 않는다.

## 왜 이 판인가

12a(판정·검증)는 대본(`case dryrun`)과 측정판(가짜 대상 + 대역 LLM)에서만 끝까지 돌았다. 11e로 그래프 번들이
GBM 단위가 되어(`code graph --gbm mx` 한 번, 사내 203초) 실제 사이트에서 조사를 돌릴 수 있게 됐다. 보려는 것은 셋:

1. **실제 대상 + 실제 LLM**에서 조사가 끝까지 가서 판정 블록이 찍히는가(어떻게 끝나든 판정은 생겨야 한다).
2. **검증**이 통과하거나(`검증 통과`), 사유를 말하며 강등하는가(`검증 미통과 → 강등` + `근거 없는 다리 제외: …`).
3. 리드가 **그래프·인덱스 액션**(`code.flow`·`code.uses`·`code.callers`·`code.trace`)을 실제로 쓰는가 — 11e가
   조사에 들어갔다는 뜻이다.

판정이 **맞는지**는 사내에서 아는 사실과 대조하는 사람의 판단이다 — 그 판단도 보고에 한 줄 적는다.

## 전제

- `code graph --gbm mx`가 돼 있다(`code status --gbm mx --fct sevt`에 그래프 절이 `✅`로 보이고 `사이트 sevt: 덮은 값 N개`가
  찍힌다). 낡음(`⚠ 낡음`)이면 `code graph --gbm mx`를 먼저 다시 돌린다.
- `config/app.json`의 `llm`에 `timeout_s`·`max_retries`가 **명시돼 있으면 그 값이 이긴다.** 생각하는 모델은 한 답에 60초가
  모자라 두 판이 r1에서 죽었다 — 명시돼 있으면 `timeout_s: 300`, `max_retries: 0`으로(없으면 그게 기본값이다).
- 역할별 모델(R2-1). 10-08 재측정으로 확정한 설정 — 사내 `app.json`의 `llm` 옆에 그대로 적는다(`llm.stream`은 chat_model이면
  기본이 켬이라 안 적어도 된다; 리포의 `config/app.json`에는 안 넣는다 — CLI 테스트가 그 파일을 복사해 쓴다):

  ```json
  "llm_roles": {
    "lead": {"model": "gauss-o-flash", "model_id": "339", "max_tokens": 1500, "expect_reported_model": "openai/gpt-oss-120b",
             "stream_idle_s": 30},
    "conclude": {"model": "gauss-o-think-beta", "model_id": "581", "timeout_s": 600}
  }
  ```

  lead `max_tokens` 400은 지금
  integrate 형식(가설 + 태스크 여럿)에서 JSON이 잘린다 — ReAct 전까지 1500. `llm describe`로 역할별 실효값을 확인하고
  `llm check --role lead`·`--role conclude`가 둘 다 붙는지 본다(`llm check`는 R2-3 ①에서 고쳤다 — 둘째 질문부터 Connection
  error가 나던 것). 분당 할당량에 걸리면 `llm.min_interval_s`(초)를 둔다 — 게이트웨이 단위로 리드·판정이 같이 지킨다.
- 순찰이 sevt에서 **finding을 내야** 케이스가 열린다. 사람이 손으로 케이스를 여는 CLI는 아직 없다. finding 0건이면
  4번은 지금 못 돌린다 — 그 사실 자체가 보고다(아래 형식 ②에서 멈춘다).

## 돌릴 것 (이 순서대로, 전부 읽기)

```
PYTHONUTF8=1 python -m src llm describe
PYTHONUTF8=1 python -m src code status --gbm mx --fct sevt
PYTHONUTF8=1 python -m src patrol check --gbm mx --fct sevt
PYTHONUTF8=1 python -m src patrol open --gbm mx --fct sevt --dry-run
PYTHONUTF8=1 python -m src patrol open --gbm mx --fct sevt
PYTHONUTF8=1 python -m src case investigate <케이스id> --trace output/traces
PYTHONUTF8=1 python -m src case trace <케이스id> --trace output/traces --brief
```

- `patrol open --dry-run`은 열릴 케이스를 보여만 준다. `케이스로 만들 것이 없다`면 **여기서 멈춘다**.
- `patrol open`(dry-run 없이)이 찍는 줄의 둘째 칸이 케이스 id다(`🆕 <id> mx/sevt <점검> …`). `↻`는 이미 열린
  케이스에 붙였다는 뜻이고 그 id를 쓰면 된다. `⬛`(억제)·`❌`(거부)면 그 사유를 보고에 적고 멈춘다.
- `case investigate`는 실제 LLM을 라운드마다 부른다. 라운드 상한은 config가 정한다 — 몇 분 걸릴 수 있다.
- `case trace --brief`는 프롬프트·응답을 **증거 본문 없이** 줄인 것이다. 길면 라운드 머리줄(`rN …`)과 `결과:` 줄만 옮긴다.

## 무엇을 확인하나

`case investigate` 출력에서:

| 보는 곳 | 기대 | 아니면 |
|---|---|---|
| `라운드 N — 끝난 이유: …` | `decision`(리드가 스스로 conclude) 또는 `max_rounds` | `llm_error`면 사유 줄(`시간 초과 (N초) — …`)과 트레이스 파일 머리의 `응답: N초`를 같이 옮긴다 |
| `판정 <종류> (<확신>) — …` 블록 | 종류가 `logic_bug/data_loss/config_error/stale_data/external/inconclusive/degraded` 중 하나, `원인 <컴포넌트>`가 토폴로지의 서비스 이름 | `판정 없음`이면 결함 — 전체 출력을 옮긴다 |
| 같은 블록의 `검증 …` 줄 | `검증 통과` / `검증 통과 (재작성 1회)` / `검증 미통과 → 강등` | 그 외 문구가 있으면 그대로 옮긴다 |
| `태스크` 목록 | `code.flow`·`code.uses`·`code.callers`·`code.trace` 중 하나 이상 | 하나도 없으면 "그래프 액션 0" — 브리핑에 `<데이터 흐름>`이 실렸는지 trace로 본다 |
| `증거 N건` | 인용된 id가 전부 이 목록에 있다(verify가 보장) | 없는 id가 있으면 결함 |
| 끝의 요약(`계약 위반`·`되물음` 줄) | 판정 턴의 계약 위반 0 | 있으면 그 줄을 옮긴다 |

`case trace --brief`에서(④가 이상할 때만): 라운드마다 `rN <노드> · 프롬프트 N자`와 `결과:`를 보고, 어느 라운드에서
`code.` 액션이 나왔는지, 판정 턴(`conclude`)의 `결과:`에 `증거에 없는 id`가 찍혔는지 본다.

## 보고 형식 (이 일곱 줄이면 된다)

```
① llm describe: <기본 한 줄 + 역할 줄 그대로>
② patrol check 마지막 줄: finding N건 · 판정 못 한 점검 M개      ← 0건이면 여기까지
③ patrol open 케이스 줄: 🆕 <id> mx/sevt <점검> …                 (실제 이름은 자리표시자로)
④ investigate: 라운드 N — 끝난 이유: … / 판정 … / 검증 … / 그래프 액션: code.flow 1·code.uses 2 (없으면 0)
⑤ 증거 N건 · 인용 id 전부 목록에 있음 (예/아니오) · 계약 위반 N
⑥ 걸린 시간(대략) · 라운드 수 · `case trace --brief` 머리줄의 `응답 N초` 중 가장 큰 것
⑦ 사람 판단 한 줄: 판정이 아는 사실과 맞나 (맞다 / 어긋난다 — 어떻게)
```

이상이 있으면 그 줄의 원문을 자리표시자 처리해서 덧붙인다. 증거 본문·호스트명·실제 이름은 옮기지 않는다.

## R2 뒤 재측정에서 더 볼 것 (10-07)

- 시작 전 `NO_PROXY`에 대상 REST 호스트와 LLM 게이트웨이 호스트가 있는지 — 없으면 REST 실패에 `프록시 경유 의심` 줄이 붙는다
  (README "프록시와 NO_PROXY").
- 명령이 끝나 내려갈 때 **트레이스백이 남는가**(`ConnectionResetError(10054)`·`Event loop is closed`). 남으면 그 전문 세 줄.
- 트레이스 파일 머리의 `응답: N초`와 `## 물어본 것 (N자)`의 N — 라운드별로. integrate 프롬프트의 `<열린 질문>` 첫 줄.

## R2-3 뒤 측정 #3에서 더 볼 것 (10-08)

- 같은 두 케이스, 설정은 위 `llm_roles` 그대로(2-3).
- 리드 턴의 **JSON 실패 횟수**: `case trace --brief`에서 `결과: 못 읽었다 — … JSON …`인 라운드 수. 0이어야 한다
  (`response_format`을 서버가 받았다는 뜻). 게이트웨이가 400으로 거부하면 그 본문 한 줄 — `llm.response_format: json_object`로
  바꿔 다시.
- **429 횟수와 대기 초**: 요약 끝줄 `429 대기: 합계 N초 · 호출 M회`(없으면 안 찍힌다).
- `kafka tail --limit 300`(또는 `peek kafka`)이 이제 몇 건을 돌려주는지 한 줄.

## R2-4 뒤 측정 #4에서 더 볼 것 (10-09)

- 같은 두 케이스, 같은 `llm_roles`. `llm`에 `"min_interval_s": 15`를 더한다 — 빠르게 3~4번 연속 부르면 걸렸다(측정 #3).
  429 대기는 이제 게이트웨이의 `nextAccessTime`까지라 보통 60초보다 짧다.
- 요약 끝줄 `429 대기: 합계 N초 · 호출 M회 · 근거 …` 그대로. 근거가 `기본값`이면 시각을 또 못 읽은 것이다 — 그 429 본문의
  `nextAccessTime` 값 한 줄.
- 판정 턴의 JSON 실패 횟수(`case trace --brief`에서 `conclude`의 `결과: 못 읽었다`).
- integrate 머리줄의 고정부와 증거 글자 수(요약 머리줄 `프롬프트 N자 = …`).
- 종료 때 10054 트레이스백이 남는가.
- `peek kafka --topic <원천토픽> --limit 300`의 건수와 걸린 초.

## R2-5 뒤 측정 #5에서 더 볼 것 (10-09)

- 같은 두 케이스, 같은 설정(`min_interval_s` 15). `llm_roles.lead`에 `"stream_idle_s": 30`을 더한다(위 JSON) — 리드 턴은 보통 15초
  안인데 측정 #4에서 조각 520개 뒤 120초를 멈춰 기다렸다. `llm describe`의 리드 줄에 `· 유휴 30s`가 보이면 붙은 것이다.
- 요약 끝줄 `스트림 끊김: N회 — 받은 글로 읽음 K · 다시 물음 M`과 `전송 실패: N회 — 사유`(없으면 안 찍힌다). 끊김이 있었으면 그
  트레이스 파일의 `스트림:` 줄 한 줄과 날것 응답의 **마지막 40자**(끝이 `}`로 닫혔는지 — 끝 표시만 빠진 답이었나를 가른다).
- stderr에 `stream_chunk_timeout fired` 줄이 더는 안 찍히는가.
- 판정이 강등됐으면 그 출력의 다리들(`원인`·`후보`의 component)과 caveat `서비스 아닌 component 제외: …` 한 줄 — 서비스 아닌 이름이
  다리로 남아 있으면 안 된다.
