# 계획 13 — HTTP 표면과 명령 채널 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 계획 12가 만든 함수들을 HTTP 엔드포인트에 붙이고, 사람의 답을 프로세스 밖에서 워커로 실어 나르는 명령 채널을 만든다.

**Architecture:** `api`는 **실행자가 아니라 클라이언트다**(스펙 §3.1·§5.2-F2). 케이스를 쓰고 이벤트를 읽는다. 조사는 `worker`만 하고, `api`는 대상 시스템(Redis/Mongo/Kafka/REST/코드 저장소)에 **붙지 않는다** — 어댑터를 조립하지 않는다.

그래서 두 가지가 필요하다. ① `POST /cases`가 연 케이스를 워커가 집어 가려면 **접수가 끝났다는 표시**가 레코드에 있어야 한다 — 없으면 데몬의 `requeue_open`이 접수 중인 케이스를 집어 대상 없이 조사한다(계획 12의 F1 경합이 바로 이것이었다). ② `POST /answers`로 들어온 답을 워커가 소비하려면 **답이 레코드에 실려 있어야** 한다 — v1의 인계 노트가 "사람의 답을 실어 나를 프로세스 밖 명령 채널이 없다"고 적어 둔 그 자리다.

접수는 `api`가 인라인으로 돈다. 접수는 조사가 아니고(LLM 호출 하나, 대상 시스템 접근 없음), 되묻는 질문이 응답에 바로 실려야 클라이언트가 폴링하지 않는다. 스펙 §4.4가 `/intake-answers`를 `/answers`와 **별도 엔드포인트**로 둔 이유가 그것이다.

**Tech Stack:** Python 3.12 · pydantic 2 · **FastAPI + uvicorn(신규)** · httpx(테스트 클라이언트, 이미 있음) · pytest(`asyncio_mode=auto`)

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) §3.1(세 프로세스) · §3.5(인증) · §3.6(제출→조회) · §4.4(표면).

**선행:** 계획 12 머지(`54747fd`, 555 tests). **계획 12 계획서 끝의 "계획 13 인계" 5건을 먼저 읽어라.**
**후속:** 계획 14(다중 RCA 후보 + Timeline)가 `GET /cases/{id}`의 판정 payload를 바꾼다. P8이 `POST /cases/{id}/label`을 연다.

---

## Global Constraints

- **`api`는 대상 시스템에 붙지 않는다.** `build_adapters`를 부르는 코드가 `src/api/` 아래에 있으면 안 된다. `tests/api/test_boundary.py`가 import 그래프로 지킨다 — 산문 규율은 읽지 않으면 무력하다(규율 9와 같은 형태).
- **`api`는 조사를 시작하지 않는다.** `run_once`/`resume_once`/`InvestigationWorker`를 `src/api/`에서 부르지 않는다. 답은 **기록**하고, 실행은 워커가 한다.
- **무raise**: 핸들러는 도메인 실패를 HTTP 상태로 옮긴다. 500은 "우리가 예상 못 한 것"에만 — `ScopeResult`/`IntakeTurn`의 `status`가 이미 3~4상이므로 그것을 그대로 옮긴다.
- **시계 주입**: `datetime.now()`는 `src/__main__.py`의 `api` 진입점에서만. 핸들러는 앱 상태에 실린 `clock`을 쓴다.
- **StrictModel**: 요청/응답 모델도 `StrictModel` — 클라이언트가 보낸 알 수 없는 키는 422다.
- **접근 술어는 한 곳**: 쓰기는 `can_access`, 읽기는 `sites_for` — 둘 다 `app.access` 하나에서. 엔드포인트마다 판정을 베끼지 않는다.
- **이벤트 어휘(규율 7)**: 새 이벤트 종류를 만들지 않는다. SSE는 저장된 이벤트 로그의 **무상태 어댑터**다.
- **주석·문서는 한국어, WHY만.** **커밋 메시지는 영어.** 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **테스트**: `rm -rf output/ && .venv/bin/python -m pytest tests/ -q` (기준선 **555 passed**).
- **완료 기준에 프로덕션 경로 스모크를 포함한다.** 계획 6~12에서 "함수는 되는데 호출부가 안 넘긴다"가 열 번 넘게 나왔고, 계획 12는 리뷰 세 라운드가 전부 동시성이었다. 각 태스크의 마지막 검증은 **실제로 서버를 띄우고 curl로** 확인한다.
- **브랜치**: `feat/plan13-http-surface`.

---

## 설계 결정 셋

### ① `intake_done`이 requeue의 문이다

