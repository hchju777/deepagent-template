# 4단계 — 순찰 프로브

> **목적**: 점검이 **무엇을 읽는가**를 config가 선언하고, 그대로 읽어서 보여 준다.
> 판정은 안 한다(5단계).

```bash
python -m src patrol probe                          # 활성 점검 전부
python -m src patrol probe --check badge_all_zero   # 하나만
python -m src patrol probe --stub-seeds s.json      # 대상에 안 붙고
```

```
✅ mx/gumi  badge_all_zero [operation] — 2개 프로브 전부 읽었다
    badge: stub-rest:summary_badge
    { "status": "ok", "observed_at": "...", "data": { "request": {...}, "response": [...] } }
    status: stub-rest:prod_status
    { ... "response": { "status": "In Production" } }
```

**rule을 쓰기 전에 응답 실물을 봐야 한다.** 필드 이름 하나가 틀리면 판정이 조용히
엉뚱해진다 — 실제로 `caution`을 `cuation`으로 적은 샘플을 받았고, 그대로 믿었으면
`alarm=0, normal=0, cuation=5`인 badge를 "전부 0"으로 판정할 뻔했다.

## 점검 하나가 프로브 **여러 개**를 묶는다

원본 템플릿은 점검 하나에 프로브 하나였다. 우리는 묶는다. 이유가 구체적이다 —

> "생산 중일 때만 0/0/0이 이상"

을 판정하려면 **두 응답**이 필요하다(`summary_badge`와 `prod_status`). 원본의
`expected_state` rule은 *한 응답 안의 두 필드*를 보는 것이라 이 모양에 안 맞는다.

```json
"patrol": { "checks": { "badge_all_zero": {
  "concern": "operation",
  "probes": {
    "badge":  { "action": "rest.query",
                "params": { "entry": "summary_badge", "params": {} } },
    "status": { "action": "rest.query",
                "params": { "entry": "prod_status", "params": {} } }
  } } } }
```

**프로브에 이름을 준다.** 5단계의 rule이 `"items": "badge"`처럼 이름으로 가리킨다 —
순서나 인덱스로 가리키면 프로브를 하나 끼워 넣을 때 조용히 어긋나고, 그 어긋남은
"판정이 이상하다"로만 드러난다.

`params`가 두 겹인 것은 포트 시그니처 그대로다(`query(entry, params)`). 이름을 바꾸면
표와 포트가 갈라지고, 갈라진 것은 아무도 안 본다.

## 동시에 읽는다 — as_of 정렬

`asyncio.gather`다. 성능 얘기가 아니라(프로브가 둘뿐이다) **시점 정렬**이다.
badge와 status를 읽는 시점이 벌어지면

> "생산 중이었는데 그 사이에 멈춤"

이 **"생산 중인데 0/0/0"**으로 보인다. 없는 이상을 만들어 내는 방향이다.

테스트가 이걸 보는 방법은 타임라인의 **모양**이다:

```
동시: start start end end        순차: start end start end
```

처음엔 "둘 다 시작됐고 이름이 다르다"만 봤는데 **그건 순차도 통과한다**(끝나고 나면
둘 다 시작돼 있다). 지우고 다시 썼다.

## 등재표가 **한 곳**이다 — `src/domain/actions.py`

이 표를 쓰는 곳이 둘이다: 순찰의 프로브(여기)와 조사의 `ProbeRunner`(10a). 10a에서
`runner_probe.py`에 두었던 것을 domain으로 옮기고 둘이 같은 것을 쓰게 했다.

각자 자기 표를 들면 언젠가 한쪽만 넓어지고, **넓은 쪽이 곧 우리 허용 범위**가 된다.
어댑터 조립을 `factory.py` 한 곳에 둔 것과 같은 이유다.

domain에 둔 이유는 계층이다. patrol이나 infrastructure에 두면 application이 그쪽을
import하게 되어 화살표가 하나 는다. 표에 있는 것은 포트 이름과 메서드 이름뿐이고
실구현은 모르므로 domain에 닫힌다(`adapters`는 덕 타이핑).

