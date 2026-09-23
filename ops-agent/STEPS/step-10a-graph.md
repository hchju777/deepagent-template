# 10a단계 — 조사 State와 그래프 배선

> **목적**: 라운드가 돈다. **LLM은 한 줄도 안 들어간다** — 울타리(라운드 상한·병렬
> 폭·게이트)가 지켜지는지를 LLM의 답과 섞지 않고 보기 위해서다.

```bash
python -m src case dryrun --plan examples/case-dryrun.json \\
                          --stub-seeds examples/stub-seeds.json
```

```
  mx/gumi — 구미 3라인 OEE가 512다 (정상 0~100)
  울타리: max_rounds=4 parallel_width=3 max_tasks=24
  라운드 2 — 끝난 이유: no_runnable
    ✅ t-1 [data_prober] 파생값(현재 OEE)을 읽는다 — redis.get key='oee:L3' → …
    ❌ t-3 [data_prober] 등재되지 않은 읽기 — 미등재 action — mongo.aggregate
    ✅ t-9 [recompute_verifier] 재계산 대조 — t-1의 증거가 있어야 한다 — …
```

**`t-9`를 보라.** 우선순위가 제일 앞(5)인데 1라운드에 안 돌고 2라운드에 돌았다.
`t-1`이 증거를 만들 때까지 게이트가 붙잡은 것이다 — 이 단계가 만든 것의 요약이다.

대본은 `examples/case-dryrun.json`을 고쳐 쓴다. **모르는 키는 거부한다** — 손으로
쓰는 파일이라 `"round"`(s 빠짐)가 조용히 무시되면 사람은 대본대로 돌았다고 믿는다
(`app.json`의 `timezone`이 그 형태로 한동안 거짓말을 했다). 설명은 `_`로 시작하는
키에 적으면 주석으로 걷힌다 — JSON에 주석이 없어서 둔 관례다.

## 그래프

```
START → frame → select → (Send로 execute×N  |  0건이면 integrate)
                  ↑                ↓
                  └──── integrate ─┘   continue면 select, 아니면 END
```

`execute → integrate`가 **고정 엣지**인 것이 barrier다. Send로 퍼진 가지가 전부
끝나야 integrate가 한 번 돈다 — 라운드 경계가 거기서 생긴다.

conclude·verify(12a)와 ask_human(13)은 아직 없다. integrate가 `conclude`를 고르면
그냥 END로 간다.

## 코드가 쥔 것 / LLM이 쥘 것

10a에는 LLM이 없지만, **자리는 이미 갈라 뒀다.** 10b에서 `frame`·`integrate`에
LLM이 들어올 때 울타리를 새로 만들 일이 없어야 한다 — 나중에 얹는 방어는 그 사이에
난 구멍을 못 막는다.

| 무엇 | 어디 |
|---|---|
| 라운드 상한 | `integrate` — 닿으면 "계속하자"를 **무시한다** |
| 병렬 폭 | `select` — 골라서 `running`으로 굴린다 |
| 실행 가능 판정 | `runnable_tasks` — 입력 증거가 **전부** 실재해야 |
| 태스크 개수 상한 | `frame`·`integrate` |
| 수명주기 소독 | `_sanitize_task` |
| 예외 흡수 | `execute` 최외곽, `ProbeRunner` |

## select가 게이트인 이유

frame은 **증거가 하나도 없는 시점**에 조사 전체를 계획한다. "원천을 읽고 → 코드를
읽고 → 재계산해서 대조한다"까지 한 번에 쓴다. 게이트가 없으면 그 체인이 라운드 1에
통째로 발사되고, **재계산 태스크는 입력 없이 돌아 전멸한다.**

`all()`이지 `any()`가 아니다. 재계산은 "원천값"과 "로직 명세"가 둘 다 있어야 성립한다.
하나만 있어도 돌리면 절반의 입력으로 기대값을 만들고, 그게 실제와 다른 것을 "이상
발견"으로 보고한다 — **틀린 답이 아니라 없는 이상을 만들어 내는 것**이라 더 나쁘다.

## 증거 id가 태스크 id에서 나오는 이유

`ev-1`, `ev-2`처럼 전역 순번을 쓰려면 번호를 나눠 주는 곳이 하나 있어야 한다.
그런데 태스크는 한 라운드에 **동시에** 돌고 병렬 가지들은 서로의 State를 못 본다.
공유 카운터를 두면 같은 번호가 두 번 나가거나, 실행 순서에 따라 번호가 달라져서
같은 입력에 같은 결과가 안 나온다.

`t-1.e1`은 나눠 줄 것이 없어 충돌이 불가능하고, "어느 태스크가 만든 증거인가"가
id에 적혀 있다.

## `ProbeRunner` — 등재제를 execute에도

