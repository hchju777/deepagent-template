# 11b단계 — 코드 추적과 재계산 대조 (닫힌 action 레인)

> **목적**: 리드가 "이 끝점(또는 이 값)을 만드는 코드가 무엇을 읽는가"를 **함수 사슬과
> file:line로** 받고, "그 결과가 원천과 맞는가"를 **코드가 센 숫자로** 받는다. 11c가 "어느
> 서비스·어느 흐름"을 줬다면 11b는 "어느 함수 몇 줄"과 "맞나 틀리나"를 준다.
>
> 상태: 3a·3b·커밋 4(측정)까지. 5a(서비스 추적, 사람이 적는 `entries`)는 **되돌렸다** — 이유와 대체 설계는
> [11d](step-11d-index.md)(전역 심볼 인덱스). 앞: [11c](step-11c-flow.md) ✅. 뒤: 11d → 12a.
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

사내 세 번째 추적(커밋 2c 뒤, `tools/trace_stats.py`로): 137/15 그대로. `__init__` 185개와 "구현체를 못
찾아" 175개는 사라졌고 서비스 포트는 이름 규약으로 골랐다고 적혔다. 그런데 **읽기 엣지 611개 중 확실이
3개** — 서비스 포트를 규약으로 고른 추정 하나가 그 아래 저장소·컬렉션 읽기 전부를 추정으로 만들었다.
등급이 정보를 잃은 것이다. 막힌 끝점 19개(그중 4개는 gap도 없다 — 자원을 안 읽는 핸들러)는 HTTP
클라이언트 안에서 깊이 상한(다른 서비스로 프록시하는 끝점 — 바깥으로 나가는 REST 호출은 아직 읽기로
안 센다, 후속), 이름 규약이 못 맞춘 포트(대소문자가 다르다), getattr.

#### 커밋 2d — 포트의 구현체는 구조로 정한다

Protocol은 언어 정의가 구조적이다(PEP 544): 선언한 메서드를 다 가진 클래스가 곧 그 포트의 구현체다.
상속도 이름도 필요 없다. 그래서 순서를 바꾼다 — provider 반환 클래스 → 상속한 클래스 → **선언한
메서드를 전부 가진 클래스**(하나면 확실, 둘셋이면 추정 + gap, 넷 이상이면 `def get(` 하나짜리 포트다
— 안 따라간다) → 같은 이름 클래스·이름 규약(구조로 못 맞춘 것이니 **추정** + gap) → 같은 이름 메서드
전부. 후보 파일은 선언 중 가장 긴 메서드 이름으로 grep한다. 같은 이름 메서드 후보에서 포트의 선언
(`...`뿐인 것)은 뺀다 — 선언은 구현체가 아니다.

덧붙인 것 둘. ① `Read.via` — 이름이 코드에 어떻게 있었나(`literal`·`key`·`alias`)를 읽기 엣지에 싣는다.
등급이 추정인 이유가 "경로가 불확실해서"인지 "이름이 상수·Enum 경유라서"인지 가르기 위해서다
(`trace_stats.py` 2번 줄이 등급×출처로 나온다). ② 이름조차 못 찾은 받는 쪽(`mongo[...]`·외부 객체)은
후보가 하나여도 안 따라간다 — 커서의 `.count()`가 레포의 유일한 `def count(`로 잘못 이어졌다.

사내에서 볼 것: `trace_stats.py`의 2번 줄. `EXTRACTED/literal`이 늘었으면 경로 문제였고, 여전히
`INFERRED/alias`·`INFERRED/key`가 대부분이면 이름의 출처가 상수·config 키라는 뜻이라 등급 규칙 자체를
다시 본다(상수 경유를 추정으로 둘 이유가 있는가).

