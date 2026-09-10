# 3b단계 — 실제 어댑터와, 데이터를 하나씩 꺼내 보는 CLI

## 목적

config에 적은 대로 **Redis·MongoDB·Kafka·REST에서 값을 하나씩 꺼내 볼 수 있게**
한다. 사내에서 접속 정보를 채우고 이 명령들을 돌리면 "붙는가"와 "무엇이 보이는가"가
한 번에 확인된다.

```bash
python -m src boot                      # 설정이 온전한가
python -m src config show               # 병합 결과 + 어느 층이 이겼는가
python -m src doctor                    # 실제로 붙는가
python -m src peek redis --key oee:L3   # 무엇이 보이는가
```

사이트가 둘 이상이면 `--gbm mx --fct gumi`로 고른다. 하나뿐이면 생략해도 된다 —
**임의로 첫 번째를 고르지는 않는다.** 그러면 다른 법인의 Redis를 들여다보게 된다.

---

## 실제로 돌려 본 결과

이 문서의 출력은 전부 **진짜 서버**에서 나온 것이다 — 로컬에 진짜
`redis-server`를 띄우고 데이터를 심었고, POST를 받는 진짜 HTTP 서버를 띄웠다.

### Redis

```console
$ python -m src peek redis --key oee:L3
{
  "status": "ok",
  "source": "redis:redis://127.0.0.1:6399/0:oee:L3",
  "observed_at": "2026-09-09T09:33:12.590019+00:00",
  "data": { "type": "string", "value": "87.2" }
}

$ python -m src peek redis --key line:L3:status        # hash도 그대로
{ ... "data": { "type": "hash", "value": {
      "state": "RUNNING", "plan": "PRODUCTION", "updated_at": "2026-09-09T08:31:00+09:00" } } }
```

`type`을 먼저 보고 분기한다 — string이면 GET, hash면 HGETALL, list면 LRANGE.
**모르는 타입은 추측해서 읽지 않는다.**

### REST — POST에 params를 실어 응답 받기

```console
$ python -m src peek rest --entry oee_summary --params '{"line":"L4","date":"2026-09-09"}'
{
  "status": "ok",
  "data": {
    "request": { "method": "POST", "path": "/api/v1/oee/summary",
                 "params": { "line": "L4", "date": "2026-09-09" } },
    "status": 200,
    "response": { "line": "L4", "oee": 512.0, "availability": 0.99,
                  "performance": 5.4, "quality": 0.96 }
  }
}
```

**응답만이 아니라 `request`도 함께 남긴다.** 응답만 보관하면 "0건"이 *현장이
멈췄다*인지 *질문을 잘못 던졌다*인지 구별할 수 없다.

### 실패도 같은 모양으로 돌아온다

```console
$ MX_GUMI_REDIS_PASSWORD=틀린비번 python -m src peek redis --key oee:L3
{
  "status": "error",
  "source": "redis:redis://127.0.0.1:6399/0:oee:L3",
  "observed_at": "2026-09-09T09:36:09.175090+00:00",
  "error": "AuthenticationError: invalid username-password pair or user is disabled."
}
```

예외가 아니라 **값**이다(2단계 규율 ③). 호출자는 `try/except`가 아니라
`if result.status == "error"`로 분기한다.

---

## 등재제 — REST가 부를 수 있는 것은 config가 정한다

포트에 `get(path)`가 **없다.** 경로를 인자로 받으면 "임의의 경로를 호출하라"가
표현 가능해지고, 그 순간 무엇을 호출할지가 config에서 코드로, 결국 LLM의 판단으로
흘러간다.

```json
// gbm/mx.json — API 스펙은 사업부 단위로 같으므로 여기에 한 번만 적는다
"entries": {
  "oee_summary": { "method": "POST", "path": "/api/v1/oee/summary",
                   "params": { "line": { "type": "str", "required": true },
                               "date": { "type": "str", "required": false } } }
}
// fct/gumi/mx.json — 법인마다 다른 것은 base_url과 키뿐이다
"rest": { "base_url": "http://gumi-twin-api:8080", "headers": { "X-API-KEY": "${...}" } }
```

```console
$ python -m src peek rest --entry delete_line --params '{}'
"error": "등재되지 않은 항목 — delete_line. 호출 가능: lines, oee_summary"

$ python -m src peek rest --entry oee_summary --params '{"line":"L4","drop_table":"x"}'
"error": "파라미터 거부 — 선언되지 않은 파라미터 — drop_table"
```

두 경우 모두 **소켓에 나가기 전에** 거부된다.

### 리스트 파라미터와, 스칼라를 감싸는 이유

실제 API의 필터는 리스트를 받는다:

```json
"summary_badge": { "method": "POST", "path": "/summary/badge",
                   "params": { "part_code": { "type": "list", "required": false },
                               "line_code": { "type": "list", "required": false } } }
```

