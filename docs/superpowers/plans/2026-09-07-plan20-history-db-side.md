# 이력 조회를 DB에서 자르기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `closed_by_fingerprint`·`closed_by_locators`가 조건에 맞는 종결 케이스를
**전부 하이드레이션한 뒤** 파이썬에서 정렬·절단하는 것을 멈추고, 정렬과 상한을 Mongo가
하게 한다.

**Architecture:** 이력 검색(tier 1~4)은 케이스마다 최대 네 번 돈다. 지금 Mongo 구현은
`find()`가 낸 커서를 통째로 `CaseRecord`로 만들고(`_to_record`가 문서마다 pydantic 검증을
돈다) `_newest_first`가 파이썬에서 정렬한 뒤 `[:limit]`한다. 같은 지문이나 같은 locator의
종결 케이스가 수천 건이면 10건을 얻으려고 수천 건을 검증한다. 정렬 키가
`status_since or updated_at`이라 `find().sort()`로는 표현이 안 되므로 집계 파이프라인의
`$ifNull`을 쓴다.

**Tech Stack:** Python 3.12, pymongo(동기), mongomock, pytest.

## Global Constraints

- 규율 1(무raise): 이 층은 저장소 계약이라 예외가 정상이다 — 새 예외를 만들지 않는다.
- 규율 5: `CaseRecord`는 `StrictModel`이다 — **파이프라인이 만든 계산 필드를 그대로
  넘기면 검증 오류**다. 반드시 `$project`로 지운다.
- 두 백엔드(인메모리·Mongo)가 **같은 순서**를 내야 한다. 지금 계약 테스트가 없다.
- 주석·문서는 한국어 WHY, 커밋 메시지는 영어.
- 테스트: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`

## 지금 사실(구현 전 실측)

```
src/infrastructure/mongo_store.py:360  closed_by_fingerprint → find() 전량 → _newest_first
src/infrastructure/mongo_store.py:365  closed_by_locators    → find() 전량 → _newest_first
src/domain/cases.py:209                _newest_first(records, limit)  # 파이썬 정렬
```

- `$match`가 쓸 인덱스는 이미 있다(`(status, fingerprint)`, `(status, target_locator)`).
  없는 것은 **정렬과 절단**이다.
- `_newest_first`의 동점 처리는 파이썬 `sorted`의 안정성에 기댄 dict 삽입 순서다 —
  Mongo의 동점 순서는 규정돼 있지 않으므로 **두 백엔드가 갈린다**. 지금은 그것을
  잡는 테스트가 없다.

## File Structure

| 파일 | 책임 |
|---|---|
| `src/domain/cases.py` (수정) | `_newest_first`의 동점 키를 명시 |
| `src/infrastructure/mongo_store.py` (수정) | 두 질의를 집계로, 정렬·절단을 DB에서 |
| `tests/domain/test_cases.py` (수정) | 동점 순서 |
| `tests/infrastructure/test_mongo_store.py` (수정) | DB 절단·계약 일치 |

---

### Task 1: 동점 순서를 두 백엔드가 합의하게 한다

**Files:**
- Modify: `src/domain/cases.py`
- Test: `tests/domain/test_cases.py`

**Interfaces:**
- Produces: `_newest_first(records, limit)` — 정렬 키가 `(종결 시각 내림, id 내림)`

같은 시각의 두 케이스는 실제로 흔하다(고정 시계 테스트, 같은 배치에서 닫힌 케이스).
동점 키가 없으면 Mongo와 인메모리가 다른 답을 내고, 그 차이는 프로덕션에서만 보인다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_같은_시각의_종결_케이스는_id_내림차순이다():
    repo = InMemoryCaseRepository()
    for cid in ("c-1", "c-3", "c-2"):
        repo.save(CaseRecord(id=cid, ..., status="closed", status_since=T))
    assert [r.id for r in repo.closed_by_fingerprint("fp", exclude_case_id="x")] == \
        ["c-3", "c-2", "c-1"]
```

- [ ] **Step 2: 실패를 확인한다** (지금은 삽입 순서라 `["c-1","c-3","c-2"]`)

- [ ] **Step 3: 구현** — `key=lambda r: (r.status_since or r.updated_at, r.id)`

- [ ] **Step 4: 전체 스위트** — 기존 테스트가 옛 동점 순서에 기대는지 확인한다.

- [ ] **Step 5: 커밋**

