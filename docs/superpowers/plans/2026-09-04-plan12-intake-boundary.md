# 계획 12 — 접수 경계 재구성과 요청 주체 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 케이스를 **먼저 열고** 접수 문답을 그 위에서 진행하게 바꾸고, 사이트 축을 사람이 안 줘도 정하게 하며, 요청 주체를 레코드에 박제한다.

**Architecture:** 지금 `_drive_chat`은 `await intake(...)`가 **끝난 뒤에야** `repo.new_case_id()`를 부른다. CLI에서는 문제가 없다 — 프로세스 하나가 처음부터 끝까지 서 있으니까. HTTP에서는 셋이 동시에 깨진다: ① 첫 요청이 돌려줄 `case_id`가 없다 ② 접수 중 되묻기가 필요하면 클라이언트가 끊기거나 서버가 재시작되는 순간 문답 전체가 사라진다 ③ 웹 사용자는 `--gbm/--fct`를 주지 않는데 `intake.py`는 "이 둘이 유일한 근거"라고 명시한다.

셋 다 **접수 경계의 문제이지 전송의 문제가 아니다.** HTTP를 얹기 전에 여기서 고친다.

**Tech Stack:** Python 3.12 · pydantic 2 · pytest(`asyncio_mode=auto`) — **새 의존성 없음**

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) §3.5(인증 — 필드 1개 + 술어 1개) · §4.4(웹 서비스 표면).

**선행:** 계획 11 머지(`118f0ca`, 505 tests).
**후속:** 계획 13(HTTP 표면)이 여기서 만든 `resolve_scope`/`open_case`/`AccessPolicy`를 그대로 엔드포인트에 붙인다.

---

## P6을 셋으로 쪼갠 이유

방향 문서의 P6 한 줄("웹 서비스: API + SSE + 인증/requested_by + 사이트 해석 + 다중 RCA 후보 + Timeline")은 **독립적으로 배포 가능한 하위 시스템 넷**을 담고 있다. 한 계획으로 쓰면 리뷰어가 "인증은 맞는데 SSE가 틀렸다"를 말할 방법이 없다.

| 계획 | 내용 | 왜 이 순서인가 |
|---|---|---|
| **12(여기)** | 접수 경계 재구성 + 사이트 해석 + `requested_by`/접근 술어 | HTTP 없이 CLI로 전부 검증된다. 나머지 둘의 **전제**다 — `POST /cases`가 돌려줄 `case_id`가 이 계획 없이는 존재하지 않는다 |
| 13 | `api` 프로세스 + 엔드포인트 9종 + SSE + 멱등 답변 | 전송 계층. 12가 만든 함수를 붙이기만 한다 |
| 14 | 다중 RCA 후보 + Timeline | `Verdict` 모양 변경 + 표현. 전송과 무관하고 보고서에도 나타난다 |

`GET /digests/{scenario}`는 P7(Fleet 집계)이 만들 시나리오 실행 기록을 읽는다 — **P6 범위 밖**이고 계획 13에서도 빼야 한다.

---

## Global Constraints

