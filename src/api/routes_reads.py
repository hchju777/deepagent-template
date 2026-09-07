"""읽기 엔드포인트 — 목록·상세·이벤트·보고서·점검 (스펙 §4.4).

```
GET /cases?gbm=&fct=&status=     sites_for로 좁힌다. 스코프 없는 전체 조회는 400
GET /cases/{id}                  상태 · 질문 · 판정 · 후보(candidates) · 단계 체크리스트 · Timeline (CaseDetail)
GET /cases/{id}/events?since=N   seq 오름차순 JSON — Accept: text/event-stream이면 SSE
GET /cases/{id}/report?format=   저장된 파일, 없으면 즉석 렌더(case show와 같다)
GET /checks?gbm=&fct=            레저 read
```

**읽기 필터가 계획 12 인계 ②다.** `sites_for`에 프로덕션 소비자가 0이었다 — 접수만
막고 읽기를 안 막으면 인증이 반쪽이다. 목록과 점검 이력은 `sites_for`로, 상세류는
`visible_record`(존재 여부 숨김)로 좁힌다.

**SSE는 저장된 로그의 폴링이다.** 프로세스 내 pub/sub을 만들지 않는다 — 이벤트는
워커(다른 프로세스)에서 생기므로 어차피 저장소를 거친다(스펙 §6 WebSocket 기각 근거).
"""
import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, StreamingResponse

from src.api.app import current_subject, hidden, runtime_of, visible_record
from src.api.models import CaseDetail, candidates_of
from src.application.labels import label_texts
from src.application.events import collect_events
from src.domain.report_model import build_report_model
from src.presentation.report import render_md
from src.presentation.report_html import render_html

router = APIRouter()

_SSE_POLL_S = 0.2       # 저장소 폴링 간격 — 이벤트는 다른 프로세스가 쓴다


def _scope_or_400(rt, subject, gbm: str | None, fct: str | None) -> tuple[str, str]:
    """요청 스코프를 검증하고 주체의 허용 범위로 좁힌다.

    스코프 없는 전체 조회는 400이다(스펙 §4.4). 허용 밖 스코프는 **빈 결과**가 아니라
    404다 — 목록은 빈 배열로 숨기지만(`test_목록은_주체의_사이트로`), 점검 이력처럼
    "그 사이트가 있는가"가 응답 자체인 것은 없는 사이트와 같은 답을 낸다.
    """
    if not gbm or not fct:
        raise HTTPException(status_code=400,
                            detail={"error": "gbm과 fct가 둘 다 필요하다 — 전체 조회는 없다"})
    allowed = rt.app.access.sites_for(subject, known=[(s.gbm, s.fct) for s in rt.sites])
    if allowed is not None and (gbm, fct) not in allowed:
        raise hidden()
    return gbm, fct


def _summary(record) -> dict:
    return {"case_id": record.id, "gbm": record.gbm, "fct": record.fct,
            "status": record.status, "concern": record.concern, "origin": record.origin,
            "symptom": record.symptom, "t0": record.t0.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "verdict_summary": record.verdict_summary}


