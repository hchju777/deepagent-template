# 계획 5: 보고·채널·E2E 벤치 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스펙 §5.1(보고서 5절)·§5.2-F1(이벤트 봉투와 세 채널)·§5.3(`chat` CLI)·§5.4-F6(발송 멱등)·§5.5-4(간판 시나리오 벤치) — 조사 결과가 사람에게 닿는 마지막 구간을 완성한다. 이것으로 v1 스코프가 닫힌다.

**Architecture:** 보고서는 **도메인 객체 → 렌더러**(md 템플릿) 한 방향이고, 소스는 계획 4b가 박제한 `store.get_case_file`+`get_verdict`+`list_evidence`+`CaseRecord`다(스레드 state 아님). 이벤트는 그래프 `updates`를 유스케이스 층의 얇은 매퍼가 **안정 소어휘 봉투**로 바꾸고, CLI가 그 봉투의 첫 소비자가 된다(§5.2). 발송은 "기록(pending) → 발송 → sent" 2상으로 레저에 남긴다.

**Tech Stack:** 계획 4b 위에 `aiosmtplib>=3.0,<5`(메일). 템플릿은 표준 라이브러리 `string.Template` — Jinja2는 반복·조건이 필요해질 때(전작 판단 계승).

**4b 인계 노트 반영 (설계 구속):**
1. 보고서 §1/§5 소스는 `get_case_file` 스냅샷 — 스레드 state를 읽지 않는다.
2. F1은 `investigate_case`/`resume_case`/`worker.run_once|resume_once`의 **시그니처를 바꾼다**(`on_event` 싱크 추가). 새 모듈만 얹어서는 안 된다.
3. chat 모드 ①은 `run_once(interaction_policy="interactive")` 패스스루 + `CaseRecord.question` 표시로 조립한다.
4. 파킹 이월: resume 중 F3 경로의 `human:answer` 박제는 Task 6 E2E가 커버한다.

## Global Constraints

- **이벤트 어휘는 정확히 5종**: `case_status_changed | round_started | task_finished | question_raised | report_ready`. 봉투는 `{event, schema_version, case_id, at, data}`. 그래프 내부 노드명·State diff는 **봉투 밖으로 나가지 않는다**(§5.2).
- **보고서는 파일 먼저**: 렌더 → `output/`에 쓰기 → 그 다음에야 메일·알림. 발송 실패가 보고서를 없애지 않는다.
- **조용한 생략 금지**: 5절은 미조사 태스크·error·기각 가설·verify 문제를 명시한다. 빈 섹션은 "없음".
- **발송 멱등(F6)**: `pending` 기록 → 발송 → `sent` 갱신. 재개·다음 틱이 pending을 재시도하고 기록 id로 중복을 억제한다.
- **채점 술어는 Verdict 구조화 필드**(원인 컴포넌트 id·판정 유형) — md 텍스트 매칭 금지(§5.5-4).
- **렌더러·매퍼·발송기는 raise하지 않는다**(호출자가 워커·데몬이므로). 시계 주입, 한국어, StrictModel, 기동 거부 철학.
- 웹 UI는 v1 스코프 밖 — 계약(봉투)만 확정한다.

## File Structure

```
requirements.txt                              # aiosmtplib
src/config/schema_app.py                      # ReportConfig(output_dir, mail{...})
src/domain/events.py                          # EngineEvent, EVENT_SCHEMA_VERSION
src/application/events.py                     # map_update_to_events (updates→봉투)
src/application/usecase.py                    # (수정) on_event 싱크 + astream
src/application/worker.py                     # (수정) on_event 패스스루
src/application/intake.py                     # 접수 대화(질문 생성→응답→Case 조립)
src/presentation/__init__.py
src/presentation/report.py                    # render_report(md 5절) + write_report
src/presentation/mail.py                      # SmtpSender/NullSender, send_pending
src/patrol/ledger.py                          # (수정) 발송 기록 2상 API
src/patrol/daemon.py                          # (수정) 종결 시 보고서·알림 발송
src/__main__.py                               # (수정) chat, case show --report
tests/domain/test_events.py
tests/application/test_events_mapper.py, test_intake.py, test_usecase_stream.py
tests/presentation/__init__.py, test_report.py, test_mail.py
tests/test_bench_scenarios.py                 # 부록 A 2종 (회귀 모드)
tests/test_cli.py                             # (추가) chat, --report
```

---

### Task 1: 이벤트 봉투 + updates 매퍼

**Files:**
- Create: `src/domain/events.py`, `src/application/events.py`
- Test: `tests/domain/test_events.py`, `tests/application/test_events_mapper.py`

