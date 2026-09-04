# 계획 11 — concern 축과 운영 이상 rule 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "파이프라인이 고장 났다"와 "데이터는 흐르는데 현장 상태가 이상하다"를 코드가 구별하게 하고, 후자를 실제로 탐지하는 rule 둘을 연다.

**Architecture:** 지금 이 시스템이 낼 수 있는 finding은 전부 **파이프라인 신호**다 — `range`(값이 범위를 벗어났다) · `exists`(값이 없다) · `freshness`(갱신이 멈췄다) · `max`(임계 초과). 넷 다 "우리 배관이 새는가"를 묻는다.

사용자가 실제로 받고 싶은 두 번째 종류는 다르다. `/summary/prod`가 `0/0/0`을 돌려주는 것, "생산중이어야 하는데 NO PLAN"인 것 — **배관은 멀쩡한데 현장이 이상한** 경우다. 이건 다른 사람이 받아야 하고, 리드 LLM이 다른 곳을 봐야 하고, 권고가 다른 종류여야 한다.

그래서 `concern: Literal["system", "operation"]`을 config에서 케이스·보고서·발행까지 꿰고, 그 축 위에서만 의미가 있는 rule 둘(`all_zero`·`expected_state`)을 추가한다.

**Tech Stack:** Python 3.12 · pydantic 2 · pytest(`asyncio_mode=auto`)

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) §3.4(concern 축) · §4.1(보고서 헤더) · P5.

**선행:** 계획 10 머지(`56d9e7a`, 471 tests).
**후속:** P7(Fleet 집계)이 `ScenarioConfig.concern`으로 같은 축을 쓴다. P6(웹)이 `GET /cases?concern=`으로 필터한다.

---

## Global Constraints

- **무raise**: rule 판정기는 `KnownRuleError`(설정 오류) 외에는 어떤 입력에도 raise하지 않는다. **데이터 이상은 finding이고 설정 오류는 KnownRuleError다** — 이 경계가 흐려지면 config 실수가 매 순찰 "이상 탐지"로 둔갑한다(CLAUDE.md 규율 1).
- **시계 주입**: `src/__main__.py` 밖에서 `datetime.now()` 금지.
- **StrictModel**: 새 모델은 `StrictModel` 상속.
- **통제 경계(규율 6)**: concern 값은 **사람이 config에 적는다**. LLM이 정하거나 데이터 모양으로 추론하지 않는다 — 라우팅 근거는 재현 가능하고 감사 가능해야 한다.
- **마법 키 냄새 맡기 금지**: 대상 시스템 응답의 모양을 보고 concern을 추론하지 않는다(계획 8 리뷰가 `runner`에서 잡은 형태).
- **주석·문서는 한국어, WHY만.** **커밋 메시지는 영어 제목 + 한국어 본문.** 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **테스트**: `rm -rf output/ && .venv/bin/python -m pytest tests/ -q` (기준선 **471 passed**).
- **완료 기준에 프로덕션 경로 스모크를 포함한다.** 계획 6~10에서 "함수는 되는데 호출부가 안 넘긴다"가 여섯 번 나왔다. 각 태스크의 마지막 검증은 **실제로 명령을 쳐서** 확인한다.
- **브랜치**: `feat/plan11-concern-axis`에서 구현하고 리뷰 통과 후 머지한다.

---

## 설계 결정: `concern`에 기본값을 둔다

스펙 §3.4는 "지금 넣어야 한다 — 나중에 소급하면 모든 레코드를 마이그레이션해야 한다"고 적었고, 그 논리대로면 `CheckConfig.concern`은 **필수 필드**여야 한다. 기본값이 있으면 아무도 생각하지 않고 지나가고, 그건 "끝점 수십 개에 `read_only: true`를 적으라고 하면 전부 `true`로 적는다"(규율 9)와 같은 형태다.

그럼에도 **기본값 `"system"`을 둔다.** 두 가지 이유다:

