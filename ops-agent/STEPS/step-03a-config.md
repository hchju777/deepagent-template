# 3a단계 — config와 기동 검증

## 목적

잘못된 설정이 **런타임이 아니라 기동 시점에** 막히게 한다. 그리고 비밀번호가
로그·에러·`print` 어디에도 안 찍히게 한다.

3단계는 둘로 나눴다. 3a는 네트워크가 필요 없고, 3b가 실제 어댑터와 접속이다.

---

## 사내 config 구조를 그대로 따랐다

키 이름을 우리 취향대로 바꾸지 않았다. `infra.redis` / `mongodb` /
`kafka.consumer` — 사내에서 이미 쓰는 형태 그대로다. 바꾸면 기존 config를
복사해 올 때마다 사람이 손으로 번역해야 하고, **번역은 언젠가 틀린다.**

## 계층 — 같은 것을 두 번 적지 않는다

법인이 늘어나면 접속 정보만 다르고 나머지는 같다. REST 등재 항목, Kafka 토픽
이름, Mongo 계정 이름은 **사업부 단위로 같고**, url과 비밀번호만 법인마다 다르다.
한 파일에 다 적으면 법인이 열 개일 때 같은 REST 스펙을 열 번 적게 되고,
스펙이 바뀌면 열 곳을 고쳐야 한다. 아홉 곳만 고치는 날이 반드시 온다.

```
config/
  app.json                전역 (시간대, 출력 경로)
  registry.json           어느 (gbm, fct) 조합이 실재하는가
  gbm/
    common.json           전 사업부·전 법인 공통          ← 제일 약함
    mx.json               mx 사업부 공통
  fct/
    gumi/
      common.json         구미 법인 공통 (전 사업부)
      mx.json             구미 × mx                      ← 제일 강함
```

아래로 갈수록 이긴다. 무엇을 어디에 적는가:

| 층 | 적는 것 | 예 |
|---|---|---|
| `gbm/common.json` | 전사 규약 | `redis.db`, `mongodb.database`, `auth_source` |
| `gbm/mx.json` | 사업부 공통 | REST 등재 항목, Kafka 토픽 매핑, Mongo 계정 이름 |
| `fct/gumi/common.json` | 법인 공통 | 그 공장 망의 타임아웃 |
| `fct/gumi/mx.json` | 법인 × 사업부 | **url, 비밀번호 참조, 브로커 목록** |

### 병합 규칙

- dict끼리는 **재귀 병합** — 아래 층이 `url`만 말해도 위 층의 `db`가 남는다.
- 리스트는 이어붙이지 않고 **교체**한다. "하나 추가"와 "이걸로 교체"를 구별할
  문법이 없고, 그 둘을 헷갈리면 다른 법인의 브로커에 붙는다.
- **`null`은 "이 키를 지워라"는 마커다.** 사업부 공통으로 켠 Kafka를 특정
  법인에서 `"kafka": null`로 끌 수 있다.

> **흔한 함정**: "앞 층이 비어 있으면 재귀를 건너뛴다"는 최적화를 넣으면
> **중첩된 null 마커를 못 지우고 지나간다.** deep-merge는 항상 전체 경로를 탄다.
> `tests/config/test_merge.py::test_빈_층_위에서도_중첩_null이_처리된다`가 지킨다.

### 검증은 층별이 아니라 **병합 결과**에 한다

`gbm/mx.json`이 `mongodb.user`를, `fct/gumi/mx.json`이 `password`를 말한다.
층마다 검증하면 둘 다 "짝이 없다"고 실패한다 — **각 층은 원래 불완전하다.**
합친 뒤에 봐야 하고, 합쳤는데도 짝이 안 맞으면 그건 진짜 오류다.

### 값의 출처를 추적한다

층이 넷이면 "분명히 바꿨는데 안 먹는다"가 반드시 생긴다. 답은 거의 항상
"아래 층이 덮고 있다"이고, 출처가 없으면 네 파일을 다 열어 봐야 안다.

```console
$ python -m src config show
값의 출처 (어느 층이 이겼는가):
  infra.kafka.consumer.bootstrap_server        fct/gumi/mx
  infra.kafka.consumer.topic.topic1            gbm/mx
  infra.mongodb.auth_source                    gbm/common
  infra.mongodb.user                           gbm/mx
  infra.redis.db                               gbm/common
  infra.redis.url                              fct/gumi/mx
  infra.rest.timeout_s                         fct/gumi/common
```

### 사이트 정체성은 파일이 말하지 않는다

층 파일에 `"site": {...}`를 적으면 **거부한다.** 정체성은 `registry.json`과
파일 경로가 정한다. 두 곳에서 오면 어긋났을 때 어느 쪽이 맞는지 아무도 모른다.

`registry.json`이 따로 있는 이유: 디렉터리를 훑어서 유추할 수 없다.
`gbm/mx.json`과 `fct/gumi/`가 있다고 해서 mx가 구미에 있다는 뜻은 아니다.
조합은 사람이 명시한다. `enabled: false`로 아직 개설 안 한 사이트를 적어 둘 수도 있다.

### `auth_source`가 왜 `admin`인가