- **무raise**: 접수·해석·접근 판정 전부. 실패는 반환 타입의 상태로 흡수한다(`ScopeResult.status`, `IntakeTurn.status`). 접수 경로에서 예외가 새면 HTTP 500이 되고, 그 시점에 케이스가 이미 열려 있으면 고아가 된다.
- **시계 주입**: `src/__main__.py` 밖에서 `datetime.now()` 금지.
- **StrictModel**: 새 pydantic 모델은 `StrictModel` 상속.
- **수명주기는 코드가 쥔다(규율 4)**: 접수 LLM이 돌려준 값으로 `status`를 정하지 않는다. 상태 전이는 기존 `ALLOWED` 전이표를 그대로 쓴다.
- **통제 경계(규율 6)**: 사이트 해석에서 **후보 목록은 코드가 만든다**(registry의 활성 사이트). LLM은 그 안에서 고를 뿐이고, 목록 밖 값을 고르면 미확정으로 처리한다 — 대상 시스템 접근 권한이 걸린 축을 LLM 자유 서술로 정하게 두면 안 된다.
- **한 경로만**: `chat`과 계획 13의 API가 **같은 함수**를 쓴다. 조립을 베끼면 언젠가 하나가 빠뜨린다(규율 8이 케이스 종결 세 경로에서 얻은 교훈).
- **주석·문서는 한국어, WHY만.** **커밋 메시지는 영어**(CLAUDE.md 언어 관례). 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **테스트**: `rm -rf output/ && .venv/bin/python -m pytest tests/ -q` (기준선 **505 passed**).
- **완료 기준에 프로덕션 경로 스모크를 포함한다.** 계획 6~11에서 "함수는 되는데 호출부가 안 넘긴다"가 여덟 번 나왔다. 각 태스크의 마지막 검증은 **실제로 명령을 쳐서** 확인한다.
- **브랜치**: `feat/plan12-intake-boundary`.

---

## 설계 결정: 사이트 미확정은 케이스를 만들지 않는다

스펙은 "확정 못 하면 기존 되묻기 메커니즘을 그대로 쓴다"고 적었다. 그대로 읽으면 **스코프가 없는 케이스**를 열고 그 위에서 되물어야 하는데, 그러면 `CaseRecord.gbm/fct`가 선택 필드가 되어야 한다. 그 둘은 지문·레저 키·사이트 런타임 맵·retention·접근 술어에 전부 꿰여 있어서, 선택으로 바꾸는 순간 그 다섯 곳이 `None`을 다루게 된다.

**스코프가 없는 케이스는 뜻이 없다** — 어떤 어댑터로 무엇을 조사할지가 정해지지 않고, 누가 볼 수 있는지도 판정할 수 없다. 그래서:

- 호출자가 `gbm/fct`를 주면 그대로 쓴다(CLI의 현재 동작).
- 안 주면 **registry의 활성 사이트를 후보로** 해석을 시도한다.
- 확정되면 케이스를 연다.
- 미확정이면 **케이스를 만들지 않고** 후보 목록과 질문을 돌려준다. 호출자가 사람에게 묻고 `gbm/fct`를 실어 다시 제출한다.

무상태 재제출이 스펙의 "제출→조회 분리"와 같은 모양이고, 초안(draft) 저장소를 새로 만들지 않아도 된다. **되묻기 메커니즘은 스코프가 정해진 뒤의 접수 문답(`target_locator` 등)에 그대로 쓴다** — 그쪽이 스펙이 걱정한 "클라이언트 끊김·서버 재시작에 문답이 사라진다"의 실제 자리다.

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `src/application/scope.py` | **신설** — 사이트 축 해석 | `resolve_scope(symptom, *, sites, deps, clock, gbm, fct) -> ScopeResult` |
| `src/application/intake.py` | 접수 | 케이스가 **이미 열린 뒤** 도는 턴 단위로 재구성 |
| `src/application/open_case.py` | **신설** — 케이스 개설 한 곳 | `open_case(...) -> CaseRecord` — CLI와 계획 13이 공유 |
| `src/domain/cases.py` | 케이스 레코드 | `requested_by`, `question_kind` |
| `src/domain/access.py` | **신설** — 접근 술어 | `AccessPolicy.can_access(subject, gbm, fct)` |
| `src/config/schema_app.py` | app config | `access.allow` 테이블 |
| `src/__main__.py` | CLI | `chat`이 새 순서를 쓴다, `--requested-by` |
| `src/boot.py` | 기동 검증 | `access.allow`의 사이트 키가 registry에 실재하는가 |

**`open_case.py`를 따로 두는 이유**: 지금 케이스를 여는 코드가 `gate.py`(순찰)와 `__main__.py`(chat) 둘에 있고, 계획 13이 세 번째를 만든다. 케이스 **종결**의 세 경로가 발행 배선을 베껴 하나가 빠뜨렸던 것(규율 8)과 같은 구조가 개설 쪽에서 반복되기 직전이다.