**Interfaces:**
- `EVENT_SCHEMA_VERSION = 1`
- `EngineEvent(StrictModel)`: `event: Literal["case_status_changed","round_started","task_finished","question_raised","report_ready"]`, `schema_version: int = EVENT_SCHEMA_VERSION`, `case_id: str`, `at: datetime`, `data: dict = {}`.
- `map_update_to_events(update: dict, *, case_id: str, clock) -> list[EngineEvent]` — LangGraph `stream_mode="updates"`가 주는 `{노드명: 부분상태}` 한 덩어리를 봉투로 변환. **노드명은 봉투 밖으로 나가지 않는다**(매핑 규칙만 노드명을 본다):
  - `select` → `round_started`, data `{"round": 상태의 round + 1이 아니라 노드가 준 값이 없으면 생략, "dispatched": [running 태스크 id들]}`.
  - `execute` → 각 태스크마다 `task_finished`, data `{"task_id", "role", "status", "evidence_ids", "error"}`.
  - `integrate` → decision이 `"ask"`면 `question_raised` data `{"question"}`; 그 외엔 이벤트 없음(라운드 시작은 select가 낸다).
  - `ask_human`/`conclude`/`verify`/`frame` → 이벤트 없음(상태 전이는 워커가 `case_status_changed`로 낸다).
  - 알 수 없는 노드명·형태 이상 → 빈 리스트(raise 금지).
- `case_status_event(case_id, status, *, clock, reason=None) -> EngineEvent` — 워커·CLI가 상태 전이를 알릴 때 쓰는 생성자.
- `report_ready_event(case_id, path, *, clock) -> EngineEvent`.

**업데이트 (최종 리뷰 I1, "Close every path the same way"):** `select` 노드 자체는
부분상태에 `round`를 싣지 않는다는 게 실측 사실이었다(위 62행의 "생략" 분기가
실제로는 유일한 분기) — `round_started.data`에 라운드 번호가 한 번도 실린 적이
없었고, CLI(`[라운드 N]`)는 항상 캐리포워드 값만 찍었다. 고침:
- `map_update_to_events(update, *, case_id, clock, round_hint: int | None = None)` —
  `round_hint`를 추가로 받는다. `select` 청크 처리 시 `partial`에 `"round"`가
  있으면 그걸 우선하고(현재는 없다), 없고 `round_hint`가 주어졌으면 그걸
  `data["round"]`에 싣는다. 둘 다 없으면 지금처럼 생략(캐리포워드 없음).
- `usecase._stream_and_collect`(Task 2)가 호출부다 — `select` 청크를 볼 때마다
  +1 하는 라운드 카운터를 유지하다가 매 `map_update_to_events` 호출에
  `round_hint=round_counter or None`으로 넘긴다. 노드(`nodes.py`)는 손대지
  않았다.
- 회귀 테스트: `tests/application/test_events_mapper.py::test_round_hint가_있으면_라운드_번호가_봉투에_실린다`,
  `tests/test_cli.py`의 chat 1왕복 테스트에 `assert "[라운드 1]" in out` 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/domain/test_events.py`:
```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from src.domain.events import EVENT_SCHEMA_VERSION, EngineEvent

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_봉투는_어휘_밖_이벤트를_거부한다():
    e = EngineEvent(event="round_started", case_id="c-1", at=T, data={"round": 1})
    assert e.schema_version == EVENT_SCHEMA_VERSION == 1
    with pytest.raises(ValidationError):
        EngineEvent(event="node_finished", case_id="c-1", at=T)
    with pytest.raises(ValidationError):
        EngineEvent(event="round_started", case_id="c-1", at=T, extra="x")
```

`tests/application/test_events_mapper.py`:
```python
from datetime import datetime, timezone

from src.application.events import case_status_event, map_update_to_events, report_ready_event
from src.domain.case import PlanTask

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CLOCK = lambda: T


def test_select는_라운드_시작을_execute는_태스크_종료를_낸다():
    running = PlanTask(id="t-1", goal="g", role="data_prober", status="running")
    events = map_update_to_events({"select": {"plan_tasks": [running]}}, case_id="c-1", clock=CLOCK)
    assert [e.event for e in events] == ["round_started"]
    assert events[0].data["dispatched"] == ["t-1"] and events[0].case_id == "c-1"

    done = PlanTask(id="t-1", goal="g", role="data_prober", status="ok",
                    result_evidence_ids=["ev-1"], result_summary="확인")
    events = map_update_to_events({"execute": {"plan_tasks": [done]}}, case_id="c-1", clock=CLOCK)
    assert [e.event for e in events] == ["task_finished"]
    assert events[0].data == {"task_id": "t-1", "role": "data_prober", "status": "ok",
                              "evidence_ids": ["ev-1"], "error": None}


def test_integrate의_ask만_질문_이벤트를_낸다():
    ask = map_update_to_events({"integrate": {"decision": "ask", "question": "계획 변경?"}},
                               case_id="c-1", clock=CLOCK)
    assert [e.event for e in ask] == ["question_raised"] and ask[0].data["question"] == "계획 변경?"
    assert map_update_to_events({"integrate": {"decision": "continue"}},
                                case_id="c-1", clock=CLOCK) == []


