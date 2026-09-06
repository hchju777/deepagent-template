"""명령 채널 — 사람의 답을 레코드에 싣는다. 실행은 워커의 몫이다 (스펙 §3.6).

v1 인계 노트가 "데몬이 파킹 케이스를 자동으로 재개하려면 사람의 답을 실어 나를
프로세스 밖 명령 채널이 필요한데 그것이 아직 없다"고 적어 뒀다. 이것이 그 채널이다.
새 큐나 컬렉션이 아니라 레코드 위의 필드 둘(`pending_answer`, `answer_key`)이다 —
`patrol`이 단일 인스턴스라 소비 경합이 없고, 저장소가 이미 내구성을 준다.

`api`는 이 함수만 부른다. `run_once`/`resume_once`를 부르지 않는다 — 부르는 순간
`api` 풀 전체가 실행자가 되고 lease가 그 사이를 중재해야 한다(스펙 §3.1).
"""
from datetime import datetime
from typing import Callable, Literal

SubmitResult = Literal["accepted", "duplicate", "pending", "busy", "not_waiting", "not_found"]


def submit_answer(case_id: str, answer: str, *, key: str, repo,
                  clock: Callable[[], datetime]) -> SubmitResult:
    """답을 싣는다. 절대 raise하지 않는다.

    판정과 쓰기를 저장소의 한 동작(`attach_answer`)에 맡긴다 — 여기서 get→save로
    하면 그 사이 워커가 claim한 lease·상태·스레드를 되돌려 조사가 죽는다(리뷰 S7).

    - `duplicate`: 같은 키가 이미 왔다(소비됐든 아니든). 클라이언트 재시도가 답을
      두 번 넣어 F3 복구를 두 번 태우는 것을 막는다. `answer_key`를 소비 뒤에도
      지우지 않는 이유다.
    - `pending`: 아직 소비되지 않은 다른 답이 있다. 덮어쓰지 않는다.
    - `busy`: 실행자(워커·`case resume`)가 lease를 쥐고 있다 — 잠시 뒤 다시 보내라.
      그 동안 실린 답은 실행자의 통째 save에 지워지거나 다음 질문에 소비된다.
    - `not_waiting`: 조사 질문에 파킹돼 있지 않거나, 이번 질문은 이미 답했다(워커가
      가져간 뒤 새 파킹 전) — 옛 질문의 답이 새 질문에 붙지 않게. 접수 질문은
      `/intake-answers`로만 답한다(둘을 섞으면 워커가 접수 답을 조사 답으로 재소비한다).
    """
    try:
        return repo.attach_answer(case_id, answer=answer, key=key, now=clock())
    except Exception:                                              # noqa: BLE001 — 무raise 계약
        return "not_found"


# ── 케이스 제출: 스코프 → 접근 → 개설 → 첫 접수 턴 ─────────────────────────────
# CLI `chat`과 `POST /cases`가 **같은 함수**를 부른다. 순서를 각자 베끼면 언젠가
# 하나가 접근 검사를 빠뜨린다 — 케이스 종결 세 경로가 발행 배선에서 겪은 것(규율 8).
from src.application.intake import IntakeTurn, intake_turn          # noqa: E402
from src.application.open_case import open_case                    # noqa: E402
from src.application.scope import ScopeResult, resolve_scope        # noqa: E402
from src.config.schema_app import StrictModel                       # noqa: E402


class SubmitCaseResult(StrictModel):
    status: Literal["opened", "unresolved", "forbidden"]
    case_id: str | None = None
    scope: ScopeResult
    turn: IntakeTurn | None = None      # opened일 때 첫 접수 턴의 결과


async def submit_case(symptom: str, *, gbm: str | None, fct: str | None, concern: str,
                      subject: str | None, sites: dict, access, repo, store,
                      clock: Callable[[], datetime], on_event: Callable,
                      max_intake_turns: int) -> SubmitCaseResult:
    """케이스 하나를 제출한다. 절대 raise하지 않는다.

    `sites`는 `(gbm, fct) → 사이트`이고 사이트는 `.topology`와 `.lead_llm`만 있으면
    된다 — `api`의 `ApiSite`도 데몬의 `SiteRuntime`도 그 둘을 갖는다(덕 타이핑).
    `api`가 어댑터 달린 `SiteRuntime`을 요구받으면 대상 시스템에 붙는 프로세스가 된다.

    첫 접수 턴까지 여기서 도는 이유: 되묻는 질문이 응답에 바로 실려야 클라이언트가
    폴링하지 않는다. 접수는 조사가 아니다(LLM 호출 하나, 대상 접근 없음).
    """
    keys = sorted(sites)
    first = sites[keys[0]] if keys else None
    scope = await resolve_scope(symptom, sites=keys, deps=first, gbm=gbm, fct=fct)
    if scope.status != "resolved":
        return SubmitCaseResult(status="unresolved", scope=scope)
    # 접수 경계 한 곳에서만 판정한다(스펙 §3.5). 조사를 시작한 뒤 막으면 이미 늦다.
    if not access.can_access(subject, scope.gbm, scope.fct):
        return SubmitCaseResult(status="forbidden", scope=scope)
    site = sites[(scope.gbm, scope.fct)]
    record = open_case(repo=repo, store=store, symptom=symptom, gbm=scope.gbm, fct=scope.fct,
                       concern=concern, requested_by=subject, clock=clock, on_event=on_event)
    turn = await intake_turn(record.id, repo=repo, store=store, deps=site,
                             topology=site.topology, clock=clock, max_turns=max_intake_turns,
                             on_event=on_event)
    return SubmitCaseResult(status="opened", case_id=record.id, scope=scope, turn=turn)
