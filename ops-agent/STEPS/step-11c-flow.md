# 11c — 데이터 흐름 그래프

> 상태: 진행 중 (커밋 1/3). 앞: [11a](step-11a-code.md). 뒤: 11b → 12a.

## 왜 이 스텝인가

11a의 네 번의 사내 실행과 세 번의 로컬 대역 실행이 같은 것을 보여 줬다. 리드가 못 한 것은
"코드의 어느 줄을 읽을까"가 아니라 **"어느 서비스, 어느 흐름을 볼까"**였다. processor → 토픽
→ sink → 컬렉션을 몰라서 api만 팠고, 역할을 목록에 붙여 줘도 "processor가 저장을 못 한다"고
썼다. 12a의 verify가 그런 판정을 심사하면 "미확정"만 쌓인다. 흐름 지도가 먼저다.

## graphify를 어떻게 쓰나 — 실험으로 정했다

`graphify`(0.9.65)를 여기 설치해 로컬 측정판의 가짜 레포(dt-core·dt-api)에 `extract
--code-only` → `cluster-only`를 돌렸다.

- 노드 10, 엣지 7. 전부 `contains`(파일→함수)와 `rationale_for`(docstring→파일).
  **서비스 사이 엣지 0개.** `alarm_events`·`mx.alarm.main`·`gumi-mx-sink` 노드 없음.
  config JSON 6개를 스캔은 했지만 노드를 안 만들었다.
- `explain "sink"` → 없음. `path "processor" "sink"` → 없음.
- `cluster-only`가 커뮤니티 이름을 지으려고 **LLM을 불렀다**(입력 41,227토큰). `--no-label`이
  있다. 사내에서는 이 호출이 게이트웨이나 `claude` CLI로 새어 나간다.

토픽과 컬렉션은 config의 문자열이고 생산자·소비자는 각자 그 문자열을 읽을 뿐이라 AST에는
둘을 잇는 관계가 없다. 레포가 커져도 안 변한다. 프로세스 사이를 브로커로 건너는 흐름은
구문이 아니다.

그다음 우리 흐름 엣지를 graphify의 `graph.json` 스키마로 적어 `graphify merge-graphs`로
합치고 다시 물었다.

```
graphify path "processor" "sink" --graph merged.json --undirected
  processor --produces [EXTRACTED]--> mx.alarm.main <--consumes [EXTRACTED]-- sink
graphify explain "alarm_events" --graph merged.json
  <-- sink [writes] dt-core/sink/writer.py:L10
  <-- api  [reads]  dt-api/api/alarms.py:L5
```

**결정**: graphify를 엔진으로 쓴다(심볼 그래프, `explain`·`path`, `merge-graphs`). 우리는
graphify가 못 보는 층 하나만 만든다 — 서비스↔자원 흐름 오버레이. 같은 스키마라 합친
`graph.json` 하나를 리드 브리핑·`code.flow`·11b의 `code_tracer`가 같이 읽는다.

지킬 것: `--code-only`와 `--no-label`만(의미 패스·LLM 라벨 금지), 커밋에 박제(`code sync`가
만들고 `code status`가 대조, 훅 없음), git에 안 넣음(실제 이름이 든다, `output/`), `--strict`와
`query`는 안 씀(브리핑은 코드가 이웃을 계산해 넣고 리드에겐 `code.flow` 하나). 그래프는
"어디"를 주고 "무엇"은 프로브가 준다 — 12a의 verify가 그 경계를 코드로 지킨다.

## 방법 (커밋 1 — `src/knowledge/flow.py`)

1. **이름은 합친 config에서.** `Topology.flow.name_paths`(종류 → 점 경로). 끝이 dict면 값들이
   이름, str이면 그것. 템플릿 값(`alarm:stats:{line}`)은 `{` 앞까지가 grep 리터럴.
2. **쓰인 자리는 `git grep -n`으로.** 리터럴 일치 = EXTRACTED, config 키 토큰 일치
   (`topics["alarm_main"]`) = INFERRED. 키 토큰은 **부모 키가 같은 줄에** 있어야 한다 —
   `format(service="processor")`의 `processor`는 그룹이 아니다.
3. **방향은 같은 줄(없으면 앞뒤 줄)의 동사로.** 식별자를 `_`·camelCase로 쪼개 조각으로 맞춘다
   (`insert_many` → insert, `reset` ≠ set). 읽기·쓰기 표는 코드 상수 하나. 둘 다면
   `mentions`(AMBIGUOUS), 없어도 `mentions` — 버리지 않는다. 종류별로 이름이 다르다:
   토픽은 consumes/produces, 그룹은 consumes_as, 컬렉션·키는 reads/writes.
