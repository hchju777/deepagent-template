# 11c — 데이터 흐름 그래프

> 상태: 진행 중 (커밋 2/3). 앞: [11a](step-11a-code.md). 뒤: 11b → 12a.

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

## 사내 config의 실제 모양 (커밋 2에서 반영)

사람 파트너가 확인해 준 모양이다(이름은 가려서):

```
infra.kafka.consumer.topic.{topic1, topic2, …}   ← 이 레포가 소비하는 토픽
infra.kafka.consumer.group_id                     ← 컨슈머 그룹 (레포당 하나)
infra.kafka.producer.topic.{topic1, …}           ← 생산하는 토픽
mongodb_collection.{이름: 값}                     ← infra 밖, 최상위
redis_key.{이름: 값}                              ← infra 밖, 최상위
  (일부 서비스는 값이 객체다: {"collection": 값, "ttl": 3} / {"key": 값, "ttl": 30})
```

값이 객체면 `FlowSource.field`(기본은 종류별 `FLOW_FIELDS`: collection→`collection`,
rediskey→`key`)에서 이름을 꺼낸다. 키 경로는 **맵의 키까지**(`mongodb_collection.alarm`)다 —
코드는 그 키로 꺼내고, `collection`·`key`는 어디에나 있어 토큰으로 못 쓴다. 문자열과 객체가
섞여 있어도 둘 다 뽑고 같은 이름은 하나로 접힌다(측정판은 dt-core가 문자열, dt-api가 객체).

객체 모양은 코드 쪽도 바꾼다 — 이름 꺼내기와 동사가 **다른 줄**에 온다
(`coll = cfg["mongodb_collection"]["alarm"]["collection"]` / 다음 줄 `mongo[coll].find(…)`).
그래서 흐름 추출의 grep만 `-C1`로 앞뒤 한 줄을 받고(`Hit.context`), 그 줄에 동사가 없으면
옆 줄의 동사를 쓴다. 다만 그 엣지는 **INFERRED**다 — 옆 줄의 동사가 다른 자원의 것일 수
있다. 리드의 `code.grep`은 그대로 0줄이다(증거가 세 배로 불면 400줄 상한이 먼저 찬다).
파서는 묶음(`--` 사이) 안에서 줄 번호로 앞뒤 한 줄만 붙인다 — 실제 `git grep -C1` 출력을
그대로 먹이는 테스트가 있다.

**`infra`는 레포당 하나다.** 레포에 서비스가 둘이면 둘이 공유하고 컨슈머 그룹도 같다.
이 사실이 설계를 둘 바꿨다.

- **config가 방향을 말한다.** `consumer.topic`에 있으면 소비, `producer.topic`에 있으면 생산.
  코드의 동사를 추정할 필요가 없다. 그래서 `FlowSpec.name_paths`(종류 → 경로)를
  `FlowSpec.sources`(경로 + 종류 + 관계)로 바꿨다. 관계가 있으면 config 층의 그 줄이 곧
  EXTRACTED 엣지다. 없는 것(`mongodb_collection`·`redis_key`)만 코드의 동사로 간다.
- **공유 레포에서는 config가 "이 레포의 누군가"까지만 안다.** 그 엣지는 레포 노드에
  붙는다(`attributed: repo`). 어느 서비스인지는 코드 줄이 가른다 — 그런데 토픽 키가
  소비·생산 양쪽 다 `topic1`이라 부모 키 하나(`topic`)로는 못 가르고, **조상 둘**
  (`consumer`/`producer` + `topic`)을 같은 줄에 요구한다. 전부를 요구하지 않는 이유는
  `kafka = cfg["infra"]["kafka"]`처럼 앞에서 묶으면 먼 조상은 그 줄에 없기 때문이다.

로컬 측정판(`tools/local_case.py`)을 이 모양으로 바꿨다. 그룹도 하나(`gumi-mx-core`)를
processor·sink가 공유하므로 lag만으로는 누가 멈췄는지 모른다 — 흐름 그래프가 필요한 이유가
측정판에도 그대로 있다.