def test_노드명은_봉투_밖으로_새지_않고_미지의_노드는_무시된다():
    events = map_update_to_events({"conclude": {"verdict": None}}, case_id="c-1", clock=CLOCK)
    assert events == []
    assert map_update_to_events({"유령노드": {"x": 1}}, case_id="c-1", clock=CLOCK) == []
    assert map_update_to_events({"execute": "형태이상"}, case_id="c-1", clock=CLOCK) == []
    dumped = map_update_to_events({"select": {"plan_tasks": []}}, case_id="c-1", clock=CLOCK)
    assert all("select" not in str(e.model_dump()) for e in dumped)


def test_상태_전이와_보고서_준비_이벤트():
    s = case_status_event("c-1", "awaiting_human", clock=CLOCK, reason="질문 대기")
    assert s.event == "case_status_changed" and s.data == {"status": "awaiting_human",
                                                           "reason": "질문 대기"}
    r = report_ready_event("c-1", "output/c-1.md", clock=CLOCK)
    assert r.event == "report_ready" and r.data["path"].endswith("c-1.md")
```

- [ ] **Step 2: FAIL 확인** → **Step 3: 구현**(매퍼는 전부 try/except로 감싸 빈 리스트 폴백) → **Step 4: 전체 PASS 후 커밋**

```bash
git add src/domain/events.py src/application/events.py tests/domain/test_events.py tests/application/test_events_mapper.py
git commit -m "Define the engine event envelope and map graph updates onto it"
```

---

### Task 2: 유스케이스 스트리밍 + 워커 패스스루

**Files:**
- Modify: `src/application/usecase.py`, `src/application/worker.py`
- Test: `tests/application/test_usecase_stream.py`, `tests/application/test_worker.py`(추가)

**Interfaces:**
- `investigate_case(..., on_event: Callable[[EngineEvent], None] | None = None)` / `resume_case(..., on_event=None)`:
  - `on_event`가 None이면 지금처럼 `ainvoke`(동작 불변 — 기존 테스트 전부 유지).
  - 주어지면 `astream(..., stream_mode="updates")`로 돌며 각 덩어리를 `map_update_to_events`로 바꿔 싱크에 넘기고, 최종 상태는 `graph.aget_state(config)`의 `.values`로 얻어 **같은 dict를 반환**한다(interrupt로 멈춘 경우 `__interrupt__` 키를 그 state의 `.tasks`에서 복원 — 워커가 `"__interrupt__" in result`로 파킹을 판단하므로 계약 유지).
  - 싱크가 raise해도 조사를 죽이지 않는다(각 호출 try/except).
- `InvestigationWorker(..., on_event: Callable | None = None)` 생성자 인자 + `run_once`/`resume_once`가 유스케이스에 패스스루하고, 상태 전이마다 `case_status_event`를 직접 싱크에 낸다(investigating/awaiting_human/closed).
- `PatrolDaemon(..., on_event=None)` → 워커에 전달(Task 5에서 발송 훅으로 사용).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_usecase_stream.py`

```python
"""on_event 싱크가 그래프 updates를 봉투로 받아보는지 — 가짜 엔진으로 결정론 검증."""
from datetime import datetime, timezone

from src.application.usecase import investigate_case
from src.domain.case import Case, PlanTask
from tests.application.test_nodes_frame import _deps

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CASE = Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T)


class FakeStreamEngine:
    """astream은 updates 덩어리를, aget_state는 최종 state를 준다."""

    def __init__(self, chunks, final):
        self._chunks, self._final = chunks, final
        self.ainvoke_calls = 0

    async def astream(self, state, config=None, stream_mode=None):
        assert stream_mode == "updates"
        for chunk in self._chunks:
            yield chunk

    async def aget_state(self, config):
        class S:
            values = self._final
            tasks = ()
        return S()

    async def ainvoke(self, state, config=None):
        self.ainvoke_calls += 1
        return self._final


async def test_싱크가_있으면_스트리밍하고_최종_state를_그대로_돌려준다():
    running = PlanTask(id="t-1", goal="g", role="data_prober", status="running")
    done = running.model_copy(update={"status": "ok", "result_evidence_ids": ["ev-1"]})
    engine = FakeStreamEngine([{"select": {"plan_tasks": [running]}},
                               {"execute": {"plan_tasks": [done]}}],
                              {"verdict": None, "round": 1})
    seen = []
    result = await investigate_case(CASE, deps=_deps([]), engine=engine, on_event=seen.append)
    assert result == {"verdict": None, "round": 1} and engine.ainvoke_calls == 0
    assert [e.event for e in seen] == ["round_started", "task_finished"]


async def test_싱크가_없으면_ainvoke_경로_그대로():
    engine = FakeStreamEngine([], {"verdict": None})
    result = await investigate_case(CASE, deps=_deps([]), engine=engine)
    assert result == {"verdict": None} and engine.ainvoke_calls == 1


async def test_싱크가_터져도_조사는_계속된다():
    engine = FakeStreamEngine([{"select": {"plan_tasks": []}}], {"ok": True})
    def boom(event):
        raise RuntimeError("싱크 고장")
    assert await investigate_case(CASE, deps=_deps([]), engine=engine, on_event=boom) == {"ok": True}
```

