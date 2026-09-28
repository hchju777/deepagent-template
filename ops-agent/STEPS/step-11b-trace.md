# 11b단계 — 코드 추적과 재계산 대조 (닫힌 action 레인)

> **목적**: 리드가 "이 끝점(또는 이 값)을 만드는 코드가 무엇을 읽는가"를 **함수 사슬과
> file:line로** 받고, "그 결과가 원천과 맞는가"를 **코드가 센 숫자로** 받는다. 11c가 "어느
> 서비스·어느 흐름"을 줬다면 11b는 "어느 함수 몇 줄"과 "맞나 틀리나"를 준다.
>
> 상태: 진행 중 (커밋 2/4). 앞: [11c](step-11c-flow.md) ✅. 뒤: 12a.
> 결정의 근거는 [decisions ⑰](decisions.md).

## 왜 "서브에이전트 3종"이 LLM 루프가 아닌가

로드맵의 11b는 "서브에이전트 3종(data_prober·code_tracer·recompute_verifier)"이고,
`PlanTask`의 주석은 "서브에이전트는 스스로 도구를 고른다"였다. **바꾼다.** 3종은 "누가
골랐나"가 아니라 **"어떤 종류의 증거인가"를 나누는 레인**이다. 리드가 등재표에서 action을
고르고, `role_for(action)`이 레인을 정하고, 프로브 실행기가 돌린다 — 10b·11a·11c의 배선
그대로에 표의 줄만 는다. 이유와 기각한 대안은 [⑰](decisions.md)에. 요약: 재현·감사가 되고,
사내 모델의 "예시를 채운다"는 성질과 맞고, `role`을 리드에게 맡겼다가 답 전체가 거부됐던
사고의 교훈이다. 잃는 것은 태스크 안의 적응 하나이고, 추적기가 막힌 지점을 file:line으로
돌려주면 리드가 다음 라운드에 `code.read`로 밟는다.

케이스마다 사람이 정하는 것은 없다. 표는 코드가 한 번, 사이트 config는 사람이 사이트당
한 번, 어느 읽기를 어떤 인자로 낼지는 리드가 라운드마다.

"어떻게 조합되는가"는 여기서도 코드가 통째로 풀지 않는다. 추적기가 200개 파일을 함수
서너 개로 좁혀 주고, 그 함수를 읽어 조합 논리를 해석하는 것은 리드다. 재계산 대조는 그
해석이 맞는지를 숫자로 확인하는 장치다.

## 무엇을 만드나

### 커밋 1 — 추적기 핵심 (`src/knowledge/trace.py`)

입력은 끝점 path(`/line/status`) 또는 심볼 이름(`get_line_status`)과 레포. 출력은 `Trace`:

```
chain:  [(file, line, qualname), …]                 핸들러부터 순서대로
reads:  [(resource_kind, name, grade, file, line)]  grade ∈ {확실, 추정}
gaps:   [(file, line, why)]                          못 따라간 지점
```

- **출발점.** 라우트 선언에서 핸들러를 찾는다. FastAPI 사내 모양(확인됨):
  `router = APIRouter(prefix="/line", tags=[…])` + `@router.get("/status", response_model=…)`
  + `async def get_line_status(service: LineServiceDep)`. 파일을 ast로 읽어 `APIRouter(` 호출의
  `prefix`와 데코레이터의 첫 문자열을 붙인다. 앱 조립부의 `include_router(x.router,
  prefix=…)`는 grep으로 찾아 앞에 붙이고, 라우터 변수를 못 이으면 `partial`로 표시한다.
  `@app.get`, `@router.api_route`, `add_api_route(`도 같은 취급.
- **호출 따라가기.** 깊이 6, 노드 200, 같은 레포 안. 번들의 graphify 심볼 그래프에 호출
  엣지가 있으면 뼈대로 쓰고, 없거나 끊기면 Python `ast`로 같은 모듈과 레포 안 import를
  잇는다. 메서드 호출(`service.get_line_status()`)은 이름으로 레포 안 정의 전부를 후보로 잡고,
  인자 주석(`LineServiceDep` → `Annotated[LineService, Depends(get_x)]`)에서 얻은 클래스로
  하나로 좁힌다. 못 좁히면 후보 전부를 `추정`으로 남긴다. `Depends(fn)`은 호출로 친다.