태스크는 **등재 항목 이름**만 댄다(`redis.get`·`mongo.find`·`rest.query` 등 8개).
미등재 action, 스키마 밖 인자, 필수 인자 누락은 **포트에 닿기 전에** 거부한다.

규율 9와 같은 이유다. `run(port, method, args)` 같은 표면을 두면 "어느 메서드를 어떤
인자로"가 표현 가능해지고, 10b에서 그 결정을 하는 것은 LLM이다. 그리고 목록에 쓰는
메서드가 없는 것은 우연이 아니다 — 포트에 없으므로 여기 적을 수도 없다.

인자를 미리 보는 이유는 따로 있다. `redis.get`에 `pattern`을 주면 어댑터가
`TypeError`를 던지는데, 그걸 흡수해 `status="error"`로 돌리면 보고서에 "Redis 조회
실패"라고 적힌다 — **원인이 우리라는 사실이 지워진다.**

11b는 같은 `TaskRunnerPort`에 `SubagentRunner`를 꽂는다. 그래프는 안 바뀐다.

## "돌릴 것이 없으면" 멈춘다

계속하기로 했는데 실행 가능 태스크가 없으면 상한까지 빈 라운드를 돈다. 그러면
보고서가 "4라운드 조사했다"고 적는데 실제로 한 일은 없다 — **한 일이 없는 것이 많은
것처럼 보이는** 형태라 조용히 거짓말이 된다. 그래서 `stopped_by`가 세 값이다:
`decision` / `max_rounds` / `no_runnable`.

## StrictModel을 지켰다

참조 구현(`../../src/application/state.py`)은 `CaseState`가 `BaseModel`이다. 그대로
따라가려다 `extra="forbid"`로 **실제 그래프를 돌려 봤고** — Send 팬아웃·조건부
엣지·리듀서까지 문제없이 돌았다. 강제가 아니었으므로 규율 5를 지킨다.

## 이 단계에서 실제로 걸린 것 둘

### `test_dead_settings`가 거짓 초록이었다

`AppConfig.investigation`을 추가했는데 읽는 코드가 하나도 없었다. 그런데 테스트는
통과했다 — `from src.domain.investigation import ...` **import 줄 세 개가 매치**해서다.

**죽은 칸을 잡는 테스트가 죽은 칸을 놓치는 것**은 그냥 버그보다 나쁘다. 그 테스트가
있다는 이유로 아무도 다시 안 보기 때문이다. `_is_read`가 이제 import 줄을 뺀다.

### 내가 쓴 주석이 틀렸다

`state.py`에 "`{item.id: item for ...}`로 합치면 순서가 재배열된다"고 적었는데,
**실제로 돌려 보니 아니었다.** 파이썬 dict는 이미 있는 키를 갱신할 때 자리를 안
옮긴다. 그걸로 RED를 만들려다 39개가 전부 통과해서 드러났다.

진짜로 깨지는 것은 "갱신된 것을 빼고 뒤에 붙이기"다. 주석을 사실로 고쳤다.
**돌려 보기 전에는 주석도 믿을 게 못 된다** — 이 리포가 계속 배우는 것.

## 검증

`pytest tests/application` — 39개. 방어를 하나씩 지워 **전부 실제로 RED를 봤다**:

| 지운 것 | |
|---|---|
| 라운드 상한 `>=`→`>` | 3 failed |
| 병렬 폭 슬라이스 | 2 failed |
| 게이트 `all()`→`any()` | 12 failed |
| 리듀서 순서 유지 | 2 failed |
| 수명주기 소독 | 1 failed |
| execute 무raise | 3 failed |
| ProbeRunner 등재 검사 | 1 failed |
| `no_runnable` 정지 | 3 failed |

무raise는 **실제로 던지는** `ExplodingRunner`로 본다. 대본 실행기는 예약된 에러를
돌려줄 뿐이라 "얌전히 실패를 보고한 것"이고, 그걸로는 방어를 지워도 초록이다 —
`ScriptedAdapter`가 이 리포에서 이미 같은 거짓 초록을 만든 적이 있다.

## 검토 포인트

1. **`max_rounds=4`가 맞는가.** 라운드 하나가 LLM 호출 여러 번이다(10b부터).
   사이트 28개에서 케이스가 몰릴 때의 비용이 여기 걸린다.
2. **`parallel_width=3`이 대상에 주는 부하.** 사이트당 세 개가 동시에 읽는다.
   `guards.max_concurrent`(사이트당 세마포어)와 어느 쪽이 먼저 조이는지 봐야 한다.
3. **`ACTIONS` 8개로 충분한가.** 조사에 필요한데 없는 읽기가 있으면 11b 전에 넣는다.
4. `stopped_by`를 12a의 보고서가 어떻게 쓸 것인가 — `no_runnable`과 `max_rounds`는
   "미확정"의 이유가 다르다.

→ 다음: 10b — `frame`·`integrate`에 LLM을 넣는다. 이 파일의 울타리는 그대로 둔 채.