## 배선 (커밋 2)

- `code graph` — 지금 체크아웃으로 그래프를 만든다. 네트워크 없음. `code sync`도 끝에 같은
  함수를 부른다(조립 한 벌).
- 만드는 순서: 서비스별 합친 config → 이름 → **레포마다** 배포 커밋에서 `git grep -n -F -C1`
  (패턴 20개씩 묶어서) → 오버레이(`flow.extract`). 레포마다 배포 SHA로 **`git worktree`를 잠깐 만들어** 거기서
  `graphify extract --code-only` + `cluster-only --no-label`을 돌리고 지운다. 작업 트리는
  안 건드리고 HEAD도 그대로다. 오버레이와 심볼 그래프를 id로 합친다.
- 산출물: `<output_dir>/graph/<gbm>-<fct>/{overlay,graph,meta}.json`. `meta.commits`는
  레포별 **실제 SHA**다(`main` 같은 참조는 움직인다). git에 안 들어간다.
- `code status`에 그래프 절이 붙는다: 만든 시각, graphify 버전, 노드·엣지, 서비스를 못 가른
  엣지 수. 배포 커밋과 다르면 **`⚠ 낡음`**. 없으면 만드는 법.
- `code flow` — 사람용. 이름 하나면 이웃, `--to`면 흐름 경로(쓰기→자원→읽기 방향), 없으면
  연결 많은 자원(god node의 우리 판).
- graphify는 `GRAPHIFY_BIN` → 실행 중인 python 옆(`.venv/Scripts`) → PATH에서 찾는다.
  없으면 오버레이만 만들고 그렇게 적는다. 조사는 돈다. 설치는 `requirements-graph.txt`(고정
  버전), 반입 절차는 README.
- 진행은 stderr에 경과 시간과 함께 찍는다. 사내 첫 실행이 몇 분을 말없이 돌자 "멈췄다"로
  읽혔다 — 원인은 이름마다 `git grep`을 따로 띄우던 것이었다(이름 100개·레포 3개면 600번,
  Windows 프로세스 비용). 지금은 레포마다 몇 번이다. 이때 리더의 400줄·2만 자 상한도 흐름
  재료에는 맞지 않아(`alarm` 같은 키 토큰은 큰 레포에서 수백 줄이 정상) 호출부가 상한을 따로
  주고, 그래도 잘리면 `meta.notes`와 `code graph` 출력에 "엣지가 빠졌을 수 있다"로 남긴다.

측정판에서 실제로 돌린 결과:

```
그래프 mx/gumi → …/output/graph/mx-gumi
     graphify 0.9.65 · dt-core ok · dt-api ok
     오버레이 노드 12 · 엣지 27 · 합친 그래프 노드 22 · 엣지 34
     권고:
       - config에 선언됐지만 코드 어디서도 안 쓰는 이름 1개 — line_state
       - 서비스를 못 가른 엣지 10개 (공유 레포 dt-core) — 토폴로지의 서비스 path를 채우면 코드 쪽은 갈린다
$ code flow processor --to sink
  processor —produces→ mx.alarm.main ←consumes— sink
  processor —produces→ mx.alarm.main   [INFERRED] processor/handler.py:L8
  sink —consumes→ mx.alarm.main   [INFERRED] sink/writer.py:L7
```

`Token cost: 0 input`이 GRAPH_REPORT에 찍히는 것을 테스트가 확인한다 — 라벨링 LLM 호출이
안 나갔다는 뜻이다.

## 사내 첫 실행에서 배운 것 (커밋 2 뒤)

실제 레포에 처음 돌려 보니 그래프가 문서·테스트만 가리키고 "안 쓰는 이름 13개", "못 가른
엣지 5374개", 서비스마다 "자원을 안 만진다"가 떴다. 원인은 셋이고, 셋 다 우리 가정이 틀렸다.