**비용이 잘못된 자리에 든다.** 필수로 만들면 `CheckConfig` 구성 지점 ~90곳(대부분 테스트 픽스처)을 고쳐야 하는데, 그중 concern이 의미 있는 곳은 거의 없다. 라우팅과 무관한 필드를 픽스처마다 적게 만드는 것은 신호가 아니라 소음이다.

> **정정(리뷰 후).** 처음에는 두 번째 근거로 "지금 있는 rule 4종은 전부 파이프라인 신호를 판정하므로 `system`이 옳은 분류다"를 적었다. **거짓이다** — `max`를 불량 수에 걸면 기존 rule로 쓴 명백한 현장 이상이고, 그것이 조용히 `system`으로 라우팅된다(리뷰어가 실행으로 반증했다). rule은 축에 대해 중립이고, 축을 정하는 것은 **점검이 무엇을 묻는가**이지 어떤 rule을 쓰는가가 아니다. 기본값의 근거는 마이그레이션 비용 하나뿐이다.

근거가 하나로 줄었으므로 완화책을 실제로 넣는다:

1. **축 전용 rule에는 명시를 요구한다.** `all_zero`·`expected_state`를 쓰면서 `concern`을 안 적으면 `CheckConfig` 검증이 거부한다. rule 이름으로 concern을 **추측하지는 않는다** — 큐 깊이가 전부 0인 것은 파이프라인 신호이므로 `"system"`이라 적으면 통과한다. 요구하는 것은 사람이 한 번 답하는 것뿐이다. 이것이 구멍을 다 막지는 못한다(위 `max` 예가 그렇다) — 우리가 그 축을 위해 만든 rule에만 답을 요구할 수 있기 때문이다.
2. **보고서 헤더에 concern을 싣는다.** 두 렌더러 모두. 잘못 분류된 점검은 첫 finding에서 눈에 띈다.

메일 제목과 `patrol status`에는 넣지 않았다 — 보고서 헤더로 충분한지 먼저 보고, 부족하면 그때 늘린다.

이 타협이 틀렸다고 판명되면(운영자가 실제로 오분류한다면) `concern`을 필수로 올린다 — 그 시점엔 근거가 생긴다.

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `src/config/schema_site.py` | 사이트 config 스키마 | `CheckConfig.concern` |
| `src/domain/patrol.py` | 순찰 도메인 | `Finding.concern` |
| `src/domain/cases.py` | 케이스 레코드 | `CaseRecord.concern`, `to_case`가 실어 나른다 |
| `src/domain/case.py` | 엔진 Case | `Case.concern` |
| `src/patrol/runner.py` | 점검 러너 | finding에 concern을 싣는다 |
| `src/patrol/gate.py` | 게이트 | finding→레코드로 concern을 옮긴다 |
| `src/application/briefing.py` | 브리핑 | concern별 조사 방향 |
| `src/config/schema_app.py` | app config | `MailConfig.recipients_by_concern` |
| `src/presentation/mail.py` | 메일 발송 | concern으로 수신자를 고른다 |
| `src/domain/report_model.py`·`report.py`·`report_html.py` | 보고서 | 헤더에 concern |
| `src/patrol/rules.py` | rule 판정 | `all_zero`, `expected_state` |
| `src/boot.py` | 기동 검증 | `recipients_by_concern` 키 검증 |

---

## Task 1: concern을 config에서 케이스까지 꿴다

**Files:** Modify `src/config/schema_site.py` · `src/domain/patrol.py` · `src/domain/cases.py` · `src/domain/case.py` · `src/patrol/runner.py` · `src/patrol/gate.py` · Test `tests/patrol/test_runner.py` · `tests/patrol/test_gate.py` · `tests/domain/test_cases.py`

**Interfaces:**
- Produces: `Concern = Literal["system", "operation"]` (`src/domain/patrol.py` — rule·config·케이스가 모두 쓴다)
- Produces: `CheckConfig.concern`, `Finding.concern`, `CaseRecord.concern`, `Case.concern`