4. **파일 → 서비스.** 레포에 서비스가 하나면 그것(EXTRACTED), 토폴로지 `path`(EXTRACTED),
   첫 디렉터리 이름 = 서비스 이름(INFERRED), 못 가르면 레포 노드에 AMBIGUOUS.
5. **config 파일 히트는 `declares`** — 레포 노드에서 자원으로.
6. **결정론.** 이름·히트를 정렬해 처리. 같은 커밋이면 같은 JSON(테스트가 두 번 돌려 대조).
7. **질의.** `neighbors(name, depth)`는 방향 무시. `shortest_path(a, b)`는 **데이터 흐름
   방향**만 통과한다(쓰기→자원→읽기). 둘 다 쓰는 하트비트 키도 2홉이지만 흐름이 아니다.

측정판에서 나온 엣지(전부 근거 줄 있음):

```
processor —consumes→ mx.alarm.raw      processor —produces→ mx.alarm.main
sink —consumes→ mx.alarm.main          sink —consumes_as→ gumi-mx-sink
sink —writes→ alarm_events             api —reads→ alarm_events
processor·sink —writes→ hb:{service}   dt-core —declares→ (선언된 이름 전부)
```

## 측정 — 결과를 보기 전에 적는다

아래는 커밋 3(브리핑 블록·`code.flow`)을 돌리기 **전에** 못 박은 것이다. 나중에 유리한
문항만 고르는 것을 막기 위해서다(11a 후반의 교훈, 그리고 참고한 글의 방식).

고정 질문 8개 — 로컬 측정판, 심은 고장은 sink 컨슈머 정지:

| # | 종류 | 질문 | 기대 |
|---|---|---|---|
| A1 | 구조 | `alarm_events`를 쓰는(writes) 서비스는 | sink |
| A2 | 구조 | `mx.alarm.main`을 소비하는 서비스와 그룹은 | sink, gumi-mx-sink |
| A3 | 구조 | processor에서 sink까지 데이터가 가는 경로는 | processor → mx.alarm.main → sink |
| A4 | 구조 | api가 읽는 자원은 | alarm_events, alarm:stats:{line} |
| A5 | 구조 | `mx.alarm.raw`를 만드는 서비스는 | (없음 — 외부 유입) |
| A6 | 구조 | sink가 쓰는 자원은 | alarm_events, alarm:stats:{line}, hb:{service} |
| B1 | 값 | sink의 batch_size는 | 그래프는 못 답해야 정상(`code.config`가 답) |
| B2 | 값 | gumi-mx-sink의 현재 lag는 | 그래프는 못 답해야 정상(`kafka.group_offsets`가 답) |

지표(로컬 haiku 루프, `<데이터 흐름>` 블록 유무로 각 3회):

- 정답 부품(sink)을 처음 짚는 라운드 번호
- 최종 가설이 부품 하나를 짚는가, "A 또는 B"로 얼버무리는가
- 지어낸 이름(`찾지 않고 이름을 댔다`) 수
- `kafka.group_offsets gumi-mx-sink`를 내는가(결정적 읽기)

토큰 배율은 재지 않는다. 우리 병목은 토큰이 아니라 방향이다.

## 커밋 계획

1. **추출기 + 질의 + 사전 등록** (이 커밋)
2. `code sync`에 graphify(`--code-only --no-label`) + 오버레이 + `merge-graphs` 배선,
   `code status`의 커밋 대조와 귀속 못 한 엣지 수·토폴로지 갱신 권고, `code flow` CLI,
   `tools/local_case.py`에 그래프 생성
3. 브리핑 `<데이터 흐름>` 블록(증상·증거의 이름을 씨앗으로 이웃 2단계, 800자), `code.flow`
   action, 위 측정

## 검토 포인트

1. 사내 config의 키 경로가 기본 표와 다르면 `flow.name_paths`만 고친다 — 사내 확인 필요
2. 공유 레포에서 서비스를 못 가른 엣지가 많으면 토폴로지 `path`를 채우는 것이 답이다
3. graphify 사내 설치(`pip install graphifyy`, tree-sitter 휠) — 안 되면 리눅스 한 대에서
   만들어 `graph.json`만 옮긴다(백로그 ⑦)