사내 네 번째 추적(커밋 2d 뒤): 137/13. 읽기 611개 = `INFERRED/key` 510 · `EXTRACTED/literal` 75 ·
`INFERRED/literal` 26. 확실이 3에서 75로 — 경로 문제는 맞았다. 그런데 남은 둘이 크다. ① 읽기의 대부분이
**config 키 토큰** 매치인데, 추적기의 키 판정은 "따옴표 안에 토큰이 있으면"이 전부였다 — 11c가 grep
판정에서 이미 버린 기준이다. 저장소 부모의 헬퍼(`_build_schema`·`_resolve_column`…)에 있는 필드명
`"summary"`·`"history"` 같은 한 단어가 config 키 토큰과 겹쳐, 그 헬퍼를 지나는 끝점마다 가짜 읽기가
붙었다. 137이라는 수 자체가 부풀려졌을 수 있다. ② 같은 이름 저장소(`XRepository`)가 구조로 안 맞아
추정으로 떨어진 gap이 175 — 포트가 선언한 메서드 중 무엇이 구현에 없는지를 gap이 말해 주지 않아 다음
판단(엄격함을 낮출지)을 못 했다.

#### 커밋 2e — 키 토큰 판정을 11c 기준으로, 이름으로 고른 구현체에는 없는 메서드를 적는다

- 키 토큰 읽기는 **줄 단위**로, 조상 키(`required_tokens`)가 같은 줄에 다 있거나 여러 조각짜리 토큰이
  통째로 따옴표 안일 때만이다(`flow._quoted_whole`과 같은 기준). `cfg["mongodb_collection"]["history"]`는
  읽기이고 `{"history": 0}`은 아니다.
- 같은 이름 클래스·이름 규약으로 고른 구현체의 gap에 `포트 메서드 N개 중 없는 것: a, b`를 적는다.
  사내에서 이 줄을 보고 결정한다: 없는 것이 죽은 선언 한둘이면 "선언의 대부분 + 호출한 메서드를 가진
  같은 이름 클래스"를 확실로 올릴지, 부모의 `raise NotImplementedError` 템플릿이면 추상 판정을 손볼지.

사내에서 볼 것: `trace_stats.py` 1·2·4번 줄. 1번의 137이 얼마나 내려가는지(가짜 읽기가 빠진 뒤의 진짜
수), 2번의 `INFERRED/key`, 4번에서 "같은 이름 클래스" gap의 원문 한 줄(없는 메서드 이름).

사내 다섯 번째 추적(커밋 2e 뒤): 137/19 그대로, `INFERRED/key` 510 → 401. 키 판정을 조여도 자원에 닿는
끝점 수가 안 줄었으니 137은 부풀려진 수가 아니었다 — 남은 401은 조상 키가 같은 줄에 있는 진짜 config 키
읽기다. 그리고 8번 줄이 답을 줬다: **포트가 선언한 8개 중 구현에 없는 것은 1개** — 죽은 선언이다. 그
하나 때문에 저장소 호출 175개가 추정으로 떨어져 있었다.

#### 커밋 2f — 구조 부합은 "호출한 메서드 + 선언의 절반 이상"

호출 `x.m()`을 받는 것은 `m`을 가진 클래스이지 포트 선언 전부를 가진 클래스가 아니다. 그래서 구조
부합의 기준을 바꾼다: 후보는 `def m(`을 가진 클래스, 그중 포트가 선언한 메서드의 **절반 이상**을 가진
것이 구현체다(하나면 확실, 둘셋이면 추정 + gap, 넷 이상이면 안 따라간다 — 전과 같다). 절반 미만이면 `get`
같은 이름의 우연이라 보고 이름 기반 fallback(추정 + 없는 메서드 목록)으로 간다. PEP 544의 "전부"는
살아 있는 코드의 포트 선언만큼 정확하지 않다 — 절반은 판단이고, 문서와 테스트에 그렇게 적는다.

사내에서 볼 것: `trace_stats.py` 2·4번 줄. "같은 이름 클래스" gap 175와 "이름 규약" 63이 사라지고
`EXTRACTED/literal`이 늘어야 한다. `INFERRED/key` 401은 남는다 — 이름이 config 키 경유라는 사실 자체는
추정이 맞는지가 다음 질문이고, 그건 커밋 3에서 리드가 등급을 어떻게 쓰는지와 함께 정한다.