**`Concern`을 `src/domain/patrol.py`에 두는 이유**: config·도메인·발행이 전부 이 타입을 쓰는데, `schema_site`에 두면 domain이 config를 import하게 된다(레이어 방향 위반). `patrol.py`는 이미 `Finding`/`CheckOutcome`이 사는 곳이고 domain이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/patrol/test_runner.py
async def test_점검의_concern이_finding에_실린다():
    # 라우팅 근거는 사람이 config에 적은 값이어야 한다 — 데이터 모양으로 추론하면
    # 재현도 감사도 안 된다(규율 6).
    adapters = _adapters_with({"oee": 512})
    check = CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/oee",
        "concern": "operation",
        "params": {"rule": "range", "field": "body.oee", "min": 0, "max": 100}})
    out = await run_check("mx", "gumi", "c", check, adapters=adapters,
                          store=InMemoryCaseStore(), clock=lambda: T)
    assert out.finding.concern == "operation"


# tests/patrol/test_gate.py
def test_게이트가_concern을_케이스에_옮긴다():
    # 여기서 끊기면 보고서·메일이 전부 기본값으로 떨어진다 — 계획 9의 시간대
    # 배선이 정확히 그렇게 끊겼다.
    finding = _finding(concern="operation")
    admit = admit_finding(finding, repo=repo, store=store, clock=lambda: T)
    assert repo.get(admit.case_id).concern == "operation"
    assert admit.case.concern == "operation"       # to_case까지 이어진다