---

## Task 1: 사이트 축 해석 — 후보는 코드가 만든다

**Files:** Create `src/application/scope.py` · Test `tests/application/test_scope.py`

**Interfaces:**
- Produces: `ScopeResult(status: Literal["resolved","unresolved"], gbm, fct, candidates, questions, problems)`
- Produces: `async def resolve_scope(symptom, *, sites, deps, clock, gbm=None, fct=None) -> ScopeResult`

`sites`는 `[(gbm, fct)]` 목록이다 — `SiteRuntime`이 아니라 이 좁은 형태를 받는 이유는 계획 13의 `api` 프로세스가 **대상 시스템에 붙지 않기** 때문이다(스펙 §3.1: "대상 시스템 접근은 어댑터 층에서만. `api`는 대상 시스템에 직접 붙지 않는다"). 어댑터가 달린 `SiteRuntime`을 요구하면 그 성질이 깨진다.

**LLM은 후보 안에서만 고른다.** 목록 밖 값을 돌려주면 미확정이다 — 대상 시스템 접근 권한이 걸린 축을 자유 서술로 정하게 두면, 프롬프트에 실린 증상 문자열 하나가 다른 법인의 Redis/Mongo를 읽는 조사를 열 수 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/application/test_scope.py
async def test_호출자가_준_스코프는_LLM을_부르지_않는다():
    # CLI의 현재 동작이다. 사람이 이미 답한 것을 다시 묻지 않는다.
    calls = []
    deps = _deps(spy=calls)
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=deps, clock=lambda: T,
                              gbm="mx", fct="gumi")
    assert out.status == "resolved" and (out.gbm, out.fct) == ("mx", "gumi")
    assert calls == []


async def test_후보_밖의_답은_미확정이다():
    # 증상 문자열 하나가 다른 법인의 Redis/Mongo를 읽는 조사를 열 수 있다.
    deps = _deps(reply='{"gbm": "다른법인", "fct": "어딘가"}')
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=deps, clock=lambda: T)
    assert out.status == "unresolved"
    assert out.candidates == [("mx", "gumi")] and out.questions


async def test_후보가_하나뿐이면_LLM_없이_확정한다():
    # 사이트가 하나인 설치에서 매번 LLM을 부르는 것은 낭비이고 실패 지점이다.
    calls = []
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=_deps(spy=calls),
                              clock=lambda: T)
    assert out.status == "resolved" and calls == []


async def test_LLM이_고른_후보는_확정된다():
    deps = _deps(reply='{"gbm": "mx", "fct": "suwon"}')
    out = await resolve_scope("수원 라인이 멈췄다", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=deps, clock=lambda: T)
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "suwon")


async def test_LLM_실패는_미확정이지_예외가_아니다():
    for reply in ("파싱 불가", "", '{"gbm": "mx"}'):        # fct 누락
        out = await resolve_scope("증상", sites=[("mx", "g"), ("mx", "s")],
                                  deps=_deps(reply=reply), clock=lambda: T)
        assert out.status == "unresolved" and out.problems


async def test_활성_사이트가_없으면_미확정이다():
    out = await resolve_scope("증상", sites=[], deps=_deps(), clock=lambda: T)
    assert out.status == "unresolved" and "활성 사이트" in " ".join(out.problems)
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: 통과 확인**

`questions`는 사람에게 보여줄 문장이다("어느 사이트인가? 후보: mx/gumi, mx/suwon"). 후보를 **반드시 함께** 돌려준다 — 질문만 주면 호출자가 유효한 답의 집합을 모른다.

- [ ] **Step 5: 커밋**

---

## Task 2: 케이스를 먼저 연다

**Files:** Create `src/application/open_case.py` · Modify `src/domain/cases.py` · Test `tests/application/test_open_case.py`

**Interfaces:**
- Produces: `open_case(*, repo, store, symptom, gbm, fct, concern, requested_by, clock, on_event) -> CaseRecord`
- Produces: `CaseRecord.requested_by: str | None = None`

