# 계획 14 — 다중 RCA 후보 + Timeline 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 판정이 근본 원인 **후보 여럿**을 증거와 함께 낼 수 있게 하고(`Verdict.alternates`), 보고서 §5와 `GET /cases/{id}`에 이벤트 로그에서 유도한 **Timeline**을 싣는다. 계획 13 인계 #6(응답 모델)·#11(`submit_answer` 포괄 except)을 같이 갚는다.

**Architecture:** 방향 문서 §307 그대로 — `Verdict.root_cause`(최상위 후보)는 두고 `alternates: list[CauseLink]`를 더한다. 벤치가 `root_cause.component`로 채점하므로 안 깨지고, `verify`의 인용 검사를 `alternates`까지 넓히면 가드레일이 유지된다. 후보의 개수 상한·중복 제거는 코드가 쥔다(규율 6). Timeline은 새 이벤트 종류 없이 기존 6종을 **소비**한다(규율 7) — `ReportModel`이 이벤트 로그에서 한 번 유도하고 md/html/API 렌더러가 갈린다(계획 7의 2단 원칙). 이벤트 스토어가 없는 프로세스에서는 "이벤트 로그 없음"을 **명시**한다(조용한 생략 금지).

**Tech Stack:** pydantic StrictModel, LangGraph 노드(conclude/verify), FastAPI, mistune(HTML 렌더 검증), mongomock 불필요(이벤트 스토어는 InMemory로 충분 — 읽기 API `since`는 포트 계약).

## Global Constraints

- 무raise(CLAUDE.md 규율 1): 노드·렌더러·유도 함수는 raise하지 않는다. `build_report_model`은 순수 함수이고 raise하지 않는다 — Timeline 유도도 같다.
- 시계 주입(규율 2): `clock: Callable[[], datetime]`만. Timeline의 시각은 이벤트의 `at`이지 지금 시각이 아니다.
- LLM이 인용한 evidence id를 신뢰하지 않는다(규율 3): `alternates`의 `evidence_ids`도 `verify`가 `state.evidence`를 우주로 검사한다.
- 수명주기·상한은 코드가 쥔다(규율 4·6): `MAX_ALTERNATES = 3`, 중복 후보 제거는 `conclude`의 코드가 한다. LLM 출력에서 그대로 받지 않는다.
- StrictModel(규율 5): 새 모델 전부 `extra="forbid"`.
- 이벤트 어휘 6종 **불변**(규율 7): Timeline은 소비자다. 새 종류를 더하지 않는다.
- 벤치 채점은 구조화 필드만(`root_cause.component`, `verdict_type`, 이번에 `alternates[i].component`) — 보고서 텍스트 매칭 금지.
- 주석·문서 한국어(WHY만), 커밋 메시지 영어, 커밋 끝 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- 테스트: `rm -rf output/; .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. RED를 실제로 본 뒤 GREEN. 각 픽스는 되돌려 테스트가 빨개지는지 돌연변이로 확인한다.

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `src/domain/case.py` | `CauseLink.confidence`, `Verdict.alternates` | 수정 |
| `src/application/nodes.py` | conclude 프롬프트에 후보 규칙·예시, `_sanitize_alternates`, verify 인용 검사 확장 | 수정 |
| `src/application/worker.py` | 스냅샷의 `alternates` 채움 | 수정 |
| `src/application/events.py` | `collect_events(store, case_id)` — `since` 페이지를 끝까지 읽는다 | 추가 |
| `src/domain/report_model.py` | `TimelineEntry`, `ReportModel.timeline/timeline_source`, `_timeline_summary`, `build_report_model(events=...)` | 수정 |
| `src/presentation/report.py`, `report_html.py` | §2 "다른 후보", §5 Timeline 표 | 수정 |
| `src/patrol/daemon.py`, `src/__main__.py`, `src/api/routes_reads.py` | `events`를 `build_report_model`에 넘긴다 | 수정 |
| `src/api/routes_reads.py`, `src/api/models.py`(신설) | `CaseDetail` 응답 모델, `candidates`, `timeline` | 수정/추가 |
| `src/application/submit.py`, `src/api/routes_cases.py` | `submit_answer` → `error`(503) | 수정 |
| `tests/…` | 각 층 미러 | 추가 |
| `docs/architecture.md`, `docs/glossary.md`, `docs/howto.md`, `tests/README.md` | 후보·Timeline·payload | 수정 |

---

### Task 1: 도메인 — `Verdict.alternates`와 후보의 신뢰도

**Files:**
- Modify: `src/domain/case.py:47-70`
- Test: `tests/domain/test_case.py`

**Interfaces:**
- Produces: `CauseLink.confidence: Literal["high","medium","low"] | None = None`(후보 전용 — 최상위의 신뢰도는 `Verdict.confidence`), `Verdict.alternates: list[CauseLink] = []`.

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/domain/test_case.py 끝에
def test_판정은_후보를_여럿_들_수_있다():
    # 방향 문서 §307: root_cause(최상위)는 그대로 두고 alternates를 더한다 — 벤치가
    # root_cause.component로 채점하므로 안 깨진다.
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                alternates=[CauseLink(component="twin-state", evidence_ids=["ev-2"],
                                      confidence="low", relation="갱신 지연 가능성")])
    assert [a.component for a in v.alternates] == ["twin-state"]
    assert v.alternates[0].confidence == "low"


def test_결론_없는_판정도_후보는_들_수_있다():
    # "확신은 없지만 후보는 이것들이다"가 웹 질의(타깃 3)의 실제 답이다.
    v = Verdict(verdict_type="inconclusive", confidence="low", narrative="n",
                alternates=[CauseLink(component="a", evidence_ids=["ev-1"])])
    assert v.root_cause is None and len(v.alternates) == 1
```

