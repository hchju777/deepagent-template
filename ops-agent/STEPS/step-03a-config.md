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

```json
{
  "site": { "gbm": "mx", "fct": "gumi" },
  "infra": {
    "redis":   { "url": "redis://...", "db": 0,
                 "password": "${MX_GUMI_REDIS_PASSWORD}" },
    "mongodb": { "url": "mongodb://...", "database": "data",
                 "user": "dmfReadOnly", "password": "${MX_GUMI_MONGO_PASSWORD}",
                 "auth_source": "admin" },
    "kafka":   { "consumer": { "bootstrap_server": ["b1:9092","b2:9092","b3:9092"],
                               "group_id": "GUMI_DMF_CONSUMER",
                               "topic": { "topic1": "GUMI_TOPIC" } } }
  }
}
```

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

```
$ python -c "from src.boot import validate_boot; ..."
[sites/mx-gumi.json] config.example/sites/mx-gumi.json가 참조하는 env가 비어 있다
  — MX_GUMI_MONGO_PASSWORD, MX_GUMI_REDIS_PASSWORD. .env에 값을 넣어라(빈 값도 없는 것으로 친다)
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

## 테스트 63개 통과

```
tests/config/test_envresolve.py    8   ${} 치환, 누락 수집, 이상한 이름
tests/config/test_schema_site.py  13   오타·모순·SecretStr·Kafka 형식
tests/config/test_loader.py       10   파일 없음/JSON 깨짐/스키마/한글/env 필수
tests/config/test_boot.py          7   전부 모아 보고, 중복 사이트, 자기 생존
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