케이스는 **원문 증상과 확정된 스코프만으로** 열린다. `target_locator`는 아직 `None`이고, 접수가 그 뒤에 채운다.

`fingerprint`는 지금처럼 `fingerprint(gbm, fct, "chat", case_id)`로 둔다 — case_id가 들어가 사람이 연 케이스는 서로 절대 같은 지문을 갖지 않는 **알려진 결함**이지만, 여기서 고치면 개설 시점에 아직 없는 `target_locator`를 지문 재료로 써야 해서 더 나빠진다. P8(이력 검색)이 갚는다. **이 사실을 코드 주석에 남겨라** — 지금 지문이 무의미하다는 것을 모르고 이력 매칭을 얹으면 조용히 안 맞는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
async def test_케이스는_접수보다_먼저_열린다():
    # HTTP 첫 요청이 돌려줄 case_id가 있어야 한다. 지금은 intake가 끝나야 생긴다.
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    events = []
    record = open_case(repo=repo, store=store, symptom="OEE가 이상하다", gbm="mx", fct="gumi",
                       concern="system", requested_by="alice", clock=lambda: T,
                       on_event=events.append)
    assert record.id and record.status == "open" and record.target_locator is None
    assert record.requested_by == "alice"
    assert repo.get(record.id) is not None            # 즉시 영속된다
    assert [e.event for e in events] == ["case_status_changed"]


def test_원문_증상이_증거로_박제된다():
    # 사람이 처음 쓴 문장은 이후 접수가 다듬어도 원문이 남아야 한다 — 판정이
    # "무엇을 물었나"를 되짚을 유일한 근거다.
    ...
    assert any(r.source == "human:symptom" for r in store.list_evidence(record.id))
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인 → Step 5: 커밋**

---

## Task 3: 접수 문답을 턴마다 박제한다

**Files:** Modify `src/application/intake.py` · `src/domain/cases.py` · Test `tests/application/test_intake.py`

**Interfaces:**
- Produces: `async def intake_turn(case_id, *, repo, store, deps, topology, clock, answer=None) -> IntakeTurn`
- Produces: `IntakeTurn(status: Literal["done","asking","error"], question, target_locator, problems)`
- Produces: `CaseRecord.question_kind: Literal["intake","investigation"] | None = None`

지금 `intake()`는 `ask` 콜백으로 **프로세스 안에서** 되묻고, 문답을 마지막에 한 번 돌려준다. 프로세스가 죽으면 전부 사라진다.

턴 단위로 바꾼다: 한 번 부르면 **한 번의 LLM 호출**을 하고, 더 물어야 하면 케이스를 `awaiting_human`으로 파킹하며 질문을 `question`에 남긴다. 다음 호출이 `answer`를 들고 오면 그것을 **먼저 증거로 박제한 뒤** 이어간다.

**`question_kind`가 필요한 이유**: `awaiting_human`은 지금 "그래프가 interrupt했다"는 뜻뿐이다. 접수도 같은 상태를 쓰면 재개하는 쪽이 **그래프를 재개할지 접수를 이어갈지** 알 수 없다. 상태를 하나 더 만드는 대신 종류를 한 필드로 구별한다 — 전이표(`ALLOWED`)를 건드리지 않는 쪽이 싸다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
async def test_되물을_것이_있으면_파킹하고_질문을_남긴다():
    turn = await intake_turn(case_id, repo=repo, store=store, deps=_deps(reply=_MISSING),
                             topology=TOPO, clock=lambda: T)
    assert turn.status == "asking" and turn.question
    record = repo.get(case_id)
    assert record.status == "awaiting_human" and record.question_kind == "intake"