사내 여섯 번째 추적(커밋 2f 뒤): **137/14**. 읽기 = `INFERRED/key` 401 · `EXTRACTED/literal` 83 ·
`INFERRED/literal` 19. "같은 이름 클래스" 175와 "이름 규약" 63이 사라졌다 — 포트 해석은 전부 확실이 됐다.
남은 gap은 "받는 쪽 미상 — 안 따라간다" 348, `getattr` 82, 저장소 부모 헬퍼와 Mongo 클라이언트 안의
깊이 상한(60·60·60·57)뿐이다. 추적기 라운드는 여기서 닫는다(2b~2f, 19/137 → 137/14, 확실 3 → 83).

열어 두는 질문 하나: `INFERRED/key` 401. 조상 키가 같은 줄에 있는 config 키 읽기(`cfg["mongodb_collection"]
["summary"]`)는 경로가 확실해도 이름이 키 경유라는 이유로 추정이다 — 11c의 grep 판정(리터럴=EXTRACTED,
키 토큰=INFERRED)과 같은 규칙이다. 리드에게 "확인하라"고 할 만큼 불확실한가? 코드는 그 키를 정확히
가리키고, 값은 config가 정한다. 등급을 올릴지, 등급은 두고 표시만 `reads(config키)`로 가를지는 커밋 3에서
리드가 등급을 어떻게 쓰는지와 함께 정한다(스펙의 EXTRACTED/INFERRED 뜻을 건드리므로 사람과 상의).

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

커밋 3은 둘로 나눴다 — **3a** `code.trace`와 레인, **3b** 재계산 대조와 예시. 3a의 출력 모양을 사내에서
한 번 보고 3b의 예시를 그 위에 얹는다.

#### 커밋 3a ✅ — 사슬을 보여 주는 모양, `code.trace`, 레인

- **사슬은 트리다.** `Step.parent`(부모 걸음의 색인)와 `Read.step`(읽기가 난 걸음)을 추적기가 남기고
  오버레이에 `chain_parent`·엣지 `step`으로 실린다. 평평한 BFS 목록으로는 "읽기로 이어진 가지"를 골라낼
  수 없었다 — 사내 사슬은 28~30걸음이고 대부분이 저장소 부모의 헬퍼와 로거다.
- **`flow.trace_lines`** — 읽기가 난 걸음의 조상만 남긴 트리. 읽기는 걸음 옆에 `확실`·`config키`·`추정`
  (2f 뒤의 1안: 등급은 그대로, 표시만 가른다 — `read_mark`), gap은 셋 + 개수, 꼬리에 "걸음 N 중 M".
  읽기가 하나도 없는 끝점은 앞 걸음 넷과 gap. 블록의 끝점 줄도 `reads:`/`reads(config키):`/`reads(추정):`.
- **`code.trace(endpoint)`** — `DeployedCodePort.trace` 추상(표면 테스트가 자동으로 지킨다), 구현은
  오버레이를 읽는다(조사 중 재추적 없음, ⑥). 그래프가 없으면 `code.flow`처럼 목록에서 숨긴다. 끝점이
  없으면·추적이 안 됐으면 실패로 답하고 다음 손(`code.grep`→`code.read`)을 적는다.
- **레인** — `role_for`: `code.*` → code_tracer, `recompute.*` → recompute_verifier, 나머지 data_prober.
  `PlanTask`의 "서브에이전트는 스스로 도구를 고른다" 주석을 ⑰에 맞게 고쳤다.

- **`python -m src code trace <path>`** — 사람이 리드가 받는 사슬을 그대로 본다(`code flow`의 짝).

사내에서 볼 것: `code trace`로 사슬 하나(저장소 헬퍼·로거 걸음이 빠졌는지, 몇 걸음인지), 그리고 조사
하나에서 리드가 `code.trace`를 부르는지와 다음 라운드의 `code.read`가 사슬의 file:line을 그대로 쓰는지.

#### 3a 보정 ✅ — 사내 `code trace` 한 번이 잡아낸 둘

`/summary` 끝점 하나를 `code trace`로 본 사람(그 코드를 짠 사람)이 두 줄을 틀렸다고 했다.

