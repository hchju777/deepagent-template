"""FastAPI 앱 팩토리 — 런타임을 주입받아 앱을 만든다. 전역 상태를 두지 않는다.

여기 있는 것은 **전송의 일**뿐이다: 헤더에서 주체를 얻고, 도메인 결과를 HTTP
상태로 옮기고, 라우터를 붙인다. 케이스를 열고 답을 싣는 판단은 전부
`src/application/`에 있고 CLI와 공유한다.
"""
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from src.api.assembly import ApiRuntime
from src.api.auth import AuthError, subject_of


def runtime_of(request: Request) -> ApiRuntime:
    return request.app.state.runtime


def current_subject(request: Request) -> str | None:
    """`Authorization`에서 주체를 얻는다. 틀린 토큰은 401, 없으면 익명(None).

    익명이 허용되는지는 여기서 정하지 않는다 — `access.allow`가 비어 있을 때만
    통과하고 그 판정은 접수 경계(`submit_case`)와 읽기 필터(`sites_for`)가 한다.
    """
    rt = runtime_of(request)
    try:
        return subject_of(request.headers.get("authorization"), rt.app.access.subjects)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def hidden() -> HTTPException:
    """미인가 읽기·쓰기는 "없는 케이스"와 **같은 응답**이다(계획 12 인계 ③).

    404와 403을 구별하면 미인가 주체가 케이스 존재 여부를 알 수 있다. 접근 검사는
    `repo.get` 뒤에 올 수밖에 없지만(gbm/fct가 필요하다) 응답은 구별하지 않는다.
    403은 스코프가 요청에 이미 드러난 `POST /cases`에서만 쓴다.
    """
    return HTTPException(status_code=404, detail={"error": "케이스를 찾을 수 없다"})


def visible_record(rt: ApiRuntime, subject: str | None, case_id: str) -> Any:
    """주체가 볼 수 있는 케이스면 레코드를, 아니면 404(존재 여부 숨김)."""
    try:
        record = rt.repo.get(case_id)
    except KeyError:
        raise hidden()
    if not rt.app.access.can_access(subject, record.gbm, record.fct):
        raise hidden()
    return record


def create_app(runtime: ApiRuntime) -> FastAPI:
    from src.api.routes_cases import router as cases_router

    app = FastAPI(title="deepagent api", docs_url=None, redoc_url=None)
    app.state.runtime = runtime
    app.include_router(cases_router, dependencies=[Depends(current_subject)])
    return app
