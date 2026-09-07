# 되먹임의 소비자 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기록만 되고 아무도 읽지 않는 두 가지 — 캘리브레이션 라벨과 조사 메트릭 —
에게 프로덕션 소비자를 준다.

**Architecture:** 계획 15가 학습 루프의 **생산 쪽**을 만들었다. 라벨은 쌓이고 스냅샷은
박제되고 메트릭은 Mongo에 들어간다. 그런데 읽는 쪽이 없다. `case label --stats`는
게이트가 열렸을 때 "confidence별 적중을 계산할 수 있다"고 **말만 하고 계산하지 않고**,
`MetricsSinkPort.metrics()`는 테스트에서만 불린다. 이 계획은 두 소비자를 만든다:
게이트가 열렸을 때의 캘리브레이션 표와 `patrol status`의 조사 지표 요약.

**Tech Stack:** Python 3.12, pydantic v2, pytest(`asyncio_mode=auto`).

## Global Constraints

- 규율 1(무raise): 저장소 장애는 상태로 흡수한다 — 관측성이 명령을 죽이면 안 된다.
- 규율 2: `datetime.now()`는 `src/__main__.py` 밖에서 부르지 않는다.
- 규율 5: 새 pydantic 모델은 `StrictModel` 상속.
- 규율 6: **게이트 임계와 분모 규칙은 코드가 쥔다.** 어느 라벨을 분모에 넣을지를
  사람이 config로 고르게 하면 숫자가 원하는 대로 나온다.
- 주석·문서는 한국어 WHY, 커밋 메시지는 영어.
- 테스트: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`

## 지금 사실(구현 전 실측)

```
$ grep -rn "\.metrics(" src/ | grep -v "self._db\|scenario.metrics"
(없음 — 프로덕션 소비자 0)
$ grep -rn "\.metrics(" tests/ | wc -l
14
```

- `case label --stats`(`src/__main__.py:306`)는 `stats.why`와 게이트 상태만 찍는다.
  게이트가 열려도 **아무것도 더 계산하지 않는다** — "계산할 수 있다"는 문장이
  프로그램의 유일한 산출이다.
- `label_stats`의 docstring이 "정확도는 여기서도, 어디서도 계산하지 않는다"라고
  적는다. 이 계획이 그 문장을 바꾼다.
- 재료는 다 있다: `VerdictSnapshot.confidence`·`root_cause_component`(retention보다
  오래 산다)와 `RootCauseLabel.agreement`·`saw_report`.

## File Structure

| 파일 | 책임 |
|---|---|
| `src/application/labels.py` (수정) | `Calibration` 모델과 `calibration()` |
| `src/__main__.py` (수정) | `case label --stats`가 표를 찍는다, `patrol status`가 지표를 찍는다 |
| `tests/application/test_labels.py` (수정) | 게이트·분모·앵커링 |
| `tests/test_cli.py` (수정) | 두 명령의 출력 |

---

### Task 1: 캘리브레이션 계산

**Files:**
- Modify: `src/application/labels.py`
- Test: `tests/application/test_labels.py`

**Interfaces:**
- Consumes: `VerdictSnapshotPort.get`(`src/domain/snapshot.py`),
  `LabelStorePort.list_for`·`labeled_case_ids`(`src/domain/label.py`),
  `label_stats`(같은 파일)
- Produces:
  ```python
  class ConfidenceBucket(StrictModel):
      confidence: str        # "high"/"medium"/"low"/"미상"
      n: int                 # 분모(unknown 라벨 제외)
      correct: int
      partially_correct: int
      wrong: int
      excluded_unknown: int  # 분모에서 뺀 수 — 숨기면 n이 작은 이유를 모른다
      saw_report: int        # 보고서를 보고 라벨한 수(앵커링 의심 표시)

  class Calibration(StrictModel):
      gate_open: bool
      why: str
      buckets: list[ConfidenceBucket] = []   # 게이트가 닫혀 있으면 빈 목록

  def calibration(*, repo, labels, snapshots) -> Calibration: ...
  ```

**설계 판단(코드가 쥔다 — 규율 6):**

- **게이트가 닫혀 있으면 `buckets`는 빈 목록이다.** 숫자를 내지 않는다. 12/40으로
  낸 30%는 다음 주에 뒤집히고, 한 번 보고되면 사람이 그것을 기억한다.
- **`unknown` 라벨은 분모에서 뺀다.** "모르겠다"는 틀렸다는 증거가 아니다. 다만
  뺀 수를 `excluded_unknown`으로 **함께 보고한다** — 조용히 빼면 n이 왜 작은지
  모른다.
- **케이스당 마지막 라벨만 센다.** 라벨은 append-only라 한 케이스에 여럿이 붙는다.
  전부 세면 한 사람이 여러 번 고친 케이스가 분모를 지배한다.
- **`confidence`가 `None`인 스냅샷은 `"미상"` 버킷이다.** 버리면 분모에 생존 편향이
  생긴다(판정이 confidence를 못 낸 케이스가 곧 어려운 케이스다).
- **스냅샷이 없는 라벨은 건너뛰고 세지 않는다.** 대조 대상이 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/application/test_labels.py
from src.application.labels import calibration
from src.domain.snapshot import InMemoryVerdictSnapshotStore, VerdictSnapshot


def test_게이트가_닫혀_있으면_버킷을_내지_않는다():
    # 기존 픽스처로 종결 3건·라벨 2건 정도를 만든다
    result = calibration(repo=repo, labels=labels, snapshots=snapshots)
    assert result.gate_open is False and result.buckets == []


def test_unknown_라벨은_분모에서_빠지고_수는_보고된다():
    # 게이트를 열 만큼 만들고, 한 confidence에 unknown을 섞는다
    bucket = next(b for b in result.buckets if b.confidence == "high")
    assert bucket.n == bucket.correct + bucket.partially_correct + bucket.wrong
    assert bucket.excluded_unknown == 1


def test_케이스당_마지막_라벨만_센다():
    # 같은 케이스에 wrong → correct 순으로 두 번 라벨
    assert bucket.correct == 1 and bucket.wrong == 0


def test_confidence가_없으면_미상_버킷이다(): ...


def test_스냅샷이_없는_라벨은_세지_않는다(): ...


def test_저장소가_던져도_캘리브레이션은_상태로_돌려준다():
    # 규율 1 — 관측성이 명령을 죽이면 안 된다
    result = calibration(repo=repo, labels=_RaisingLabels(), snapshots=snapshots)
    assert result.gate_open is False and "실패" in result.why
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -B -m pytest tests/application/test_labels.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'calibration'`