- [ ] **Step 2: RED 확인** — `pytest tests/domain/test_case.py -q` → `ValidationError: extra ... alternates`.

- [ ] **Step 3: 구현**

```python
class CauseLink(StrictModel):
    component: str                        # 토폴로지의 서비스/locator 참조
    evidence_ids: list[str]
    relation: str | None = None           # 기여 요인: 근본 원인과의 관계 / 후보: 왜 후보이고 왜 최상위가 아닌가
    # 후보(alternates) 전용 — 최상위 후보의 신뢰도는 Verdict.confidence다. root_cause·
    # contributing에서는 None으로 둔다(둘을 한 필드로 합치면 보고서가 두 값을 보인다).
    confidence: Literal["high", "medium", "low"] | None = None


class Verdict(StrictModel):
    verdict_type: VerdictType
    root_cause: CauseLink | None = None
    alternates: list[CauseLink] = []      # 최상위 다음의 후보들, 유력한 순(방향 문서 §307)
    contributing: list[CauseLink] = []
    ...
```

- [ ] **Step 4: GREEN + 전체** — `pytest tests/ -q`.
- [ ] **Step 5: 커밋** — `git commit -m "Let a verdict carry alternate root-cause candidates"`.

---

### Task 2: conclude — 후보를 묻고, 코드가 상한·중복을 쥔다

**Files:**
- Modify: `src/application/nodes.py:55-75`(프롬프트), `:292-312`(conclude)
- Test: `tests/application/test_nodes.py`(없으면 `tests/application/test_graph_e2e.py`에)

**Interfaces:**
- Produces: `MAX_ALTERNATES = 3`, `_sanitize_alternates(verdict: Verdict) -> Verdict`(순수).

- [ ] **Step 1: 실패하는 테스트**