사람이 CLI에서 쓰기 자연스러운 것은 스칼라다. 그래서 **1개짜리 리스트로 감싼다**:

```console
$ python -m src peek rest --entry summary_badge --params '{"line_code": "P222"}'
"request": { "params": { "line_code": ["P222"] },
             "wrapped_as_list": ["line_code"] },
"response": { "filters": { "line_code": ["P222"] }, "total": 2 }
```

**거부하는 것보다 감싸는 편이 안전하다.** `"P222"`를 문자열로 그냥 보내면 서버가
그 필터를 **무시하고 전체를 돌려줄** 수 있고, 그러면 "조건에 맞는 것이 이만큼
있다"는 **거짓 안심**이 된다. 조용한 실패가 시끄러운 실패보다 항상 나쁘다.

감싼 사실은 `wrapped_as_list`로 남는다 — 사람이 준 것과 소켓에 나간 것이 다르면
말해야 하고, 증거에 기록되는 `request`는 **나간 것**이어야 한다.

원소는 스칼라만 받는다:

```console
$ python -m src peek rest --entry summary_badge --params '{"line_code":[["중첩"]]}'
"error": "파라미터 거부 — line_code[0]은 스칼라여야 한다 — list이 왔다"
```

중첩 리스트나 dict를 허용하면 body 모양이 사실상 자유가 되고, **"닫힌 스키마"라는
말의 뜻이 사라진다.**

**POST를 열어도 읽기 전용인 이유**: POST를 허용하는 것과 "임의의 body로 임의의
경로에 POST하라"를 허용하는 것은 다르다. 메서드 수준에서 잃은 안전장치를 **body
수준의 닫힌 스키마**가 대신한다.

`read_only: true` 같은 플래그는 두지 않는다. 목록에 없으면 문이 안 열리므로
플래그가 중복이고, 끝점 수십 개에 적으라고 하면 사람은 기동 검증을 통과시키려고
전부 `true`로 적는다. **아무도 생각하지 않는 체크박스의 안전 가치는 0이다.**

---

## Mongo — 필터 연산자 허용 목록

```console
$ python -m src peek mongo --collection oee --filter '{"$where":"this.oee>100"}'
"error": "filter: 허용되지 않은 연산자 — $where"
```

`$where`와 `$function`은 **서버에서 자바스크립트를 실행한다.** 읽기 전용 계정이라도
CPU를 태우고, 표현식에 따라 대상 DB를 멈출 수 있다. 우리는 데이터를 안 쓰지만
**성능에도 개입하지 않아야** 진짜 읽기 전용이다.

금지 목록이 아니라 허용 목록인 이유: 새 연산자는 계속 생기고, 금지 목록은 항상
뒤늦다. 중첩된 `$or` 안에 숨은 것도 찾는다.

### `limit + 1`을 읽는 이유

`limit=100`으로 100건이 나왔을 때 "딱 100건"인지 "더 있는데 잘린 것"인지 구별할 수
없다. 101건을 요청해 101건이 오면 잘린 것이고, 그러면 봉투가 `complete=False`로
말한다. **부정 증거("없다")는 표본이 완전할 때만 성립한다.**

---

## Kafka — 컨슈머 그룹에 들어가지 않는다

```python
consumer = AIOKafkaConsumer(bootstrap_servers=...,
                            group_id=None,             # ← 그룹에 안 들어간다
                            enable_auto_commit=False)  # ← 커밋할 그룹도 없다
await consumer.topics()                                # ← 메타데이터를 먼저 채운다
consumer.assign(tps)                                   # ← subscribe가 아니다
```

`subscribe(group_id=...)`였다면 브로커가 리밸런스를 돌리고 `__consumer_offsets`에
커밋이 남는다. 그건 쓰기이고, 같은 group_id를 쓰는 실제 서비스의 **파티션을 빼앗는다.**

**`await consumer.topics()` 한 줄이 왜 필요한가**: `assign()` 직후 바로 읽으면
파티션 메타데이터가 아직 비어 있어 **"데이터 없음"이라는 조용한 거짓말**을 돌려준다.
에러가 아니라 정상적인 0건으로 보이기 때문에 제일 위험한 실패 모드다.

lag은 `AIOKafkaAdminClient`로 밖에서 조회한다. 감시 그룹은 **법인마다 여러 개**다 —
같은 Kafka에 서비스가 여럿 붙어 각자 자기 그룹을 쓰고, 그 수는 늘어난다:

```console
$ python -m src peek kafka --lag            # config의 group_ids 전부
"group": "dt-processor-mx-gumi",  "total_lag": 2
"group": "dt-sink-mx-gumi",       "total_lag": 5

$ python -m src doctor
  kafka  ✅ dt-processor-mx-gumi lag 2
  kafka  ✅ dt-sink-mx-gumi lag 5
```