- **이름 수집.** 지나간 함수마다 11c의 `Name` 목록(collection·rediskey·topic 값), config
  키, Enum 멤버(`MEMBER = "config_key"` 정의를 레포당 한 번 스캔)를 찾는다. 리터럴 직접
  참조는 `확실`, Enum·config 키 경유는 `추정`. `getattr`·문자열 조립으로 고르는 자원은
  `gaps`에 남긴다.
- 던지지 않는다. 파싱이 실패한 파일은 `gaps`에 적고 계속 간다. 시계는 받지 않는다(순수 함수).

테스트는 가짜 레포 뭉치 하나로: prefix 조립, `include_router` 한 겹, 주석으로 좁히기, Enum
경유 `추정`, `getattr` gap, 깊이 상한, 문법 오류 파일.

커밋 1 ✅ — `src/knowledge/trace.py`. 계획과 다른 점과 덧붙인 점:

- 라우트 조립(prefix·include_router)은 11c 커밋 5의 `flow.routes_from_hits`가 이미 하므로 추적기는
  `Route`(파일·데코레이터 줄)를 받아 그 아래 `def`에서 출발한다. 끝점 path와 심볼 이름 둘 다 출발점이다.
  심볼 정의가 여럿이면 전부 출발점으로 삼고 읽기는 추정, gap에 "정의 N개"를 남긴다.
- graphify 호출 엣지를 뼈대로 쓰는 것은 **넣지 않았다.** 사내 번들의 `graph.json` 노드가 파일·줄까지
  담는지 아직 확인이 안 됐고(backlog), ast 리졸버가 먼저 있어야 비교 기준이 생긴다. 커밋 2에서 사내
  graph.json 하나를 보고 정한다.
- 해석은 같은 레포 안 Python `ast`다. 이름 호출은 같은 모듈 → import → 레포 `def 이름(` grep(모듈
  함수만), 메서드 호출은 `self`/`self.f = Cls()`/인자 주석(`Annotated[Cls, Depends(g)]`까지 벗김)/모듈
  별칭/`Cls().m()`으로 클래스를 좁히고, 못 좁히면 레포의 **메서드** 정의 전부를 후보로 따라간다.
  후보가 여럿인 길 아래의 읽기는 리터럴이어도 추정이고 gap에 "후보 N개"가 남는다. grep이 0건이면
  외부 라이브러리로 보고 조용히 지나간다(`redis.get`, `mongo[...]`).
- `Depends(g)`의 g는 본문 호출 뒤에 따라간다 — 사슬 앞쪽이 데이터 경로여야 리드가 먼저 읽는다.
- 읽기 등급: 리터럴 그대로면 확실, 따옴표 친 config 키 토큰이나 `alias_index`(대문자 상수·Enum
  정의 줄 `LINE_STATUS = "line_status"`)의 식별자 경유면 추정. 텍스트 매칭이라 함수 안의 문자열 조각이
  우연히 키 토큰과 같으면 추정 읽기가 하나 더 붙을 수 있다 — 등급이 추정인 이유가 그것이다.
- gap: `getattr`, 문법 오류 파일(gap 남기고 계속), 깊이 6·노드 200 상한(멈춘 자리와 못 따라간 호출
  이름). 문서·테스트 파일의 정의는 코드 엣지와 같은 필터로 뺀다.
- 파일 읽기와 grep은 `Source`(`read(path)`, `grep([리터럴…])`)로 주입한다. 테스트는 메모리 사전,
  운영은 커밋 2·3에서 배포 커밋의 git 리더(`show`·`grep -F`)로 감싼다.

### 커밋 2 — `code graph`가 끝점마다 돌린다

11c 커밋 5의 끝점 노드 전부에 추적기를 돌려 `endpoint reads resource` 엣지를 origin
`trace`, 등급과 file:line과 함께 박는다. 블록에는 `reads(추정): …`처럼 보인다. `code status`
요약에 "끝점 N개 중 자원까지 이어진 M개, 막힌 K개". 조사 중에는 안 돌린다(⑥ — 조사 중 그래프
갱신 금지). 비용은 끝점당 밀리초 단위(호출 그래프는 레포당 한 번 파싱).

커밋 2 ✅ — 계획과 다른 점과 덧붙인 점:

