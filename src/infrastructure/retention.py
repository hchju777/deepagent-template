"""보존 스윕 — 스펙 §4.6, 계획 4b·계획 5(F6).

케이스가 닫혀도, 순찰이 매 회차 남기는 스크래치 증거·레저 실행 이력·발송
레저(sends)도, 스레드 체크포인트도 영원히 쌓이면 안 된다. 이 모듈은 그
다섯을 한 번의 스윕으로 정리한다:

  ① 오래 닫혀 있던 케이스의 증거+판정+케이스 파일 삭제, 그 스레드도 폐기.
     처리한 레코드는 purged_at을 스탬프한다(계획 4b I7) — updated_at은 이
     스윕이 손대지 않으므로(스레드만 비운다) purged_at 없이는 다음 스윕도
     같은 조건(updated_at < evidence_before)에 다시 걸려 store.purge_case를
     반복 호출한다. purged_at is not None인 레코드는 ①에서 다시 고르지 않는다.
  ② 오래된 레저 실행 이력 삭제
  ③ 순찰 스크래치 케이스(scratch_case_id, "patrol:" 접두)의 오래된 증거만 삭제
     (스크래치 케이스 자체는 다음 회차에도 계속 쓰이므로 지우지 않는다)
  ④ 스레드만 폐기하고 케이스 자체는 건드리지 않는다 — 아직 열려 있는
     케이스(사람이 언제든 돌아올 수 있다)뿐 아니라 이미 닫힌 케이스도 대상이다
     (계획 4b I7): ①의 증거 보존기한(closed_case_evidence_d, 보통 더 김)보다
     스레드 보존기한(checkpoint_ttl_d, 보통 더 짧음)이 먼저 지나면 "증거는
     아직 있지만 체크포인트만 죽은" 구간이 생기기 때문이다. 나이 판단은
     status_since(없으면 updated_at) 기준이다(I2와 동일한 이유 — 게이트의
     finding 첨부 등 상태와 무관한 갱신에 흔들리지 않도록). thread_ids에서
     제거해두면 다음 재개는 F3(스레드 부재 시 새로 시작) 경로를 그대로 타
     새 스레드가 열린다.
  ⑤ 오래된 발송 레저(sends) 삭제(F6, 계획 5) — retention.sends_d 이전 기록을
     완료(sent)·미완료(pending) 구분 없이 정리한다. 그만큼 오래 pending으로
     남은 시도는 사실상 죽은 시도로 보고 재시도 대상에서도 함께 뺀다.

절대 raise하지 않는다: 레코드 하나(케이스 하나·스레드 하나)의 실패뿐 아니라
목록 조회 자체(list_by_status/list_case_ids/list_open)의 실패도 나머지
정리를 막아서는 안 된다 — 실패한 항목은 건너뛰고 나머지 항목은 계속
돈다(Mongo 구현이 문서 하나의 역직렬화 실패로 목록 조회 자체를 raise할
수 있다). 시계는 clock()으로만 얻는다(결정론 테스트).
"""
from datetime import datetime, timedelta
from typing import Callable, Protocol

from src.config.schema_app import RetentionConfig
from src.domain.cases import CaseRepositoryPort
from src.domain.store import CaseStorePort
from src.domain.events import EventStorePort
from src.patrol.ledger import LedgerPort

Clock = Callable[[], datetime]


class _CheckpointerPort(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


async def sweep_retention(*, repo: CaseRepositoryPort, store: CaseStorePort,
                          ledger: LedgerPort, checkpointer: _CheckpointerPort | None,
                          clock: Clock, retention: RetentionConfig,
                          events: EventStorePort | None = None) -> dict[str, int]:
    """다섯 가지 보존 규칙을 한 번에 훑고 항목별 처리 건수를 돌려준다."""
    now = clock()
    counts = {"closed_cases": 0, "ledger_runs": 0, "scratch_evidence": 0, "expired_threads": 0,
             "sends": 0, "events": 0}

    # ① 오래된 종결 케이스 — 증거+판정+케이스 파일 삭제, 스레드 폐기, purged_at 스탬프
    evidence_before = now - timedelta(days=retention.closed_case_evidence_d)
    try:
        closed_records = repo.list_by_status("closed")
    except Exception:                                                  # noqa: BLE001
        closed_records = []
    for record in closed_records:
        if record.purged_at is not None:            # 이미 처리됨 — 재선택 방지(I7)
            continue
        if record.updated_at >= evidence_before:
            continue
        try:
            store.purge_case(record.id)
        except Exception:                                              # noqa: BLE001
            continue
        counts["closed_cases"] += 1
        update: dict[str, object] = {"purged_at": now}
        if checkpointer is not None and record.thread_ids:
            for thread_id in record.thread_ids:
                try:
                    await checkpointer.adelete_thread(thread_id)
                except Exception:                                       # noqa: BLE001
                    pass
            # 폐기 성공 여부와 무관하게 비운다 — 종결 케이스는 다시 재개되지
            # 않으므로 다음 스윕이 같은 케이스를 반복 처리할 이유가 없다.
            update["thread_ids"] = []
            update["thread_versions"] = {}
        try:
            repo.save(record.model_copy(update=update))
        except Exception:                                              # noqa: BLE001
            pass

    # ② 레저 실행 이력 정리
    ledger_before = now - timedelta(days=retention.ledger_d)
    try:
        counts["ledger_runs"] = ledger.prune_runs_before(ledger_before)
    except Exception:                                                  # noqa: BLE001
        pass

    # ③ 순찰 스크래치 케이스의 오래된 증거만 정리 (케이스 자체는 유지)
    try:
        scratch_ids = store.list_case_ids("patrol:")
    except Exception:                                                  # noqa: BLE001
        scratch_ids = []
    for case_id in scratch_ids:
        try:
            counts["scratch_evidence"] += store.purge_evidence_before(case_id, ledger_before)
        except Exception:                                              # noqa: BLE001
            continue

    # ④ 오래 멈춘 케이스의 스레드만 폐기(열린 케이스 + 닫힌 케이스 — I7) — 다음
    # 재개(또는 다음 조사)는 F3로 새 스레드를 연다. 나이는 status_since(없으면
    # updated_at) 기준.
    if checkpointer is not None:
        ttl_before = now - timedelta(days=retention.checkpoint_ttl_d)
        try:
            open_records = repo.list_open()
        except Exception:                                              # noqa: BLE001
            open_records = []
        try:
            closed_for_ttl = repo.list_by_status("closed")   # ①과 별개로 다시 읽는다(위에서 비운 스레드 반영)
        except Exception:                                              # noqa: BLE001
            closed_for_ttl = []
        for record in [*open_records, *closed_for_ttl]:
            since = record.status_since or record.updated_at
            if since >= ttl_before or not record.thread_ids:
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

    # ⑤ 발송 레저(sends) 정리(F6, 계획 5) — sent/pending 무관하게 sends_d 이전 기록 삭제
    sends_before = now - timedelta(days=retention.sends_d)
    try:
        counts["sends"] = ledger.prune_sends_before(sends_before)
    except Exception:                                                  # noqa: BLE001
        pass

    # ⑥ 오래된 이벤트 — events가 주입되지 않은 호출부(옛 테스트 등)는 건너뛴다.
    #    개별 실패가 스윕 전체를 죽이지 않게 다른 규칙과 같은 방식으로 감싼다.
    if events is not None:
        try:
            counts["events"] = events.prune_before(now - timedelta(days=retention.events_d))
        except Exception:                                          # noqa: BLE001
            pass

    return counts
