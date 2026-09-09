# 2단계 — 도메인 모델과 포트

## 목적

조사 엔진이 다룰 **명사**를 정하고, 대상 시스템에 닿는 **표면**을 못 박는다.
여기서 심는 규율이 둘이고, 둘 다 나중에 넣으면 비싸다.

---

## 규율 ③: 실패는 예외가 아니라 값이다 (무raise)

Redis가 안 붙거나 Mongo가 타임아웃 나는 것은 "예상 밖의 일"이 아니라
운영 중에 **정상적으로 일어나는 일**이다.

여기서 `raise`하면 그 예외는 순찰 잡을 타고 스케줄러까지 올라간다.
APScheduler는 예외를 낸 잡을 조용히 스케줄에서 **빼버릴 수 있고**, 그러면
순찰이 스스로 죽어도 아무도 모르는 상태가 된다. 밤새 케이스가 안 열렸을 때
그게 "이상이 없어서"인지 "순찰이 죽어서"인지 구별할 수 없다 — 그리고 조용한
쪽이 항상 더 위험하다.

그래서 실패는 `status="error"`로 흡수해서 **정상 반환**한다:

```python
result = await adapter.get("oee:L3")
if result.status == "error":          # try/except가 아니라 if
    return f"읽기 실패: {result.error}"
return f"OEE={result.data}"
```

`ProbeResult.failed(...)`가 실패에도 **언제 실패했는지**를 봉투에 남긴다.
"응답이 없다"와 "3분 전에 응답이 없었다"는 다른 사실이다.

### 모순된 상태는 아예 만들 수 없게 한다

```python
ProbeResult(status="error", ...)              # error 없이 → ValidationError
ProbeResult(status="ok", error="timeout")     # 성공인데 에러 → ValidationError
```

`status`만 보고 분기하는 코드가 안전하려면 이 둘이 **구조적으로 불가능**해야 한다.

---

## "잘렸다"를 조용히 넘기지 않는다

상한(`limit=100`)에 걸려 100건만 읽은 것과, 실제로 100건뿐인 것은 **완전히
다른 사실**이다. 전자를 후자로 착각하면 이런 결론이 나온다:

> "최근 1시간에 불량 기록이 없었다 → 설비는 정상"

실제로는 상한 밖에 있었을 뿐인데 말이다. **부정 증거("없다")는 표본이 완전할
때만 성립한다.**

```python
Envelope(observed_at=T0, complete=False)                      # 이유 없으면 거부
Envelope(observed_at=T0, complete=True, truncated_reason=...)  # 모순이라 거부
```

양방향으로 막는다. `complete=False`인 봉투로는 "없다"를 주장할 수 없고,
나중에 판정 검증 단계(10단계)가 이 플래그를 본다.

---

## 규율 ④: 읽기 전용을 코드의 성질로 만든다

포트(ABC)에 `post`/`put`/`delete`를 **아예 만들지 않는다.** 그러면 "쓰라"고
말하는 것 자체가 표현 불가능해진다. LLM이 도구로 이 포트를 받을 때도
마찬가지다 — 존재하지 않는 메서드는 환각으로도 부를 수 없다.

`tests/domain/test_ports.py`가 표면을 단정한다:

```python
WRITE_VERBS = {"post", "put", "delete", "set", "insert", "update",
               "write", "save", "produce", "send", "commit", ...}
```

이 이름이 포트에 나타나면 **테스트가 실패한다.** 급할 때 한 줄 추가하고
리뷰에서 놓치는 경로를 막는다.

### Kafka는 특히 조심해야 한다

```python
consumer.subscribe(topics, group_id="...")    # ← 이건 읽기가 아니다
```

컨슈머 그룹에 참여하면 브로커가 **리밸런스를 돌리고** `__consumer_offsets`에
커밋이 기록된다. 그리고 같은 `group_id`를 쓰는 실제 서비스가 있으면 우리가
그 서비스의 **파티션을 빼앗는다.** 모니터링하러 들어가서 대상을 멈추는 셈이다.

그래서 `KafkaInspectorPort`는 두 가지만 한다:
- `group_offsets(group)` — AdminClient로 **다른** 그룹의 lag을 밖에서 조회
- `tail(topic, limit)` — `assign()`으로 그룹 밖에서 직접 읽기(커밋 없음)

---

## 규율 ⑤: 의존 방향을 테스트가 지킨다

```
presentation  →  application  →  domain  ←  infrastructure
```

`domain`이 `infrastructure`를 import하는 순간, 그 아래 모든 테스트가 어댑터를
필요로 하게 된다. 한 번 깨지면 되돌릴 때는 이미 수십 개가 얽혀 있다.

`tests/domain/test_layering.py`가 AST로 import 문을 훑어 막는다.

### 실제로 확인해 본 것

같은 검사기를 원본 템플릿(`../src/domain/`)에 돌려 봤다:

```
case.py:12 — src.config.schema_app
cases.py:6 — src.config.schema_app
envelope.py:12 — src.config.schema_app
... 총 10건
```

전부 `StrictModel`을 `config`에서 가져오느라 생긴 위반이다. 1단계에서
`StrictModel`을 `domain/base.py`에 둔 것이 이걸 미리 막았다 — **그때는 그냥
"기반 모델을 어디 둘까"였는데, 지금 보니 계층 경계를 정하는 결정이었다.**

---

## 테스트 25개 통과

```
tests/domain/test_base.py          4   1단계
tests/domain/test_envelope.py     10   무raise, 잘림, 모순 거부
tests/domain/test_ports.py         5   쓰기 메서드 부재, async, 추상
tests/domain/test_layering.py      3   의존 방향
tests/test_portability.py          3   인코딩(Windows)
```

`test_ports.py`와 `test_layering.py`에는 **검사기 자신을 검사하는 테스트**가
들어 있다. 위반 코드를 일부러 만들어 "이게 잡히는가"를 확인한다 — 검사기가
아무것도 안 잡는 채로 초록이면 그게 제일 나쁘다.

## 아직 없는 것

- `Case`·`Verdict` — 6·8단계로 미뤘다. 저장소와 엔진 없이 보면 추상적이기만 하다.
- `Envelope.requested_as_of`/`effective_as_of` — Kafka 어댑터가 붙는 3단계에서
  추가한다. 보존 기간 밖으로 밀려 earliest로 폴백했을 때 "요청한 시점"과
  "실제로 얻은 시점"이 다르다는 것을 말해야 하는데, 그 상황이 아직 없다.
- `RestProberPort` — 대상 REST API가 있는지 확인 후.

→ 다음: [3단계 — config와 실제 DB 연결](step-03-config-and-adapters.md)
