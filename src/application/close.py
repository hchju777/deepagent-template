"""케이스 종결 — 스펙 §1.1, 계획 4b.

종결(transition → "closed")과 스레드 폐기를 "한 동작"으로 묶는다: 정상
완료처럼 스레드를 Time Travel·디버깅용으로 남겨야 하는 종결도 있고
(discard_threads=False, TTL 스윕이 나중에 지운다), 타임아웃·F3 재시작
실패처럼 즉시 폐기해야 하는 종결도 있다(discard_threads=True). 이 둘을
호출부가 매번 따로 조합하지 않도록 close_case 하나로 노출한다.

스레드 폐기 개별 실패는 삼키지 않는다 — 그렇다고 종결 자체를 막지도
않는다: checkpointer 쪽 문제로 사람이 이미 끝낸 케이스가 "닫히지 않은
채" 큐에 남는 것이 더 나쁘다. 대신 실패 내용을 closed_reason에 덧붙여
재저장해 나중에 추적할 수 있게 한다.
"""
from typing import Protocol

from src.application.lifecycle import Clock, is_timed_out, transition
from src.domain.cases import CaseRecord, CaseRepositoryPort


class _CheckpointerPort(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


async def close_case(case_id: str, *, repo: CaseRepositoryPort,
                     checkpointer: _CheckpointerPort | None, clock: Clock,
                     reason: str, discard_threads: bool) -> CaseRecord:
    """케이스를 종결하고 필요하면 그 스레드들을 폐기한다(한 동작).

    discard_threads=False면 스레드는 손대지 않는다(보존 스윕이 나중에
    checkpoint_ttl_d로 지운다). True면 record.thread_ids를 각각
    checkpointer.adelete_thread로 폐기한다 — checkpointer가 None이면
    폐기할 대상이 없으므로 건너뛴다. 개별 스레드 폐기 실패는 삼키지
    않고 사유에 덧붙여 재저장하되, 종결 자체는 막지 않는다.
    """
    record = transition(repo.get(case_id), "closed", clock=clock, reason=reason)
    repo.save(record)
    if discard_threads and checkpointer is not None:
        failures: list[str] = []
        for thread_id in record.thread_ids:
            try:
                await checkpointer.adelete_thread(thread_id)
            except Exception as exc:                                   # noqa: BLE001
                failures.append(f"{thread_id}: {exc}")
        if failures:
            record = record.model_copy(update={
                "closed_reason": f"{record.closed_reason} (스레드 폐기 실패: {'; '.join(failures)})",
            })
            repo.save(record)
    return record


async def sweep_timeouts(*, repo: CaseRepositoryPort, checkpointer: _CheckpointerPort | None,
                         clock: Clock, timeout_h: int) -> list[str]:
    """awaiting_human 상태로 timeout_h를 넘긴 케이스를 모두 닫는다.

    타임아웃 종결은 사람이 다시 돌아올 가망이 낮다고 보고 스레드를 즉시
    폐기한다(discard_threads=True). 닫은 케이스 id 목록을 반환한다.
    """
    closed_ids: list[str] = []
    for record in repo.list_open():
        if is_timed_out(record, clock=clock, timeout_h=timeout_h):
            await close_case(record.id, repo=repo, checkpointer=checkpointer, clock=clock,
                             reason="awaiting_human 타임아웃 — 미해결 종결", discard_threads=True)
            closed_ids.append(record.id)
    return closed_ids