`tests/application/test_worker.py`에 추가:
```python
async def test_워커는_상태_전이_이벤트를_낸다():
    repo, store, ledger = InMemoryCaseRepository(), InMemoryCaseStore(), InMemoryLedger()
    _open_case(repo, store)
    deps = make_e2e_deps(store, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON])
    seen = []
    worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                                 deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                                 clock=lambda: T, owner="w-1", max_concurrent=1, lease_ttl_s=60,
                                 ledger=ledger, knowledge_digests_for_site=lambda g, f: {},
                                 on_event=seen.append)
    assert await worker.run_once("c-1") == "closed"
    statuses = [e.data["status"] for e in seen if e.event == "case_status_changed"]
    assert statuses[0] == "investigating" and statuses[-1] == "closed"
```

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/application/usecase.py src/application/worker.py tests/application/test_usecase_stream.py tests/application/test_worker.py
git commit -m "Stream engine updates as events without changing the ainvoke contract"
```

---

### Task 3: 보고서 렌더러

**Files:**
- Modify: `src/config/schema_app.py`
- Create: `src/presentation/__init__.py`, `src/presentation/report.py`, `tests/presentation/__init__.py`
- Test: `tests/presentation/test_report.py`

**Interfaces:**
- `ReportConfig(StrictModel)`: `output_dir: str = "output"`, `mail: MailConfig = MailConfig()`. `MailConfig(StrictModel)`: `enabled: bool = False`, `host: str = ""`, `port: int = 25`, `sender: str = ""`, `recipients: list[str] = []`, `username: str | None = None`, `password: SecretStr | None = None`, `use_tls: bool = False`. `AppConfig.report: ReportConfig = ReportConfig()`.
- `render_report(record: CaseRecord, *, verdict: Verdict | None, evidence: list[EvidenceRecord], case_file: dict | None, clock) -> str` — 스펙 §5.1 5절 md. 순수 함수, raise 금지:
  - §1 요약: 케이스 id·스코프(gbm/fct)·origin·증상·T0·판정 한 줄(`verdict.verdict_type` + narrative 첫 줄)·신뢰도·**태스크 에러율**(case_file의 plan_tasks에서 `error/전체`).
  - §2 판정: `root_cause`(컴포넌트 + 증거 id) → `contributing[]`(관계 서술 포함) → caveats. verdict None이면 "판정 없음(종결 사유: …)".
  - §3 조치 권고: `recommendations` 번호 목록(빈 목록이면 "없음").
  - §4 증거: id·출처·as_of·`complete`(불완전이면 ⚠ 표시)·effective_as_of·요지(body_digest 앞 12자).
  - §5 조사 경위: 라운드 수, 태스크별 status 표(미조사=pending/cancelled 명시), 기각 가설(refuted) + 이유(refuting 증거 id), `verify_problems`, qa_log 항목 요약. **각 하위 섹션 빈 값은 "없음"**.
- `write_report(text: str, *, output_dir: str, case_id: str) -> str` — `{output_dir}/{case_id}.md`에 UTF-8로 쓰고 경로 반환. 디렉터리 없으면 생성. 실패 시 raise하지 않고 `""` 반환(호출자가 보고).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/presentation/test_report.py`