계획 12의 F1 경합(접수 중인 `open` 케이스를 데몬이 집는다)을 가드 세 개로 좁혔지만 닫지 못했다 — `repo.save`에 CAS가 없다. **근본 원인은 requeue가 접수 중인 케이스를 구별하지 못하는 것**이다. `CaseRecord.intake_done: bool`을 두고 `requeue_open`이 `intake_done=True`인 `open`만 집으면, 접수 중인 케이스에 워커가 붙을 경로 자체가 사라진다. 계획 12의 `_not_ours` 가드는 그대로 두되(방어 심층), 주 방어선은 이 문이다.

순찰이 연 케이스(`gate.py`)는 finding에서 `target_locator`가 오므로 개설 시점에 `True`다. 사람이 연 케이스는 `False`로 열리고 `_finish`/`_give_up`이 `True`로 바꾼다.

### ② 명령 채널은 레코드 위의 필드 둘이다

새 컬렉션·큐를 만들지 않는다. `CaseRecord.pending_answer: str | None`과 `answer_key: str | None`(멱등 키). `POST /answers`는 이 둘을 쓰고 202를 돌려준다. 데몬의 `requeue_open`이 `awaiting_human` + `pending_answer` 있는 케이스를 큐에 넣고, 워커가 `run_forever`에서 그것을 만나면 `answer_case`로 보낸다 — **계획 12가 만든 그 함수다.** CLI `case resume`은 지금처럼 인라인으로 `answer_case`를 부른다. 경로가 둘이지만 분기는 한 곳이다.

멱등 키: 같은 `answer_key`가 다시 오면 202 + 아무것도 안 한다. 클라이언트 재시도가 답을 두 번 넣어 F3 복구 경로를 두 번 태우는 것을 막는다.

### ③ 인증은 토큰→주체 표 하나다

`access.subjects: dict[str, SecretStr]`(주체 → 토큰, `${ENV}` 참조). `Authorization: Bearer <토큰>`을 역조회해 주체를 얻는다. 표가 비어 있으면 모든 요청이 익명(주체 `None`)이고, 계획 12의 정책대로 `access.allow`가 비어 있을 때만 통과한다. **토큰 발급·회전·세션·OAuth는 만들지 않는다** — 리버스 프록시가 그것을 하고 우리는 주체만 받는 배치가 표준이고, 그때는 이 표를 비우고 프록시가 넣는 헤더를 신뢰하도록 한 줄 바꾸면 된다(그 스위치도 지금은 만들지 않는다).

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `src/domain/cases.py` | 케이스 레코드 | `intake_done`, `pending_answer`, `answer_key` |
| `src/application/worker.py` | 워커·큐 | `requeue_open`이 `intake_done` 문을 지키고 `pending_answer`를 집는다; `run_forever`가 답을 `answer_case`로 보낸다 |
| `src/application/open_case.py` · `intake.py` · `src/patrol/gate.py` | 개설·접수 | `intake_done` 설정 |
| `src/application/submit.py` | **신설** — 답 기록 | `submit_answer(case_id, answer, key, *, repo, clock) -> str` |
| `src/config/schema_app.py` | app config | `access.subjects` |
| `src/api/__init__.py` · `app.py` · `auth.py` · `routes_cases.py` · `routes_reads.py` | **신설** — HTTP 표면 | FastAPI 앱 팩토리, 인증 의존성, 핸들러 |
| `src/api/assembly.py` | **신설** — `api` 조립 | `assemble_api(config_root, repo_root, env, *, clock)` — 어댑터 없이 |
| `src/__main__.py` | CLI | `api` 서브커맨드 |
| `src/boot.py` | 기동 검증 | `access.subjects`의 토큰이 비어 있지 않은가 |
| `requirements.txt` | 의존성 | `fastapi`, `uvicorn` |
| `tests/api/` | **신설** | `test_boundary.py`(import 그래프), 엔드포인트별 테스트 |

**`src/api/`를 `presentation/` 아래가 아니라 별도로 두는 이유**: presentation은 케이스 종결 후 산출물(보고서·메일)이고 프로세스 안에서 불린다. `api`는 **별도 프로세스의 진입점**이다 — 스펙 §3.1의 세 프로세스(`api`/`worker`/`patrol`) 중 하나이고, 경계 테스트(`build_adapters`를 import하지 않는다)가 패키지 단위로 걸린다.

---

## Task 1: `intake_done` — requeue가 접수 중인 케이스를 집지 않는다

**Files:** Modify `src/domain/cases.py` · `src/application/worker.py` · `src/application/open_case.py` · `src/application/intake.py` · `src/patrol/gate.py` · Test `tests/application/test_worker.py` · `tests/application/test_open_case.py` · `tests/application/test_intake_turn.py` · `tests/patrol/test_gate.py`

