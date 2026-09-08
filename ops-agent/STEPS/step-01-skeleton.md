# 1단계 — 뼈대와, 어길 수 없게 만든 규율 둘

## 만든 것

```
ops-agent/
  requirements.txt        의존성 — 단계마다 자란다
  requirements-dev.txt    테스트용
  pytest.ini              asyncio_mode=auto, live_llm 마커
  src/
    domain/base.py        StrictModel, Clock          ← 이번 단계의 전부
    config/  infrastructure/  patrol/  application/  presentation/
  tests/
    conftest.py           고정 시계 T0
    domain/test_base.py
```

디렉터리를 미리 다 만든 이유: **의존 방향을 처음부터 못 박기 위해서**다.

```
presentation  →  application  →  domain  ←  infrastructure
```

화살표가 전부 `domain`을 향한다. `domain`은 아무것도 import하지 않는다 —
pydantic 말고는. 이 규칙 하나가 나중에 "테스트가 Redis를 필요로 하는" 사태를
막는다. 판정 로직이 도메인에 있고 도메인이 어댑터를 모르면, 판정 테스트는
Redis 없이 돈다.

## 규율 ①: `extra="forbid"`

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

pydantic 기본값은 모르는 키를 **조용히 버린다**. config에

```json
{ "tls_verfiy": false }
```

라고 오타를 내면? 기본값(검증 켬)이 그대로 돈다. 사람은 껐다고 믿고, 코드는
켠 채로 돈다. 이 불일치는 밤에 조용히 틀린다.

`extra="forbid"`는 그 순간을 즉시 검증 오류로 바꾼다. 앞으로 **모든** 모델은
이걸 상속한다 — 도메인 모델도, config 모델도.

## 규율 ②: `datetime.now()`를 함수 안에서 부르지 않는다

```python
Clock = Callable[[], datetime]
```

"3분 지난 데이터를 stale로 볼 것인가"를 판정하는 함수가 안에서 `datetime.now()`를
부르면 그 함수는 **테스트할 수 없다**. 실행할 때마다 답이 달라지기 때문이다.

그래서 시각은 인자로 받는다:

```python
def _is_stale(observed_at, *, clock, max_age_s) -> bool:
    return (clock() - observed_at).total_seconds() > max_age_s
```

테스트는 `lambda: T0`를 주고, 프로덕션은 `datetime.now`를 준다. **코드는 한 벌**이다.
진짜 시계를 부르는 곳은 CLI 진입점 딱 한 곳이 된다.

전역 변수나 기본 인자(`clock=datetime.now`)로 박아 넣지 마라 — 기본값이 있으면
누군가 반드시 안 넘기고, 그 경로만 테스트 밖으로 샌다.

## 실행

```bash
cd ops-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -v
```

```
tests/domain/test_base.py::test_모르는_키는_조용히_버려지지_않고_거부된다 PASSED
tests/domain/test_base.py::test_선언한_키는_평소처럼_받는다 PASSED
tests/domain/test_base.py::test_시계를_주입하면_판정이_결정론이_된다 PASSED
tests/domain/test_base.py::test_같은_함수를_실제_시계로_불러도_같은_모양이다 PASSED
4 passed
```

## 왜 규율부터인가

이 두 개는 나중에 넣을 수 없다. 모델을 200개 만들고 나서 `extra="forbid"`를
켜면 그날 하루가 사라지고, 시계를 30군데서 부른 뒤에 주입으로 바꾸면 그건
리팩터링이 아니라 재작성이다. **처음 10분에 넣으면 공짜고, 나중에 넣으면 비싸다.**

→ 다음: [2단계 — 도메인 모델과 포트](step-02-domain.md)