```python
from datetime import datetime, timezone

from src.domain.case import CauseLink, Verdict
from src.domain.cases import CaseRecord
from src.domain.store import EvidenceRecord
from src.presentation.report import render_report, write_report

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
RECORD = CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="OEE 512%",
                    t0=T, target_locator="rest:/oee", created_at=T, updated_at=T,
                    status="closed", closed_reason="조사 완료")
VERDICT = Verdict(verdict_type="stale_data", confidence="high",
                  narrative="plan-sync가 키를 못 썼다.\n분모가 옛 값으로 남았다.",
                  root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-2"]),
                  contributing=[CauseLink(component="twin-aggregator", evidence_ids=["ev-3"],
                                          relation="키 부재 시 옛 값 폴백")],
                  recommendations=["plan:7:today 재생성", "폴백 로직 개선"],
                  caveats=["배포 버전 미검증"])
EVIDENCE = [EvidenceRecord(id="ev-1", source="rest:/oee", body_digest="a" * 64, as_of=T),
            EvidenceRecord(id="ev-2", source="redis:plan:7", body_digest="b" * 64,
                           as_of=T, complete=False)]
CASE_FILE = {
    "plan_tasks": [
        {"id": "t-1", "goal": "mongo 조회", "role": "data_prober", "status": "ok",
         "result_evidence_ids": ["ev-1"], "result_summary": "확인", "error": None,
         "input_evidence_ids": [], "priority": 10},
        {"id": "t-2", "goal": "재계산", "role": "recompute_verifier", "status": "error",
         "result_evidence_ids": [], "result_summary": None, "error": "타임아웃",
         "input_evidence_ids": [], "priority": 20},
        {"id": "t-3", "goal": "코드 추적", "role": "code_tracer", "status": "pending",
         "result_evidence_ids": [], "result_summary": None, "error": None,
         "input_evidence_ids": [], "priority": 30}],
    "hypotheses": [{"id": "h-1", "statement": "서빙 이상", "status": "refuted",
                    "supporting_ids": [], "refuting_ids": ["ev-1"]},
                   {"id": "h-2", "statement": "계산 이상", "status": "supported",
                    "supporting_ids": ["ev-2"], "refuting_ids": []}],
    "round": 2, "qa_log": [{"kind": "round_cap"}], "verify_problems": []}


def test_보고서는_5절을_모두_담고_에러율과_불완전_증거를_드러낸다():
    text = render_report(RECORD, verdict=VERDICT, evidence=EVIDENCE,
                         case_file=CASE_FILE, clock=lambda: T)
    for heading in ("## 1. 요약", "## 2. 판정", "## 3. 조치 권고", "## 4. 증거", "## 5. 조사 경위"):
        assert heading in text
    assert "mx/gumi" in text and "OEE 512%" in text and "stale_data" in text
    assert "1/3" in text                                  # 태스크 에러율
    assert "plan-sync" in text and "ev-2" in text
    assert "twin-aggregator" in text and "키 부재 시 옛 값 폴백" in text
    assert "배포 버전 미검증" in text
    assert "⚠" in text                                     # 불완전 증거 표시
    assert "t-3" in text and "pending" in text              # 미조사 명시
    assert "h-1" in text and "서빙 이상" in text            # 기각 가설
    assert "라운드" in text and "2" in text


def test_판정도_케이스파일도_없으면_없음을_명시한다():
    bare = RECORD.model_copy(update={"closed_reason": "awaiting_human 타임아웃 — 미해결 종결"})
    text = render_report(bare, verdict=None, evidence=[], case_file=None, clock=lambda: T)
    assert "판정 없음" in text and "타임아웃" in text
    assert text.count("없음") >= 3                          # 권고·증거·경위 빈 섹션
    assert "## 5. 조사 경위" in text


def test_파일로_먼저_쓴다(tmp_path):
    path = write_report("본문", output_dir=str(tmp_path / "out"), case_id="c-1")
    assert path.endswith("c-1.md")
    from pathlib import Path
    assert Path(path).read_text(encoding="utf-8") == "본문"
    assert write_report("x", output_dir="/proc/불가/경로", case_id="c-1") == ""   # 실패는 빈 문자열
```

- [ ] **Step 2~4**: FAIL → 구현(`string.Template` 또는 f-string 조립 — 어느 쪽이든 순수 함수) → 전체 PASS → 커밋

```bash
git add src/config/schema_app.py src/presentation tests/presentation
git commit -m "Render the five-section report from the persisted case file"
```

---

### Task 4: 메일 발송 + 발송 레저 2상 (F6)

**Files:**
- Modify: `requirements.txt`, `src/patrol/ledger.py`
- Create: `src/presentation/mail.py`
- Test: `tests/presentation/test_mail.py`

**Interfaces:**
- `requirements.txt`: `aiosmtplib>=3.0,<5` (주석: report.mail.enabled일 때만 실제 로드).
- `LedgerPort` 추가: `record_send(send_id: str, *, kind: str, target: str, at: datetime) -> bool`(이미 있으면 False — 중복 억제), `mark_sent(send_id, at) -> None`, `pending_sends(limit: int = 50) -> list[dict]`(`{send_id, kind, target, at}`), `prune_sends_before(before) -> int`. InMemory·Mongo 양쪽.
- `MailSenderPort(ABC)`: `async send(subject: str, body: str, *, recipients: list[str]) -> None`. `NullSender`(로그만), `SmtpSender(cfg: MailConfig)`(지연 import aiosmtplib, `password.get_secret_value()`만 사용).
- `async send_report(case_id, subject, body, *, sender, ledger, cfg: MailConfig, clock) -> str` — 반환 `"sent"|"skipped"|"duplicate"|"failed"`:
  - `cfg.enabled`가 False → `"skipped"`(레저 기록 없음).
  - `send_id = f"report:{case_id}"`; `record_send`가 False면 `"duplicate"`.
  - `sender.send(...)` 성공 → `mark_sent` → `"sent"`. 예외 → `"failed"`(pending으로 남아 재시도 대상, raise 금지).