**Interfaces:** Produces: `CaseRecord.intake_done: bool = True`(기본값 True — 계획 12 이전 레코드는 전부 접수를 마친 것이다)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/application/test_worker.py
def test_접수_중인_케이스는_requeue가_집지_않는다():
    # 계획 12의 F1 경합의 근본 원인 — 가드 셋으로 좁혔지만 repo.save에 CAS가 없어
    # 닫지 못했다. requeue가 접수 중인 케이스를 구별하면 워커가 붙을 경로 자체가 없다.
    repo = InMemoryCaseRepository()
    repo.save(_record("c-1", status="open", intake_done=False))
    repo.save(_record("c-2", status="open", intake_done=True))
    queue = CaseQueue()
    assert queue.requeue_open(repo, clock=lambda: T) == 1
    assert queue._queue.get_nowait() == "c-2"


# tests/application/test_open_case.py
def test_사람이_연_케이스는_접수_전이다():
    record = open_case(...)
    assert record.intake_done is False


# tests/application/test_intake_turn.py
async def test_접수가_끝나면_intake_done이_True다():        # _finish
async def test_접수를_포기해도_intake_done이_True다():      # _give_up — 대상 없이 조사에 들어간다


# tests/patrol/test_gate.py
def test_순찰이_연_케이스는_접수를_마친_상태다():
    # finding에서 target_locator가 오므로 접수할 것이 없다.
    result = admit_finding(_finding(store), ...)
    assert repo.get(result.case_id).intake_done is True
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: 통과 확인**

`_finish`/`_give_up`의 `_save(...)`에 `intake_done=True`를 얹는다. `open_case`는 `intake_done=False`. `gate.py`의 `CaseRecord(...)`는 기본값(True)을 쓴다 — 명시적으로 적어 의도를 남겨라.

- [ ] **Step 5: 변이 확인** — `requeue_open`의 필터를 지우면 빨간불인가.

- [ ] **Step 6: 커밋**

---

## Task 2: 명령 채널 — 답을 레코드에 싣고 워커가 소비한다

**Files:** Create `src/application/submit.py` · Modify `src/domain/cases.py` · `src/application/worker.py` · `src/patrol/daemon.py` · Test `tests/application/test_submit.py` · `tests/application/test_worker.py` · `tests/patrol/test_daemon.py`

**Interfaces:**
- Produces: `CaseRecord.pending_answer: str | None = None`, `answer_key: str | None = None`
- Produces: `submit_answer(case_id, answer, *, key, repo, clock) -> Literal["accepted","duplicate","not_waiting","not_found"]`
- Modifies: `CaseQueue.requeue_open`이 `awaiting_human` + `pending_answer` 케이스도 넣는다
- Modifies: `InvestigationWorker.run_forever`가 `pending_answer`가 있으면 `answer_case`로 보낸다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/application/test_submit.py
def test_답은_레코드에_실리고_실행되지_않는다():
    # api는 실행자가 아니다 — 답을 기록만 하고 워커가 집어 간다.
    repo = InMemoryCaseRepository()
    repo.save(_parked("c-1", question="q"))
    assert submit_answer("c-1", "답", key="k-1", repo=repo, clock=lambda: T) == "accepted"
    record = repo.get("c-1")
    assert record.pending_answer == "답" and record.answer_key == "k-1"
    assert record.status == "awaiting_human"          # 상태 전이는 워커의 몫


def test_같은_키는_두_번_싣지_않는다():
    # 클라이언트 재시도가 답을 두 번 넣으면 F3 복구가 두 번 돈다.
    ...
    assert submit_answer("c-1", "다른 답", key="k-1", ...) == "duplicate"
    assert repo.get("c-1").pending_answer == "답"           # 첫 답이 남는다


def test_기다리지_않는_케이스에는_싣지_않는다():
    repo.save(_record("c-1", status="investigating"))
    assert submit_answer("c-1", "답", key="k", ...) == "not_waiting"


def test_없는_케이스는_not_found다():
    assert submit_answer("없음", "답", key="k", repo=repo, clock=lambda: T) == "not_found"


# tests/application/test_worker.py
async def test_워커가_실린_답을_소비해_재개한다():
    # 데몬이 파킹 케이스를 자동으로 재개하려면 "사람의 답을 실어 나를 프로세스 밖
    # 명령 채널"이 필요했다(v1 인계 노트) — 이것이 그 채널이다.
    ...
    queue.requeue_open(repo, clock=...)        # awaiting_human + pending_answer를 집는다
    await worker.run_forever(stop)             # 또는 run_once 경유의 소비 경로
    record = repo.get("c-1")
    assert record.pending_answer is None and record.answer_key == "k-1"   # 키는 남긴다(멱등)
    assert record.status == "closed"


async def test_접수_질문에_실린_답은_접수를_이어간다():
    # 명령 채널도 answer_case를 거친다 — 분기는 한 곳이다.
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