---

### Task 2: Mongo가 정렬하고 자른다

**Files:**
- Modify: `src/infrastructure/mongo_store.py`
- Test: `tests/infrastructure/test_mongo_store.py`

**Interfaces:**
- Produces: `_history_pipeline(match: dict, limit: int) -> list[dict]` (모듈 수준 헬퍼)

```python
def _history_pipeline(match, limit):
    return [
        {"$match": match},
        # 정렬 키가 coalesce라 find().sort()로는 표현이 안 된다. status_since는 계획 4b
        # 이후에 생긴 필드라 옛 레코드에는 없다.
        {"$addFields": {"_closed_at": {"$ifNull": ["$status_since", "$updated_at"]}}},
        {"$sort": {"_closed_at": -1, "id": -1}},
        {"$limit": limit},
        # CaseRecord는 StrictModel이다 — 계산 필드를 남기면 검증 오류가 난다.
        {"$project": {"_closed_at": 0}},
    ]
```

`limit <= 0`은 DB에 가기 전에 빈 목록이다 — `$limit: 0`은 Mongo가 거부한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_이력은_DB에서_잘려_온다(db, monkeypatch):
    # 상한만 단정하면 파이썬 절단으로도 통과한다 — 하이드레이션 건수를 센다.
    repo = MongoCaseRepository(db)
    for i in range(25):
        repo.save(CaseRecord(id=f"c-{i}", ..., status="closed", fingerprint="fp",
                             status_since=T + timedelta(minutes=i)))
    seen = []
    original = MongoCaseRepository._to_record
    monkeypatch.setattr(MongoCaseRepository, "_to_record",
                        staticmethod(lambda doc: seen.append(doc) or original(doc)))
    rows = repo.closed_by_fingerprint("fp", exclude_case_id="x", limit=10)
    assert [r.id for r in rows] == [f"c-{i}" for i in range(24, 14, -1)]
    assert len(seen) == 10                      # 전량 하이드레이션이 아니다