- `async retry_pending(*, sender, ledger, cfg, clock, render: Callable[[dict], tuple[str,str]]) -> int` — pending 각각 재발송 시도, 성공 수 반환. 데몬 스윕이 호출.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/presentation/test_mail.py`

```python
from datetime import datetime, timezone

from src.config.schema_app import MailConfig
from src.patrol.ledger import InMemoryLedger
from src.presentation.mail import NullSender, retry_pending, send_report

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
CFG = MailConfig(enabled=True, host="smtp", sender="a@x", recipients=["b@y"])


class RecordingSender(NullSender):
    def __init__(self, fail_times=0):
        self.sent, self._fail = [], fail_times

    async def send(self, subject, body, *, recipients):
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("SMTP 거부")
        self.sent.append((subject, recipients))


async def test_기록_먼저_발송_그다음_sent():
    ledger, sender = InMemoryLedger(), RecordingSender()
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T) == "sent"
    assert sender.sent == [("제목", ["b@y"])] and ledger.pending_sends() == []


async def test_발송_실패는_pending으로_남고_재시도가_비운다():
    ledger, sender = InMemoryLedger(), RecordingSender(fail_times=1)
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T) == "failed"
    assert [p["send_id"] for p in ledger.pending_sends()] == ["report:c-1"]
    done = await retry_pending(sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T,
                               render=lambda rec: ("제목", "본문"))
    assert done == 1 and ledger.pending_sends() == []


async def test_중복_발송은_억제되고_비활성은_건너뛴다():
    ledger, sender = InMemoryLedger(), RecordingSender()
    await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger, cfg=CFG, clock=lambda: T)
    assert await send_report("c-1", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=CFG, clock=lambda: T) == "duplicate"
    assert len(sender.sent) == 1
    off = MailConfig()
    assert await send_report("c-2", "제목", "본문", sender=sender, ledger=ledger,
                             cfg=off, clock=lambda: T) == "skipped"
```

- [ ] **Step 2~4**: FAIL → 구현(+ Mongo 레저 발송 컬렉션·인덱스) → 전체 PASS → 커밋

```bash
git add requirements.txt src/patrol/ledger.py src/infrastructure/mongo_store.py src/presentation/mail.py tests/presentation/test_mail.py
git commit -m "Send reports once: record pending, deliver, then mark sent"
```

---

### Task 5: 데몬 배선 — 종결 시 보고서·발송, 재시도 스윕

**Files:**
- Modify: `src/patrol/daemon.py`
- Test: `tests/patrol/test_daemon.py`(추가)

**Interfaces:**
- `PatrolDaemon(..., report_cfg: ReportConfig, mail_sender=None)`:
  - **`InvestigationWorker(..., on_closed: Callable[[str], Awaitable] | None = None)`** 훅을 새로 둔다 — 워커가 케이스를 닫은 직후(`_finish`의 close_case 뒤) `await on_closed(case_id)`를 부른다. `on_event` 싱크는 동기라 완료를 보장할 수 없어(fire-and-forget) 보고서 발행에는 쓰지 않는다(4a on_missed에서 겪은 것과 같은 이유). 훅이 raise해도 종결 결과는 그대로다(try/except).
  - `async _publish_report(case_id)`: `repo.get` + `store.get_verdict/list_evidence/get_case_file` → `render_report` → `write_report`(파일 먼저) → `report_ready_event` 싱크 → `send_report`(메일 config에 따라). 전부 try/except.
  - `sweep_job`에 `retry_pending` 추가.
- `assemble_sites`는 그대로. `report_cfg`는 `app.report`.

**업데이트 (최종 리뷰 I4·M2, "Close every path the same way"):**
- `_render_case_report`가 `render_report`를 부를 때 **`evidence_summaries`**
  (증거 id → 요지 문자열, 최대 120자)를 함께 넘긴다 —
  `{r.id: repr(store.get_evidence(case_id, r.id))[:120] for r in evidence}`를
  각 증거 조회마다 try/except로 감싸 개별 실패는 건너뛴다. §4 "요지" 열이
  예전엔 `body_digest[:12]`였다(§5.1이 요구하는 요지가 아니었다) — 이제 실제
  본문 요약이 실린다. `render_report`가 `evidence_summaries=None`을 받으면
  (기본값, 기존 호출부는 그대로) 열 이름을 "본문 digest"로 정직하게 표기한다
  (`src/presentation/report.py`, Task 3 인터페이스도 이 인자가 새로 늘었다).
- `mail.retry_pending`이 서두에서 `if not cfg.enabled: return 0`으로 즉시
  빠진다(M2) — 예전엔 이걸 안 봐서 메일을 끈 뒤에도 스윕이 NullSender로
  "발송"하고 `mark_sent`를 찍어 "보낸 적 없는 발송"을 레저에 남겼다.
- 회귀 테스트: `tests/presentation/test_report.py::test_evidence_summaries가_있으면_요지_열에_실리고_없으면_digest로_정직하게_표기한다`,
  `tests/presentation/test_mail.py::test_비활성_상태의_스윕은_건드리지_않고_0을_돌려준다`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/patrol/test_daemon.py`에 추가