**소비 순서가 중요하다**: 워커는 `pending_answer`를 **읽고 지운 뒤** `answer_case`를 부른다. 지우기 전에 부르면 `answer_case`가 실패했을 때 다음 requeue가 같은 답을 또 넣는다. 지운 뒤 실패하면 답은 `human:answer` 증거로 이미 박제돼 있으므로(워커의 기존 동작) 잃지 않는다. `answer_key`는 **지우지 않는다** — 멱등의 근거다.

`requeue_open`의 docstring을 갱신하라 — "open은 무조건 회수한다"가 이제 거짓이다(`intake_done` 문).

- [ ] **Step 5: 데몬으로 실제 확인**

```python
# tests/patrol/test_daemon.py
async def test_데몬이_실린_답으로_파킹_케이스를_재개한다():
    # 스케줄러의 requeue_job → 큐 → 워커. 데몬이 resume_once를 부르지 않는다는
    # architecture.md의 서술은 여전히 참이다 — 워커가 answer_case를 부른다.
```

- [ ] **Step 6: 커밋**

---

## Task 3: `api` 조립과 인증 — 어댑터 없이

**Files:** Create `src/api/__init__.py` · `src/api/assembly.py` · `src/api/auth.py` · Modify `src/config/schema_app.py` · `src/boot.py` · Test `tests/api/test_boundary.py` · `tests/api/test_assembly.py` · `tests/api/test_auth.py` · `tests/test_boot.py`

**Interfaces:**
- Produces: `ApiSite(gbm, fct, topology, lead_llm)`, `ApiRuntime(app, sites: list[ApiSite], repo, store, events, ledger, clock)`
- Produces: `assemble_api(config_root, repo_root, env, *, clock, llm_factory=None) -> ApiRuntime`
- Produces: `subject_of(authorization_header: str | None, subjects: dict[str, SecretStr]) -> str | None`
- Produces: `AppConfig.access.subjects: dict[str, SecretStr] = {}`

- [ ] **Step 1: 경계 테스트를 먼저 쓴다**

```python
# tests/api/test_boundary.py
def test_api는_대상_시스템_어댑터를_import하지_않는다():
    # 스펙 §3.1: api는 대상 시스템에 붙지 않는다. 산문 규율은 읽지 않으면 무력하다.
    import ast, pathlib
    forbidden = {"src.infrastructure.factory", "src.infrastructure.redis_reader",
                 "src.infrastructure.mongo_reader", "src.infrastructure.kafka_inspector",
                 "src.infrastructure.rest_prober", "src.infrastructure.code_repo",
                 "src.patrol.daemon", "src.application.worker"}
    for path in pathlib.Path("src/api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) else [])
            hit = forbidden & set(names)
            assert not hit, f"{path}: {hit}"
```

`src.application.worker`도 금지 목록에 있는 이유: `api`가 `InvestigationWorker`를 만들 수 있으면 실행자가 된다.

- [ ] **Step 2: 인증 테스트**

```python
# tests/api/test_auth.py
def test_토큰이_주체로_역조회된다():
    subjects = {"alice": SecretStr("tok-a"), "bob": SecretStr("tok-b")}
    assert subject_of("Bearer tok-b", subjects) == "bob"


def test_표가_비어_있으면_익명이다():
    assert subject_of("Bearer 아무거나", {}) is None
    assert subject_of(None, {}) is None


def test_틀린_토큰은_익명이_아니라_거부다():
    # 틀린 토큰을 익명으로 떨어뜨리면 allow가 비어 있는 설치에서 아무 문자열이나 통과한다.
    with pytest.raises(AuthError):
        subject_of("Bearer 틀림", {"alice": SecretStr("tok-a")})


def test_비교는_상수_시간이다():
    # 문자열 == 은 첫 불일치 바이트에서 멈춘다 — 타이밍으로 토큰을 한 바이트씩 캘 수 있다.
    import inspect
    from src.api import auth
    assert "compare_digest" in inspect.getsource(auth)
```

- [ ] **Step 3: 조립 테스트**

```python
# tests/api/test_assembly.py
def test_조립에_어댑터가_없다(tmp_path):
    rt = assemble_api(tmp_path / "config", tmp_path, ENV, clock=lambda: T,
                      llm_factory=lambda name: object())
    assert not hasattr(rt.sites[0], "adapters")
    assert rt.sites[0].topology is not None          # 접수의 locator 후보용
```

boot: `access.subjects`의 토큰이 빈 문자열이면 거부(`${ENV}` 미설정이 조용히 빈 토큰이 되면 그 주체는 누구나다).

- [ ] **Step 4~5: 구현 → 통과 확인 → 커밋**

---

## Task 4: 쓰기 엔드포인트 — 접수와 답

**Files:** Create `src/api/app.py` · `src/api/routes_cases.py` · Test `tests/api/test_routes_cases.py`