```python
def test_후보는_코드가_상한과_중복을_쥔다():
    # 규율 4·6: LLM이 5개를 내도 3개, 최상위와 같은 컴포넌트·서로 같은 컴포넌트는 버린다.
    # 조용히 버리지 않는다 — caveat에 남긴다.
    from src.application.nodes import _sanitize_alternates
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                alternates=[CauseLink(component=c, evidence_ids=["ev-1"])
                            for c in ["plan-sync", "a", "a", "b", "c", "d"]])
    out = _sanitize_alternates(v)
    assert [a.component for a in out.alternates] == ["a", "b", "c"]
    assert any("후보" in cv and "plan-sync" in cv and "d" in cv for cv in out.caveats)


async def test_conclude는_후보를_소독해서_State에_올린다():
    # ScriptedLLM이 최상위와 같은 후보를 내면 State의 verdict에는 없어야 한다.
    ...  # make_e2e_deps + 판정 JSON에 "alternates":[{"component":"plan-sync",...},{"component":"twin-state",...}]
    # 그래프 완주 뒤 store.get_verdict(cid).alternates == [twin-state]
```

- [ ] **Step 2: RED 확인.**

- [ ] **Step 3: 구현**

```python
MAX_ALTERNATES = 3     # 상한은 코드가 쥔다(규율 6) — 보고서 §2가 읽히는 길이의 한계


def _sanitize_alternates(verdict: Verdict) -> Verdict:
    """후보 목록의 상한·중복은 LLM이 아니라 코드가 정한다(규율 4·6). 버린 것은 caveat에.

    validator로 거부하면 conclude의 파싱 실패 경로(degraded "조사 종료 불가")로 떨어져
    후보 하나 중복됐다고 조사 전체가 실패한다 — 그래서 거부가 아니라 소독이다.
    """
    seen = {verdict.root_cause.component} if verdict.root_cause else set()
    kept, dropped = [], []
    for link in verdict.alternates:
        if link.component in seen or len(kept) >= MAX_ALTERNATES:
            dropped.append(link.component)
            continue
        seen.add(link.component)
        kept.append(link)
    if not dropped:
        return verdict
    return verdict.model_copy(update={
        "alternates": kept,
        "caveats": verdict.caveats + [f"후보 정리: {', '.join(dropped)} 제외(중복 또는 상한 {MAX_ALTERNATES} 초과)"]})
```

conclude: `return {"verdict": _sanitize_alternates(verdict)}`. 프롬프트 규칙에 한 줄과 예시를 더한다:

```
- 근본 원인 후보가 여럿이면 가장 유력한 것을 root_cause에, 나머지를 alternates에 유력한 순으로(최대 3) — 각 후보도 실재하는 증거 id를 인용하고 confidence와 relation(왜 후보인지)을 적는다.
{{"verdict_type": "logic_bug", "root_cause": {{...}}, "alternates": [{{"component": "...", "evidence_ids": ["ev-2"], "confidence": "low", "relation": "..."}}], "contributing": [], ...}}
```

- [ ] **Step 4: GREEN + 돌연변이** — `MAX_ALTERNATES`를 5로, `seen` 초기값을 빈 집합으로 각각 바꿔 테스트가 빨개지는지.
- [ ] **Step 5: 커밋** — `"Ask conclude for alternate candidates, and let the code bound them"`.

---

### Task 3: verify — 후보의 인용도 같은 우주로 검사한다

**Files:**
- Modify: `src/application/nodes.py:313-345`
- Test: `tests/application/test_graph_e2e.py`(verify 테스트가 있는 파일)

- [ ] **Step 1: 실패하는 테스트** — 후보 하나가 `state.evidence`에 없는 id를 인용 → `verify_problems`에 `"없는 id ev-99 인용"` → 재작성 1회 → 그래도 실패면 `confidence="low"` 강등. 최상위·기여 요인은 깨끗한데 후보만 더러운 경우로 만든다(후보를 검사하지 않으면 통과해 버리는 형태).

- [ ] **Step 2: RED** — 후보 미검사라 `verify_problems == []`.