def test_계산_필드가_레코드로_새지_않는다(db): ...
def test_limit_0은_DB에_안_간다(db): ...
def test_두_백엔드가_같은_순서를_낸다(db): ...   # 동점 포함
```

- [ ] **Step 2~4: RED → 구현 → GREEN**

- [ ] **Step 5: 커밋**

---

### Task 3: 문서

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: 이력 검색 절에 절단 지점을 적는다** — 정렬 키가 coalesce라 집계를 쓴다는
  것과, 동점을 `id`로 가르는 이유(두 백엔드 합의)를 적는다.

- [ ] **Step 2: 커밋**

---

## 인계(계획 20 이후)

1. **정렬 키는 DB 안에서 폭을 맞춘다** — 시각이 ISO 문자열이고 pydantic이 마이크로초 0일
   때 소수부를 생략하므로, 날것으로 정렬하면 `Z` > `.`라 정각이 같은 초의 최신으로 뒤집힌다
   (검증 리뷰 BLOCKER — 순서만이 아니라 `$limit`이 다른 집합을 고른다). 모듈 docstring이
   범위 비교 세 곳에서 이미 적은 함정이고, 정렬은 파이썬으로 미룰 수 없어 파이프라인에서
   푼다. `$dateFromString`·`$toDate`가 직접적이지만 mongomock이 둘 다 미구현이라 오프라인
   검증이 불가능하다 — 실제 Mongo를 요구하는 테스트를 만들지 않는다는 규약이 우선이다.
   **직렬화 폭(20 또는 27)에 기대는 코드**이므로 그 성질을 테스트가 못박는다.
2. **`$sort`와 `$limit`은 붙어 있어야 한다** — 실제 Mongo가 둘을 top-k 정렬로 합쳐 메모리를
   limit으로 묶는다. 사이에 스테이지를 끼우면 계산 필드 위의 정렬이 후보 전체를 인메모리에
   올린다. 주석으로만 지키고 있다 — mongomock으로는 이 성질을 관측할 수 없다.
3. **동점 키 `id`는 사전순이다** — `c-99` > `c-1000`이다. 목적이 "최신순"이 아니라 **두
   백엔드의 합의**이므로 의도한 동작이고, 테스트가 그 성질을 명시한다.
4. **`$sort`가 계산 필드 위에서 도므로 인덱스를 못 쓴다** — `$match`가 인덱스로 후보를
   좁힌 뒤의 인메모리 정렬이다(기본 32MB 한도). 같은 지문·같은 locator의 종결 케이스가
   그 한도를 넘길 규모가 되면 `status_since`를 쓰기 시점에 항상 채우고 그 필드로 직접
   정렬해야 한다 — 그건 데이터 마이그레이션이다.
5. **같은 폭 함정이 있던 형제 두 곳을 함께 갚았다** — `MongoDigestStore._rows`(정각
   리포트가 최신으로 뒤집혀 `latest()`가 구버전을 돌려줬다. 추세 비교의 유일한 재료다)와
   `MongoLabelStore.list_for`(정각 라벨이 뒤로 밀려 append-only 순서 계약이 깨졌다 —
   계획 19의 캘리브레이션이 마지막 행을 "사람의 최종 믿음"으로 읽으므로 정정 전 라벨이
   세어졌다). `MongoLedger.metrics`는 `at.isoformat()`이 `+00:00`을 붙이는데 `+`가 `.`보다
   작아 **우연히** 순서가 맞는다 — 우연이므로 그 자리도 시각 정렬을 바꿀 때 다시 봐야 한다.
6. **정렬 테스트는 `동점 키` 순서와 시간 순서를 어긋나게 둬야 한다** — 같게 두면 동점
   키가 우연히 정답을 내서, 정렬 키를 무력화하는 변조가 통과한다. 동점 키는 자리마다
   다르다(이력은 문서 필드 `id`, 라벨·집계는 삽입 순서 `_id`) — "삽입 순서"로 일반화하면
   이력 쪽이 안 맞는다. 이 계획에서 같은 함정을 **네 번** 밟았다.
7. **동점 키를 더할 때는 두 백엔드를 함께 움직여라** — digest에 `_id` 내림차순을 더하면서
   인메모리를 안 고쳐, 없던 갈라짐을 만들고 `latest()`가 정반대 리포트를 냈다(검증 리뷰
   MEDIUM 1). 형제 둘에는 계약 테스트가 있고 digest에만 없어서 아무도 못 잡았다 —
   **저장소 순서 계약에는 항상 두 구현을 대조하는 테스트를 붙여라.**
8. **mongomock으로는 못 잡는 자리들** — `$limit: 0`(실제 Mongo는 거부, mongomock은 `[]`),
   **오름차순** `_id` 동점 키의 효과(mongomock 정렬이 안정적이라 유무가 관측 안 된다 —
   내림차순에서는 관측된다. 라벨만 해당하고 이력·집계는 변조가 잡힌다),
   `$dateFromString`·`$toDate`·`$indexOfCP`·`$substrCP`(미구현), `$substr`의 null·비문자열
   처리(실제와 다르다). 이 자리들의 변조는 오프라인에서 전부 등가로 보인다.
9. **`list_by_status`는 여전히 전량 하이드레이션이다** — `label_stats`·`calibration`이
   종결 케이스 전부를 `CaseRecord`로 만들어 id만 쓴다(계획 19 인계 4번). 같은 계열의
   부채이고, id만 내는 포트 메서드가 답이다.

10. **되감긴 시계에서는 "마지막 라벨 = 사람의 최종 믿음"이 깨진다** — 두 저장소 모두
    `labeled_at` 1순위라 나중에 단 라벨이 더 이른 시각이면 앞으로 간다. 두 백엔드가
    **같은** 답을 내는 것은 이 계획이 보장하지만, 그 답이 캘리브레이션(계획 19)의 전제와
    맞는지는 별개 문제다.

11. **`_id`를 동점 키로 쓰는 것은 한 프로세스 안에서만 "삽입 순서"다** — ObjectId의 중간
    5바이트가 프로세스별 난수라, 같은 초에 다른 프로세스가 쓴 두 문서는 도착 순서가 아니라
    난수로 갈린다. 라벨 쪽은 `labels.py`가 이 예외를 적어 뒀고 집계 쪽은 아직 없다.
12. **저장소 포트의 docstring이 동점 계약을 안 적는다** — `DigestStorePort.list`는
    "최신순", `CaseRepositoryPort.closed_by_fingerprint`는 "최신순으로"뿐이다. 이 계획이
    동점 순서를 구속력 있는 계약으로 만들었으니 포트가 그것을 말해야 한다
    (`LabelStorePort.list_for`만 이번에 갱신됐다).
