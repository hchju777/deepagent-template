# 7단계 — 사내 LLM 연결

## 왜 순서를 바꿨는가

원래 계획은 순찰(4·5)→케이스(6)→LLM(7)이었다. 바꾼 이유는 **LLM 연결이 가장
불확실한 구간**이기 때문이다. TLS(사내 CA), 헤더 인증, 프록시, 그리고 무엇보다
**그 모델이 우리가 요구하는 JSON을 낼 수 있는가** — 이 넷은 사내에서만 알 수 있다.

엔진을 다 만든 뒤에 "모델이 JSON을 못 낸다"를 알면 프롬프트 설계를 다시 해야 한다.
**제일 비싼 실패를 제일 먼저** 확인한다.

---

## 사내 게이트웨이: OpenAI 규약인데 인증만 다르다

| 헤더 | 값 | 출처 |
|---|---|---|
| `X-FABRIX-CLIENT` | Pass Key | `${LLM_PASS_KEY}` |
| `X-OPENAPI-TOKEN` | Client Key | `${LLM_CLIENT_KEY}` |
| `X-LLM-MODEL-ID` | 모델 ID (`"339"`) | config의 `llm.model_id` |

`Authorization` 헤더는 보지 않는다. 그런데 `ChatOpenAI`는 api_key가 없으면
`OPENAI_API_KEY` env를 뒤지다 죽으므로 자리를 채워야만 한다 — 기본값 `"EMPTY"`는
소켓에 나가도 아무 뜻이 없는 sentinel이고, 진짜 인증은 헤더 셋이 한다.

**모델 ID는 config에, 키는 `.env`에** 둔다. 모델 ID는 비밀이 아니고(게이트웨이가
공개하는 식별자다), 비밀이 아닌 것을 `.env`에 넣으면 "이 설정이 무엇인지" 보려고
매번 `.env`를 열어야 한다 — 게다가 `.env`는 git에 없어서 리뷰도 안 된다.

### env 이름 규칙

```
{무엇에 붙는가}_{무엇인가}        LLM_PASS_KEY, MAIL_AGENT_API_KEY, REDIS_PASSWORD
```

**벤더 이름을 넣지 않는다.** `GAUSS_LLM_PASS_KEY`나 `FABRIX_TOKEN`이라고 쓰면
게이트웨이가 교체되는 날 그 이름이 거짓말이 되고, 이름을 고치려면 모든 배치의
`.env`를 같이 고쳐야 한다. `LLM_*`은 사내 게이트웨이든 OpenAI든 같은 자리다.

하이픈이 든 이름(`MAIL-Address`)은 쓰지 않는다 — 셸에서 설정할 수 없다. 참고로
`${...}` 해석기는 그런 이름도 **참조로 인식해서 "없다"고 보고**한다(좁은 정규식은
조용히 문자열 그대로 통과시켜 버리므로 일부러 넓게 잡았다 — 3a단계 참고).

```json
{
  "llm": {
    "adapter": "chat_model",
    "provider": "openai_compatible",
    "model": "gauss-o-flash",
    "model_id": "339",
    "temperature": 0.0,
    "base_url": "${LLM_BASE_URL}",
    "pass_key": "${LLM_PASS_KEY}",
    "client_key": "${LLM_CLIENT_KEY}",
    "tls": { "use_system_store": true }
  }
}
```

### 헤더 조립은 한 곳뿐이다

```python
def gateway_headers(self) -> dict[str, str]:
    headers["X-FABRIX-CLIENT"] = self.pass_key.get_secret_value()
    headers["X-OPENAPI-TOKEN"] = self.client_key.get_secret_value()
    headers["X-LLM-MODEL-ID"] = self.model_id
```

어댑터마다 각자 조립하면 하나가 이름을 틀려도(`X-OPENAI-TOKEN` ↔ `X-OPENAPI-TOKEN`은
한 글자 차이다) 다른 쪽은 멀쩡해서, 증상이 **"어떤 경로는 되고 어떤 경로는 401"**
이 된다. 그 증상으로 원인을 찾는 데 반나절이 간다.