- 레포마다 `trace.Tracer` 하나가 파싱 캐시와 `def 이름(` grep 캐시, 별칭 색인을 끝점들 사이에서
  공유한다. 파싱 gap은 파일별로 두고 각 trace는 자기가 건드린 파일의 것만 가져간다 — 다른 끝점의
  문법 오류가 이 끝점의 결과로 새지 않는다. 같은 끝점을 두 번 돌리면 두 번째는 읽기도 grep도 0이다.
- `DeployedCode.source_for(repo)`가 추적기의 `Source`다 — 배포 커밋의 `show(whole=True)`(줄 수로 안
  자른다, 잘리면 파싱이 깨진다)와 `grep -F`. 던지지 않는다.
- 엣지는 `endpoint —reads→ resource`, origin `trace`, 확실→EXTRACTED, 추정→INFERRED, file:line. 끝점
  노드에 `traced`·`chain`·`gaps`를 문자열로 남긴다(커밋 3의 `code.trace`가 이걸 그대로 증거로 낸다).
- 블록의 끝점 줄에 `reads: …`와 `reads(추정): …`가 붙고(자원에 종류 표기), 끝점 씨앗의 2단계에 그
  자원 줄이 **코드 층의 `writes`/`reads`**와 함께 온다 — 사다리의 셋째 칸 "그 데이터를 쓰는 서비스"는
  config(declares)가 아니라 코드 층에 있다. 토픽이면 produces/consumes다.
- `summary`에 `endpoints_traced`(읽기 엣지가 하나라도 있는 끝점)와 `endpoints_blocked`(추적은 됐는데
  읽기 없이 gap만 남은 끝점). `code graph`의 진행줄과 권고, `code status`에 "자원까지 M · 막힘 K".
- flow.html은 추적 엣지를 **안 그린다** — 자원 열 안의 선이 되어 지도를 흐린다. 블록과 `code flow`가
  보여 준다.
- graphify 호출 엣지는 여전히 안 쓴다. 사내 `graph.json`의 graphify 노드 키 이름을 받으면 커밋 3에서
  정한다. 사내에서 볼 것: `code graph` 뒤 "끝점 추적: 자원까지 이어진 M개 · 막힌 K개" 두 숫자와,
  등재 항목 path로 `code flow <path>`를 쳤을 때 `—reads→` 줄이 몇 개 나오는지.

#### 커밋 2b — 좁히기 규칙을 사내 모양에 맞춘다

사내 첫 추적(커밋 2 직후, 끝점 156개): **자원까지 이어진 19 · 막힌 137**. gap은 "후보" 3,032개와
"깊이 상한" 156개(끝점 전부), 사슬 길이는 17~26. "후보" 이름은 `get` 하나가 2,564개. 사슬 하나를
읽어 보니 두 가지였다.

- **주석이 Protocol 포트다.** 핸들러의 `service: XDep`는 `Annotated[XPort, Depends(get_x)]`이고
  `XPort`는 `Protocol` — 그 메서드는 `...`뿐이라 사슬이 거기서 끝났다. 구현체가 무엇인지 아는
  자리는 provider `get_x()`의 `return XImpl(...)`뿐이다.
- **받는 쪽을 모르는 `.get(`이 옆으로 퍼진다.** 못 좁힌 호출은 레포의 `def get(` 후보 전부를
  따라갔고(커밋 1의 규칙), 사내 레포에는 `get`이 HTTP 클라이언트·캐시 클라이언트·설정 로더·배포
  설정에 다 있다. 끝점마다 넷을 다 밟고 그 아래서 또 `get`을 만나 깊이 예산 6을 잡음이 먹었다.

같은 실행의 `graph.json`에는 graphify 노드·엣지가 없었다(링크 relation이 전부 우리 것) — 그 빌드에서
graphify가 건너뛰어졌거나 실패한 것이다. 결정: **11b는 ast만 쓴다.** graphify 호출 그래프는 이름
기준이라 받는 쪽의 타입을 못 좁히므로 있어도 이 문제를 못 푼다. 좁히기 규칙(전부 `trace.py`):

1. **포트를 뚫는다.** 좁힌 클래스가 `Protocol`/`ABC`거나 메서드가 추상(`...`·`pass`·
   `raise NotImplementedError`)이면 구현체로 간다 — 주석의 `Depends(provider)`가 돌려주는 클래스
   (확실) → `class X(Port)`로 상속한 클래스들(하나면 확실, 여럿이면 전부 추정 + gap 하나) → 같은
   이름의 메서드 전부(추정 + gap).