1. **서비스는 같은 코드다.** processor 5개, sink 2개가 한 레포의 같은 코드를 환경변수
   (`DEPLOY_DOMAIN` 등)로 역할만 바꿔 띄운다. "이 파일은 어느 서비스 것인가"는 질문이
   틀렸다 — 파일은 레포 것이고 서비스는 레포에 역할을 얹은 것이다. 그래서: config 엣지는
   **서비스별 합친 config**에서 grep 없이 만든다(역할마다 고른 층이 다르면 소비 토픽도
   다르게 나온다). 코드 엣지는 레포에 붙이고 `runs` 엣지(서비스→레포)가 다리다. 경로 탐색은
   자원만으로 먼저, 없으면 다리를 허용한다. "path를 채워라"는 권고는 지웠다.
2. **이름은 Enum과 공통 헬퍼 뒤에 있다.** config는 `"prodcheck_before_cur_worker_all":
   {"key": "BATCH:…", "ttl": 60}`, 코드는 `PROD_BEFORE_WORKER_ALL =
   "prodcheck_before_cur_worker_all"`(Enum), 매핑 표, `for …, storage_key in MAPPING:`,
   공통 함수의 `cfg["redis_key"][storage_key]`. 값도 조상 키도 같은 줄에 안 온다. 텍스트
   매칭이 닿는 것은 **Enum 정의 줄 하나**뿐이라 그것만 잡는다(따옴표 통째 키 규칙,
   `mentions`). 그다음 홉(Enum 멤버 → 표 → 루프 → 접근 함수)은 리드가 `code.grep`으로
   밟는 11b의 일이다. "안 쓰는 이름"은 권고에서 빼고 요약의 숫자("코드 줄에서 직접 못
   찾은 이름")로만 둔다.
3. **문서·테스트·주석이 코드 엣지의 60%였다.** `*.md`, `tests/`, `test_*.py`, `#`·독스트링
   줄은 히트에서 뺀다.
4. **근거 줄이 `_dev` 층이었다.** config 엣지의 근거를 레포의 config 파일 grep에서 첫 파일로
   집으니 알파벳순으로 앞선 `config/factories/_dev/…`가 찍혔다. 이제 `flow_names()`가 그
   서비스가 실제로 합친 층 원문에서 값이 적힌 줄을 찾는다(마지막에 이긴 층부터). grep은 그것이
   없을 때의 대체다.
5. **같은 이름의 토픽과 컬렉션.** 데이터를 토픽 이름과 같은 컬렉션에 넣는 서비스가 있어
   `consumes`와 `declares`가 같은 것을 가리키는 듯 보였다. `code flow`의 상세 줄은 자원에
   종류를 붙인다(`… [collection]`). 경로 한 줄(`render_path`)은 관계가 종류를 말하므로 그대로다.

그래서 11c의 그래프는 **config 층은 정확하게(서비스 단위, EXTRACTED), 코드 층은 첫 홉의
포인터까지만** 준다. 커밋 3의 브리핑 블록도 config 층만 싣는다. 부수로 잡은 버그: worktree
자리를 상대 경로로 `git -C <레포>`에 넘겨 대상 레포 안에 만들고 있었다(WinError 267).

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

1. 추출기 + 질의 + 사전 등록 ✅
2. 사내 config 모양 반영, `code sync`/`code graph`에 graphify + 오버레이 + 병합 배선,
   `code status`의 커밋 대조·권고, `code flow`, 측정판 갱신 ✅ (이 커밋)
3. 브리핑 `<데이터 흐름>` 블록(증상·증거의 이름을 씨앗으로 이웃 2단계, 800자, **config 층
   엣지만**), `code.flow` action, 위 측정

## 검토 포인트

1. 사내 config의 키 경로가 기본 표와 다르면 `flow.name_paths`만 고친다 — 사내 확인 필요
2. 공유 레포에서 서비스를 못 가른 엣지가 많으면 토폴로지 `path`를 채우는 것이 답이다
3. graphify 사내 반입 심사(`requirements-graph.txt`, 휠 32개 — 절차는 README) — 안 되면
   리눅스 한 대에서 만들어 `graph.json`만 옮긴다(백로그 ⑦). 심사 전까지는 오버레이만으로 간다