- [ ] **Step 3: 구현**

`label_stats`를 먼저 부르고 `gate_open`이 False면 즉시 돌려준다. 열려 있으면
`labeled_case_ids()` ∩ 종결 id를 돌며 `snapshots.get(case_id)`와 마지막 라벨을 짝지어
버킷을 만든다. 전체를 `try/except Exception`으로 감싸 `why`에 실패를 적는다.

- [ ] **Step 4: 통과를 확인한다**

- [ ] **Step 5: 커밋**

```bash
git commit -m "Compute the calibration the gate says is possible"
```

---

### Task 2: `case label --stats`가 표를 찍는다

**Files:**
- Modify: `src/__main__.py`
- Test: `tests/test_cli.py`

호출부가 안 바뀌면 계산은 아무도 안 본다 — 이 리포가 반복해서 데인 자리다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_case_label_stats가_게이트가_열리면_confidence별_표를_찍는다(capsys, ...):
    # 종결 라벨을 게이트가 열릴 만큼 만들고 실제 CLI 진입점을 부른다
    assert "high" in out and "적중" in out


def test_게이트가_닫혀_있으면_표를_안_찍는다(capsys, ...):
    assert "적중" not in out
```

- [ ] **Step 2~4: RED → 구현 → GREEN**

`_cmd_case_label`의 `--stats` 분기에서 `calibration(...)`을 부르고, `buckets`가
비어 있지 않을 때만 표를 찍는다. 각 행에 `excluded_unknown`과 `saw_report`를 함께
낸다 — 앵커링 의심을 숫자 옆에 두지 않으면 사람이 적중률만 읽는다.

- [ ] **Step 5: 커밋**

---

### Task 3: `patrol status`가 조사 지표를 찍는다

**Files:**
- Modify: `src/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `MetricsSinkPort.metrics(name, limit)`

`investigation.duration_s`는 `outcome` 태그(`closed`/`failed`)를 달고 쌓인다. 사람이
볼 것은 건수와 중앙값이다 — 평균은 파킹 한 건이 며칠 걸리면 통째로 왜곡된다.
**실패를 분모에서 빼지 않는다**(생존 편향 — 스냅샷이 `failed`를 남기는 것과 같은 이유).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_patrol_status가_조사_지표를_찍는다(capsys, ...):
    # 레저에 duration_s를 outcome=closed 둘·failed 하나로 넣고 status를 부른다
    assert "조사 3건" in out and "실패 1건" in out


def test_지표가_없으면_없다고_말한다(capsys, ...):
    assert "관측치 없음" in out


def test_레저가_던져도_status는_다른_정보를_계속_찍는다(capsys, ...):
    # 규율 1 — 메트릭은 버려도 되는 관측치다
    assert "하트비트" in out
```

- [ ] **Step 2~4: RED → 구현 → GREEN**

- [ ] **Step 5: 커밋**

---

### Task 4: 문서

**Files:**
- Modify: `docs/architecture.md`, `docs/howto.md`

- [ ] **Step 1: 학습 루프가 이제 닫혔다고 적는다**

생산(라벨·스냅샷·메트릭)과 소비(캘리브레이션 표·`patrol status`)를 짝지어 적고,
분모 규칙(unknown 제외·케이스당 마지막 라벨·failed 포함)을 명시한다. 규칙이
문서에 없으면 다음 사람이 숫자를 다르게 읽는다.

- [ ] **Step 2: 커밋**

---

## 인계(계획 19 이후)

1. **캘리브레이션은 `confidence`만 가른다** — `concern`·`origin`·`verdict_type`별로도
   물을 수 있고 스냅샷에 재료가 다 있다. 지금 안 하는 이유는 게이트가 열리는 데
   종결 라벨 30건이 필요한데 그것을 축 셋으로 더 쪼개면 각 칸이 한 자리가 되기
   때문이다. 라벨이 쌓이면 그때.
2. **`saw_report`는 표시만 하고 판단하지 않는다** — 앵커링 보정(보고서를 본 라벨에
   가중치를 다르게)은 통계적 근거가 필요하고, 지금은 그 근거가 없다.
3. **메트릭은 `investigation.duration_s` 하나뿐이다** — 토큰은 여전히 미측정이고
   보고서 푸터가 그렇게 적는다. 새 메트릭을 더하면 `patrol status`의 소비자도 함께
   더해야 한다(이 계획이 갚은 것이 정확히 그 부채다).