---

## TLS: 전역을 끄지 않는다

사내에서 흔히 쓰는 처방은 이것이다:

```python
_os.environ["CURL_CA_BUNDLE"] = ""
_os.environ["REQUESTS_CA_BUNDLE"] = ""
_ssl._create_default_https_context = _ssl._create_unverified_context
_requests.Session.request = <verify=False를 박은 패치>
```

네 줄 다 **프로세스 전체**를 바꾼다. 이 시스템에서는 그게 이런 뜻이다:

```
LLM 게이트웨이 하나 붙이려고
  → Redis 접속의 인증서 검증도 꺼진다
  → MongoDB 접속의 인증서 검증도 꺼진다
  → Kafka 접속의 인증서 검증도 꺼진다
  → 대상 REST 접속의 인증서 검증도 꺼진다
  → requests를 쓰는 모든 사내 SDK·클라우드 SDK도 같이 꺼진다
```

**모니터링 대상보다 모니터링 도구가 더 위험해진다.** 그리고 그 사실은 어디에도
기록되지 않는다 — 로그에도, 설정에도 안 남는다.

대신 config로 셋 중 하나를 고른다. 범위는 **이 커넥션 하나**다:

| 선택 | 언제 | 어떻게 |
|---|---|---|
| `use_system_store: true` | **Windows 권장** | `truststore`가 OS 인증서 저장소를 본다 |
| `ca_bundle: "C:/certs/ca.pem"` | 번들 파일이 있을 때 | 그 번들만 신뢰 |
| `verify: false` | 번들 구하기 전 임시 | 켜지면 **시끄럽게 경고** |

`use_system_store`가 권장인 이유: 사내 루트 CA는 **이미 Windows 인증서 저장소에
있다**(브라우저로 게이트웨이가 열리니까). 파이썬만 그걸 안 보고 `certifi` 번들만
본다. `truststore`가 그 간극을 메우므로 **`.pem` 내보내기가 불필요해진다.**

```console
$ python -m src llm describe
  chat_model/openai_compatible gauss-o-flash(id=339) → https://... [인증: 헤더 셋(사내 게이트웨이), TLS: ⚠ 검증 끔]

$ python -m src llm ask "pong"
[llm] ⚠ TLS 검증이 꺼져 있다 (tls.verify=false). Windows면 tls.use_system_store=true로
      바꾸면 사내 CA를 그대로 쓴다
```

못 막을 것은 **최소한 시끄럽게** 만든다(기동 거부 철학의 형제).

`tests/infrastructure/test_tls.py`가 전역 비침범을 단정한다 — `ssl` 전역,
`os.environ`, `requests.Session.request` 셋 다 그대로인지 확인한다. 산문 규율은
급할 때 깨지므로 테스트가 지킨다.

### 프록시

전사 프록시가 env에 박혀 있는데 게이트웨이는 사내망 안이라 프록시를 거치면 안 되는
경우가 있다. `NO_PROXY`에 호스트를 넣는 것이 정석이지만 그 env를 우리가 바꿀 수 없는
배치도 있다 — 그때 `trust_env_proxy: false`가 **이 커넥션만** 프록시를 무시한다.

---

## 어댑터가 둘인 이유

```
adapter: "chat_model"   langchain ChatOpenAI   ← 기본. 8단계 그래프가 이 객체를 받는다
adapter: "http"         httpx로 직접            ← langchain 없이 같은 규약
adapter: "echo"         네트워크 없음            ← 개발·배선 확인
```

**`http`를 같이 두는 이유가 셋이다.** (사내 langchain 설치는 되는 것으로 확인됐으니
1번은 보험이다.)