- [ ] **Step 3: 구현** — `links += list(verdict.alternates)` 한 줄. 주석: "후보도 LLM이 인용한 id다(규율 3) — 최상위만 검사하면 후보가 환각 id를 실은 채 보고서 §2에 나간다."

- [ ] **Step 4: GREEN + 돌연변이(그 줄 제거).**
- [ ] **Step 5: 커밋** — `"Verify the citations of alternate candidates too"`.

---

### Task 4: 스냅샷 — `alternates`를 채운다(계획 7이 열어 둔 자리)

**Files:**
- Modify: `src/application/worker.py:422-433`
- Test: `tests/application/test_worker.py`(스냅샷 테스트 옆)

- [ ] **Step 1: 실패하는 테스트** — 후보가 있는 판정으로 닫힌 케이스의 `snapshots.get(cid).alternates == ["twin-state"]`.
- [ ] **Step 2: RED**(`[]`).
- [ ] **Step 3: 구현** — `alternates=[a.component for a in verdict.alternates] if verdict else [],`.
- [ ] **Step 4: GREEN.** **Step 5: 커밋** — `"Record alternate candidates in the verdict snapshot"`.

---

### Task 5: 보고서 §2 — "다른 후보"(md + html)

**Files:**
- Modify: `src/presentation/report.py:157-184`, `src/presentation/report_html.py`(`cause_items` 조립부, `<h2>2. 판정</h2>`)
- Test: `tests/presentation/test_report.py`, `tests/presentation/test_report_html.py`

- [ ] **Step 1: 실패하는 테스트**

```python
def test_판정_절은_다른_후보를_신뢰도와_증거와_함께_낸다():
    model = _model(verdict=Verdict(..., alternates=[CauseLink(component="twin-state",
                   evidence_ids=["ev-7"], confidence="low", relation="갱신 지연")]))
    md = render_md(model)
    assert "- 다른 후보:" in md and "twin-state (신뢰도 low, 증거: ev-7) — 갱신 지연" in md

def test_후보가_없으면_없음을_명시한다():  # 조용한 생략 금지
    assert "- 다른 후보:\n  없음" in render_md(_model(...))
```
HTML: 같은 두 경우 + `mistune`으로 렌더한 결과에 `<li>`가 있는지(계획 7 리뷰의 "고쳤다는 보고를 믿지 않는다").

- [ ] **Step 2: RED.** **Step 3: 구현** — §2에 근본 원인 다음, 기여 요인 앞에:

```python
    lines.append("- 다른 후보:")
    if not verdict.alternates:
        lines.append("  없음")
    else:
        for a in verdict.alternates:
            ids = ", ".join(a.evidence_ids) or "없음"
            conf = f"신뢰도 {a.confidence}, " if a.confidence else ""
            relation = f" — {a.relation}" if a.relation else ""
            lines.append(f"  - {a.component} ({conf}증거: {ids}){relation}")
```

- [ ] **Step 4: GREEN + 전체.** **Step 5: 커밋** — `"Show alternate candidates in the verdict section"`.

---

### Task 6: Timeline 유도 — `ReportModel.timeline`

**Files:**
- Modify: `src/domain/report_model.py:30-57, 144-200`
- Create: `collect_events` in `src/application/events.py`
- Test: `tests/domain/test_report_model.py`, `tests/application/test_events.py`(있으면)

**Interfaces:**
- Produces:
  ```python
  class TimelineEntry(StrictModel):
      seq: int
      at: datetime
      event: str            # EventKind 값 그대로
      summary: str          # 코드가 만든 한 줄(아래 _timeline_summary)
  ReportModel.timeline: list[TimelineEntry] = []
  ReportModel.timeline_source: Literal["events", "none"] = "none"   # none = 이 프로세스에 이벤트 스토어가 없다
  build_report_model(record, *, verdict, evidence, case_file, clock,
                     evidence_summaries=None, events: list[EngineEvent] | None = None)
  collect_events(store: EventStorePort, case_id: str, *, page: int = 200) -> list[EngineEvent]
  ```

