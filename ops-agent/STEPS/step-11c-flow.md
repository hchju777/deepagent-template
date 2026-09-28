# 11c — 데이터 흐름 그래프

> 상태: 진행 중 (커밋 3a/6 — 다음은 3b 측정). 앞: [11a](step-11a-code.md). 뒤: [11b](step-11b-trace.md) → 12a.

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
- **사람용 산출물**(같은 번들 디렉터리): `flow.html`은 오버레이를 우리가 직접 그린 한 장이다.
  외부 참조 0(graphify의 `graph.html`은 vis-network를 unpkg.com에서 받아 사내망에서 빈 화면).
  세 열 흐름 배치, 레포마다 색 하나(이름순 고정), 클릭 초점과 근거 패널, 관계·종류 필터,
  `#node=`·`#repo=` 링크. 다크 고정. graphify가 있으면 레포별 `reports/<레포>/GRAPH_REPORT.md`
  (worktree와 함께 지워지던 것)와 합친 그래프의 `wiki/`(`graphify export wiki`, md 묶음)도 남긴다.
- graphify는 `GRAPHIFY_BIN` → 실행 중인 python 옆(`.venv/Scripts`) → PATH에서 찾는다.
  없으면 오버레이만 만들고 그렇게 적는다. 조사는 돈다. 설치는 `requirements-graph.txt`(고정
  버전), 반입 절차는 README.
- 진행은 stderr에 경과 시간과 함께 찍는다. 사내 첫 실행이 몇 분을 말없이 돌자 "멈췄다"로
  읽혔다 — 원인은 이름마다 `git grep`을 따로 띄우던 것이었다(이름 100개·레포 3개면 600번,
  Windows 프로세스 비용). 지금은 레포마다 몇 번이다. 이때 리더의 400줄·2만 자 상한도 흐름
  재료에는 맞지 않아(`alarm` 같은 키 토큰은 큰 레포에서 수백 줄이 정상) 호출부가 상한을 따로
  주고, 그래도 잘리면 `meta.notes`와 `code graph` 출력에 "엣지가 빠졌을 수 있다"로 남긴다.

## 배선 (커밋 3 — 그래프를 리드에게)

- 브리핑의 `<데이터 흐름>` 블록(frame·integrate 둘 다, `{flow}` 자리). 씨앗은 증상·증거·가설
  본문에 **글자 그대로** 나온 그래프 이름(세 글자부터, 서비스 먼저). 씨앗의 config 층 이웃
  1단계, 그다음 씨앗 자원에 닿은 서비스의 토픽(2단계). 씨앗이 없으면 토픽 골격(누가 내고
  누가 받나). 800자에서 끊고 끊었다고 적는다. 머리에 "config에서 뽑은 배선이다, 실제 동작은
  프로브로 확인하라"를 박는다 — 그래프는 "어디"이고 프로브가 "무엇"이다.
- `code.flow(name)` — 리드가 도중에 더 물을 때. config 엣지 먼저, 코드 엣지는 확신 순으로
  첫 홉의 포인터까지(40줄 상한). 그래프가 없는 조사에서는 목록에서 뺀다.
- **배포 커밋과 같은 그래프만 싣는다.** 없음과 낡음은 같은 취급(None)이다. 조사 시작 때
  `_code_if_ready`가 번들의 `meta.commits`를 실제 SHA와 대조한다.

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

## 사내 첫 조사 trace에서 배운 것 (커밋 3a 뒤)

블록은 사내에서 의도대로 렌더됐다(접기 `레포{…5}`, `공유 config`, `+4줄` 절단,
`code.flow` 등재). 그런데 조사는 r0부터 r6까지 끝점을 만드는 코드에 한 번도 가지 않았다.
trace를 읽으니 원인은 모델이 아니라 우리 쪽에 있었다.

1. **frame 규칙이 블록과 모순된다.** `investigate-frame.md`에 "이번 라운드는 무엇이 있는지
   찾는 라운드다 — 이름을 인자로 받는 읽기는 다음 라운드에 낸다"와 "지금은 데이터만 읽을 수
   있으므로 `data_prober`를 써라"가 아직 있다. 둘 다 11a·11c 이전 문장이다. 블록이 이름을
   주는데 규칙은 모른다고 하라 하고, 예시는 발견 3종이다. r0 태스크가 전부 예시와 같은
   action이었던 것은 시킨 대로 한 것이다(10b의 "예시가 곧 출력이다").
2. **끝점 층이 없다.** 그래프에 서비스·토픽·컬렉션·키는 있지만 "이 REST 경로를 누가
   서빙하고 무엇을 읽어 만드는가"가 없다. 증상이 끝점 응답의 값인데 그 끝점에서 시작할
   발판이 없으니 리드는 증상 문장의 단어로 컬렉션을 찍었다.
3. **씨앗이 틀렸다.** 증상 문장의 응답 필드 이름이 같은 이름의 컬렉션에 글자로 걸려
   그 컬렉션이 씨앗이 됐다. 글자 일치는 필드와 자원을 못 가른다.