```

- [ ] **Step 2: 실패 확인** — `CheckConfig`가 `concern`을 모른다(`extra="forbid"`).

- [ ] **Step 3: 구현**

```python
# src/domain/patrol.py
Concern = Literal["system", "operation"]
"""무엇이 이상한가의 축 — 라우팅의 기반(스펙 §3.4).

- system: 파이프라인이 고장. Kafka lag, Redis TTL 만료, Mongo 미갱신, API 5xx.
- operation: 데이터는 흐르는데 현장 상태가 이상. 0/0/0, 생산중이어야 하는데 NO PLAN.

**사람이 config에 적는다.** 데이터 모양으로 추론하지 않는 이유는 라우팅 근거가
재현 가능하고 감사 가능해야 하기 때문이다(규율 6) — "왜 이 메일이 나한테 왔나"에
답할 수 있어야 한다.
"""
```

`CheckConfig.concern: Concern = "system"` (기본값 근거는 위 "설계 결정" 절).
`Finding.concern: Concern = "system"` / `CaseRecord.concern: Concern = "system"` / `Case.concern: Concern = "system"`.
`runner.make_finding`이 `concern=check.concern`을, `gate`가 `concern=finding.concern`을, `CaseRecord.to_case`가 `concern=self.concern`을 넘긴다.

- [ ] **Step 4: 통과 확인 + 변이 확인**

각 홉(runner→finding, gate→record, to_case→Case)을 **하나씩 지우고** 빨간불이 뜨는지 보라. 안 뜨면 그 홉은 무테스트다.

- [ ] **Step 5: 커밋**

---

## Task 2: `all_zero` — 0/0/0을 판정한다

**Files:** Modify `src/patrol/rules.py` · Test `tests/patrol/test_rules.py`

**Interfaces:** Produces: rule 이름 `"all_zero"`, params `{field, min_count?}`

사용자가 든 첫 번째 운영 이상이다. `/summary/prod`가 `{"badge": [0, 0, 0]}`을 돌려준다 — **응답은 정상이고 배관도 멀쩡한데 현장 숫자가 전부 0**이다.

`exists`로는 안 된다: 값이 있으니 통과한다. `max`로도 안 된다: 0은 임계를 안 넘는다. `range(min=1)`은 "하나라도 0이면 finding"이라 다른 뜻이다 — 야간에 한 라인만 쉬어도 울린다.

**빈 것과 전부 0인 것을 구별한다.** `[]`는 "전부 0"이 아니라 "표본이 없다"이고, 그 둘을 같은 finding으로 묶으면 "질문을 잘못했다"와 "현장이 멈췄다"가 한 통에 섞인다 — 계획 9가 전부-또는-전무로 막으려던 바로 그 혼동이다. `min_count`(기본 1) 미만이면 **다른 사유의 finding**을 낸다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_all_zero는_전부_0일_때만_finding이다():
    assert _judge({"badge": [0, 0, 0]}, field="body.badge").status == "finding"
    assert _judge({"badge": [0, 3, 0]}, field="body.badge").status == "ok"
    assert _judge({"badge": 0}, field="body.badge").status == "finding"       # 스칼라
    assert _judge({"badge": {"a": 0, "b": 0}}, field="body.badge").status == "finding"  # dict


def test_빈_표본은_전부_0과_다른_사유다():
    # "질문을 잘못했다"와 "현장이 멈췄다"를 한 통에 넣으면 구별할 수 없게 된다.
    verdict = _judge({"badge": []}, field="body.badge")
    assert verdict.status == "finding" and "표본" in verdict.reason
    assert "전부 0" not in verdict.reason


def test_min_count_미만이면_판정하지_않는다():
    # 라인 30개 중 2개만 돌아온 표본으로 "전부 0"을 단정하면 안 된다.
    verdict = _judge({"badge": [0, 0]}, field="body.badge", min_count=3)
    assert verdict.status == "finding" and "표본" in verdict.reason


def test_비수치가_섞이면_데이터_이상이지_설정_오류가_아니다():
    # 값이 이상한 것과 설정이 잘못된 것은 다른 문제다(규율 1).
    verdict = _judge({"badge": [0, "없음", 0]}, field="body.badge")
    assert verdict.status == "finding" and "수치" in verdict.reason


def test_field가_없으면_설정_오류다():
    import pytest
    with pytest.raises(KnownRuleError):
        _judge({"badge": [0]}, field=None)


def test_min_count가_수치가_아니면_설정_오류다():
    import pytest
    with pytest.raises(KnownRuleError):
        _judge({"badge": [0]}, field="body.badge", min_count="셋")
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: 통과 확인**

`0`과 `False`를 구별하라 — 파이썬에서 `False == 0`이다. `_is_exact`(`query_rules.py`)가 이미 같은 함정을 다룬다. bool이 섞인 것은 수치가 아니므로 데이터 이상 finding이다.

`0.0`과 `-0.0`은 0이다. `NaN`은 0이 아니고 수치도 아니다 — 데이터 이상 finding.

- [ ] **Step 5: 커밋**

---

## Task 3: `expected_state` — "생산중이어야 하는데 NO PLAN"

**Files:** Modify `src/patrol/rules.py` · Test `tests/patrol/test_rules.py`

**Interfaces:** Produces: rule 이름 `"expected_state"`, params `{field, expect, when?}`

사용자가 든 두 번째 운영 이상이다. **한 필드의 값이 다른 필드의 값에 비추어 말이 되는가**를 본다.

```json
{
  "rule": "expected_state",
  "field": "body.prod_status",
  "expect": ["생산중", "대기"],
  "when": {"field": "body.plan_status", "equals": "생산중"}
}
```

"계획상 생산중일 때, 실제 상태는 `생산중` 또는 `대기` 중 하나여야 한다. `NO PLAN`이면 finding."

`when`이 없으면 무조건 판정한다. `when`이 안 맞으면 **ok**다 — 판정 대상이 아닌 것과 정상인 것을 구별할 필요가 있는지 검토했으나, 3상(ok/finding/skipped)의 `skipped`는 **예산 소진** 전용이고(러너가 그렇게 쓴다) 여기에 세 번째 뜻을 얹으면 `patrol status`가 두 가지 다른 이유를 한 칸에 보여준다. 대신 finding이 아니라는 사실만 남긴다.

**`expect`는 값 목록이지 표현식이 아니다.** 비교 연산자·정규식·범위를 열면 rule이 작은 질의 언어가 되고, 그건 config가 코드가 되는 길이다 — 규율 6이 "재현·상한·감사"를 코드에 두라고 한 이유와 반대 방향이다. 필요해지면 그때 새 rule을 연다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_기대한_상태가_아니면_finding이다():
    data = {"plan_status": "생산중", "prod_status": "NO PLAN"}
    v = _judge_state(data, field="body.prod_status", expect=["생산중", "대기"],
                     when={"field": "body.plan_status", "equals": "생산중"})
    assert v.status == "finding" and "NO PLAN" in v.reason and "생산중" in v.reason


def test_기대한_상태면_ok다():
    data = {"plan_status": "생산중", "prod_status": "생산중"}
    assert _judge_state(data, field="body.prod_status", expect=["생산중", "대기"],
                        when={"field": "body.plan_status", "equals": "생산중"}).status == "ok"


def test_when이_안_맞으면_판정하지_않는다():
    # 계획이 없는 라인이 NO PLAN인 것은 정상이다.
    data = {"plan_status": "휴무", "prod_status": "NO PLAN"}
    assert _judge_state(data, field="body.prod_status", expect=["생산중"],
                        when={"field": "body.plan_status", "equals": "생산중"}).status == "ok"


def test_when_없이도_쓸_수_있다():
    assert _judge_state({"prod_status": "NO PLAN"}, field="body.prod_status",
                        expect=["생산중"]).status == "finding"


def test_필드가_없으면_데이터_이상이다():
    # 필드 부재는 finding이지 KnownRuleError가 아니다(규율 1).
    v = _judge_state({"prod_status": None}, field="body.prod_status", expect=["생산중"])
    assert v.status == "finding" and "없" in v.reason


def test_expect가_리스트가_아니면_설정_오류다():
    import pytest
    for bad in (None, "생산중", [], {"a": 1}):
        with pytest.raises(KnownRuleError):
            _judge_state({"prod_status": "x"}, field="body.prod_status", expect=bad)


def test_when의_모양이_틀리면_설정_오류다():
    import pytest
    for bad in ({"field": "body.x"}, {"equals": "y"}, {"field": 1, "equals": "y"}, "문자열"):
        with pytest.raises(KnownRuleError):
            _judge_state({"prod_status": "x"}, field="body.prod_status",
                         expect=["생산중"], when=bad)
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

finding 사유는 **무엇을 기대했고 무엇을 봤는지 둘 다** 적어라. `"NO PLAN"`만 적으면 보고서를 읽는 사람이 왜 그게 문제인지 모른다.

- [ ] **Step 5: 커밋**

---

## Task 4: 발행이 concern으로 갈린다

**Files:** Modify `src/config/schema_app.py` · `src/presentation/mail.py` · `src/boot.py` · Test `tests/presentation/test_mail.py` · `tests/test_boot.py`

**Interfaces:**
- Produces: `MailConfig.recipients_by_concern: dict[str, list[str]] = {}`
- Modifies: `send_report(..., concern: Concern = "system")`

concern 축의 존재 이유가 여기서 처음으로 **실제 효과**를 낸다. 그 전까지는 필드가 실려 다니기만 한다.

```json
"mail": {
  "enabled": true,
  "recipients": ["platform-oncall@example.com"],
  "recipients_by_concern": {"operation": ["mes-ops@example.com"]}
}
```

`recipients_by_concern`에 해당 concern이 있으면 그것을, 없으면 `recipients`로 폴백한다. **폴백을 두는 이유**: 전부 적으라고 강제하면 사람이 같은 목록을 두 번 쓰게 되고, 한쪽만 고치는 순간 조용히 갈라진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
async def test_operation_케이스는_다른_수신자에게_간다():
    cfg = MailConfig(enabled=True, host="h", sender="s",
                     recipients=["platform@x"],
                     recipients_by_concern={"operation": ["ops@x"]})
    sender = _SpySender()
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger, cfg=cfg,
                      clock=lambda: T, concern="operation")
    assert sender.calls[0]["recipients"] == ["ops@x"]


async def test_선언되지_않은_concern은_기본_수신자로_간다():
    # 같은 목록을 두 번 쓰게 만들면 한쪽만 고치는 순간 조용히 갈라진다.
    cfg = MailConfig(enabled=True, host="h", sender="s", recipients=["platform@x"],
                     recipients_by_concern={"operation": ["ops@x"]})
    await send_report("c-1", "제목", "본문", ..., concern="system")
    assert sender.calls[0]["recipients"] == ["platform@x"]


async def test_수신자_기록도_concern을_따른다():
    # 레저의 target이 실제 수신자와 다르면 "누구에게 갔나"를 사후에 알 수 없다.
    ...
    assert ledger.rows[0].target == "ops@x"


def test_알_수_없는_concern_키는_기동을_거부한다(tmp_path):
    # 오타("operations")면 그 목록이 영원히 안 쓰이고 아무도 모른다.
    ...
    assert any("operations" in e.problem for e in errors)
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

`MailConfig`에 검증자를 두는 것과 boot에서 검사하는 것 중 **검증자**를 골라라 — config 로드 시점에 잡히면 `config show`에서도 드러난다. boot 검사는 검증자가 못 보는 것(교차 참조)에만 쓴다.

**호출부를 반드시 확인하라.** `send_report`에 인자를 열어 두고 `daemon._render_case_mail`/`_publish_report`가 안 넘기면 프로덕션에서 기본값으로 떨어진다 — 계획 9의 `timezone_name`이 정확히 그랬다. **기본값을 두지 말고 키워드 필수로 만들어라.**

- [ ] **Step 5: 커밋**

---

## Task 5: 보고서와 브리핑이 concern을 말한다

**Files:** Modify `src/domain/report_model.py` · `src/presentation/report.py` · `src/presentation/report_html.py` · `src/application/briefing.py` · Test 각각

보고서 §1에 concern을 넣는다(스펙 §4.1의 헤더). **오분류가 조용하지 않게 하는 것이 이 태스크의 목적**이므로, 두 렌더러 모두에 넣고 둘 다 테스트한다(HTML이 기본 포맷이다 — 계획 10 리뷰가 여기서 구멍을 잡았다).

브리핑에는 concern별 한 줄을 더한다:

```python
_CONCERN_HINT = {
    "system": "파이프라인 고장을 의심하라 — 상류 서비스·큐·캐시·스키마.",
    "operation": "배관은 정상일 수 있다. 현장 상태와 계획 데이터의 불일치를 의심하라.",
}
```

**이 문장이 LLM의 판정을 대신 하지 않는다.** 어디를 먼저 볼지를 말할 뿐이고, 무엇이 맞는 판단인지는 여전히 LLM이 정한다(규율 6). 힌트가 결론을 지시하면 그건 우리가 판정을 코드에 박은 것이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_보고서가_concern을_보여준다():        # md
def test_HTML도_concern을_보여준다():          # html — 기본 포맷
def test_브리핑이_concern별로_다른_방향을_준다():
    system = build_briefing(_case(concern="system"), slice)
    operation = build_briefing(_case(concern="operation"), slice)
    assert system != operation
    assert "현장" in operation and "현장" not in system
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

- [ ] **Step 5: 커밋**

---

## Task 6: 예시·문서·벤치

**Files:** `config.example/gbm/mx.json` · `knowledge.example/target_api/mx/gumi.json` · `stub-seeds.example.json` · `docs/config-reference.md` · `docs/howto.md` · `docs/glossary.md` · `docs/architecture.md` · `README.md` · `tests/test_bench_scenarios.py`

예시 트리에 **운영 이상 점검 하나**를 추가한다 — `prod.status_matches_plan`(`expected_state`). 시드가 `{"plan_status": "생산중", "prod_status": "NO PLAN"}`을 돌려주게 하고, pinned 명세에도 두 필드를 넣어 기동 검증(계획 10 검사 10·11)을 통과시켜라.

`patrol run`이 이제 **두 종류의 케이스**를 연다 — 그것이 이 계획의 산출물이 실제로 도는 증거다.

벤치 시나리오에 운영 이상 하나를 추가하라. 채점은 `Verdict`의 구조화 필드만 본다(`tests/README.md`) — 보고서 텍스트 매칭 금지.

- [ ] **Step 1: 예시 트리에 점검 추가 + 실제로 쳐서 확인**

```bash
set -a; . ./.env.example; set +a
python -m src knowledge validate --config-root config.example --repo-root .
python -m src patrol run --for-seconds 5 --stub-seeds stub-seeds.example.json \
  --config-root config.example --repo-root .