**Interfaces:**
- Produces: `create_app(runtime: ApiRuntime) -> FastAPI`
- Produces: `POST /cases` · `POST /cases/{id}/intake-answers` · `POST /cases/{id}/answers`

```
POST /cases  {symptom, gbm?, fct?, concern?}
  202 {case_id, status, question?}          ── 스코프 확정 + 개설 + 첫 접수 턴까지
  400 {candidates, questions, problems}     ── 스코프 미확정(케이스 없음)
  403                                        ── 접근 거부
  401                                        ── 토큰 틀림

POST /cases/{id}/intake-answers  {answer}
  200 {status, question?, target_locator?}  ── 접수 턴 하나
  409                                        ── 접수 질문에 파킹된 케이스가 아니다(not_ours)
  403 / 404

POST /cases/{id}/answers  {answer, key}
  202 {result: "accepted"|"duplicate"}       ── 기록만. 워커가 집어 간다
  409 {result: "not_waiting"}
  403 / 404
```

**`POST /cases`가 첫 접수 턴까지 도는 이유**: 첫 응답에 질문이 실려야 클라이언트가 폴링하지 않는다. 접수는 조사가 아니다 — LLM 호출 하나이고 대상 시스템 접근이 없다.

**403과 404의 구별** — 계획 12 인계 ③: 미인가 주체가 케이스 존재 여부를 알 수 있다. **미인가면 404를 낸다** — 존재하는 케이스든 없는 케이스든 같은 응답이다. 접근 검사는 `repo.get` 뒤에 올 수밖에 없지만(gbm/fct가 필요하다), 응답은 구별하지 않는다. 403은 스코프가 응답에 이미 드러난 `POST /cases`에서만 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/api/test_routes_cases.py — fastapi.testclient.TestClient
def test_케이스_개설은_case_id를_즉시_돌려준다(client, repo):
    r = client.post("/cases", json={"symptom": "OEE가 이상하다", "gbm": "mx", "fct": "gumi"})
    assert r.status_code == 202
    assert r.json()["case_id"] and repo.get(r.json()["case_id"]).status in ("open", "awaiting_human")


def test_스코프_미확정은_케이스를_만들지_않는다(client, repo):
    r = client.post("/cases", json={"symptom": "뭔가"})           # 사이트가 둘인 픽스처
    assert r.status_code == 400 and "mx/gumi" in str(r.json()["candidates"])
    assert repo.list_open() == []


def test_접수_질문이_첫_응답에_실린다(client):
    r = client.post("/cases", json={...})                          # 접수 LLM이 missing을 낸다
    assert r.status_code == 202 and r.json()["question"]


def test_접수_답은_턴_하나를_돈다(client, repo):
    ...
    r = client.post(f"/cases/{cid}/intake-answers", json={"answer": "라인 7"})
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert repo.get(cid).intake_done is True


def test_답은_기록되고_실행되지_않는다(client, repo):
    # api는 실행자가 아니다.
    ...
    r = client.post(f"/cases/{cid}/answers", json={"answer": "없다", "key": "k-1"})
    assert r.status_code == 202 and repo.get(cid).pending_answer == "없다"
    assert repo.get(cid).status == "awaiting_human"


def test_같은_키는_duplicate다(client): ...


def test_미인가_주체에게는_존재_여부를_숨긴다(client_as_bob, repo):
    # 계획 12 인계 ③ — 404와 403을 구별하면 케이스 존재가 새어 나간다.
    real = client_as_bob.post(f"/cases/{existing}/answers", json={...})
    ghost = client_as_bob.post("/cases/없음/answers", json={...})
    assert real.status_code == ghost.status_code == 404
    assert real.json() == ghost.json()


def test_알_수_없는_키는_422다(client):
    # StrictModel — 클라이언트 오타가 조용히 무시되지 않는다.
    assert client.post("/cases", json={"symptom": "s", "gbm": "mx", "fct": "gumi",
                                       "concen": "operation"}).status_code == 422


def test_틀린_토큰은_401이다(client): ...
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

`POST /cases`는 계획 12의 함수를 **그대로** 순서대로 부른다: `resolve_scope` → `can_access` → `open_case` → `intake_turn`. `_run_chat`과 같은 순서다 — 조립을 베끼지 말고 순서만 같게. (둘을 한 함수로 합칠 수 있으면 합쳐라 — `src/application/`에 두고 둘이 부른다.)

- [ ] **Step 5: 커밋**

---

## Task 5: 읽기 엔드포인트 — 목록·상세·이벤트·보고서·점검

**Files:** Create `src/api/routes_reads.py` · Test `tests/api/test_routes_reads.py`

