# 5단계 — rule 판정과 finding

> **목적**: "**지금 이 응답이** 이상한가"를 답한다. 상태를 갖지 않는 순수 함수다 —
> "얼마나 지속됐나"는 6단계.

```bash
python -m src patrol check                     # 지금 걸면 뭐가 걸리나
python -m src patrol check --all-sites         # 28사이트 전부
python -m src patrol check --stub-seeds s.json
```

```
❌ mx/gumi  badge_all_zero [operation] — 5개 중 4건
    Line/Defect        전부 0 — alarm·caution·normal이 모두 0이다  {"alarm":0,"caution":0,"normal":0}
    Line/Downtime      필드 부재 — caution   {"alarm":"0","caution":"(없음)","normal":"0"}
    Line/Target Rate   식별자가 중복이다 — #0과 #3가 같다
    Part/Target Rate   수치가 아닌 값 — caution: bool이다 (False)
⬜ mx/sevt  badge_all_zero [operation] — 판정 안 함 — status.response.status가 'Idle'다
⚠ nw/sev   badge_all_zero [operation] — 가드를 확인할 수 없다 — status.response.status가 없다
```

## 네 가지 상태 — `ok`가 아닌 것이 셋이다

| | 뜻 | 왜 따로 있나 |
|---|---|---|
| `ok` | 판정했고 이상 없음 | |
| `finding` | 판정했고 이상 있음 | |
| `skipped` | **안** 했다 — 생산 중이 아니라서 | |
| `unreachable` | **못** 했다 — 못 읽었거나 가드를 확인 못 함 | |

**`skipped`와 `unreachable`을 같은 칸에 넣으면 대상이 죽은 날이 "쉬는 날"로 보인다.**
그리고 둘 중 어느 것도 `ok`가 아니다 — 못 한 것을 "이상 없음"으로 접으면 감시가 자기
실패를 숨긴다. 사이트가 28개라 하루에 하나쯤은 안 붙고, 그건 예외가 아니라 일상 경로다.

## 판정 규칙 — 전부

```json
"rule": "items_all_zero",
"params": {
  "items":     { "probe": "badge", "path": "response" },
  "identity":  ["group", "title"],
  "counts":    ["alarm", "caution", "normal"],
  "only_when": { "probe": "status", "path": "response.status",
                 "equals": "In Production" }
}
```

| 상황 | 결과 |
|---|---|
| 프로브 하나라도 실패 | `unreachable` |
| **`only_when`의 path가 없다** | **`unreachable`** |
| `status != "In Production"` | `skipped` |
| `items` 경로가 없다 / 리스트가 아니다 | `finding` |
| 빈 리스트 | `ok` |
| identity 필드 부재 / 식별자 중복 | `finding` |
| **`counts`에 적었는데 응답에 없다** | **`finding`** |
| 값에 bool·NaN | `finding` |
| **전부 0** | **`finding`** |

## 왜 `path`를 config가 적는가

REST 어댑터는 응답을 `{"request": ..., "status": 200, "response": <실제>}`로 감싼다.
그런데 mongo·redis 프로브는 모양이 또 다르다. rule이 "REST면 알아서 벗긴다"를 하면
프로브 종류마다 특례가 생기고, **그 특례는 어댑터가 응답 모양을 바꾸는 날 조용히
틀린다.** 경로를 config가 적으면 그 순간 기동이나 판정이 시끄럽게 말한다.

## `MISSING` — 부재와 `null`을 가른다

```python
get_path({"status": None}, "status")  is None      # 대상이 null이라고 말했다
get_path({},              "status")  is MISSING    # 우리가 잘못 물었다
```

`None`을 부재의 표시로 쓰면 둘이 같아진다. 전자는 대상의 사실이고 후자는 **우리 config가
틀렸다는 신호**다.

## `counts`에 적은 필드가 없으면 finding인 이유

이 시나리오는 가정이 아니다 — **`caution`을 `cuation`으로 적은 샘플을 실제로 받았다.**

없는 필드를 조용히 건너뛰면 `alarm=0, normal=0, cuation=5`인 badge를 `[0, 0]`만 보고
**"전부 0"이라 판정한다.** 틀린 답이 아니라 **없는 이상을 만들어 내는** 방향이고,
그렇게 난 알람은 현장 사람을 헛걸음시킨다.

## finding은 항목 하나에 하나다

badge 셋이 0/0/0이면 finding도 셋이다. 묶지 않는 이유 셋:

