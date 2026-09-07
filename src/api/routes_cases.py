"""쓰기 엔드포인트 — 접수와 답 (스펙 §4.4).

```
POST /cases                      202 {case_id, status, question?}   / 400 미확정 / 403 / 401
POST /cases/{id}/intake-answers  200 {status, question?, target_locator?} / 409(not_ours·stale_question) / 404
POST /cases/{id}/answers         202 {result} / 409(pending·busy·not_waiting·stale_question) / 404 / 503
POST /cases/{id}/label           202 {result} / 404 — 실제 원인 되먹임(append-only)
```

`/intake-answers`의 409는 본문이 두 모양이다 — `stale_question`은 턴 모양
(`{status, question, target_locator, problems}`), `not_ours`는 `{detail: {problems}}`.
클라이언트가 한 벌로 처리할 수 없으니 상태 코드만 보고 본문 모양을 가정하지 마라.

`api`는 실행자가 아니다 — `/answers`는 **기록만** 한다. 워커가 집어 간다.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.app import current_subject, hidden, runtime_of, visible_record
from src.application.intake import intake_turn
from src.application.labels import submit_label
from src.domain.label import Agreement, Resolution
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
    # 조사 답변(`Answer`)과 대칭이다 — 읽기 표면이 접수 질문의 번호를 내주는데 쓰기
    # 표면이 거부하면 웹 UI가 접수 되묻기에 답하는 순간 그대로 경합에 노출된다.
    question_seq: int | None = None


class Answer(StrictModel):
    answer: str
    key: str
    # 클라이언트가 **본** 질문 번호(If-Match). 안 보내면 예전처럼 받는다 — 기존
    # 클라이언트를 깨지 않는다. 보내면 그 사이 질문이 바뀌었을 때 409로 거절한다.
    question_seq: int | None = None


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
                             max_turns=rt.app.engine.max_intake_turns, on_event=_event_sink(rt),
                             expect_seq=body.question_seq)
    if turn.status == "not_ours":
        raise HTTPException(status_code=409, detail={"problems": turn.problems})
    body_out = {"status": turn.status, "question": turn.question,
                "target_locator": turn.target_locator, "problems": turn.problems}
    if turn.status == "stale_question":
        # `POST /answers`의 `stale_question`과 같은 코드를 쓴다 — 두 표면이 같은 사실을
        # 다른 코드로 말하면 클라이언트가 두 벌의 처리를 짠다.
        return JSONResponse(status_code=409, content=body_out)
    return body_out


@router.post("/cases/{case_id}/answers", status_code=202)
async def post_answer(case_id: str, body: Answer, request: Request,
                      subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    visible_record(rt, subject, case_id)
    result = submit_answer(case_id, body.answer, key=body.key, repo=rt.repo, clock=rt.clock,
                           expect_seq=body.question_seq)
    status: Literal[202, 409, 503] = (503 if result == "error"
                                      else 409 if result in ("not_waiting", "pending", "busy",
                                                            "stale_question")
                                      else 202)
    return JSONResponse(status_code=status, content={"result": result})


class Label(StrictModel):
    """실제 원인 되먹임. 4분류를 Literal로 강제한다 — 자유 문자열이면 나중에 집계가
    우리 정규화기를 측정하게 된다(계획 15/P8)."""
    agreement: Agreement
    resolution: Resolution | None = None
    actual_component: str | None = None
    actual_verdict_type: str | None = None
    saw_report: bool = False
    labeled_by: str | None = None


@router.post("/cases/{case_id}/label", status_code=202)
async def post_label(case_id: str, body: Label, request: Request,
                     subject: str | None = Depends(current_subject)):
    rt = runtime_of(request)
    visible_record(rt, subject, case_id)        # 다른 쓰기와 같은 접근 검사
    result = submit_label(case_id, agreement=body.agreement, resolution=body.resolution,
                          actual_component=body.actual_component,
                          actual_verdict_type=body.actual_verdict_type,
                          saw_report=body.saw_report, labeled_by=body.labeled_by or subject,
                          repo=rt.repo, labels=rt.labels, clock=rt.clock)
    status: Literal[202, 404, 503] = (404 if result == "not_found"
                                      else 503 if result == "error" else 202)
    return JSONResponse(status_code=status, content={"result": result})