위 숫자는 진짜 브로커에서 나온 것이다 — 6건을 넣고 한 그룹은 4건, 다른 그룹은
1건까지 소비시킨 뒤 조회했다.

하나만 볼 수 있게 만들면 **나머지가 밀려도 모른다.** `--group`으로 하나만 지정할
수도 있지만 기본은 전부다.

그룹 이름에 법인이 박혀 있으므로(`-mx-gumi`) `group_ids`는 `gbm/` 층이 아니라
`fct/{fct}/{gbm}.json`에 적는다. `"dt-processor-{gbm}-{fct}"` 같은 템플릿 문법을
만들지 않는 이유: 오타 난 템플릿은 **존재하지 않는 그룹을 조용히 감시하고**,
그 결과는 lag 0 — "정상"과 구별되지 않는다.

---

## 스텁 ↔ 실구현: 전환점은 한 곳뿐

`src/infrastructure/factory.py`의 `build_adapters`가 유일한 조립 지점이다.
조립을 여러 곳이 각자 베끼면 언젠가 한 경로만 다른 어댑터를 쓰고, 증상은
"CLI로는 되는데 순찰에서는 안 된다"로 나타난다.

```bash
python -m src peek redis --key oee:L3                       # 실제 Redis
python -m src peek redis --key oee:L3 --stub-seeds fake.json # 가짜 데이터
```

**가짜 데이터가 config가 아니라 플래그인 이유**: 실제 대상에 붙일 때는 플래그를
빼면 되고, **빼는 것을 잊을 수 없다.** config에 남는 설정이면 "실전환 전에 지워라"를
체크리스트에 적어야 하고, 그건 사람의 기억에 기대는 안전이다.

스텁은 실구현과 **같은 계약**을 지킨다 — 없는 키는 error가 아니라 `None`,
잘리면 봉투가 말한다, 필터 검사도 같은 함수를 쓴다. 계약이 갈라지면 테스트는
통과하는데 사내에서 깨진다.

---

## 무엇이 어디서 검증됐나

| | 이 리포에서 | 사내에서 |
|---|---|---|
| Redis | ✅ 진짜 서버로 전 구간 | `doctor` |
| REST | ✅ 진짜 HTTP 서버(POST 포함) | `peek rest` |
| Kafka | ✅ 진짜 브로커(복수 그룹 lag·그룹 미생성까지 확인) | `peek kafka --lag` |
| Mongo | ⚠️ 로직만(필터·잘림·변환) — 실서버 바이너리를 구할 수 없었다 | **`doctor`로 꼭 확인** |

`tests/live/`가 실제 시스템에 붙는 테스트를 담고 있고, 기본 실행에서는 빠진다:

```bash
export OPS_TEST_REDIS_URL=redis://gumi-redis:6379
export OPS_TEST_REDIS_PASSWORD=...
pytest tests/live -m live -v
```

오프라인 테스트가 잠그는 것은 **로직**이다(필터를 막는가, 잘림을 아는가).
live 테스트가 잠그는 것은 그걸로 절대 알 수 없는 것이다 — 진짜로 붙는가,
인증이 통하는가, **그리고 스텁과 실구현의 계약이 정말 같은가.**

그중 하나는 우리 코드가 아니라 **계정 권한**을 확인한다:

```python
async def test_읽기_전용_계정이면_쓰기가_거부된다(mongo_reader):
    with pytest.raises(Exception) as caught:
        await db[collection].insert_one({"_ops_agent_write_probe": True})
    assert "not authorized" in str(caught.value).lower()
```

포트에 쓰기 메서드가 없으니 코드로는 못 쓴다. 그래도 계정 자체가 읽기 전용인지는
**별개의 방어층**이고, 그건 우리가 아니라 DBA가 정한 것이므로 실물로 확인할 가치가 있다.

---

## 사내에서 할 일

```powershell
# 1. .env 채우기
notepad .env      # MX_GUMI_REDIS_PASSWORD, MX_GUMI_MONGO_PASSWORD

# 2. 설정이 온전한가
.venv\Scripts\python.exe -m src boot

# 3. 실제로 붙는가
.venv\Scripts\python.exe -m src doctor

# 4. 데이터가 보이는가
.venv\Scripts\python.exe -m src peek redis --key <실제키>
.venv\Scripts\python.exe -m src peek mongo --collection <컬렉션> --filter "{}" --limit 3
.venv\Scripts\python.exe -m src peek kafka --lag
.venv\Scripts\python.exe -m src peek rest --list
```

4번에서 보이는 **실제 데이터의 모양**이 다음 단계(순찰 프로브)의 입력이다.
키 이름과 필드 이름이 확정돼야 "무엇이 이상인가"를 config로 쓸 수 있다.

→ 다음: [4단계 — 순찰 프로브](step-04-probes.md)