async def test_답은_이어가기_전에_먼저_박제된다():
    # 박제를 뒤로 미루면 그 사이 프로세스가 죽었을 때 사람의 답이 사라진다.
    # 계획 4b의 F3 경로가 human:answer에서 같은 판단을 했다.
    await intake_turn(case_id, ..., deps=_deps(reply=_MISSING))
    calls = []
    await intake_turn(case_id, ..., deps=_deps(reply="깨진 응답", spy=calls), answer="라인 7")
    assert any("라인 7" in repr(store.get_evidence(case_id, r.id))
               for r in store.list_evidence(case_id))


async def test_접수가_끝나면_target_locator가_레코드에_들어간다():
    turn = await intake_turn(case_id, ..., deps=_deps(reply=_RESOLVED))
    assert turn.status == "done"
    record = repo.get(case_id)
    assert record.target_locator == "rest:/oee"
    assert record.status == "open" and record.question_kind is None


async def test_LLM_실패는_error이지_예외가_아니다():
    turn = await intake_turn(case_id, ..., deps=_deps(reply="파싱 불가"))
    assert turn.status == "error" and turn.problems
    assert repo.get(case_id).status == "open"        # 고아 상태로 남지 않는다
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

**재시도 상한을 코드가 쥔다**(규율 6). 지금 `intake()`는 "재시도 최대 1회"를 코드에 박아 뒀다. 턴으로 바꾸면 호출자가 무한히 부를 수 있으므로, 케이스별 접수 턴 수 상한(`engine.max_intake_turns`, 기본 3)을 두고 넘으면 `error`로 끝낸다 — 넘은 뒤에도 케이스는 살아 있고 `target_locator=None`으로 조사에 들어간다(지금 "이중 실패" 경로와 같은 착지점).

- [ ] **Step 5: 커밋**

---

## Task 4: 재개가 두 종류 질문을 구별한다

**Files:** Modify `src/application/worker.py`(`resume_once` 진입부) · `src/__main__.py` · Test `tests/application/test_worker.py` · `tests/test_cli.py`

`case resume`이 지금은 무조건 그래프를 재개한다. 접수 질문에 파킹된 케이스에 그걸 하면 스레드가 없어 실패하고, 그 실패가 F3 복구 경로를 태워 **접수 질문을 조사 실패로 둔갑**시킨다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
async def test_접수_질문에_답하면_접수를_이어간다():
    # 그래프를 재개하려 들면 스레드가 없어 실패하고, F3가 그 실패를 조사 실패로
    # 기록한다 — 사람 눈에는 "답했는데 조사가 깨졌다"로 보인다.
    ...
    assert repo.get(case_id).question_kind is None
    assert repo.get(case_id).target_locator == "rest:/oee"


async def test_조사_질문은_그대로_그래프를_재개한다():
    # 기존 동작이 안 깨지는지 — question_kind가 None인 옛 레코드도 포함한다.
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

`question_kind is None`(계획 12 이전에 파킹된 레코드)은 **그래프 재개**로 다룬다 — 그때는 접수 파킹이 존재하지 않았으므로 그것만이 가능한 뜻이다.

- [ ] **Step 5: 커밋**

---

## Task 5: 요청 주체와 접근 술어

**Files:** Create `src/domain/access.py` · Modify `src/config/schema_app.py` · `src/boot.py` · `src/__main__.py` · Test `tests/domain/test_access.py` · `tests/test_boot.py`

**Interfaces:**
- Produces: `AccessPolicy(allow: dict[str, list[str]])`, `.can_access(subject, gbm, fct) -> bool`, `.sites_for(subject) -> list[tuple[str, str]] | None`
- Produces: `AppConfig.access.allow: dict[str, list[str]]`(주체 → `["mx/gumi", "mx/*"]`)

스펙 §3.5의 근거를 그대로 옮기면: 읽기 전용이라고 폭발 반경이 작지 않다. 인증 없는 `POST /cases {gbm:"mx", fct:"suwon"}`은 실질적으로 **"수원 법인의 Redis/Mongo/Kafka와 소스 저장소에 읽기 권한을 가진 LLM 에이전트를 돌리고 결과를 메일로 보내라"**는 요청이다.

