"""쓰기 엔드포인트 — 접수와 답 (스펙 §4.4).

```
POST /cases                      202 {case_id, status, question?}   / 400 미확정 / 403 / 401
POST /cases/{id}/intake-answers  200 {status, question?, target_locator?} / 409 / 404
POST /cases/{id}/answers         202 {result} / 409 / 404
```

`api`는 실행자가 아니다 — `/answers`는 **기록만** 한다. 워커가 집어 간다.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.app import current_subject, hidden, runtime_of, visible_record
from src.application.intake import intake_turn
from src.application.submit import submit_answer, submit_case
from src.config.schema_app import StrictModel
from src.domain.concern import Concern

router = APIRouter()


class NewCase(StrictModel):
    symptom: str
    gbm: str | None = None
    fct: str | None = None
    concern: Concern = "system"


class IntakeAnswer(StrictModel):
    answer: str


class Answer(StrictModel):
    answer: str
    key: str


def _event_sink(rt):
    """api가 낸 이벤트도 워커와 같은 저장소로 — SSE가 읽을 로그는 하나다."""
    def sink(event):
        try:
            rt.events.append(event)
        except Exception:                                          # noqa: BLE001
            pass                    # 이벤트 저장 실패가 접수를 막아서는 안 된다(계획 6 계약)
    return sink


@router.post("/cases", status_code=202)
async def post_case(body: NewCase, request: Request,
                    subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    out = await submit_case(
        body.symptom, gbm=body.gbm, fct=body.fct, concern=body.concern, subject=subject,
        sites=rt.by_key, access=rt.app.access, repo=rt.repo, store=rt.store, clock=rt.clock,
        on_event=_event_sink(rt), max_intake_turns=rt.app.engine.max_intake_turns)
    if out.status == "unresolved":
        return JSONResponse(status_code=400, content={
            "candidates": [f"{g}/{f}" for g, f in out.scope.candidates],
            "questions": out.scope.questions, "problems": out.scope.problems})
    if out.status == "forbidden":
        # 스코프가 요청에 이미 드러나 있으므로 숨길 것이 없다 — 여기서만 403이다.
        raise HTTPException(status_code=403, detail={"error": "이 사이트에 접근할 수 없다"})
    record = rt.repo.get(out.case_id)
    # 접수 결과를 그대로 싣는다 — 포기(error)를 "정상 개설"처럼 보이면 클라이언트는
    # 왜 조사가 대상 없이 도는지 모른다(조용한 생략 금지). 예시 트리(가짜 LLM
    # 호스트)를 실제로 쳐 보니 question:null만 와서 구별이 안 됐다.
    return {"case_id": out.case_id, "status": record.status,
            "question": out.turn.question if out.turn.status == "asking" else None,
            "intake": {"status": out.turn.status, "problems": out.turn.problems}}


@router.post("/cases/{case_id}/intake-answers")
async def post_intake_answer(case_id: str, body: IntakeAnswer, request: Request,
                             subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    record = visible_record(rt, subject, case_id)
    site = rt.by_key.get((record.gbm, record.fct))
    if site is None:
        raise hidden()              # 비활성 사이트의 케이스 — 존재 여부를 숨긴다
    turn = await intake_turn(case_id, repo=rt.repo, store=rt.store, deps=site,
                             topology=site.topology, clock=rt.clock, answer=body.answer,
                             max_turns=rt.app.engine.max_intake_turns, on_event=_event_sink(rt))
    if turn.status == "not_ours":
        raise HTTPException(status_code=409, detail={"problems": turn.problems})
    return {"status": turn.status, "question": turn.question,
            "target_locator": turn.target_locator, "problems": turn.problems}


@router.post("/cases/{case_id}/answers", status_code=202)
async def post_answer(case_id: str, body: Answer, request: Request,
                      subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    visible_record(rt, subject, case_id)
    result = submit_answer(case_id, body.answer, key=body.key, repo=rt.repo, clock=rt.clock)
    status: Literal[202, 409] = 409 if result in ("not_waiting", "pending") else 202
    return JSONResponse(status_code=status, content={"result": result})
