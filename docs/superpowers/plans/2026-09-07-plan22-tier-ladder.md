# tier 사다리 역전 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이력 검색이 tier를 나누기 **전에** 자르는 것을 멈춘다 — 같은 사이트(tier 2)
후보가 실재하는데 다른 사이트(tier 3)가 K를 채우는 역전을 없앤다.

**Architecture:** `history._candidates`가 `closed_by_locators`를 **한 번** 부르고(기본
상한 20) 그 결과를 파이썬에서 tier 2/3으로 나눈다. 절단이 분리보다 먼저라, 같은
locator의 최신 20건이 전부 다른 사이트면 tier 2가 조용히 0건이 된다. 저장소가 사이트로
거를 수 있게 해서 tier마다 자기 상한을 갖게 한다.

**출처**: 계획 15(`de3d4b1`·`529e322`)부터 있던 결함이다. 계획 20은 정렬·절단을 DB로
옮겼을 뿐 의미가 같다 — 회귀가 아니라 잠복이었다. 기존 tier 테스트 14건이 전부 상한보다
작은 픽스처를 써서 못 봤다.

**Tech Stack:** Python 3.12, pymongo(동기), mongomock, pytest.

## Global Constraints

- 규율 1(무raise): 이 층은 저장소 계약이라 예외가 정상이다.
- 두 백엔드(인메모리·Mongo)가 **같은 답**을 내야 하고 계약 테스트가 그것을 지킨다
  (계획 20에서 세 번 데인 자리).
- 정렬 키의 폭 맞춤(`_fixed_width_iso`)과 동점 키를 새 질의도 그대로 쓴다.
- **픽스처는 상한보다 크게** 만든다 — 작으면 절단이 안 일어나 이 결함이 안 보인다.
- 주석·문서는 한국어 WHY, 커밋 메시지는 영어.
- 테스트: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`

## 지금 사실(구현 전 실측)

```
반환: [(3, 'c-other-0'), (3, 'c-other-1'), (3, 'c-other-2')]
tier 2가 저장소에 실재: 5    결과에 tier 2 있나: False
```
(같은 사이트 5건은 오래됐고 다른 사이트 25건이 최신인 픽스처)

## File Structure

| 파일 | 책임 |
|---|---|
| `src/domain/cases.py` (수정) | 포트에 사이트 필터, 인메모리 구현 |
| `src/infrastructure/mongo_store.py` (수정) | 같은 필터를 `$match`에 |
| `src/application/history.py` (수정) | tier 2·3을 따로 질의 |
| `tests/domain/test_cases.py`·`tests/infrastructure/test_mongo_store.py` | 필터·두 백엔드 합의 |
| `tests/application/test_history.py` | 역전 재현 |
| `docs/architecture.md` | 사다리 계약과 절단 지점 |

---

### Task 1: 저장소가 사이트로 거른다

**Files:**
- Modify: `src/domain/cases.py`, `src/infrastructure/mongo_store.py`
- Test: `tests/domain/test_cases.py`, `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
```python
def closed_by_locators(self, locators: list[str], *, exclude_case_id: str,
                       limit: int = 20,
                       site: tuple[str, str] | None = None,
                       exclude_site: tuple[str, str] | None = None) -> list[CaseRecord]:
```
`site`는 "그 사이트만", `exclude_site`는 "그 사이트를 뺀 것만". 둘 다 선택이고 서로
독립이다 — 한 파라미터에 모드를 섞으면 "site 없이 same_site=True"처럼 뜻 없는 조합이
표현 가능해진다.

Mongo는 `{"gbm": g, "fct": f}`와 `{"$or": [{"gbm": {"$ne": g}}, {"fct": {"$ne": f}}]}`로
표현한다(`$nor` 대신 `$or`+`$ne` — mongomock 지원이 확실하다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_사이트로_거른_종결_케이스만_돌려준다():
    # 두 백엔드가 같은 답을 내야 한다 — 계획 20에서 이 계약이 세 번 깨졌다.
    ...
    assert [r.id for r in repo.closed_by_locators(["rest:/oee"], exclude_case_id="x",
                                                  site=("mx", "gumi"))] == [...]
    assert [r.id for r in repo.closed_by_locators(["rest:/oee"], exclude_case_id="x",
                                                  exclude_site=("mx", "gumi"))] == [...]
```

- [ ] **Step 2~4: RED → 구현 → GREEN**

- [ ] **Step 5: 커밋**

---

### Task 2: tier 2·3을 따로 질의한다

**Files:**
- Modify: `src/application/history.py`
- Test: `tests/application/test_history.py`

- [ ] **Step 1: 역전을 재현하는 테스트를 쓴다**

```python
def test_다른_사이트가_최신이어도_같은_사이트_이력이_굶지_않는다():
    # **픽스처가 상한(20)보다 커야 한다** — 작으면 절단이 안 일어나 결함이 안 보인다.
    # 기존 tier 테스트 14건이 전부 그래서 못 봤다.
    ... 같은 사이트 5건(오래됨) + 다른 사이트 25건(최신) ...
    assert any(h.tier == 2 for h in hits)
```

- [ ] **Step 2~4: RED → 구현 → GREEN**

`_candidates`가 tier 2와 tier 3을 각각 질의한다. 제너레이터이므로 tier 2가 K를 채우면
tier 3 질의는 **아예 일어나지 않는다**(저장소 호출은 tier가 실제로 필요할 때만).

- [ ] **Step 5: 커밋**

---

### Task 3: 문서

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: 사다리 계약 옆에 절단 지점을 적는다** — "낮은 tier로 한 번만"이 성립하려면
  tier마다 자기 상한이 있어야 한다는 것, 그리고 절단을 분리보다 먼저 하면 사다리가
  뒤집힌다는 것.

- [ ] **Step 2: 커밋**

---

## 인계(계획 22 이후)

1. **tier 4는 여전히 한 번에 자른다** — 상류 locator 여럿을 한 질의로 묶고 상한 20을
   건다. tier 4는 그 안에서 더 나눌 축이 없으므로(전부 "상류 대상") 같은 역전이 없다.
2. **`limit=20`은 여전히 임의의 값이다** — K=3을 채우기에 넉넉하다는 것 말고 근거가 없다.
   tier마다 자기 상한을 갖게 됐으니 이제 낮춰도 안전하다.
