"""concern 축 — 무엇이 이상한가(스펙 §3.4).

- `system`: 파이프라인이 고장. Kafka lag, Redis TTL 만료, Mongo 미갱신, API 5xx.
- `operation`: 데이터는 흐르는데 현장 상태가 이상. 0/0/0, 생산중이어야 하는데 NO PLAN.

**사람이 config에 적는다.** 응답 모양으로 추론하지 않는 이유는 라우팅 근거가
재현·감사 가능해야 하기 때문이다(규율 6) — "왜 이 메일이 나한테 왔나"에 답할 수
있어야 한다.

기본값 `"system"`은 **분류가 아니라 마이그레이션 비용에 대한 타협이다.** 한때
"기존 rule은 전부 파이프라인 신호이므로 system이 옳은 답"이라고 적었으나 그건
거짓이다 — `max`를 불량 수에 걸면 기존 rule로 쓴 명백한 현장 이상이고 그것이
조용히 `system`으로 라우팅된다. rule은 축에 대해 중립이고, 축을 정하는 것은
**점검이 무엇을 묻는가**이지 어떤 rule을 쓰는가가 아니다.

그 구멍을 전부 막으려면 이 필드를 필수로 올려야 하는데, 그러면 라우팅과 무관한
테스트 픽스처 ~90곳이 이 값을 적게 된다. 대신 두 가지로 좁혔다: ①
`CheckConfig`가 **축 전용 rule**(`all_zero`·`expected_state`)에는 명시를 요구한다
②`concern`이 보고서 헤더에 실려 오분류가 첫 finding에서 눈에 띈다. 이 타협이
틀렸다고 판명되면(운영자가 실제로 오분류한다면) 그때 필수로 올린다 — 그 시점엔
근거가 생긴다.

값이 둘뿐인 이유도 이벤트 어휘와 같다(규율 7) — 둘로 표현 불가능한 것을 만나기
전엔 늘리지 않는다.

**이 타입만 사는 파일인 이유는 레이어 방향이다.** config(`schema_app`의 수신자
검증·`schema_site`의 CheckConfig)와 domain(Finding·Case·CaseRecord)과 발행
(mail·report·briefing)이 전부 이 어휘를 쓰는데, 모든 domain 모델이 이미
`schema_app.StrictModel`에 의존하므로 `schema_app`이 `domain.patrol`을 import하면
순환이 닫힌다. 아무것도 import하지 않는 leaf에 두면 그 문제가 사라지고, 값을
여러 곳에 베껴 언젠가 갈라지는 것도 막는다.
"""
from typing import Literal

Concern = Literal["system", "operation"]

CONCERNS: tuple[str, ...] = ("system", "operation")
"""런타임에 값 목록이 필요한 자리(config 키 검증 등)를 위한 짝.

`typing.get_args(Concern)`으로도 얻을 수 있지만, 그것은 타입 내부 표현에 기대는
접근이라 파이썬 버전에 따라 흔들린다.
"""