```python
async def test_종결되면_보고서가_파일로_먼저_쓰이고_이벤트가_난다(tmp_path):
    from src.config.schema_app import ReportConfig
    store, repo, ledger = InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    seen = []
    daemon = _daemon(store, repo, ledger, lead=[FRAME_ONE_TASK, INTEGRATE_CONCLUDE, VERDICT_JSON],
                     report_cfg=ReportConfig(output_dir=str(tmp_path / "out")),
                     on_event=seen.append)
    daemon.build()
    await daemon.run_one("mx", "gumi", "api.oee", CHECK)
    assert await daemon.worker.run_once(await daemon.queue.get()) == "closed"
    from pathlib import Path
    written = list((tmp_path / "out").glob("*.md"))
    assert len(written) == 1 and "## 2. 판정" in written[0].read_text(encoding="utf-8")
    assert [e.event for e in seen if e.event == "report_ready"]
```

(테스트 헬퍼 `_daemon`에 `report_cfg`·`on_event` 인자를 추가하되 기존 호출은 기본값으로 동작 유지.)

- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/patrol/daemon.py src/application/worker.py tests/patrol/test_daemon.py
git commit -m "Publish a report the moment a case closes"
```

---

### Task 6: 접수 대화 + `chat` CLI + 벤치

**Files:**
- Create: `src/application/intake.py`, `tests/application/test_intake.py`, `tests/test_bench_scenarios.py`
- Modify: `src/__main__.py`, `tests/test_cli.py`
- Test: 위 + `tests/test_cli.py`(추가)

**Interfaces:**
- `async intake(symptom: str, *, deps, topology, clock, ask: Callable[[str], Awaitable[str]] | None = None) -> IntakeResult` — 접수 3단계(§2.4 interrupt 원칙의 CLI판, 그래프 밖):
  - `IntakeResult(StrictModel)`: `symptom`, `gbm`, `fct`, `target_locator: str | None`, `qa: list[dict]`.
  - LLM에 증상+사이트 목록+토폴로지 locator 목록을 주고 `{"gbm","fct","target_locator","missing": [질문…]}`을 요구(`parse_structured`). `missing`이 있고 `ask`가 주어지면 각 질문을 물어 답을 모아 한 번 더 요청(재시도 1회). 파싱 이중 실패 → 첫 사이트·locator None으로 진행하고 `qa`에 실패 기록(조사 자체는 가능해야 하므로 raise·중단 없음).
- CLI `chat --gbm G --fct F [--symptom TEXT]`:
  1. 증상 미지정이면 stdin으로 받는다. `intake`(ask는 stdin 프롬프트).
  2. `repo.new_case_id()` → `CaseRecord(origin="human", status="open", symptom, t0=now, target_locator, …)` 저장.
  3. `InvestigationWorker(..., owner=f"chat-{host}-{pid}", on_event=이벤트 출력 싱크)`로 `run_once(case_id, interaction_policy="interactive")` — 워커 `run_once(case_id, *, interaction_policy="autonomous")`로 인자 추가(패스스루).
  4. 결과가 `awaiting_human`이면 `CaseRecord.question`을 출력하고 stdin으로 답을 받아 `resume_once` 반복(최대 config `investigations.awaiting_human_timeout_h`와 무관하게 대화 루프는 사용자가 끊을 때까지; Ctrl-D면 파킹 안내 후 종료).
  5. `closed`면 보고서 경로를 출력(데몬 없이도 `_publish_report` 동등 경로를 CLI가 직접 호출).
  - 이벤트 출력: `[라운드 2] 태스크 t-1 ok (증거 ev-1)` 같은 한 줄 요약 — **봉투 필드만** 사용.
- `case show --report`: 저장된 보고서를 다시 렌더해 stdout에 출력(파일이 없으면 즉석 렌더).
- **벤치**(`tests/test_bench_scenarios.py`): 스펙 부록 A 두 시나리오를 스텁+스크립트 LLM 회귀 모드로. 채점은 `verdict.root_cause.component`와 `verdict_type`만:
  - A.1 OEE 512% → `component == "plan-sync"`, `verdict_type == "stale_data"`.
  - A.2 멈춘 라인 → `component == "equip-sync"`, `verdict_type == "stale_data"`.
  - 각 시나리오는 `run_check → admit_finding → worker.run_once → 보고서 파일` 전 구간을 돈다(4b 파킹 항목인 resume 중 F3 경로는 A.2에 park→resume을 넣어 함께 커버).
  - 모듈 상단에 `# 평가 모드(실 LLM)는 CI에서 돌리지 않는다 — 스펙 §5.5-4` 주석과 실행 방법 한 줄.