**지금 하는 이유**: `(gbm, fct)` 축이 이미 레코드·지문·레저 키·사이트 런타임 맵 전체에 꿰여 있어 그 위에 주체를 얹는 건 싸다. 이벤트 스토어·read API·UI를 주체 없이 다 만든 뒤 소급하면 그 셋을 전부 다시 만져야 한다.

**최소형을 지켜라 — 필드 1개, 포트 1개, 검사 1곳.** 역할·권한 등급·리소스별 ACL은 만들지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_선언이_없으면_전부_허용한다():
    # 단일 팀 설치에 인증 설정을 강요하지 않는다 — 빈 테이블은 "제한 없음"이다.
    assert AccessPolicy(allow={}).can_access("alice", "mx", "gumi")


def test_선언이_있으면_목록_밖은_거부한다():
    policy = AccessPolicy(allow={"alice": ["mx/gumi"]})
    assert policy.can_access("alice", "mx", "gumi")
    assert not policy.can_access("alice", "mx", "suwon")
    assert not policy.can_access("bob", "mx", "gumi")       # 선언 없는 주체


def test_사업부_와일드카드():
    policy = AccessPolicy(allow={"alice": ["mx/*"]})
    assert policy.can_access("alice", "mx", "suwon")
    assert not policy.can_access("alice", "gbm2", "suwon")


def test_주체가_없으면_거부한다():
    # 익명 요청이 선언된 테이블을 통과하면 인증이 없는 것과 같다.
    assert not AccessPolicy(allow={"alice": ["mx/gumi"]}).can_access(None, "mx", "gumi")


def test_sites_for는_읽기_필터의_근거다():
    # 목록 API가 같은 술어를 써야 한다 — 접수만 막고 읽기를 안 막으면 무의미하다.
    assert AccessPolicy(allow={"alice": ["mx/gumi"]}).sites_for("alice") == [("mx", "gumi")]
    assert AccessPolicy(allow={}).sites_for("alice") is None      # None = 제한 없음
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

boot: `access.allow`의 사이트 키가 registry에 실재하는지 확인한다. 오타(`mx/gumii`)면 그 주체는 영원히 아무것도 못 보는데 아무도 모른다 — 기동 거부 철학 그대로다. `*` 와일드카드는 사업부 실재만 본다.

CLI: `chat`에 `--requested-by`(기본값은 없음 — 주지 않으면 `None`이고, `allow`가 비어 있을 때만 통과한다). 접수 경계에서 `can_access`를 부르고 거부면 exit 1.

- [ ] **Step 5: 커밋**

---

## Task 6: CLI가 새 순서를 쓴다 + 문서

**Files:** Modify `src/__main__.py` · Test `tests/test_cli.py` · 문서

`_drive_chat`을 Task 1~5의 함수로 다시 조립한다: `resolve_scope` → `open_case` → `intake_turn` 반복(stdin으로 답) → 워커. **계획 13은 같은 함수들을 HTTP 핸들러에 붙인다** — 조립을 베끼지 않는다.

`chat`의 관찰 가능한 동작은 그대로다: 증상을 받고, 필요하면 되묻고, 조사하고, 보고서를 낸다. 바뀌는 것은 **케이스가 언제 열리는가**뿐이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_chat이_스코프_없이도_돈다(tmp_path, ...):
    # --gbm/--fct 없이 증상만으로 사이트를 해석한다(웹 사용자와 같은 경로).
    code = main(["chat", "--symptom", "수원 라인이 멈췄다", ...])
    assert code == 0


def test_스코프_미확정이면_후보를_보여주고_케이스를_안_연다(tmp_path, capsys, ...):
    # 케이스를 만들고 되묻는 대신 후보를 주고 다시 제출하게 한다 — 스코프가
    # 없는 케이스는 어떤 어댑터로 무엇을 조사할지가 정해지지 않는다.
    code = main(["chat", "--symptom", "뭔가 이상하다", ...])
    assert code == 1
    out = capsys.readouterr()
    assert "mx/gumi" in out.err and repo_is_empty


