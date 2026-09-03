"""보존 스윕 — 스펙 §4.6, 계획 4b.

케이스가 닫혀도, 순찰이 매 회차 남기는 스크래치 증거·레저 실행 이력도,
스레드 체크포인트도 영원히 쌓이면 안 된다. 이 모듈은 그 넷을 한 번의
스윕으로 정리한다:

  ① 오래 닫혀 있던 케이스의 증거+판정 삭제, 그 스레드도 폐기
  ② 오래된 레저 실행 이력 삭제
  ③ 순찰 스크래치 케이스(scratch_case_id, "patrol:" 접두)의 오래된 증거만 삭제
     (스크래치 케이스 자체는 다음 회차에도 계속 쓰이므로 지우지 않는다)
  ④ 아직 열려 있어도 오래 멈춰 있는 케이스는 스레드만 폐기한다 — 케이스는
     그대로 두되(사람이 언제든 돌아올 수 있다) 스레드는 죽은 체크포인트로
     묵혀두지 않는다. thread_ids에서 제거해두면 다음 재개는 F3(스레드
     부재 시 새로 시작) 경로를 그대로 타 새 스레드가 열린다.

절대 raise하지 않는다: 항목 하나(케이스 하나·스레드 하나)의 실패가 나머지
정리를 막아서는 안 된다 — 실패는 건너뛰고 계속한다. 시계는 clock()으로만
얻는다(결정론 테스트).
"""
from datetime import datetime, timedelta
from typing import Callable, Protocol

from src.config.schema_app import RetentionConfig
from src.domain.cases import CaseRepositoryPort
from src.domain.store import CaseStorePort
from src.patrol.ledger import LedgerPort

Clock = Callable[[], datetime]


class _CheckpointerPort(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


async def sweep_retention(*, repo: CaseRepositoryPort, store: CaseStorePort,
                          ledger: LedgerPort, checkpointer: _CheckpointerPort | None,
                          clock: Clock, retention: RetentionConfig) -> dict[str, int]:
    """네 가지 보존 규칙을 한 번에 훑고 항목별 처리 건수를 돌려준다."""
    now = clock()
    counts = {"closed_cases": 0, "ledger_runs": 0, "scratch_evidence": 0, "expired_threads": 0}

    # ① 오래된 종결 케이스 — 증거+판정 삭제, 스레드 폐기
    evidence_before = now - timedelta(days=retention.closed_case_evidence_d)
    for record in repo.list_by_status("closed"):
        if record.updated_at >= evidence_before:
            continue
        try:
            store.purge_case(record.id)
        except Exception:                                              # noqa: BLE001
            continue
        counts["closed_cases"] += 1
        if checkpointer is not None:
            for thread_id in record.thread_ids:
                try:
                    await checkpointer.adelete_thread(thread_id)
                except Exception:                                       # noqa: BLE001
                    pass

    # ② 레저 실행 이력 정리
    ledger_before = now - timedelta(days=retention.ledger_d)
    try:
        counts["ledger_runs"] = ledger.prune_runs_before(ledger_before)
    except Exception:                                                  # noqa: BLE001
        pass

    # ③ 순찰 스크래치 케이스의 오래된 증거만 정리 (케이스 자체는 유지)
    for case_id in store.list_case_ids("patrol:"):
        try:
            counts["scratch_evidence"] += store.purge_evidence_before(case_id, ledger_before)
        except Exception:                                              # noqa: BLE001
            continue

    # ④ 오래 멈춘 열린 케이스의 스레드만 폐기 — 다음 재개는 F3로 새 스레드
    if checkpointer is not None:
        ttl_before = now - timedelta(days=retention.checkpoint_ttl_d)
        for record in repo.list_open():
            if record.updated_at >= ttl_before or not record.thread_ids:
                continue
            discarded: list[str] = []
            for thread_id in record.thread_ids:
                try:
                    await checkpointer.adelete_thread(thread_id)
                except Exception:                                       # noqa: BLE001
                    continue
                discarded.append(thread_id)
            if not discarded:
                continue
            try:
                remaining = [t for t in record.thread_ids if t not in discarded]
                remaining_versions = {t: v for t, v in record.thread_versions.items()
                                      if t not in discarded}
                repo.save(record.model_copy(update={
                    "thread_ids": remaining, "thread_versions": remaining_versions,
                }))
                counts["expired_threads"] += len(discarded)
            except Exception:                                          # noqa: BLE001
                continue

    return counts