2. **필드와 지역 변수를 더 좁힌다.** `self.x = param`은 `__init__`의 주석으로, 클래스 본문 `x: Cls`,
   같은 함수의 `v = provider()`는 provider의 반환 클래스로. 부모 클래스의 메서드·필드도 본다(사내
   저장소는 공통 메서드가 `BaseRepo`에 있다).
3. **받는 쪽 미상이면 안 따라간다.** 후보가 여럿이면 이름당 gap 하나(`get: 받는 쪽 미상, 후보 N개 —
   안 따라간다`)만 남긴다. 리드가 그 줄을 `code.read`로 보면 된다. 인자에 주석이 없는 경우만 전처럼
   후보 전부를 추정으로 따라간다. `router.get("/x")` 같은 **데코레이터는 훑지 않는다** — 등록이지
   호출 경로가 아니고, 훑으면 외부 클래스 싱글턴의 `.get`이 끝점마다 gap으로 남는다.
4. **읽기를 더 넓게 본다.** 실행 시점 클래스와 부모들의 본문 상수(`collection = "…"`, 사슬의 등급)와
   함수가 참조하는 모듈 상수표(`MAPPING = [(Enum.A, Keys.A), …]`, 추정). 리터럴은 **따옴표 안**에
   있어야 읽기다 — 템플릿 `line:{id}`의 리터럴 `line:`이 시그니처 `line: str`과 겹쳤다.

테스트는 지어낸 이름의 픽스처(포트·provider·부모 클래스·상수표·`def get(` 셋)로 다섯 개, RED 스윕
열 개. 사내에서 볼 것은 같은 두 숫자 — `code graph` 뒤 "끝점 추적: 자원까지 이어진 M개 · 막힌 K개"와
gap 종류별 수. 137이 크게 줄지 않으면 다음 사슬 하나를 다시 읽는다.

사내 두 번째 추적(커밋 2b 뒤, 끝점 156개): **자원까지 137 · 막힌 15** — 19/137에서 뒤집혔다. 남은
gap은 "받는 쪽 미상 — 안 따라간다" 533(그중 `__init__` 185), "구현체를 못 찾아 같은 이름 N개" 175+63,
`getattr` 82, 깊이 상한 288. 사슬은 28~30걸음이 64개 — 걸음의 대부분은 저장소 부모 클래스의 내부 헬퍼와
로거 래퍼다(읽기로 이어지지 않는 가지를 어떻게 보여 줄지는 커밋 3).

#### 커밋 2c — 상속하지 않는 구현체와 `super()`

gap 원문과 클래스 선언을 보니 셋이었다.

- **포트와 구현체가 같은 이름이다.** `application/…/ports.py`의 `XRepository(Protocol)`와
  `infrastructure/…`의 `XRepository(부모 저장소)`. 상속 관계가 없으니 "상속한 클래스" grep은 0개였고,
  같은 이름 메서드 전부로 떨어져 175번 퍼졌다.
- **서비스 포트는 접미사만 다르다.** `XServiceProtocol` ↔ `XService`. provider가 `return XService(...)`가
  아니라 컨테이너에서 꺼내 주므로 반환 클래스도 없다.
- **`__init__: 후보 61개`는 `super().__init__()`이다.** 받는 쪽이 `super()` 호출이라 못 좁혔다.

규칙(`trace.py`): 포트에 상속 구현체가 없으면 ① 다른 모듈의 **같은 이름 클래스**(Protocol 아닌 것) —
하나면 확실, 여럿이면 추정 + gap. ② 없으면 **이름 규약** `XProtocol`/`XPort`/`XInterface`/`XABC`/
`AbstractX`/`IX` → `X` — 추정 + gap "이름 규약으로 골랐다". ③ 그 다음이 전과 같은 같은 이름 메서드
전부. `super().m()`은 부모 중 m을 가진 첫 클래스로 가되 실행 시점 클래스는 자식 그대로다(자식의
`collection = …`을 잃지 않는다). 부모가 외부면 조용히. ②만 코드가 보증하지 않는 판단이라 확실로
올리지 않는다.

사내에서 볼 것: 같은 두 숫자와, gap 분포에서 `__init__`과 "구현체를 못 찾아"가 사라졌는지.

### 커밋 3 — action과 레인