4. **순찰 케이스는 출발점을 이미 안다.** `CaseRecord.check` → `CheckConfig.probes` →
   `ProbeSpec(rest.query, entry)` → `RestEntry.path`가 전부 config다. 지금은 `Case`에
   symptom 문장만 넘어가서 리드가 그걸 못 본다. 사람이 끝점 별칭 표를 적을 일이 아니다 —
   사람 케이스는 13단계 접수가 "어느 화면·어느 API"를 묻는다.

지킬 것(⑮): 프롬프트에 사내 어휘를 넣지 않는다 — 출발점 줄도 config에서 **생성**한다.
그 두 케이스로 튜닝하지 않는다 — 위 넷은 어느 사이트에서도 같은 일반 결함이다.

"어떻게 조합되는가"는 그래프가 못 담는다. 끝점에서 DAO까지 함수 사슬과 읽는 자원을 뽑아
**어느 함수 몇 줄을 읽을지**를 리드에게 주는 것이 [11b](step-11b-trace.md)이고, 조합
논리를 읽고 해석하는 것은 리드다. 11b가 더하는 것은 재계산 대조 — 조합 결과가 원천과
맞는지를 코드가 숫자로 확인한다.

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
   `code status`의 커밋 대조·권고, `code flow`, 측정판 갱신 ✅
3. 브리핑 `<데이터 흐름>` 블록(config 층만, 800자), `code.flow` action ✅ (3a)
   — **3b: 위 측정을 프롬프트를 고치기 전에 돌린다.** 사전 등록의 문항은 블록을 겨냥했으므로
   블록만 켜고 끄고 잰다. 이게 4·5의 기준선이다.
4. **출발점과 사다리.** `Case`에 `check`·`target`을 실어 `case_block`이 접수 경로 한 줄을
   config에서 생성한다("순찰 점검 X · 프로브 Y · rest.query Z · GET /path"; 사람 케이스는 없음).
   frame 규칙에서 "이름을 모른다"·`data_prober` 문장을 빼고 사다리 규칙을 넣는다:
   증상이 관찰된 자리(끝점·화면·리포트) → 그 자리를 만드는 서비스와 코드 → 그 코드가 읽는
   데이터 → 그 데이터를 쓰는 서비스, 한 홉씩 상류로; 증상 문장의 단어가 자원 이름과 같다고
   그 자원으로 바로 뛰지 않는다. frame 예시를 사다리 모양으로: 출발 REST 항목이 있으면
   ① 그 항목 `rest.query`(재현) ② 그 path를 서빙 서비스에서 `code.grep` ③ 발견 수; 씨앗만
   있으면 씨앗 서비스의 `code.config`·`code.flow`가 앞; 아무것도 없으면 지금과 같다.
   integrate에 "확인된 홉의 상류로 한 홉" 한 줄. 블록 조정: 2단계는 produces·consumes만,
   2단계에서도 접기, 관계당 8개 + "외 N개", 머리말 축소. 씨앗 순위: 접수 경로의 항목·서비스
   먼저, 본문에서 글자로 걸린 자원은 뒤에 개수 제한.
5. **끝점 노드(자동).** 출처 둘 — `rest.entries`의 path 전부, api 레포의 라우트 선언.
   사내 모양은 FastAPI: 같은 파일의 `APIRouter(prefix="/line")` + `@router.get("/status")`을
   ast로 붙여 `/line/status`; 앱 조립부의 `include_router(…, prefix=…)`가 있으면 앞에 붙이고
   못 이으면 `partial`. `service serves endpoint` 엣지에 file:line. `flow_text`에서 끝점
   씨앗은 "serves: api [service]" 줄이 맨 앞, `code.flow(path)`가 끝점에도 답한다.
   flow.html에 종류 하나. `code graph` 권고에 "끝점 N개 중 등재 M개, 서빙 서비스를 못 찾은
   K개". `endpoint reads resource` 엣지는 여기서 만들지 않는다 — 11b의 추적기가 채운다.
6. **재측정.** 같은 8문항·같은 지표를 사다리 켜고 다시 잰다. 3b 기준선과 나란히 적고
   README의 11c를 ✅로 닫는다.

## 검토 포인트

1. 사내 config의 키 경로 — 커밋 2에서 사내 모양을 확인해 반영했다
2. ~~공유 레포에서 서비스를 못 가른 엣지가 많으면 토폴로지 `path`를 채우는 것이 답이다~~ —
   틀린 가정이었다("사내 첫 실행에서 배운 것" 1). 서비스는 같은 코드이고 config 층이 가른다
3. graphify — 사내에 설치됐다(사내 `requirements.txt`). 이 리포에서는 `requirements-graph.txt`로
   분리 유지, 없으면 오버레이만
4. 라우트 선언 모양 — 확인됐다(FastAPI, 커밋 5). `include_router`의 prefix 사용 여부는 추적기가
   두 경우를 다 다루므로 안 물어도 된다