```
GET /cases?gbm=&fct=&status=     ── sites_for로 필터. 스코프 필터 필수(전체 조회 없음)
GET /cases/{id}                  ── 상태 · 질문 · 판정 요약 · 단계 체크리스트(ReportModel.stages)
GET /cases/{id}/events?since=N   ── seq 오름차순 JSON 배열
GET /cases/{id}/events           ── Accept: text/event-stream이면 SSE
GET /cases/{id}/report?format=html|md
GET /checks?gbm=&fct=            ── 레저 read(최근 실행)
```

**읽기 필터가 계획 12 인계 ②다** — `sites_for`에 프로덕션 소비자가 0이었다. `GET /cases`는 `sites_for(subject, known=registry)`로 **반드시** 좁힌다. `None`(제한 없음)이면 요청의 `gbm/fct` 필터만 적용한다. **`gbm/fct` 없는 전체 조회는 400** — 스펙 §4.4가 "사이트 스코프 필터 필수"라 했다.

**SSE는 저장된 로그의 폴링이다.** `events.since(case_id, after)`를 짧은 간격으로 읽어 `id: {seq}\ndata: {json}\n\n`으로 흘리고, 케이스가 `closed`이고 남은 이벤트가 없으면 끝낸다. 프로세스 내 pub/sub을 만들지 않는다 — 이벤트는 다른 프로세스(워커)에서 생기므로 어차피 저장소를 거쳐야 한다(스펙 §6 "WebSocket을 1차 전송으로" 기각 근거).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_목록은_주체의_사이트로_좁혀진다(client_as_alice, repo):
    # alice는 mx/gumi만. mx/suwon 케이스가 있어도 안 보인다.
    ...
    assert {c["gbm"] + "/" + c["fct"] for c in r.json()["cases"]} == {"mx/gumi"}


def test_스코프_없는_전체_조회는_400이다(client): ...


def test_상세에_단계_체크리스트가_있다(client):
    r = client.get(f"/cases/{cid}")
    assert [s["stage"] for s in r.json()["stages"]] == ["frame", "select", ...]


def test_이벤트는_seq_순서다(client, events): ...


def test_SSE는_since_이후를_흘리고_종결에서_끝난다(client):
    with client.stream("GET", f"/cases/{cid}/events", headers={"Accept": "text/event-stream"}) as r:
        body = "".join(r.iter_text())
    assert "id: 1\n" in body and body.count("data:") == 3


def test_보고서는_저장된_파일을_돌려준다(client, tmp_path): ...
def test_점검_이력은_레저를_읽는다(client, ledger): ...
def test_미인가_읽기는_404다(client_as_bob): ...
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인 → Step 5: 커밋**

---

## Task 6: `api` 명령 + 문서 + 실제 스모크

**Files:** Modify `src/__main__.py` · `requirements.txt` · `README.md` · `docs/architecture.md` · `docs/howto.md` · `docs/config-reference.md` · `docs/going-live.md` · `docs/glossary.md` · `CLAUDE.md` · Test `tests/test_cli.py`

```bash
python -m src api --host 127.0.0.1 --port 8080 --config-root config --repo-root .
```

기동 검증을 먼저 돌리고(`validate_boot`), `assemble_api`, `create_app`, uvicorn. `--stub-seeds`는 **받지 않는다** — `api`에 어댑터가 없으므로 시드가 갈 곳이 없다.

- [ ] **Step 1: 실제로 띄워서 curl로 확인한다**

```bash
set -a; . ./.env.example; set +a
python -m src api --port 8080 --config-root config.example --repo-root . &
sleep 2
curl -s -X POST localhost:8080/cases -H 'content-type: application/json' \
  -d '{"symptom": "OEE가 이상하다"}'                        # 202 + case_id
curl -s localhost:8080/cases?gbm=mx\&fct=gumi                # 목록
curl -s localhost:8080/cases/c-1                             # 상세
curl -s -N localhost:8080/cases/c-1/events -H 'accept: text/event-stream' &   # SSE
# 다른 터미널에서 patrol run(데몬)을 띄우면 워커가 케이스를 집어 가고 SSE에 이벤트가 흐른다
kill %1 %2
```

**메모리 백엔드에서는 `api`와 `patrol run`이 저장소를 공유하지 않는다** — 이 스모크는 `store.backend: "mongo"`에서만 끝까지 간다. 예시 트리로는 `POST /cases` → 202와 읽기 엔드포인트까지만 확인하고, 그 사실을 README에 적어라. `tests/api/`의 통합 테스트는 같은 프로세스 안에서 `api` 앱과 워커에 같은 InMemory 저장소를 주입해 전 구간을 돈다.

- [ ] **Step 2: 문서**