```
기대: `OK` + 케이스 **둘** + 보고서 둘. 보고서 하나는 `concern: operation`.

- [ ] **Step 2: 문서**

- `docs/config-reference.md`: `patrol.checks.<이름>.concern` 행, `report.mail.recipients_by_concern` 행, rule 표에 `all_zero`·`expected_state` 두 행(params 포함), 기동 검증 목록에 새 항목.
- `docs/glossary.md`: `concern` 항목 — 두 값의 뜻과 **누가 정하는가**(사람, config).
- `docs/howto.md`: "0/0/0 같은 운영 이상을 잡고 싶다" 절.
- `docs/architecture.md`: rule 4종 → 6종, concern이 라우팅을 가른다는 한 줄.
- `README.md`: 예시가 케이스 둘을 연다는 서술(빠른 시작 문단).
- **`grep -rn "rule 판정.*4종\|range.*exists.*freshness.*max" docs/ CLAUDE.md`로 낡은 서술을 전부 찾아라** — 계획 10에서 "프로브 4종"이 문서 두 곳에 남아 있었다.
- `CLAUDE.md` 코드 지도: rule 4종 → 6종.

- [ ] **Step 3: 벤치 시나리오 추가**

- [ ] **Step 4: 전체 통과 + 커밋**

---

## Self-Review

**스펙 커버리지**: §3.4(concern 축을 `CheckConfig`·`CaseRecord`에, 수신자·브리핑 라우팅) → Task 1·4·5. `ScenarioConfig.concern`은 P7이 그 스키마를 만들 때 같이 온다 — 여기서 미리 만들지 않는다(스키마가 없는데 필드만 정의할 수 없다). §4.1 헤더의 concern → Task 5. P5의 "rule 확장(all_zero · expected_state)" → Task 2·3.

**타입 일관성**: `Concern`은 `src/domain/patrol.py`에 한 번 정의하고 config·도메인·발행이 전부 그것을 import한다. `Literal["system","operation"]`을 여러 곳에 베끼면 한쪽에 값을 더할 때 갈라진다.

**하지 않는 것**:

| 하지 않는 것 | 왜 |
|---|---|
| concern을 데이터·응답 모양으로 **추론** | 라우팅 근거가 재현·감사 가능해야 한다(규율 6). "왜 이 메일이 나한테 왔나"에 답할 수 있어야 한다 |
| concern별 **심각도(severity)** 필드 | 지금 소비자가 없다. 심각도는 수신자와 다른 축이고, 쓸 곳이 생기기 전에 만들면 아무도 안 채우는 필드가 된다 |
| `expect`에 비교 연산자·정규식·범위 | rule이 작은 질의 언어가 되면 config가 코드가 된다. 필요해지면 새 rule을 연다 |
| `skipped`에 "판정 대상 아님"을 얹기 | `skipped`는 예산 소진 전용이다. 두 뜻을 한 칸에 넣으면 `patrol status`가 다른 이유를 같게 보여준다 |
| `ScenarioConfig.concern` | P7이 그 스키마를 만들 때 함께. 스키마 없이 필드만 둘 수 없다 |
| concern별 **에스컬레이션 정책**(재시도·호출) | v1 범위 밖(조치 실행은 사람의 몫) |
| 세 번째 concern 값 | 두 개로 표현 불가능한 것을 만나기 전엔 안 늘린다 — 이벤트 어휘와 같은 규율(규율 7) |