## `concern` — 원인이 아니라 "먼저 물어볼 곳"

| | 먼저 묻는 것 |
|---|---|
| `system` | 인프라에게 — "파이프라인 살아 있나?" |
| `operation` | 현장에게 — "라인 진짜 섰나요?" |

**0/0/0의 원인이 집계 코드일 수 있다. 그래도 `operation`이다.** 현장에 먼저 묻는 것이
가설을 제일 빨리 자르기 때문이고, 어느 답이 와도 이득이기 때문이다:

```
현장: "돌고 있는데요?"  →  집계 코드·데이터 유입 의심으로 넘어간다
현장: "지금 섰어요"     →  끝. 조사할 게 없다
```

원인은 `verdict_type`(12a)이 담는다. **둘이 다른 것은 정상이고**, `operation`으로 열려
`logic_bug`로 닫히는 케이스가 이 시스템이 제일 값을 하는 경우다.

**기본값을 두지 않았다.** 원본은 `= "system"`인데, 그 주석이 스스로 그것을 "분류가
아니라 마이그레이션 비용에 대한 타협"(픽스처 90곳)이라고 적었다. 우리는 점검이
0개라 그 비용이 0이고, 기본값이 있으면 **첫 rule부터 오분류**다.

## 기동이 막는 것 — 오타는 런타임에 못 고친다

| 검사 | 왜 |
|---|---|
| `action`이 등재 목록에 있는가 | `ProbeSpec`이 **로드 시점**에 본다 |
| `rest.query`의 `entry`가 실재하는가 | `boot`가 본다 |
| `params`가 그 항목의 닫힌 스키마를 통과하는가 | `boot`가 본다 |

`summary_badge`를 `summary_bagde`로 적으면 매 순찰마다 실패하는데, 그 실패는
`unreachable`로 흡수되어 **"대상이 안 붙는다"처럼 보인다.** 진짜 장애와 구별이 안 되고,
28사이트에서는 그런 줄 하나가 묻힌다.

## "못 읽었다"는 세 번째 상태다

순찰의 산출물은 평온할 때 "아무 일도 없음"인데, **못 붙었을 때의 산출물도 같은
모양**이다. 사이트가 28개라 하루에 하나쯤은 안 붙는다 — 예외가 아니라 일상 경로다.

프로브가 하나라도 실패하면 `ProbeSet.status`가 `unreachable`이고, 명령의 종료 코드가
0이 아니다. **판정할 수 없는 것을 "이상 없음"으로 접으면 감시가 자기 실패를 숨긴다.**

## 이 단계에서 실제로 걸린 것 둘

### `776 passed`인데 CLI가 깨져 있었다

`ProbeRunner`에 `clock`을 필수로 올렸는데 `__main__`의 호출부가 안 따라갔다. 테스트는
전부 통과했다 — 그 경로를 부르는 테스트가 없었다. 명령을 직접 돌려서야 `TypeError`가
나왔다. handover가 적어 둔 그 함정이고(`build_prompt`), **초록불은 증거가 아니다.**
`test_CLI가_실제로_돈다`를 추가했다.

### 내 동시성 테스트가 순차를 통과시켰다

위의 "동시에 읽는다" 절 참고. RED를 만들어 보지 않았으면 못 찾았다.

## 검토 포인트

1. **`prod_status`의 실제 응답 모양.** 지금은 `{"status": ...}` dict 하나로 가정했다.
   라인별 리스트면 5단계의 가드가 **라인마다 짝을 맞춰야** 한다 — 설계가 달라진다.
2. **호출 단위.** `{}`로 전체 한 번인가, 라인마다인가. 후자면 28사이트 × 라인 수 ×
   2회가 3분마다 나간다.
3. `patrol probe`의 출력이 응답 전문이라 길다. 5단계에서 요약 모드가 필요할지.

→ 다음: 5단계 — rule 판정과 finding (아직 없다. 실행 순서는 [로드맵](step-00-overview.md))