- `README.md`: 세 프로세스(`api`/`worker=patrol run`/…) 그림 한 줄과 `api` 기동 명령. **메모리 백엔드의 한계**를 명시.
- `docs/architecture.md`: §1에 `src/api/` 계층 추가, §2에 "모드 ③: HTTP" — `POST /cases` → 워커가 집어 감 → `POST /answers` → 명령 채널. **"데몬은 `resume_once`를 부르지 않는다"**는 여전히 참이나 "파킹 케이스를 자동으로 재개할 수 없다"는 이제 **거짓**이다 — 그 문단을 고쳐라(§5). 상태표에 변화 없음(전이는 안 늘었다).
- `docs/config-reference.md`: `access.subjects` 행, 기동 검증 항목 추가·재번호.
- `docs/going-live.md`: `api` 프로세스 배치 절 — 리버스 프록시 뒤에 두고 TLS는 프록시가, 토큰은 `.env`.
- `docs/howto.md`: "웹에서 케이스를 열고 싶다" 절 — curl 예시 셋.
- `docs/glossary.md`: 명령 채널, `intake_done`, `pending_answer`.
- `CLAUDE.md` 코드 지도 + 규율 목록에 **"`api`는 대상 시스템에 붙지 않는다"** 한 줄(규율 9 옆).
- **`grep -rn "명령 채널\|자동으로 재개\|resume_once" docs/ CLAUDE.md`로 낡은 서술을 전부 찾아라.**

- [ ] **Step 3: 전체 통과 + 커밋**

---

## Self-Review

**스펙 커버리지**: §3.1(api는 클라이언트, 어댑터 없음) → Task 3 경계 테스트. §3.5(호출자 인증 + 읽기 필터) → Task 3·5. §3.6(제출→조회, SSE는 읽기 최적화, 명령으로 답 넣기, 멱등 키) → Task 2·4·5. §4.4의 엔드포인트 중 `POST /cases/{id}/label`(P8)과 `GET /digests/{scenario}`(P7)는 **범위 밖**이고, 나머지 8종은 전부 Task 4·5. `wall-clock 상한`은 계획 6이 이미 했다.

**계획 12 인계 5건**: ①TOCTOU → Task 1의 `intake_done` 문이 워커 경로를 없앤다. api 인스턴스 간 경합은 남는다 — `_save` docstring이 그것을 적고 있다. ②읽기 필터 → Task 5. ③존재 오라클 → Task 4(미인가 404). ④`answer_case` status 가드 → Task 2에서 `submit_answer`가 `not_waiting`으로 앞에서 막는다. ⑤접수 lease → ①로 대체.

**타입 일관성**: `submit_answer`의 반환 어휘(`accepted/duplicate/not_waiting/not_found`)와 워커의 어휘(`closed/awaiting_human/busy/skipped/failed`)는 다르다 — 전자는 "기록했는가", 후자는 "실행 결과"다. 섞지 않는다. HTTP 상태 매핑은 Task 4 표 하나에서만.

**하지 않는 것**:

| 하지 않는 것 | 왜 |
|---|---|
| WebSocket | 스펙 §6 기각. 클라이언트가 보내는 것은 제출·답 둘뿐이고 둘 다 내구성과 멱등성을 원한다 |
| 프로세스 내 이벤트 pub/sub | 이벤트는 워커에서 생기므로 어차피 저장소를 거친다. SSE는 저장된 로그의 폴링이다 |
| 토큰 발급·회전·세션·OAuth | 리버스 프록시가 한다. 우리는 주체만 받는다 |
| 역할·권한 등급·리소스별 ACL | 계획 12와 같다 — 필드 1개, 술어 1개 |
| `api`가 조사를 시작하는 것 | `api`는 실행자가 아니다. `run_once`가 `src/api/`에 있으면 `api` 풀 전체가 실행자가 되고 lease가 그 사이를 중재해야 한다 |
| `api`에 어댑터 조립 | 대상 시스템 접근은 어댑터 층에서만, 그 층은 워커에만 |
| 새 컬렉션(명령 큐) | 레코드의 필드 둘로 충분하다. `patrol`이 단일 인스턴스라 소비 경합이 없다 |
| `POST /cases/{id}/label` | P8(학습 루프) |
| `GET /digests/{scenario}` | P7(Fleet 집계) |
| 다중 RCA 후보 payload | 계획 14. `GET /cases/{id}`는 지금의 `Verdict` 모양을 그대로 낸다 |
| 페이지네이션·정렬·전문 검색 | `GET /cases`는 사이트 스코프 필터 + status만. 요구가 관측되기 전엔 안 늘린다 |
| GraphQL·범용 쿼리 | 스펙 §6 기각 |

---

## 계획 14 인계

리뷰 한 라운드 + 픽스 웨이브에서 남은 것들. 블로커 셋(`submit_answer`의 비원자
save·소비 실패 시 답 소실·옛 답이 새 질문에 붙음)은 `attach_answer`/`take_answer`/
`restore_answer` 프리미티브와 `question_seq`/`answered_seq` 짝으로 닫았다. 남은 것:

1. **접수 `_save`의 TOCTOU** — 같은 케이스에 동시에 오는 두 `/intake-answers`가
   증거를 중복 박제하고, 한 순서에서 `awaiting_human` + `intake_done=True` + 대상 설정이라는
   모순 레코드를 남긴다(리뷰 S3 실증). `attach_answer`가 연 조건부 `$set` 형태를 `_save`에도
   그대로 쓰면 닫힌다. requeue가 그 사이 못 집는 것만 지금 보장한다.
2. **경계가 패키지 단위다** — `src/api/`의 전이 import 클로저는 깨끗하지만, `python -m src api`의
   **프로세스**는 `src/__main__.py`를 거쳐 `daemon`·`worker`·`factory`·대상 리더 전부를 import한다
   (인스턴스·소켓은 없음, 실측). `_run_api`를 얇은 별도 진입점으로 떼고 `boot.py`의 live 경로
   import를 지연시키면 닫힌다. 그리고 `test_boundary.py`는 `ast.Import`만 보므로
   `importlib.import_module("src.application.worker")`를 못 잡는다 — 프로세스 수준
   `sys.modules` 단정을 추가할 것.
3. **SSE 부하** — 저장소 호출을 스레드풀로 뺐지만(리뷰 S8: 시청자 3명이 무관한 GET을 50배
   느리게 했다) `_SSE_POLL_S=0.2`로 케이스 100개×100명이면 초당 5만 조회다. 폴링 간격을
   config로 빼거나 케이스별 마지막 seq 캐시.
4. **입력 위생** — `symptom=""`·`key=""` 허용(빈 키가 accepted되면 진짜 키가 `pending`),
   목록 정렬이 문자열(`c-1, c-10, c-2`), `status=bogus`→200 `[]`, 소문자 `bearer` 거부
   (RFC 7235는 스킴 대소문자 무시), `--port`에 help 없음.
5. **`GET /cases/{id}/report`** — `case show --report`와 달리 다른 확장자 폴백이 없다
   (`report.format`을 바꾼 뒤 옛 보고서를 못 읽는다).
6. **응답 모델이 dict** — 계획서는 StrictModel을 말했으나 응답은 dict다(요청만 StrictModel).
   계획 14가 `GET /cases/{id}` payload를 바꿀 때 응답 모델을 세우면 그때 같이.
7. **`config-reference.md`의 기동 검증 번호가 실행 순서가 아니다** — 목록은 종류별이고
   `boot.py`는 사이트 루프 안팎으로 나뉜다. 번호를 없애고 이름으로 부르는 쪽이 낫다
   (`boot.py` 주석은 이미 그렇게 했다).
8. **계획 13 이전에 파킹된 레코드**는 `question_seq=0`이라 `attach_answer`가 `not_waiting`을
   낸다 — CLI `case resume`은 그 필드를 안 보므로 그쪽으로는 답할 수 있다. 배포된 것이
   없어 마이그레이션은 하지 않았다. 한다면 `question_seq`만 `$set`해도 된다 — Mongo
   구현의 CAS 술어는 문서의 **원값**(부재는 `null`로 맞는다)을 쓰므로 `answered_seq`
   부재는 안전하다(검증 리뷰가 잡은 "부재 필드 ≠ 기본값 0 → CAS가 영원히 져 무한
   재귀"는 고쳤고, 재분류는 두 바퀴로 상한을 뒀다).
9. **`POST /cases/{id}/answers`에 `question_seq` If-Match가 없다** — 클라이언트가 Q1을 보고
   답하는 사이 그래프가 Q2로 파킹하면 그 답이 Q2에 실린다(요청에 `question_seq`를 실어
   서버가 대조하면 막힌다). CLI `case resume`도 같다 — 사람이 Q1을 보고 쓰는 사이 데몬이
   API 답으로 Q1을 소비해 Q2로 파킹하면 CLI의 답이 Q2에 실린다. seq 쌍은 **서버 안의**
   옛 답 소비만 막는다.
10. **혼용 경로의 우선순위는 코드가 정했다** — API로 실린 답이 있는데 CLI `case resume`이
    먼저 오면 직접 답이 이기고 실린 답은 `human:answer_dropped`(reason=superseded)로
    남는다. `resume_once`가 lease를 잡은 뒤에 가져가고, lease가 살아 있는 동안 attach는
    `busy`라 창이 없다. 반대(실린 답 우선, CLI는 거절)가 맞다고 보면 그 분기 하나다.
11. **`submit_answer`의 포괄 except**(`attach_answer`가 던지면 `not_found`)는 테스트가
    없다 — 저장소 장애가 404로 보이는 것이 맞는지 계획 14에서 다시 본다.