def test_접수_중_프로세스가_죽어도_문답이_남는다(tmp_path, ...):
    # 계획 12의 존재 이유다. 첫 턴 뒤 새 main() 호출로 이어간다.
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

- [ ] **Step 5: 실제로 쳐 본다**

```bash
set -a; . ./.env.example; set +a
python -m src chat --symptom "OEE가 이상하다" --stub-seeds stub-seeds.example.json \
  --config-root config.example --repo-root .          # --gbm/--fct 없이
python -m src case list --config-root config.example --repo-root .
```
`config.example`은 활성 사이트가 하나이므로 LLM 없이 확정되어야 한다.

- [ ] **Step 6: 문서**

- `docs/config-reference.md`: `access.allow` 절, `engine.max_intake_turns` 행, 기동 검증 목록에 새 항목.
- `docs/architecture.md`: §2 모드 ①의 흐름을 새 순서로. **케이스가 접수보다 먼저 열린다**는 사실과 이유.
- `docs/howto.md`: `--gbm/--fct` 없이 `chat`을 쓰는 법, `--requested-by`.
- `docs/glossary.md`: `requested_by`·`question_kind`·사이트 해석.
- `CLAUDE.md` 코드 지도: `scope.py`·`open_case.py`·`access.py`.
- **`grep -rn "intake\|접수" docs/ CLAUDE.md`로 낡은 서술을 전부 찾아라** — 계획 10·11에서 낡은 개수·분류 서술이 각각 문서 두 곳에 남았다.

- [ ] **Step 7: 커밋**

---

## Self-Review

**스펙 커버리지**: §3.5(필드 1개 + 술어 1개 + 접수 경계 한 곳 + 읽기 필터) → Task 5. §4.4의 "접수가 케이스보다 먼저" → Task 2·3. "사이트 해석 단계를 intake 앞에" → Task 1. 나머지 §4.4(엔드포인트·SSE)는 계획 13.

**타입 일관성**: `ScopeResult`/`IntakeTurn` 둘 다 `status` 필드로 3상을 표현한다 — `CheckOutcome`·`AdmitResult`와 같은 형태라 무raise 규율이 반환 타입에 보인다. `AccessPolicy.sites_for`가 `None`을 "제한 없음"으로 쓰는 것은 `[]`("아무것도 못 봄")와 구별하기 위해서다 — 계획 10의 `response_props`가 같은 구별을 했다.

**하지 않는 것**:

| 하지 않는 것 | 왜 |
|---|---|
| HTTP 엔드포인트·SSE | 계획 13. 여기서 만들면 리뷰어가 접수와 전송을 따로 판단할 수 없다 |
| `CaseRecord.gbm/fct`를 선택 필드로 | 지문·레저 키·사이트 맵·retention·접근 술어 다섯 곳이 `None`을 다루게 된다. 스코프 없는 케이스는 뜻이 없다 |
| 접수 초안(draft) 저장소 | 미확정은 케이스를 안 만들고 무상태 재제출로 푼다 |
| 역할·권한 등급·리소스별 ACL | 스펙 §3.5가 "필드 1개, 포트 1개, 검사 1곳"으로 못 박았다. 요구가 관측되기 전엔 늘리지 않는다 |
| 실제 인증(토큰 검증·세션) | 주체는 전송 계층이 정한다(계획 13). 여기는 **주체가 주어졌을 때의 판정**만 |
| `chat` 지문에서 `case_id` 제거 | 개설 시점에 `target_locator`가 아직 없어 지금 고치면 더 나빠진다. P8이 이력 검색과 함께 |
| 접수 문답을 그래프 첫 라운드로 흡수 | 접수는 "무엇을 조사할지"를 정하고 그래프는 "왜 그런지"를 찾는다. 합치면 `max_rounds` 상한이 두 일을 함께 세게 된다 |
| `GET /digests/{scenario}` | P7이 만들 기록을 읽는다 — P6 범위 밖 |
