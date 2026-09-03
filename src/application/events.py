"""LangGraph updates → 엔진 이벤트 매퍼 — 계획 5(보고·채널).

`stream_mode="updates"`는 슈퍼스텝마다 `{노드명: 부분상태}` 한 덩어리를 낸다.
이 모듈은 그 덩어리를 봉투(EngineEvent) 목록으로 바꾼다. 매핑 규칙 자체는
노드명을 알아야 하지만(어느 노드가 무엇을 냈는지), **노드명은 봉투 밖으로
나가지 않는다** — data에는 상태 필드만 싣는다.

매퍼는 스트리밍 루프 한가운데서 호출되므로 절대 raise하지 않는다: 알 수 없는
노드명·형태가 어긋난 부분상태는 조용히 빈 리스트로 폴백한다(전체를 try/except로
감싼다 — 노드 하나의 형태 이상이 같은 덩어리의 다른 이벤트까지 삼켜도, 그건
stream_mode="updates"가 한 덩어리에 노드 하나만 담는 정상 동작과 어긋나지
않는다).
"""
from src.application.lifecycle import Clock
from src.domain.cases import CaseStatus
from src.domain.events import EngineEvent


def map_update_to_events(update: dict, *, case_id: str, clock: Clock) -> list[EngineEvent]:
    """update의 {노드명: 부분상태}를 이벤트 봉투 목록으로 바꾼다.

    - select → round_started (dispatched: 이번에 running으로 굴린 태스크 id들)
    - execute → 태스크마다 task_finished
    - integrate → decision이 "ask"일 때만 question_raised
    - 그 외(frame/ask_human/conclude/verify)·미지의 노드·형태 이상 → 이벤트 없음
    """
    try:
        events: list[EngineEvent] = []
        for node, partial in update.items():
            if node == "select":
                events.extend(_select_events(partial, case_id=case_id, clock=clock))
            elif node == "execute":
                events.extend(_execute_events(partial, case_id=case_id, clock=clock))
            elif node == "integrate":
                events.extend(_integrate_events(partial, case_id=case_id, clock=clock))
            # frame/ask_human/conclude/verify/미지의 노드 → 이 매핑 규칙에서는 이벤트 없음
        return events
    except Exception:
        return []


def _select_events(partial: dict, *, case_id: str, clock: Clock) -> list[EngineEvent]:
    tasks = partial["plan_tasks"]
    data = {"dispatched": [task.id for task in tasks if task.status == "running"]}
    if "round" in partial:
        data["round"] = partial["round"]
    return [EngineEvent(event="round_started", case_id=case_id, at=clock(), data=data)]


def _execute_events(partial: dict, *, case_id: str, clock: Clock) -> list[EngineEvent]:
    tasks = partial["plan_tasks"]
    return [
        EngineEvent(event="task_finished", case_id=case_id, at=clock(), data={
            "task_id": task.id, "role": task.role, "status": task.status,
            "evidence_ids": task.result_evidence_ids, "error": task.error})
        for task in tasks]


def _integrate_events(partial: dict, *, case_id: str, clock: Clock) -> list[EngineEvent]:
    if partial.get("decision") != "ask":
        return []
    return [EngineEvent(event="question_raised", case_id=case_id, at=clock(),
                        data={"question": partial.get("question")})]


def case_status_event(case_id: str, status: CaseStatus, *, clock: Clock,
                      reason: str | None = None) -> EngineEvent:
    """워커·CLI가 케이스 상태 전이를 알릴 때 쓰는 생성자."""
    return EngineEvent(event="case_status_changed", case_id=case_id, at=clock(),
                       data={"status": status, "reason": reason})


def report_ready_event(case_id: str, path: str, *, clock: Clock) -> EngineEvent:
    """보고서 파일이 준비됐음을 알리는 생성자."""
    return EngineEvent(event="report_ready", case_id=case_id, at=clock(), data={"path": path})