- [ ] **Step 1: 실패하는 테스트**

```python
def test_Timeline은_이벤트_로그를_seq_순으로_요약한다():
    events = [EngineEvent(event="case_status_changed", case_id="c-1", at=T, seq=1, data={"status": "open", "reason": "finding"}),
              EngineEvent(event="round_started", case_id="c-1", at=T, seq=2, data={"round": 1, "dispatched": ["t-1"]}),
              EngineEvent(event="task_finished", case_id="c-1", at=T, seq=3, data={"task_id": "t-1", "role": "mongo", "status": "ok", "evidence_ids": ["ev-1"], "error": None}),
              EngineEvent(event="question_raised", case_id="c-1", at=T, seq=4, data={"question": "계획 변경?"}),
              EngineEvent(event="verdict_formed", case_id="c-1", at=T, seq=5, data={"verdict_type": "stale_data", "confidence": "high", "rewritten": False}),
              EngineEvent(event="report_ready", case_id="c-1", at=T, seq=6, data={"path": "/r/c-1.html"})]
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={}, clock=lambda: T,
                               events=list(reversed(events)))            # 순서를 섞어 넣어도
    assert [e.seq for e in model.timeline] == [1, 2, 3, 4, 5, 6]
    assert [e.summary for e in model.timeline] == [
        "상태 → open (finding)", "라운드 1 시작 — 태스크 1개", "태스크 t-1(mongo) ok — 증거 1개",
        "질문: 계획 변경?", "판정 stale_data (high)", "보고서 /r/c-1.html"]
    assert model.timeline_source == "events"

def test_이벤트_스토어가_없으면_Timeline_없음을_명시한다():
    model = build_report_model(_record(), verdict=None, evidence=[], case_file={}, clock=lambda: T)
    assert model.timeline == [] and model.timeline_source == "none"

def test_모르는_data_형태에도_Timeline은_raise하지_않는다():
    ev = EngineEvent(event="task_finished", case_id="c-1", at=T, seq=1, data={})
    model = build_report_model(_record(), ..., events=[ev])
    assert model.timeline[0].summary == "태스크 ?(?) ? — 증거 0개"

# tests/application/test_events.py
def test_collect_events는_페이지를_끝까지_읽는다():
    store = InMemoryEventStore()
    for i in range(5): store.append(EngineEvent(event="round_started", case_id="c-1", at=T, data={"round": i}))
    assert [e.seq for e in collect_events(store, "c-1", page=2)] == [1, 2, 3, 4, 5]
```

- [ ] **Step 2: RED.** **Step 3: 구현**

```python
# report_model.py
def _timeline_summary(event) -> str:
    """이벤트 6종 → 한 줄. 어휘가 바뀌면 여기가 유일한 갱신 지점이다(규율 7)."""
    d = event.data if isinstance(event.data, dict) else {}
    kind = event.event
    if kind == "case_status_changed":
        reason = d.get("reason")
        return f"상태 → {d.get('status', '?')}" + (f" ({reason})" if reason else "")
    if kind == "round_started":
        return f"라운드 {d.get('round', '?')} 시작 — 태스크 {len(_as_list(d.get('dispatched')))}개"
    if kind == "task_finished":
        return (f"태스크 {d.get('task_id', '?')}({d.get('role', '?')}) {d.get('status', '?')}"
                f" — 증거 {len(_as_list(d.get('evidence_ids')))}개")
    if kind == "question_raised":
        return f"질문: {d.get('question', '?')}"
    if kind == "verdict_formed":
        tail = " · 재작성 뒤 강등" if d.get("rewritten") else ""
        return f"판정 {d.get('verdict_type', '?')} ({d.get('confidence', '?')}){tail}"
    if kind == "report_ready":
        return f"보고서 {d.get('path', '?')}"
    return kind


def _timeline(events) -> list[TimelineEntry]:
    entries = []
    for e in sorted(events, key=lambda e: (e.seq if isinstance(e.seq, int) else 0)):
        try:
            entries.append(TimelineEntry(seq=e.seq if isinstance(e.seq, int) else 0, at=e.at,
                                         event=e.event, summary=_timeline_summary(e)))
        except Exception:                                          # noqa: BLE001 — 순수·무raise
            continue
    return entries
```
`build_report_model`: `timeline=_timeline(events) if events is not None else []`, `timeline_source="events" if events is not None else "none"`.