- **조사가 찍을 데를 갖는다.** "어딘가 0/0/0"이면 조사 대상이 빈다
- **6단계가 대상별로 연속을 센다.** 묶으면 "A는 3회째, B는 1회째"를 못 가른다
- **중복 케이스 방지도 대상 단위**여야 맞다

## 화이트리스트를 두지 않았다

badge 목록을 config에 적어 두는 안을 기각했다. 방향이 반대라 성질이 다르다:

```
화이트리스트 : 적어야 감시된다  → 안 적으면 새 badge가 조용히 감시 밖    ✗
제외 목록    : 적어야 안 본다    → 안 적으면 시끄러울 뿐                  ✓
```

**제외 목록도 지금은 안 만든다.** 실제 거짓 양성을 본 뒤에 연다 — 아무도 안 쓴 칸을
미리 두면 "아무도 생각하지 않는 체크박스"가 된다.

## 사이트 격리 — 이게 `runner.py`의 존재 이유다

구미 Mongo가 안 붙는다고 SEVT 순찰이 멈추면 **장애 하나가 감시 전체를 끈다.**
세 겹으로 막는다:

1. 읽기(`domain/actions.py`) 무raise
2. 프로브 묶음(`patrol/probes.py`) 무raise
3. **사이트 단위 무raise** — `build_adapters`부터가 던질 수 있다(config는 통과했는데
   호스트 이름이 안 풀리는 경우 등)

3번이 없으면 1·2번이 아무리 견고해도 **한 줄에서 전부 죽는다.** 못 본 사이트는
`unreachable` 결과로 **이름이 남는다** — 조용히 건너뛰면 28개 중 하나가 빠진 것을
아무도 모른다.

## 로드 시점에 막는 것

`rule`이 `Literal["items_all_zero"]`이고 `params`가 `StrictModel`이라, 오타난 rule
이름도 모르는 키도 **config를 읽는 모든 경로**에서 걸린다. 여기에 더해
`CheckConfig`가 교차 검증을 한다:

> `"items": {"probe": "badges"}`처럼 **선언되지 않은 프로브**를 가리키면 거부한다.

계획에서는 이걸 기동(`boot`) 검사로 넣으려 했는데, 스키마에 두는 쪽이 낫다 —
기동뿐 아니라 `patrol check`·`config show` 등 config를 읽는 **모든** 경로가 탄다.

## 검증

`pytest tests/patrol tests/config/test_schema_patrol.py` — 방어를 하나씩 지워
**전부 실제로 RED를 봤다**:

| 지운 것 | |
|---|---|
| 가드 부재 → `unreachable` | 1 failed |
| `only_when` 가드 자체 | 3 failed |
| **개수 필드 부재 검사**(`cuation`) | 1 failed |
| bool 검사 | 1 failed |
| 식별자 중복 검사 | 1 failed |
| **identity를 `title`만으로** | 6 failed |
| 리스트 아닌 응답 거부 | 1 failed |
| **사이트 격리** | 3 failed |
| `MISSING` → `None` | 3 failed |

## 이 단계에서 걸린 것

`CheckConfig`에 `rule`·`params`를 필수로 올리자 **4단계 테스트 픽스처 7개가 깨졌다.**
시그니처를 바꾸면 전수 확인이 필요하다는 handover의 규율이 그대로 적용된 자리고,
이번엔 테스트가 바로 잡아 줬다.

## 검토 포인트

1. **`_shift`를 쓸 것인가.** `prod_status`가 교대 시각을 준다
   (`start_dt: "2026-09-16 08:00:00"`). 교대 시작 직후엔 집계가 아직 0일 수 있어
   거짓 양성이 날 여지가 있다 — **실데이터로 확인한 뒤** 유예를 열지 정한다.
   6단계의 N회 연속이 1차 방어다.
2. **badge가 전부 0/0/0이면 finding이 badge 수만큼 난다.** 그건 "전체 장애"이므로
   맞는 동작이지만, 6단계가 케이스를 그만큼 열면 안 된다 — 게이트의 묶는 규칙이 필요하다.
3. `examined`(판정한 항목 수)를 보고서가 어떻게 쓸 것인가. "이상 없음"이 0개를 본
   결과인지 12개를 본 결과인지는 다른 사실이다.

→ 다음: 6단계 — 케이스 저장소와 게이트 (N회 연속·중복 방지). 실행 순서는
[로드맵](step-00-overview.md).