1. **의존성이 막힌 환경을 위한 보험**: 사내 PyPI 미러에서 langchain 계열이 빠지거나
   버전이 내려가면 `chat_model`을 쓸 수 없다. 규약은 같으므로 `adapter: "http"`
   한 줄로 같은 게이트웨이에 그대로 붙는다.
2. **디버깅**: 401이 났을 때 "헤더가 정말 나갔는가"를 보려면 요청을 직접 만드는 쪽이
   빠르다. langchain 내부를 헤집을 필요가 없다.
3. **포트가 진짜 추상인지 증명한다**: 구현이 하나뿐인 포트는 추상인 척하는
   별칭이다. 두 번째 구현이 같은 계약을 통과하는 것을 본 뒤에야 "갈아끼울 수
   있다"가 사실이 된다.

둘은 같은 `LlmPort`를 구현하므로 엔진 입장에서는 구별되지 않는다. 테스트가
**같은 게이트웨이에 두 어댑터로 붙여** 같은 답이 나오는지 확인한다.

---

## 다른 LLM으로 갈아끼우기

`provider: "openai_compatible"`은 사내 게이트웨이만이 아니다. **config만 바꾸면** 붙는 것들:

| 대상 | 설정 |
|---|---|
| OpenAI 본체 | `base_url: "https://api.openai.com/v1"`, `api_key: "${OPENAI_API_KEY}"`, pass_key/client_key 비움 |
| vLLM · LiteLLM | `base_url`만 바꾼다 |
| Ollama | `base_url: "http://localhost:11434/v1"`, `api_key: "ollama"` |
| 다른 사내 게이트웨이 | `headers: {"X-Tenant": "mx", ...}` — 임의 헤더를 받는다 |

사내 키(`pass_key`/`client_key`)를 비우면 사내 헤더 셋이 **붙지 않고** `api_key`가
`Authorization`으로 나간다. 코드를 고칠 일은 없다.

OpenAI 규약이 아닌 것(Anthropic 등)을 붙이려면 `LlmPort`를 구현하는 어댑터 파일
하나를 더하고 `adapter`에 이름을 추가하면 된다 — **포트 표면이 `ask`와 `describe`
둘뿐**이라 구현할 것이 적다. 표면을 좁게 유지한 값어치가 여기서 나온다.

---

## 실패는 예외가 아니라 값이다

LLM 호출은 **일상적으로 실패한다** — 타임아웃, 429, 게이트웨이 재시작. 여기서
raise하면 8단계의 LangGraph superstep 전체가 죽고, 그 케이스는 `investigating`
상태로 영원히 남는다.

```console
$ python -m src llm ask "pong"          # 게이트웨이가 죽어 있을 때
{
  "status": "error",
  "asked_at": "2026-09-10T04:33:28.236997Z",
  "model": "gauss-o-flash",
  "error": "ConnectError: All connection attempts failed",
  "latency_s": 0.08
}
```

실패에도 **언제 실패했는지**가 남는다. "응답이 없다"와 "3분 전에 응답이 없었다"는
다른 사실이다.

200인데 모양이 OpenAI 규약과 다른 경우도 **실패로 본다.** 조용히 빈 문자열을
돌려주면 엔진은 "LLM이 아무 말도 안 했다"로 읽고 그 이유를 영원히 모른다.

---

## 간단한 질문 묶음

```console
$ python -m src llm check
  chat_model/openai_compatible gauss-o-flash(id=339) → http://... [인증: 헤더 셋(사내 게이트웨이), TLS: 검증 켬]

  붙는가      ✅ (1.467s) pong
  한국어      ✅ (0.005s) 설비 가동률이 낮아지는 흔한 원인은 잦은 단시간 정지다.
  JSON     ✅ (0.004s) ```json
{"verdict": "ok", "score": 1}
```
```

**JSON 항목이 제일 중요하다.** 8단계의 노드(frame/integrate/conclude)와 접수가
전부 LLM 응답을 JSON으로 파싱한다. 모델이 그걸 못 하면 조사가 도중에 **조용히
멈추고**, 증상은 "보고서가 비어 있다"로 나타난다.