```python
# src/application/events.py
def collect_events(store, case_id: str, *, page: int = 200) -> list[EngineEvent]:
    """since 페이지를 끝까지 읽는다 — 보고서는 부분 Timeline을 내면 안 된다(조용한 생략)."""
    out, cursor = [], 0
    while True:
        chunk = store.since(case_id, after_seq=cursor, limit=page)
        out.extend(chunk)
        if len(chunk) < page:
            return out
        cursor = chunk[-1].seq
```

- [ ] **Step 4: GREEN + 돌연변이** — 정렬 제거(순서 테스트), `< page` 조건을 `== 0`으로(페이지 테스트가 무한 루프가 아니라 실패하는지 — timeout 붙여 실행).
- [ ] **Step 5: 커밋** — `"Derive a timeline from the event log into the report model"`.

---

### Task 7: 보고서 §5 Timeline 표(md + html) + 호출부 배선

**Files:**
- Modify: `src/presentation/report.py:241-`, `report_html.py`(`<h2>5. 조사 경위</h2>` 아래), `report.py:27-45`(`render_report`에 `events=None`)
- Modify: `src/patrol/daemon.py:250-260`(`self.events`가 있으면 `collect_events`), `src/__main__.py:266`(`case show` 즉석 렌더 — `Persistence`의 events), `src/api/routes_reads.py:143`(report 즉석 렌더 — `rt.events`)
- Test: `tests/presentation/test_report.py`, `test_report_html.py`, `tests/patrol/test_daemon.py`(보고서에 Timeline 행), `tests/test_cli.py`(`case show` 즉석 렌더에 Timeline), `tests/api/test_routes_reads.py`

- [ ] **Step 1: 실패하는 테스트** — md에 `| seq | 시각 | 이벤트 | 요약 |` 표와 행 `| 1 | 2026-09-03T08:00:00+00:00 | case_status_changed | 상태 → open (finding) |`; `timeline_source == "none"`이면 `- Timeline: 이벤트 로그 없음(이 프로세스에 이벤트 스토어가 없다)`. HTML은 mistune 없이 문자열 `<h3>Timeline</h3>` + `<table>` + 이스케이프(`question`에 `<b>`를 넣어 `&lt;b&gt;`). 호출부 셋: 데몬 발행 보고서 파일에 Timeline 행이 있고(이벤트 스토어 주입), `case show`가 파일 없을 때 즉석 렌더에 실으며, `GET /cases/{id}/report`(파일 없음)도 싣는다.

- [ ] **Step 2: RED.** **Step 3: 구현** — md §5 맨 앞(라운드 줄 뒤): 표 앞뒤 빈 줄(계획 7의 GFM lazy continuation 교훈). 호출부:

```python
# daemon._report_model
events = collect_events(self.events, case_id) if self.events is not None else None
return build_report_model(..., events=events)
```

- [ ] **Step 4: GREEN + 전체 + 호출부 돌연변이**(각 호출부에서 `events=` 제거 → 해당 테스트 RED — "함수는 되는데 호출부가 안 넘긴다"가 이 리포의 반복 실패 유형이다).
- [ ] **Step 5: 커밋** — `"Render the timeline in the report, from every process that renders one"`.

---