**업데이트 (최종 리뷰 C1·M4·I3·M11·M12, "Close every path the same way"):**
`case resume`(계획 4b가 만든 `_cmd_case_resume`, `src/__main__.py`)이 `InvestigationWorker(...)`에
`on_event`도 `on_closed`도 넘기지 않아, 세 종결 경로(데몬·chat·case resume) 중
이 경로만 보고서·메일·이벤트 없이 케이스를 닫고 있었다(C1, 머지 차단).
- **`_build_publisher(app, sites, store, repo, ledger, checkpointer, clock) -> tuple[on_event, on_closed]`**
  (`src/__main__.py`, M4) — `_run_chat`과 `_cmd_case_resume`이 각자 "발행용
  PatrolDaemon 셸(build()/run()은 부르지 않는다) + `_make_event_printer()`"를
  따로 조립하던 걸 한 곳으로 모았다. 둘 다 이 헬퍼가 돌려준 `(on_event,
  on_closed)`를 `InvestigationWorker(...)`에 그대로 넘긴다 — `on_closed`는
  `daemon._publish_report`다.
  `deps_for_site`/`digests_for_site`는 각 호출부가 이미 갖고 있던 `by_key` 기반
  클로저를 그대로 쓴다(이 헬퍼가 대신하지 않는다 — `_cmd_case_resume`은 미등록
  사이트를 CLI 단에서 먼저 걸러내는 자기만의 트리아지가 있다).
- `_drive_chat`이 `repo.save(record)` 직후 `intake_result.qa`가 비어 있지 않으면
  `store.put_evidence(case_id, "human:intake", {"qa": intake_result.qa}, as_of=now)`로
  박제한다(I3) — 워커의 `human:answer`(`worker.py`)와 같은 형태라
  `evidence_refs_for_case`가 그래프 초기 증거로 실어 나른다. 예전엔 접수
  문답이 모아지기만 하고 버려져(데이터 손실) 엔진이 전혀 몰랐다.
- 벤치(M11): `tests/test_bench_scenarios.py`가 `render_report`/`write_report`를
  직접 다시 부르던 `_publish` 헬퍼를 걷어내고, `_publish_daemon(...)`이 조립한
  최소 `PatrolDaemon`의 `_publish_report`를 `on_closed`로 워커에 붙인다 — 벤치도
  실제 발행 배선을 탄다.
- 벤치(M12): A.2가 `resume_case`를 항상 강제 실패시켜 정상 park→answer→resume을
  한 번도 돌지 않던 문제를 고쳐, 정상 재개 1개(`test_A2_멈춘_라인은_정상_재개로도_...`)
  + F3 강제 1개(`test_A2_멈춘_라인은_park_resume의_F3_경로를_거쳐_...`)로 나눴다.
  A.1·A.2(양쪽 다) 모두 `verdict.confidence == "high"` 단언을 추가했다(verify
  가드레일을 명시 술어로 못박는다).
- 회귀 테스트: `tests/test_cli.py::test_case_resume도_보고서를_남기고_이벤트를_찍는다`,
  `tests/test_cli.py::test_접수_문답은_human_intake_증거로_박제된다`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/application/test_intake.py`, `tests/test_bench_scenarios.py`, `tests/test_cli.py` 추가분(chat은 stdin monkeypatch로 1왕복만 검증)
- [ ] **Step 2~4**: FAIL → 구현 → 전체 PASS → 커밋

```bash
git add src/application/intake.py src/__main__.py tests/application/test_intake.py tests/test_bench_scenarios.py tests/test_cli.py
git commit -m "Take a symptom by conversation and run the benchmark scenarios end to end"
```

---

## 완료 기준 (계획 5)

- `.venv/bin/pytest` 전체 통과.
- 부록 A 두 시나리오가 **회귀 모드에서 구조화 필드로 채점**되며 통과하고, 각각 보고서 파일이 5절을 갖춘 채 `output/`에 남는다.
- `python -m src chat --gbm mx --fct gumi --symptom "OEE가 이상하다"`가 스텁 config에서 접수→조사→보고서까지 완주(컨트롤러 수동 검증).
- 이벤트 봉투 5종 밖의 값이 CLI 출력에 나타나지 않는다(그래프 내부 노드명 비노출).

## v1 이후

웹 UI(같은 봉투 구독), bootstrap 워크플로우(§3.5), LogReader/MetricsReader, 개발 시스템(코드 수정·리뷰·정합성 체크).