코드펜스(```json ... ```)를 벗겨서라도 파싱한다 — 모델들이 습관적으로 붙이고,
그걸 실패로 치면 멀쩡한 모델을 못 쓰게 된다. 단, 관용 범위는 **8단계와 같아야**
한다. 여기서 통과한 것이 엔진에서 깨지면 이 검사가 거짓 안심이 된다.

---

## 사내에서 확인할 것

```powershell
# 1. .env 채우기 — LLM_BASE_URL / LLM_PASS_KEY / LLM_CLIENT_KEY
# 2. config/app.json의 llm 블록 (model_id·model 확인)
.venv\Scripts\python.exe -m src boot           # ca_bundle 경로·키 형식까지 본다
.venv\Scripts\python.exe -m src llm describe   # 붙기 전에 설정을 확인
.venv\Scripts\python.exe -m src llm check      # 붙는가 · 한국어 · JSON

# 3. 실제 질의 테스트 (여섯 개)
.venv\Scripts\python.exe -m pytest tests/live -m live_llm -v
```

live 테스트가 확인하는 것 중 **오프라인에서 절대 알 수 없는 셋**:

| 테스트 | 실패하면 |
|---|---|
| `조사_엔진이_요구하는_모양의_JSON을_낼_수_있다` | **프롬프트 설계를 바꿔야 한다** — 필드를 줄이거나, 예시를 더 주거나, 한 번에 하나씩 묻거나 |
| `같은_질문에_같은_답을_낸다` | temperature=0인데 답이 달라진다 → **결정론 벤치를 만들 수 없다**(건너뛰지 않고 실패로 알린다) |
| `설정한_모델이_실제로_쓰인다` | config가 안 먹었다 — 답은 오므로 아무도 알아채지 못하는 조용한 실패 |

### TLS가 실패하면 순서대로

1. `tls.use_system_store: true` — Windows 저장소의 사내 CA를 쓴다 (권장)
2. 안 되면 `ca_bundle`에 `.pem` 경로 — 경로는 `"C:/certs/ca.pem"`처럼 슬래시로
3. 급하면 `tls.verify: false` — **매 호출 경고가 찍힌다.** 그게 의도다

어떤 경우에도 `ssl._create_default_https_context`를 건드리지 마라. 그 한 줄이
대상 시스템 접속 다섯 종의 검증을 같이 끈다.

---

## 테스트

```
tests/config/test_schema_llm.py          13   반쯤 채운 인증, ASCII 헤더, 비밀값 비노출
tests/infrastructure/test_tls.py         15   전역 비침범(ssl·env·requests), 방침 선택
tests/infrastructure/test_llm_adapters.py 14   **진짜 HTTP**로 두 어댑터 × 계약
tests/infrastructure/test_llm_fakes.py    4   대본 재생, 소진 시 던짐
tests/infrastructure/test_llm_factory.py  5   조립 한 곳, 포트 표면
tests/live/test_live_llm.py               6   사내에서만 (live_llm 마커)
```

`test_llm_adapters.py`가 **인프로세스 가짜 게이트웨이**를 띄운다. httpx를 목으로
갈아끼우지 않은 이유: 그러면 "헤더가 실제로 소켓에 나갔는가"를 검증할 수 없다.
가짜 게이트웨이는 헤더 셋을 검사해 틀리면 401을 주므로, **200이 온 것 자체가
헤더가 맞게 나갔다는 증거**다.

→ 다음: [8단계 — 메일 발송 Agent API](step-08-mail.md)

메일 Agent는 한 텍스트 필드(`input_value`)에 수신자·제목·본문을 담아 보내고
Agent가 그것을 파싱해 메일로 보낸다. 그 모양은 사내 규약이라 그대로 따르고,
본문에 섞여 들어온 글자가 수신자 지시로 읽히지 않게 하는 것은 8단계에서 다룬다.