@router.get("/cases")
def list_cases(request: Request, gbm: str | None = None, fct: str | None = None,
               status: str | None = None, subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    if not gbm or not fct:
        raise HTTPException(status_code=400,
                            detail={"error": "gbm과 fct가 둘 다 필요하다 — 전체 조회는 없다"})
    # 목록은 허용 밖이면 **빈 배열**이다 — 404를 내면 "그 사이트에 케이스가 있다"가
    # 새고, 빈 배열은 "없다"와 "볼 수 없다"를 같게 보인다.
    allowed = rt.app.access.sites_for(subject, known=[(s.gbm, s.fct) for s in rt.sites])
    if allowed is not None and (gbm, fct) not in allowed:
        return {"cases": []}
    statuses = [status] if status else ["open", "investigating", "awaiting_human", "closed"]
    records = [r for s in statuses for r in rt.repo.list_by_status(s)
               if r.gbm == gbm and r.fct == fct]
    return {"cases": [_summary(r) for r in sorted(records, key=lambda r: r.id)]}


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: str, request: Request, subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    record = visible_record(rt, subject, case_id)
    verdict = rt.store.get_verdict(case_id)
    log = collect_events(rt.events, case_id)
    model = build_report_model(record, verdict=verdict, evidence=rt.store.list_evidence(case_id),
                               case_file=rt.store.get_case_file(case_id), clock=rt.clock,
                               events=log.events, timeline_error=log.error)
    return {**_summary(record),
            "question": record.question, "question_kind": record.question_kind,
            "requested_by": record.requested_by, "intake_done": record.intake_done,
            "target_locator": record.target_locator,
            "stages": [s.model_dump() for s in model.stages],
            "verdict": verdict.model_dump(mode="json") if verdict else None,
            "candidates": candidates_of(verdict),
            "timeline": [e.model_dump(mode="json") for e in model.timeline],
            "timeline_source": model.timeline_source,
            "timeline_error": model.timeline_error,
            "task_error_rate": model.task_error_rate,
            "knowledge_digests": model.knowledge_digests}


def _sse(rt, case_id: str, after: int):
    """저장된 로그를 폴링해 흘린다. 케이스가 닫히고 남은 이벤트가 없으면 끝낸다."""
    async def stream():
        cursor = after
        while True:
            # 저장소 호출은 sync(pymongo)다 — 이벤트 루프에서 직접 부르면 시청자 셋이
            # 무관한 GET을 50배 느리게 만든다(리뷰 S8 실측). 스레드풀로 뺀다.
            batch = await run_in_threadpool(rt.events.since, case_id, cursor)
            for event in batch:
                cursor = event.seq
                payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                yield f"id: {event.seq}\nevent: {event.event}\ndata: {payload}\n\n"
            if not batch:
                try:
                    if (await run_in_threadpool(rt.repo.get, case_id)).status == "closed":
                        return
                except KeyError:
                    return
                await asyncio.sleep(_SSE_POLL_S)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache"})


@router.get("/cases/{case_id}/events")
def get_events(case_id: str, request: Request,
               since: int = Query(0, ge=0, le=2**53),   # bson int64 안·JS 안전 정수 안
               subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    visible_record(rt, subject, case_id)
    if "text/event-stream" in request.headers.get("accept", ""):
        return _sse(rt, case_id, since)
    events = rt.events.since(case_id, since)
    return {"events": [e.model_dump(mode="json") for e in events]}


@router.get("/cases/{case_id}/report")
def get_report(case_id: str, request: Request,
               format: Literal["html", "md"] | None = None,
               subject: str | None = Depends(current_subject)):
    """저장된 파일을 먼저, 없으면 즉석 렌더 — `case show --report`와 같은 규칙."""
    rt = runtime_of(request)
    record = visible_record(rt, subject, case_id)
    fmt = format or rt.app.report.format
    media = "text/html" if fmt == "html" else "text/markdown"
    path = Path(rt.app.report.output_dir) / f"{case_id}.{fmt}"
    if path.exists():
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type=media)
    log = collect_events(rt.events, case_id)
    model = build_report_model(record, verdict=rt.store.get_verdict(case_id),
                               evidence=rt.store.list_evidence(case_id),
                               case_file=rt.store.get_case_file(case_id), clock=rt.clock,
                               events=log.events, timeline_error=log.error,
                               labels=label_texts(rt.labels, case_id))
    return PlainTextResponse(render_html(model) if fmt == "html" else render_md(model),
                             media_type=media)


@router.get("/digests/{scenario}")
def get_digests(scenario: str, request: Request, limit: int = Query(20, ge=1, le=100),
                subject: str | None = Depends(current_subject)):
    """집계 실행 기록(계획 16). 사이트를 가로지르므로 사이트 단위 필터가 아니라 **시나리오
    단위**로 본다 — 주체가 시나리오 scope의 사이트를 하나라도 못 보면 아무것도 안 준다."""
    rt = runtime_of(request)
    allowed = rt.app.access.sites_for(subject, known=[(s.gbm, s.fct) for s in rt.sites])
    runs = rt.digests.list(scenario, limit=limit)
    if allowed is not None and runs:
        touched = {(c.gbm, c.fct) for run in runs for c in run.coverage}
        if not touched <= set(allowed):
            raise hidden()
    return {"runs": [r.model_dump(mode="json") for r in runs]}


@router.get("/checks")
def list_checks(request: Request, gbm: str | None = None, fct: str | None = None,
                limit: int = Query(20, ge=1, le=200),
                subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    gbm, fct = _scope_or_400(rt, subject, gbm, fct)
    site = rt.by_key.get((gbm, fct))
    if site is None:
        raise hidden()
    return {"gbm": gbm, "fct": fct, "checks": [
        {"check": name,
         "runs": [o.model_dump(mode="json") for o in rt.ledger.runs(gbm, fct, name, limit=limit)]}
        for name in site.check_names]}
