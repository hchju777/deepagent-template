# 계획 17 — 답이 붙는 자리를 못 박는다: 질문 대조와 접수 저장의 CAS

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사람이 준 답이 **다른 질문의 답으로 소비되는** 경합(계획 13 인계 #9)과, 같은 케이스에 동시에 오는 두 접수 답변이 레코드를 망가뜨리는 경합(계획 13 인계 #1)을 닫는다.

**Architecture:** 둘은 같은 문제의 두 얼굴이다 — **같은 레코드를 두 경로가 읽고 쓰는데 그 사이가 원자적이 아니다.** 계획 13이 답 채널에 연 조건부 `$set`(`attach_answer`)이 이미 그 형태를 갖고 있으므로, 같은 관용구를 두 곳에 확장한다:

1. **질문 대조(If-Match)** — 클라이언트가 "내가 본 질문 번호"를 실어 보내고, 저장소가 **답을 싣는 그 한 동작 안에서** 대조한다. 대조를 프로브·라우트에서 미리 하면 그 사이에 파킹이 또 일어난다.
2. **접수 저장의 CAS** — `_save`가 읽은 시점의 `updated_at`을 술어로 걸고 조건부로 쓴다. 낙관적 동시성의 교과서 형태이고, 새 버전 필드를 만들지 않아도 된다(`updated_at`이 이미 매 저장마다 바뀐다).

**무엇이 실제로 깨지는가**(둘 다 실측 가능한 시나리오):

- 사람이 웹에서 Q1을 읽고 3분간 답을 쓴다. 그 사이 다른 경로(CLI·데몬)가 조사를 재개해 그래프가 Q2로 파킹한다. 사람의 답이 도착하면 레코드는 `awaiting_human`이고 `question_seq > answered_seq`이므로 **Q1의 답이 Q2의 답으로 소비된다.** 리드는 엉뚱한 대답을 근거로 판정한다.
- 같은 케이스에 `/intake-answers`가 둘 동시에 온다. 증거가 중복되고, 한 순서에서는 `awaiting_human`인데 `intake_done=True`이고 대상까지 설정된 **모순 레코드**가 남는다(계획 13 리뷰 S3이 실증했다).

**Tech Stack:** 기존 CAS 관용구(`_cas`), pydantic StrictModel, mongomock 계약 테스트, FastAPI.

## Global Constraints

- 무raise(규율 1): 대조 실패·CAS 패배는 예외가 아니라 **반환값의 상태**다. `submit_answer`의 어휘(`accepted`/`duplicate`/`pending`/`busy`/`not_waiting`/`not_found`/`error`)에 한 종류를 더한다.
- 시계 주입(규율 2), StrictModel(규율 5), 이벤트 어휘 6종 불변(규율 7 — 대조 실패는 이벤트가 아니다).
- **판정과 쓰기는 한 동작이다.** 라우트에서 미리 읽어 비교하고 저장소에 넘기면 그 사이가 다시 창이다 — 계획 13이 `attach_answer`를 만든 이유 그대로다.
- 인메모리와 Mongo 구현이 **같은 판정**을 해야 한다(계약 테스트로 고정). 갈리면 테스트는 초록인데 프로덕션만 틀린다.
- 주석·문서 한국어(WHY만), 커밋 메시지 영어 + `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 테스트: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`. RED 먼저, 각 픽스는 되돌려 확인(`-B` 필수 — pyc 함정).

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `src/domain/cases.py` | `attach_answer(..., expect_seq=None)` 포트·InMemory | 수정 |
| `src/infrastructure/mongo_store.py` | 같은 계약의 CAS 술어 + 접수 CAS 저장 | 수정 |
| `src/application/submit.py` | `submit_answer(..., expect_seq=None)`, 어휘에 `stale_question` | 수정 |
| `src/api/routes_cases.py` | `Answer.question_seq`(선택) → 409 | 수정 |
| `src/api/routes_reads.py` | 상세 응답이 `question_seq`를 낸다(클라이언트가 실어 보낼 재료) | 수정 |
| `src/application/answer.py` | 직접 답(CLI) 경로의 대조 | 수정 |
| `src/__main__.py` | `case resume --question-seq`, `case show`가 번호를 보인다 | 수정 |
| `src/application/intake.py` | `_save`가 CAS를 쓴다 | 수정 |
| `docs/*` | 어휘·동시성 서술 | 수정 |

---

### Task 1: 저장소가 질문 번호를 대조한다

**Files:** Modify `src/domain/cases.py`, `src/infrastructure/mongo_store.py` · Test `tests/domain/test_cases.py`, `tests/infrastructure/test_mongo_store.py`

**Interfaces:** `attach_answer(case_id, *, answer, key, now, expect_seq: int | None = None) -> str`. `expect_seq`가 주어지고 레코드의 `question_seq`와 다르면 `"stale_question"`.

- [ ] **Step 1: 실패하는 테스트**(인메모리·Mongo 같은 계약)

```python
def test_다른_질문의_답은_거절된다(repo_or_db):
    # 사람이 Q1을 보고 답을 쓰는 사이 그래프가 Q2로 파킹했다.
    _parked(repo, question_seq=2)
    assert repo.attach_answer("c-1", answer="Q1의 답", key="k-1", now=T,
                              expect_seq=1) == "stale_question"
    assert repo.get("c-1").pending_answer is None      # 쓰지 않았다

def test_번호를_안_주면_예전처럼_받는다(repo):
    assert repo.attach_answer("c-1", answer="a", key="k", now=T) == "accepted"

def test_대조는_CAS_술어에도_들어간다(db):
    # 읽고 나서 파킹이 일어나도 진다 — 미리 비교하고 넘기면 그 사이가 다시 창이다.
    _interleave(db, "c-1", before_cas={"question_seq": 3})
    assert repo.attach_answer("c-1", answer="a", key="k", now=T, expect_seq=2) == "stale_question"
```

- [ ] **Step 2: RED** → **Step 3:** 구현. Mongo는 술어에 `question_seq: expect_seq`를 더한다(원값 `doc.get`과 같은 규약). 재분류 루프가 `stale_question`을 낼 수 있어야 한다.
- [ ] **Step 4: GREEN + 돌연변이**(대조 제거, 술어에서 빼고 사전검사만) → **Step 5: 커밋** `"Let the store refuse an answer aimed at a question that moved"`.

---

### Task 2: HTTP 표면이 번호를 받고 돌려준다

**Files:** Modify `src/application/submit.py`, `src/api/routes_cases.py`, `src/api/routes_reads.py`, `src/api/models.py` · Test `tests/application/test_submit.py`, `tests/api/test_routes_cases.py`, `tests/api/test_routes_reads.py`

**Interfaces:** `Answer.question_seq: int | None = None`; `submit_answer(..., expect_seq=None)`; `SubmitResult`에 `"stale_question"` 추가 → 409. `CaseDetail.question_seq: int`.

- [ ] **Step 1: 실패하는 테스트**
  - `GET /cases/{id}`가 `question_seq`를 낸다(클라이언트가 되돌려 보낼 재료가 응답에 없으면 If-Match가 쓸모없다).
  - 번호를 실어 보내고 그 사이 파킹이 일어났으면 409 `{"result": "stale_question"}`이고 **답이 저장되지 않는다**.
  - 번호가 맞으면 202 `accepted`.
  - 번호를 **안** 보내면 기존 동작 그대로(하위 호환 — 기존 클라이언트를 깨지 않는다).
- [ ] **Step 2: RED** → **Step 3:** 구현.
- [ ] **Step 4: GREEN + 돌연변이**(라우트가 번호를 안 넘김, 409 매핑 누락) → **Step 5: 커밋** `"Take the question number a client answered, and refuse a stale one"`.

---

### Task 3: CLI도 같은 대조를 쓴다

**Files:** Modify `src/__main__.py`, `src/application/answer.py` · Test `tests/test_cli.py`

- [ ] **Step 1: 실패하는 테스트**
  - `case show`가 질문과 함께 번호를 보인다(사람이 되돌려 적을 재료).
  - `case resume --question-seq 1`인데 레코드가 2면 exit 2와 안내, 그래프를 재개하지 않는다.
  - `--question-seq`를 안 주면 기존 동작.
- [ ] **Step 2: RED** → **Step 3:** 구현. 직접 답 경로는 `attach_answer`를 안 타므로 `answer_case`가 `resume_once` **직전에**(lease를 잡은 뒤) 대조한다 — 계획 13이 supersede를 lease 아래로 옮긴 것과 같은 이유다.
- [ ] **Step 4: GREEN + 돌연변이** → **Step 5: 커밋** `"Let a person say which question they answered"`.

---

### Task 4: 접수 저장의 CAS

**Files:** Modify `src/domain/cases.py`, `src/infrastructure/mongo_store.py`, `src/application/intake.py` · Test `tests/domain/test_cases.py`, `tests/infrastructure/test_mongo_store.py`, `tests/application/test_intake_turn.py`

**Interfaces:** `update_if(case_id, *, expect: dict, fields: dict, now: datetime) -> bool`. 읽은
시점의 값(시각 + 접수 소유 필드)을 술어로 건 조건부 저장. 집행 중 이름과 술어가 함께
넓어졌다 — 시각 하나로는 고정 시계에서 두 턴이 모두 이긴다(인계 #3).

- [ ] **Step 1: 실패하는 테스트**
  - 인메모리·Mongo: 그 사이 남이 저장했으면 `False`이고 **아무것도 안 바뀐다**.
  - `_interleave`로 읽기와 CAS 사이에 남의 쓰기를 끼워 넣어 실제로 지는 경로를 지난다.
  - 접수: 같은 케이스에 두 턴이 동시에 돌면 **둘째가 `not_ours`로 손을 뗀다**(증거 중복도 모순 레코드도 없다).
  - 접수가 이긴 쪽은 예전과 똑같이 진행한다.
- [ ] **Step 2: RED** → **Step 3:** 구현. `_save`가 `repo.get` → 가드 → `update_if` 순으로 가고, 지면 `not_ours` 문구를 돌려준다.
- [ ] **Step 4: GREEN + 돌연변이**(CAS를 평범한 save로, 술어에서 `updated_at` 제거) → **Step 5: 커밋** `"Make the intake save conditional on what it read"`.

---

### Task 5: 문서와 인계

`docs/architecture.md`(답 채널의 대조·접수 CAS), `docs/glossary.md`(**stale_question**), `docs/howto.md`(curl에 `question_seq`), `docs/config-reference.md`(변경 없음 확인), 계획 13 인계 #1·#9를 **갚음**으로 표시, 이 계획서 인계.

- [ ] 문서가 부른다고 적은 함수는 `grep`으로 호출부를 확인한 뒤 쓴다. → 커밋 `"Document what closes the two answer races"`.

---

## 자기 검토

- **커버리지**: 계획 13 인계 #9(If-Match 부재) → Task 1·2·3; 인계 #1(접수 TOCTOU) → Task 4.
- **뺀 것(YAGNI)**: 전면 낙관적 동시성(모든 저장에 버전 토큰), 답변 큐잉(늦은 답을 다음 질문에 자동 이월 — 사람이 무엇에 답했는지 모르는 채로 옮기는 것이 문제의 원인이다), 이벤트 종류 추가.
- **가장 위험한 지점**: 대조를 CAS 술어가 아니라 사전검사로만 두는 것. 그러면 창이 좁아질 뿐 닫히지 않고, 테스트는 초록인데 프로덕션에서만 진다. Task 1 Step 1의 `_interleave` 테스트가 그 방어선이다.

## 인계(계획 17 이후)

1. **번호는 선택이다** — 안 보내는 클라이언트는 예전 경합에 그대로 노출된다. 강제하려면
   `Answer.question_seq`를 필수로 올리고 기존 클라이언트를 깨야 한다. 웹 UI가 생기면 결정한다.
2. **`stale_question`은 답을 보관하지 않는다** — 사람이 쓴 답이 버려진다. 큐잉(늦은 답을
   다음 질문에 이월)은 의도적으로 안 했다: 사람이 무엇에 답했는지 모르는 채 옮기는 것이
   문제의 원인이다.
3. **접수 CAS의 술어는 시각 + 접수 소유 필드다** — 시각 하나로는 고정 시계에서 둘 다
   이긴다(검증 리뷰 M-5). 소유 필드를 함께 걸어 닫았고 단조 카운터를 새로 만들지 않았다.
   접수가 새 필드를 쓰게 되면 `_expect`와 `_SAVED_FIELDS` **둘 다** 갱신해야 한다.
4. **`_SAVED_FIELDS`가 접수 소유 필드의 유일한 목록이다** — 접수가 새 필드를 쓰게 되면
   여기 더해야 하고, 안 더하면 그 필드가 조용히 저장되지 않는다.
5. **대조는 `resume_once`가 lease를 잡은 뒤에 한다** — CLI·chat 둘 다 번호를 그리로
   흘린다. 사전검사로 두면 그 판정과 재개 사이가 통째로 창이다(검증 리뷰 M-1).
6. **저장소 술어의 직렬화는 `save`와 같아야 한다** — `.isoformat()`은 `+00:00`, pydantic의
   `model_dump(mode="json")`은 `Z`다. 이 둘을 섞어 Mongo 접수가 100% 실패했고 인메모리
   테스트에는 전혀 안 보였다(검증 리뷰 B1). `to_jsonable_python`으로 통일했다 —
   **저장소에 새 술어를 걸 때 이 함정을 먼저 확인하라.**
7. **`intake_turn`은 이제 Mongo repo로도 돈다**(테스트 2건). 저장소 계약을 바꾸는 변경을
   인메모리만으로 검증하면 프로덕션 쓰기 경로를 한 번도 안 지난다.
8. **Mongo `attach_answer` 술어의 `question_seq` 줄은 중복이다** — 사전검사를 지난 이상
   읽은 값 술어와 같은 값이라, 읽고 나서 파킹이 일어난 경우를 실제로 잡는 것은 재분류
   두 바퀴다(검증 리뷰 M2). 불변식을 명시하려고 남겼고 주석에 그렇게 적었다.
9. **`_expect`와 `_SAVED_FIELDS`는 같이 움직인다** — 전자는 "무엇이 안 바뀌었어야 하는가",
   후자는 "무엇을 쓰는가"다. 접수가 새 필드를 쓰면 둘 다 갱신해야 하고, 안 하면 각각
   조용한 실패(거짓 승리 / 저장 누락)가 된다.
