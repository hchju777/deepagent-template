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

## 범위 밖 — 12b·13으로

- 판정이 사람에게 닿는 경로(보고서·이벤트·메일)는 12b. 지금은 CLI 출력과 `--trace`의 `summary.md`뿐이다.
- `ask`(사람에게 묻기)는 13. `Decision`에 자리를 안 만들었다 — 만들면 route가 END로 흘려보내는 구멍이 생긴다.
- 판정이 케이스 저장소에 남지 않는다(12b가 보고서와 함께 정한다).