- **`alarm [collection] 확실`** — 그 줄은 배지 상태값 `alarm`·`caution`·`normal`을 세는 자리였다. 컬렉션의
  config 값이 `alarm`이라 따옴표 안의 `"alarm"`이 전부 컬렉션 읽기로 잡혔고, 리터럴이라 확실이었다.
  11c의 grep 판정(코드 층 `reads`/`mentions` 엣지)도 같은 약점이었다. 규칙: **한 단어 리터럴**(`_ : . -`가
  없는 것)은 같은 줄에 읽기/쓰기 동사(`flow.direction`)가 있어야 자원이다 — grep 판정과 추적기 둘 다.
  여러 조각짜리(`alarm_events`, `mx.alarm.main`)는 전처럼 어디 있든 잡는다. 11c가 키 토큰에 세운 "한
  단어는 조상 키 없이는 안 받는다"와 같은 정신이다.
- **`GUMI [rediskey]`** — 사내 관례는 `redis_key.prefix` + `:` + 값이 실제 키다. 이름 추출이 그 절의 모든
  leaf를 완전한 키로 봐서 접두사가 키 자원으로 떴고, **더 나쁘게** 진짜 키들이 접두사 없이 리드에게
  나가고 있었다 — 리드가 그대로 `redis.get`을 내면 빈 결과가 오고 그건 "없다"로 읽힌다(아직 사내에서
  그렇게 빈 결과가 온 적은 없었다고 한다). 지식 선언으로 푼다: `flow.sources` 항목에 `prefix`(접두사
  leaf 이름)와 `join`(기본 `:`). 접두사 leaf는 자원이 아니고, 나머지 leaf의 `Name.value`는 완전한 키,
  `Name.code_value`는 코드에 실제로 있는 값이다 — 라벨·브리핑·`redis.get`은 전자, grep·별칭 색인·
  추적기는 후자를 쓴다. `knowledge/topology/mx.json`과 스키마 기본값에 선언했다. 선언이 없거나 접두사
  leaf가 없는 층이면 전처럼 동작한다.

보정 뒤 같은 끝점을 다시 보니 두 줄은 빠졌는데 **읽기가 하나도 없었다** — 그 서비스는 redis 키 여럿을 읽는데,
보정 전에도 진짜 키는 없었고 접두사 leaf 하나가 redis의 유일한 흔적이었다. 코드를 보니 키 토큰을 **코드 쪽
템플릿**으로 조립한다: `cfg.get("redis_key", f"<머리>_{name}")`. 토큰이 통째로 없으니 키 판정이 못 본다.
규칙 하나 더: 조상 키가 같은 줄에 있고 `f"<머리>{…}"`가 있으면(머리는 여러 조각짜리), 그 머리로 시작하는 키
전부가 읽기(추정, config키)다 — config 값 템플릿(`alarm:stats:{line}` → `alarm:stats:`)을 리터럴로 잡는 것과
거울 관계다. 11c의 grep 판정은 토큰 단위 grep이라 이 모양을 원리상 못 본다(후속).

배운 것: 도구가 낸 사슬을 **그 코드를 아는 사람**이 한 번 읽는 것이 측정 열 번보다 빨랐다. 3b 뒤에도
끝점 하나를 골라 같은 검증을 한다.

#### 3b-1 ✅ — 원천 재집계 action·실행기·게이트