### Task 8: API — `CaseDetail` 응답 모델, `candidates`, `timeline`

**Files:**
- Create: `src/api/models.py`
- Modify: `src/api/routes_reads.py:79-95`
- Test: `tests/api/test_routes_reads.py`

**Interfaces:**
```python
class Candidate(StrictModel):
    rank: int                       # 1 = root_cause
    component: str
    confidence: str | None
    evidence_ids: list[str]
    rationale: str | None           # CauseLink.relation

class CaseDetail(StrictModel):      # GET /cases/{id} — 계획 13 인계 #6
    case_id: str; gbm: str; fct: str; status: str; concern: str; origin: str
    symptom: str; t0: str; updated_at: str; verdict_summary: str | None
    question: str | None; question_kind: str | None; requested_by: str | None
    intake_done: bool; target_locator: str | None
    stages: list[dict]; verdict: dict | None
    candidates: list[Candidate]     # root_cause(rank 1) + alternates(rank 2..), 방향 문서 §385
    timeline: list[dict]            # TimelineEntry.model_dump(mode="json")
    task_error_rate: str; knowledge_digests: dict[str, str]
```

- [ ] **Step 1: 실패하는 테스트** — 후보 2개 판정이 있는 케이스의 상세: `candidates == [{"rank":1,"component":"plan-sync","confidence":"high",...},{"rank":2,"component":"twin-state","confidence":"low",...}]`; 판정 없음 → `candidates == []`; `timeline`이 이벤트 스토어의 항목을 싣는다(`rt.events.append(...)` 뒤 GET); 응답이 `CaseDetail`로 검증된다(`response_model=CaseDetail` — 모르는 키가 섞이면 500이 아니라 테스트에서 잡히게 `CaseDetail.model_validate(r.json())`).

- [ ] **Step 2: RED.** **Step 3: 구현** — `candidates` 유도는 코드 한 곳(`_candidates(verdict) -> list[Candidate]`): rank 1의 confidence는 `verdict.confidence`, 이후는 `link.confidence`.

- [ ] **Step 4: GREEN + 전체.** **Step 5: 커밋** — `"Answer GET /cases/{id} with a response model, candidates and a timeline"`.

---

### Task 9: `submit_answer` — 저장소 장애는 `not_found`가 아니다(계획 13 인계 #11)

**Files:**
- Modify: `src/application/submit.py:17-40`, `src/api/routes_cases.py:92-99`
- Test: `tests/application/test_submit.py`, `tests/api/test_routes_cases.py`

- [ ] **Step 1: 실패하는 테스트** — `repo.attach_answer`가 `RuntimeError("mongo down")`을 던지면 `submit_answer(...) == "error"`; API는 503 `{"result": "error"}`. 404로 보이면 클라이언트는 "케이스가 없다"고 믿고 재시도하지 않는다.
- [ ] **Step 2: RED**(`not_found`/404). **Step 3: 구현** — `SubmitResult`에 `"error"`, except 분기가 `"error"`, 라우트 `503 if result == "error"`. docstring의 어휘 표 갱신.
- [ ] **Step 4: GREEN.** **Step 5: 커밋** — `"Report a repository failure on POST /answers as 503, not 404"`.

---

### Task 10: 벤치·문서·인계

**Files:**
- Modify: `tests/test_bench_scenarios.py:132-180`(A1의 `_VERDICT_A1`에 `alternates` 추가, `verdict.alternates[0].component == "twin-state"` 단정 — 구조화 필드만), `docs/architecture.md`(§5 보고서·이벤트 소비자에 Timeline, `Verdict.alternates`), `docs/glossary.md`(**후보(alternates)**, **Timeline** 항목), `docs/howto.md`(api 절: `candidates`·`timeline` 예시 payload), `tests/README.md`, 이 계획서 끝 "인계".