- `code.trace` — `("code", "trace", ("target",), ("service",))`. `DeployedCodePort.trace`
  추가(추상, `tests/domain/test_ports.py`가 표면을 단정). 결과는 증거 한 건:

  ```
  t-4.e1 [code_tracer] /line/status
    api/routers/line.py:12 get_line_status
    → api/services/line.py:40 LineService.get_line_status
    → api/repos/line.py:22 find(line_state [collection])   확실
    못 따라감: api/services/line.py:47 getattr(...)
  ```

- `recompute.count`·`recompute.sum` — `("recompute", "count", ("collection", "filter",
  "expect"), ())`, sum은 `field` 추가. `expect`는 `{"evidence": "t-2.e1", "path":
  "response.items[0].alarm"}` — 앞선 증거 안의 값을 가리킨다. 실행기는 mongo 어댑터로
  세고(읽기만), 인용된 증거에서 기대값을 꺼내 `{recomputed, expected, match}`를 증거로
  남긴다. 값이 그 path에 없으면 `error`("기대값을 못 찾았다")이지 불일치가 아니다.
  **`expect.evidence`는 코드가 `input_evidence_ids`에 강제로 넣는다**(`_sanitize_new_task`,
  규율 4) — 그래야 select 게이트가 그 증거가 생긴 뒤에만 돌린다. 리드는 코드를 읽고
  collection·filter를 채우는 판단만 한다. Redis·Kafka 판은 필요가 보이면 그때.
- `role_for`: `code.*` → `code_tracer`, `recompute.*` → `recompute_verifier`, 나머지
  `data_prober`. `PlanTask`의 "서브에이전트는 스스로 도구를 고른다" 주석을 고친다.
- 브리핑 예시: `rest.query` 증거가 생긴 뒤 integrate 예시에 `code.trace`(target: "위 증거에서
  본 끝점 path")가, trace 증거가 생긴 뒤 `recompute.count`(expect.evidence: "위 trace가 나온
  rest 증거 id")가 나온다. 예시가 곧 출력이므로(10b) 여기서 사다리의 다음 칸을 보여 준다.
- `case trace`는 증거 내용을 안 찍는 규칙 그대로 — 사슬의 file:line은 찍어도 되지만 읽은
  자원의 **값**은 안 찍는다.

### 커밋 4 — 측정 (결과를 보기 전에 적는다)

측정판은 11c와 같다(로컬 가짜 레포 dt-core·dt-api, 심은 고장 = sink 컨슈머 정지). 11c 커밋 6의
숫자가 기준선이다. 로컬 haiku 루프, 11b 켜고 끄고 각 3회.

| # | 질문 | 기대 |
|---|---|---|
| T1 | 출발 REST 항목이 있는 케이스에서 리드가 `code.trace`를 내는 첫 라운드 | r1 이내 |
| T2 | api 핸들러에서 컬렉션까지 사슬이 이어지는가, 등급은 | 이어진다, `확실` |
| T3 | 고장을 심었을 때 `recompute.count`가 불일치를, 안 심었을 때 일치를 내는가 | 둘 다 맞아야 |
| T4 | 정답 부품(sink)을 처음 짚는 라운드 | 11c 기준선보다 앞 |
| T5 | 지어낸 이름 수 | 기준선 이하 |
| T6 | `getattr` 간접 참조 픽스처에서 추적기가 gap을 남기고, 리드가 그 자리를 `code.read`로 내는가 | 남긴다 / 낸다 |
| T7 | 최종 가설이 부품 하나를 짚는가, "A 또는 B"인가 | 하나 |

토큰 배율은 재지 않는다(11c와 같은 이유 — 병목은 방향이다).

## 범위 밖

- LLM 서브에이전트 루프. 필요해지면 `PlanTask.action=None`인 태스크로 레인 하나만 연다.
- 레포를 건너는 호출(HTTP로 다른 서비스를 부르는 것) — 그건 그래프의 `serves` 엣지가
  이미 잇는다.
- 런타임 관측(Mongo 프로파일러·접근 로그·트레이스) — 대상에 켜져 있어야 읽을 수 있고 켜는 건
  쓰기다. 켜져 있으면 정적 추적의 검증 용도로만.
- `response_model` 스키마 → 응답 필드 → `items_all_zero.counts` 잇기 — 메모만. 필드 수준
  지식이 필요해지면 그때.