**이름부터.** "재계산"이 아니라 **원천 재집계**다. 로직을 실행하지 않는다. 리드가 코드를 읽고 세운
기대("배지의 alarm은 이 창의 alarm 문서 수여야 한다") 중 *세거나 더하면 확인되는 것*을 우리 읽기 경로로
원천에서 다시 만들어, 앞선 증거의 값과 대조한다. 의의는 셋 — ① 화면의 숫자와 독립된 숫자(다르면 "원천과
화면 사이에서 바뀌었다"는, 읽기로는 안 나오는 사실), ② 표본으로 못 세는 전체 수(`mongo.find`는 잘린 표본),
③ filter와 대조 대상이 적힌 기계의 증거(규율 3의 숫자 판). 한계도 분명하다 — 질의 모양(filter·집계)의
기대에만 듣고, merge·후처리 같은 변환은 경계값 대조(프로브)와 읽기(리드)의 몫이며, 그 함수의 어느 줄이
틀렸는지는 실행이 필요해 v1 밖(개발 시스템)이다. "파이프라인 로직의 대부분이 filter·집계"라는 말은 과하다
— 그렇지 않은 로직에서 이 시스템이 잡는 것은 **깨진 홉이 어디냐**까지다.

- `RecomputePort(count, sum)` — 대상 포트가 아니지만 등재 목록이 가리키는 표면이라 포트로 두고 쓰기 동사
  없음·추상·async 검사를 같이 받는다. `recompute.count(collection, filter, expect)`,
  `recompute.sum(collection, filter, field, expect)`. `expect = {"evidence": "t-1.e1", "path": "items[0].n"}`
  — 앞선 증거 **안의 값 위치**(dict 키와 목록 색인만). 리드가 숫자를 옮겨 적으면 대조가 전사 실수를
  검증하게 되므로 위치로 가리키게 한다.
- `application/recompute.py` `Recomputer` — mongo 읽기 포트로 세거나 문서를 받아 한 필드를 더한다(표본이
  잘렸으면 합계 대신 error). 기대값이 없거나 숫자가 아니면 **error**지 불일치가 아니다 — 잘못 가리킨 것과
  틀린 것은 다른 사실이다.
- `ProbeRunner`가 자기가 만든 결과의 **원본**을 증거 id별로 프로세스 안에 들고 있다가(상한 256)
  `recompute.*`를 가로채 실행기에 넘긴다. State의 증거 body는 렌더한 텍스트라 값을 못 꺼내고, 저장소에
  원본을 남기는 것은 비밀·용량 문제라 안 한다. 다른 프로세스에서 재개된 케이스면 첫 recompute가 "모른다"고
  답하고 리드가 그 읽기를 다시 낸다 — 판단이 들어간 자리이고 문서에 이렇게 적는다.
- `_sanitize_task`가 `expect.evidence`를 `input_evidence_ids`에 강제(규율 4) — select 게이트가 그 증거가
  생긴 뒤에만 돌린다. 목록에는 mongo가 있는 사이트에서만 보인다.

#### 3b-2 ✅ — 브리핑 예시의 사다리 칸 · 대본 통합 시험

10b에서 잰 성질 — 리드는 판단해서 고르는 게 아니라 **예시의 틀을 채운다** — 를 사다리에도 쓴다. 목록에만 있고
예시에 없는 action은 안 나온다. 그래서 증거가 그 칸에 닿은 라운드에 그 칸을 integrate 예시 **첫 줄**로 둔다.

- `briefing._ladder_step` — State의 태스크에서 어느 칸까지 왔는지를 읽는다. rest 증거(증상 재현)가 있고 그
  끝점의 사슬이 오버레이에 있으면 `code.trace(endpoint=그 path)`(입력 증거 = rest 증거). trace 증거까지 있으면
  그 끝점의 추적 읽기 중 컬렉션(`flow.traced_reads`, 확실 → config키 → 추정)에 대한
  `recompute.count(collection=그 컬렉션, filter=지시문, expect={"evidence": rest 증거 id, "path": 지시문})`
  (입력 증거 = trace·rest 증거). 한 라운드에 한 칸 — 다음 칸의 입력이 이 칸의 증거라 둘을 같이 보여 주면 뒤
  칸은 게이트에 붙잡힌 채 번호만 쓴다. `integrate_fields`가 `plan_tasks`와 그래프를 넘긴다.
- **진짜 값(path·증거 id)을 박는다.** `supporting_ids`가 모양만 보여 주는 것과 반대다 — 여기서는 그대로 베끼는
  것이 정확히 원하는 출력이다(frame의 `code.grep patterns=[path]`와 같은 선택). `expect.path`만 지시문이다:
  rest 원본은 `{request, status, response}`라 `response` 아래라는 것까지만 우리가 안다. 숫자를 옮겨 적게 하지
  않는 3b-1의 선택 그대로.
- **없는 문은 안 보여 준다** — 그래프 없음·그 끝점 미추적·코드 없는 사이트면 `code.trace` 칸이 없고, mongo
  없는 사이트거나 이미 냈으면 `recompute` 칸이 없다. 예시에 뜬 문이 error로 답하면 리드는 그 라운드를 잃는다.
- 대본 통합 — `StubDeployedCode`(seeds의 `code.trace`만 안다, 나머지 읽기는 지어내지 않고 없다고 한다),
  `build_adapters`는 seeds에 `code` 절이 있을 때만 그것을 조립한다. `examples/case-ladder.json`: 배지 0 vs
  원천 alarm 문서 2 — 폭이 3인데도 세 라운드에 한 칸씩 돌고 마지막이 `recomputed 2 / expected 0 / match False`를
  남긴다. `python -m src case dryrun --plan examples/case-ladder.json --stub-seeds examples/stub-seeds.json`.
- 검증은 소비자로 — 리드가 받는 예시 JSON 원문과 `case dryrun` 출력을 직접 봤다.

메모: ① `code.trace` 칸의 억제는 **이 path로 낸 적이 있나**(상태 불문)다. 처음엔 action 이름(`used`)
기준이었는데 커밋 4 예비 판에서 대역이 `endpoint`에 서비스 이름을 넣어 두 번 실패하자 칸이 사라져 사다리를 끝내
못 밟았다 — 인자 이름도 그때 `target`에서 `endpoint`로 바꿨다(목록 한 줄이 리드가 보는 시그니처 전부다).
② 코드 없는 사이트(mongo만)에는 recompute 칸이 없다 —
컬렉션을 고를 근거(추적)가 없어서다. 필요해지면 발견 읽기에서 고르는 규칙을 더한다. ③ 사내에서는 이 칸이
실제로 리드의 출력이 되는지가 커밋 4의 T1·T3다.

**후속 (3b 뒤 첫 번째): 서비스 추적.** 끝점이 없는 서비스(processor·sink)에 대해 ① 출발점(컨슈머 콜백·
스케줄 잡 — 지식에 사람이 적는 편이 정확하다) ② 읽기뿐 아니라 쓰기 수집 ③ 함수 단위 `A —derives→ B`
엣지(키 A를 읽는 함수가 토픽 T를 만든다). flow.html의 3홉과 리드의 사다리 셋째 칸이 "그 서비스가 읽는 것
전부"에서 "그 키를 만드는 함수가 실제로 읽는 것"으로 좁아진다. 로직 재현이 아니라 함수 단위 배선이다.

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

### 커밋 4 결과 — 켬 3 · 끔 3 (하네스 `f7eaf64` + 아래 예비 판의 두 픽스)

**판.** `tools/local_case.py`(변형 sink-stopped). 가짜 api의 배지 핸들러를 사내 모양(캐시 키 → 비면 저장소
조회, 화면 형식은 getattr로 고름)으로 고쳐 사슬이 컬렉션에 닿게 했다 — 예전 판은 redis만 읽어 T2·T6을 잴 수
없었다. **켬** = `code graph`의 오버레이 그대로. **끔** = 같은 번들에서 추적(노드의 chain·gaps, trace 엣지)만
벗긴 것 — 11c 상태. 목록의 `code.trace`·`recompute` 줄은 끔에도 남는다("예시에 없으면 안 낸다"가 3b-2의
주장이라 그 자체를 잰다). 대역은 턴마다 새 haiku 에이전트(ask 파일만 읽고 reply 파일만 쓴다, 11a·11c와 같다).

**예비 판(pre-on-1·pre-off-1) — 표에 안 넣는다, 하네스가 다르다.** 켬의 r1 예시가 `code.trace target=/summary/badge`를
보여 줬는데 대역은 `target=sink`·`target=processor`로 두 번 냈고(둘 다 "끝점에 없다"), 그 뒤로 예시에서 칸이
사라져(억제가 action 이름 기준) 사다리를 끝내 못 밟았다. 둘을 고치고 다시 쟀다 — 인자 이름 `target` →
`endpoint`(목록 한 줄이 리드가 보는 시그니처 전부다), 칸의 억제는 "이 path로 낸 적이 있나"(상태 불문). 그리고
그 두 시도는 **끝점이 없는 서비스를 추적하고 싶다**는 뜻이었다 — 아래 서비스 추적 항목.

| # | 기대 | 결과 |
|---|---|---|
| T1 | `code.trace` r1 이내 | 켬 **r1 · r1 · r0** (3/3). 끔에서도 목록만 보고 off-1이 r3에 냈고(추적 없어 error), off-3은 r0에 `code.flow(/summary/badge)` |
| T2 | 이어진다, `확실` | 이어진다(`summary_badge → badge → count_recent`, `alarm:stats:{line}`·`alarm_events`). 등급은 **config키**(INFERRED via key). "확실"은 2f의 표시 분리 전에 적은 기대다 — config 키로 컬렉션을 고르는 코드는 원리상 config키가 맞다. 기대를 정정 |
| T3 | 고장 심으면 불일치 / 아니면 일치 | **도구**(dryrun, 예고에서 재해석한 대로): sink-stopped **일치** 0=0 · cache-stale **불일치** 2≠0 · healthy **일치** 2=2. **리드**: `recompute.count`를 낸 판 **0/3** — r2~r3 예시에 있었는데도 셋 다 같은 뜻을 `mongo.count`/`mongo.find occ_date $gte`로 직접 냈다 |
| T4 | sink를 처음 짚는 라운드 — 11c 기준선(2·4·없음)보다 앞 | 켬 **r2 · r2 · r1** (on-1은 "processor/sink" 병기, sink 단독은 r4) / 끔 **r5 · 없음 · r2** |
| T5 | 지어낸 이름 — 기준선 이하 | 켬 1·1·2 / 끔 1·3·0 (손으로 셈). 계측기는 7건 중 **1건**(`alarm-processor`)만 잡았다 — 레포·서비스 이름을 그룹으로 댄 것(`dt-core`·`dt-core-sink`·`processor`)과 템플릿을 엉뚱하게 채운 키(`alarm:stats:gumi`·`…:gumi_line`)는 아는 이름으로 친다 → ⑤ |
| T6 | 추적기가 gap을 남기고 / 리드가 그 자리를 `code.read` | 남긴다(`api/alarms.py:L15 getattr…`, r2 프롬프트에 실림) / 그 자리를 낸 판 **0/3** — 셋 다 sink 코드로 갔다(`code.read sink/writer.py` 두 판). gap이 증상과 무관한 가지(화면 형식)라 리드의 선택이 틀리지 않다 |
| T7 | 최종 가설이 부품 하나 | 켬 **3/3 sink 하나** / 끔 sink 하나 1(off-3), processor+sink 병기 1(off-1), **processor 오답** 1(off-2) |

| 판 | sink 지목 | 최종 | `group_offsets gumi-mx-core` | 지어낸 이름 | `code.trace` | 원천 재집계 | 종료 |
|---|---|---|---|---|---|---|---|
| on-1 | r2 (단독 r4) | sink | r5 (r3엔 `dt-core`) | 1 | r1 | — (r2 `find occ_date $gt`) | r6 상한 |
| on-2 | r2 | sink | r4 (r3엔 `dt-core-sink`) | 1 | r1 | — (r2 `find occ_date $gte`) | r6 상한 |
| on-3 | r1 | sink | 없음 (r3 `dt-core-sink`) | 2 | r0 | — (r2 `count occ_date $gte`) | r4 conclude |
| off-1 | r5 (r2엔 sink **refuted**) | processor+sink | r3 | 1 | r3 (error) | — | r5 conclude |
| off-2 | 없음 | **processor (오답)** | r4 (r3엔 `processor`) | 3 | — | — | r6 상한 |
| off-3 | r2 | sink | r3 | 0 | — | — | r5 conclude |

읽는 법:

- **정답률 켬 3/3, 끔 1.5/3.** sink 지목 중앙값 켬 r2 / 끔 r5. 끔(5·없음·2)이 11c 기준선(2·4·없음)과 같은
  급이고 켬이 앞이다. n=3, haiku 대역, 상한이지 예측이 아니다(11a).
- **켬이 앞선 자리는 "API가 잘못 읽는다"를 닫는 라운드다.** `code.trace`가 r1에 "끝점은 캐시를 보고 비면
  컬렉션을 센다"를 주니 그 가설이 r2~r3에 닫혔다(on-1 h-2 refuted r3, on-2 "0은 증상" r2). 끔은 끝까지
  supported로 남거나(off-1 h-1) 사슬 없이 processor로 샜다(off-2). 사다리 둘째 칸의 값이 이것이다.
- **원천 재집계는 안 냈다(0/3).** 예시를 셋 다 봤는데 같은 뜻을 익숙한 읽기로 냈다. 두 가지가 보인다 —
  ① 예시의 `filter` 자리가 "위 증거에서 본 필드 이름: 찾으려는 값"이라 **창**(`occ_date $gte`)의 모양이 안
  보이는데 리드가 원한 것은 창이었다. ② 기대값(배지 0)이 사소해 대조의 값이 안 보였다 — 이 도구의 의의
  (표본으로 못 세는 전체 수, 기계의 대조)는 표본이 큰 사내에서 드러난다. 예시 모양을 이 판에 맞춰 바꾸는 것은
  튜닝이라 안 한다(⑮) — 사내 첫 실행에서 다시 본다.
- **공유 그룹 lag 오독(11c l-3)이 off-2에서 재현됐다.** `mx.alarm.main` lag 1830을 processor 것으로 읽어
  processor를 원인으로 찍었다. 켬 셋은 전부 sink로 읽었다 — 트레이스 덕인지 `<데이터 흐름>`의 consumes 줄과
  사다리의 결과인지 n=3으로는 못 가른다.
- **서비스 추적 수요가 실측으로 나왔다.** 예비 판의 `code.trace target=sink/processor`, on-1의 `code.flow dt-core`,
  `code.read sink/writer.py`(on-1·on-2·off-2·off-3), off-2의 컨슈머 설정 grep 넷. 리드는 끝점이 없는 서비스의
  코드를 따라가고 싶어 하고 지금은 grep·read로 더듬는다. 후속 "서비스 추적"의 근거다.
- 계측기 메모: 대역 한 턴이 "썼다"고 하고 파일이 없어 새 에이전트로 다시 돌렸다(11c ④와 같다). `code trace`
  CLI는 번들의 `graph.json`을, 리드는 `overlay.json`을 읽는다 — 내용이 같아 문제는 아니지만 끔 판을 만들 때
  둘 다 벗겨야 했다. 한 파일로 모으는 것이 맞다(백로그).

**후속.** ⑤ 이름 검사를 인자 종류별로 + 템플릿 채움 검사(T5의 계측기 구멍, 7건 중 1건). 원천 재집계 예시의
`filter` 모양은 사내 첫 실행 뒤에. 번들의 그래프 파일 단일화. 그리고 ~~서비스 추적~~ →
[11d 심볼 인덱스](step-11d-index.md)로 바뀌었다(출발점을 적지 않고 인덱스에서 유도한다).

## 범위 밖

- LLM 서브에이전트 루프. 필요해지면 `PlanTask.action=None`인 태스크로 레인 하나만 연다.
- 레포를 건너는 호출(HTTP로 다른 서비스를 부르는 것) — 그건 그래프의 `serves` 엣지가
  이미 잇는다.
- 런타임 관측(Mongo 프로파일러·접근 로그·트레이스) — 대상에 켜져 있어야 읽을 수 있고 켜는 건
  쓰기다. 켜져 있으면 정적 추적의 검증 용도로만.
- `response_model` 스키마 → 응답 필드 → `items_all_zero.counts` 잇기 — 메모만. 필드 수준
  지식이 필요해지면 그때.