- [ ] **Step 1: 벤치 확장 → RED(alternates 빈 목록) → Step 3에서 이미 GREEN이어야 한다(Task 2~3이 됐다면). 아니면 무엇이 빠졌는지가 드러난다.**
- [ ] **Step 2: 문서.** 문서가 어떤 함수를 부른다고 쓰면 `grep`으로 호출부를 확인한 뒤 쓴다(CLAUDE.md "문서가 주장하는 배선").
- [ ] **Step 3: 전체 통과 → 커밋** — `"Score alternate candidates in the bench, and document the timeline"`.

---

## 자기 검토

- **커버리지**: 방향 문서 §286(판정 후보 다수) → Task 1·2·3·5·8; §296(§5 Timeline) → Task 6·7·8; §307(도메인 형태) → Task 1; 계획 7 인계 `VerdictSnapshot.alternates` → Task 4; 계획 13 인계 #6 → Task 8, #11 → Task 9.
- **의도적으로 뺀 것(YAGNI)**: 방향 문서 §385 payload의 `coverage`·`as_of` 블록(스냅샷·§4가 이미 갖고 있고 소비자가 없다), 후보별 권고(권고는 판정 하나의 것), Timeline의 SSE 재생(`/events`가 이미 seq로 한다).
- **타입 일관성**: `CauseLink.confidence`는 Task 1에서 정의하고 Task 5·8이 같은 이름으로 읽는다. `TimelineEntry`는 Task 6이 정의하고 Task 7·8이 `model_dump(mode="json")`으로 낸다. `collect_events`는 Task 6이 만들고 Task 7의 세 호출부가 쓴다.

## 인계(계획 14 이후 — P7 Fleet / P8 관측성·라벨)

1. **후보의 정답 대조는 P8이다.** `VerdictSnapshot.alternates`에 컴포넌트 이름이 남으니
   라벨(`POST /cases/{id}/label`)이 생기면 "정답이 최상위였나 / 후보 안에 있었나 / 없었나"
   세 칸의 캘리브레이션이 가능하다. 지금은 채우기만 한다.
2. **Timeline은 이벤트 로그가 살아 있는 동안만 완전하다.** retention 스윕이
   `case_events`를 걷으면 옛 케이스의 보고서 즉석 렌더는 "이벤트 없음"을 낸다 — 파일로
   발행된 보고서에는 발행 시점의 Timeline이 남아 있다. 스냅샷에 Timeline을 박제할지는
   P8의 관측성 설계에서 정한다(지금은 소비자가 없다).
3. **`candidates`의 `coverage`·`as_of` 블록**(방향 문서 §385)은 넣지 않았다 — §4 증거 표와
   스냅샷이 이미 갖고 있고 웹 소비자가 아직 없다. 웹 UI가 생길 때 payload를 다시 본다.
4. **`_sanitize_alternates`는 컴포넌트 이름의 문자열 동일성만 본다.** `plan-sync`와
   `plan_sync`는 다른 후보다. 토폴로지 locator로 정규화하려면 conclude가 토폴로지를
   받아야 한다(지금은 안 받는다).
5. **`CaseDetail.stages/verdict/timeline`은 `list[dict]`/`dict`다** — 내부 모델을 그대로
   dump한다. 웹 클라이언트가 생기면 그 셋도 응답 모델로 세운다.
6. **후보가 기여 요인과 같은 컴포넌트여도 거르지 않는다** — "이것 대신"과 "이것에 더해"가
   같은 컴포넌트를 가리키는 것이 모순인지는 설계 판단이다(검증 리뷰 L2). 지금은 둔다.
7. **Timeline이 걷힌 뒤**: retention이 `case_events`를 걷으면 즉석 렌더는 "이벤트 없음"을
   낸다 — "조사가 이벤트를 안 냈다"와 구별되지 않는다(리뷰 L6). 파일 보고서에는 발행 시점의
   Timeline이 남는다. 스냅샷에 Timeline을 박제할지는 P8에서.