```
use admin
db.createUser({ user: "dmfReadOnly", roles: [{ role: "read", db: "data" }] })
```

이렇게 만든 계정은 **`admin` DB에 산다.** 읽을 대상은 `data`지만 인증은 `admin`에서
한다 — 접속 문자열에 `authSource=admin`이 없으면 "인증 실패"가 나고, 그 메시지는
계정이나 비밀번호 문제처럼 보여서 한참 헤맨다.

### `group_id`는 "감시할 그룹"이다

스키마 docstring에 못 박아 두고, 테스트가 그 문장이 사라지지 않았는지까지 본다.
이 뜻을 놓치면 모니터링 에이전트가 운영 컨슈머의 파티션을 빼앗는다(2단계 참고).

---

## 비밀번호: `SecretStr`

```python
>>> cfg = RedisConfig(url="redis://h:6379", password="hunter2")
>>> repr(cfg.password)
SecretStr('**********')
>>> cfg.password.get_secret_value()
'hunter2'
```

평문 `str`이면 `print(config)`, 예외 메시지, 로그 한 줄 — 이 셋 중 하나에서
**반드시** 샌다. `SecretStr`은 실수로 찍어도 안 새고, 값을 꺼내려면
`.get_secret_value()`라고 명시적으로 써야 해서 **그 한 줄이 코드 리뷰에서 눈에 띈다.**

## 비밀번호를 config에 `${...}`로 남기는 이유

어댑터가 `os.environ`을 몰래 읽으면 config만 봐서는 "이 사이트를 띄우려면 무엇이
필요한지" 알 수 없다. `${MX_GUMI_REDIS_PASSWORD}`라고 적혀 있으면 **기동 검증이
그 키가 비어 있다고 미리 말해 줄 수 있다.**

```console
$ python -m src boot
❌ 문제 1건:
  [mx/gumi] mx/gumi가 참조하는 env가 비어 있다 — MX_GUMI_MONGO_PASSWORD,
  MX_GUMI_REDIS_PASSWORD, MX_GUMI_REST_KEY. .env에 값을 넣어라(빈 값도 없는 것으로 친다)
```

---

## 기동 거부 철학: 전부 모아서 한 번에

`validate_boot`는 첫 문제에서 죽지 않는다. 문제가 3개면 3개를 다 돌려준다.

하나씩 던지면 사람은 이 짓을 3번 한다 — 고치고 → 돌리고 → 다음 오류 → 고치고.
**사내 서버에 배포해 놓고 이걸 반복하면 30분이 사라진다.**

```python
errors = validate_boot(config_root, env=os.environ)   # 절대 raise하지 않는다
```

`app.json`이 깨져도 사이트 검증을 계속하고, `a.json`이 깨져도 `b.json`을 계속 본다.
마지막에 `list[BootError]`로 전부 보고한다. 검증기 자신이 죽으면 "통과했는지
실패했는지"조차 알 수 없으므로 최외곽에 `except Exception`도 둔다.

---

## 만드는 중에 테스트가 잡은 버그 하나

`${VAR}`를 찾는 정규식을 처음엔 이렇게 썼다:

```python
_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")   # 환경변수 이름 규칙
```

문법적으로 옳다. 그런데 `${MY-KEY}`처럼 **이름에 하이픈이나 오타가 섞이면
참조로 인식조차 안 되고**, `"${MY-KEY}"`라는 문자열이 그대로 비밀번호가 되어
소켓에 나간다. 증상은 "인증 실패" 하나뿐이라 원인이 안 보인다.

넓게 잡아서 env에 없으면 **누락으로 보고**하도록 고쳤다:

```python
_REFERENCE = re.compile(r"\$\{([^{}]+)\}")
```

> 이 버그는 "문제 셋을 셋 다 보고하는가" 테스트에서 드러났다. 일부러 넣은
> 세 번째 오류가 **보고되지 않아서** 테스트가 실패했고, 왜인지 보니 참조가
> 인식조차 안 되고 있었다. 검증 로직을 테스트하지 않았으면 이건 사내에서
> "비밀번호를 분명히 넣었는데 인증이 안 된다"로 나타났을 것이다.

---

## 테스트

```
tests/config/test_envresolve.py    8   ${} 치환, 누락 수집, 이상한 이름
tests/config/test_merge.py        11   층 병합, null 마커, 출처 추적
tests/config/test_schema_site.py  13   오타·모순·SecretStr·Kafka 형식
tests/config/test_loader.py       15   4층 병합·site 거부·JSON/스키마/한글/env
tests/config/test_boot.py          9   전부 모아 보고, registry 중복, 비활성 건너뛰기
```

`test_loader.py`의 마지막 테스트를 보라:

```python
def test_env는_기본값이_없어_반드시_넘겨야_한다(tmp_path):
    with pytest.raises(TypeError, match="env"):
        load_site_config(path)      # 기본값이 있으면 누군가 반드시 안 넘긴다
```

`env=os.environ`을 기본값으로 두면 편하지만, **한 군데서 안 넘기는 사고**가
그때부터 가능해진다. 그 경로만 치환이 안 된 채로 돌고 아무도 모른다.

→ 다음: [3b단계 — 실제 어댑터와 접속 확인](step-03b-adapters.md)
