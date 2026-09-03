"""실행 레저 — 점검 실행 이력과 데몬 하트비트를 남긴다 (스펙 §4.6-4).

레저는 판정에 관여하지 않는다: run_check가 만든 CheckOutcome을 그대로
받아 적재할 뿐이다. consecutive_errors는 4b의 자기 감시 점검(연속 error
N회 알림)이 그대로 쓸 수 있도록 "가장 최근부터 error가 몇 번 연속됐는가"를
센다 — 중간에 ok/finding이 한 번이라도 끼면 그 지점에서 끊긴다. skipped는
투명하다: 예산 부족 등으로 건너뛴 회차는 스트릭을 끊지도 잇지도 않고 그냥
지나친다(4a 미너 반영 — skipped를 error 취급하지도, 회복 신호로 보지도 않는다).
"""
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime

from src.domain.patrol import CheckOutcome


class CheckLedgerPort(ABC):
    """점검 실행 이력과 데몬 하트비트. 소비자: runner·daemon·selfcheck·worker·patrol status."""

    @abstractmethod
    def record_run(self, gbm: str, fct: str, check: str, outcome: CheckOutcome) -> None: ...

    @abstractmethod
    def last_run(self, gbm: str, fct: str, check: str) -> CheckOutcome | None: ...

    @abstractmethod
    def consecutive_errors(self, gbm: str, fct: str, check: str) -> int: ...

    @abstractmethod
    def heartbeat(self, at: datetime) -> None: ...

    @abstractmethod
    def last_heartbeat(self) -> datetime | None: ...

    @abstractmethod
    def runs(self, gbm: str, fct: str, check: str, limit: int = 50) -> list[CheckOutcome]: ...

    @abstractmethod
    def prune_runs_before(self, before: datetime) -> int:
        """before 이전에 기록된 실행 이력을 전부 삭제하고 삭제 건수를 반환한다."""
        ...


class SendLedgerPort(ABC):
    """발송 2상 멱등 레저. 소비자: mail.send_report·retry_pending."""

    @abstractmethod
    def record_send(self, send_id: str, *, kind: str, target: str, at: datetime) -> bool:
        """send_id를 pending으로 기록한다. 이미 있으면 아무것도 하지 않고 False(중복 억제)."""
        ...

    @abstractmethod
    def mark_sent(self, send_id: str, at: datetime) -> None:
        """send_id를 발송 완료로 표시해 pending 목록에서 뺀다."""
        ...

    @abstractmethod
    def pending_sends(self, limit: int = 50) -> list[dict]:
        """아직 mark_sent되지 않은 발송 기록을 반환한다. 각 항목은
        {send_id, kind, target, at}."""
        ...

    @abstractmethod
    def prune_sends_before(self, before: datetime) -> int:
        """before 이전에 기록된 발송 이력(완료분 포함)을 전부 삭제하고 삭제 건수를 반환한다."""
        ...


class LedgerPort(CheckLedgerPort, SendLedgerPort):
    """두 책임을 다 쓰는 소비자(데몬 조립·retention 스윕)용 합집합.

    구현을 쪼개지 않는 이유: MongoLedger는 이미 ledger_runs/sends/ledger_meta로
    컬렉션이 갈라져 있고 retention knob도 ledger_d/sends_d로 분리돼 있다 —
    저장은 이미 갈라졌고 인터페이스만 융착돼 있었다. 실제 분리가 필요해지는
    시점(다른 채널이 발송만 쓰거나, 메트릭 sink가 붙을 때)에 구현을 나눈다.
    """


class InMemoryLedger(LedgerPort):
    def __init__(self):
        self._runs: dict[tuple[str, str, str], list[CheckOutcome]] = defaultdict(list)
        self._heartbeat_at: datetime | None = None
        self._sends: dict[str, dict] = {}          # send_id -> {send_id, kind, target, at, sent}

    def record_run(self, gbm, fct, check, outcome):
        self._runs[(gbm, fct, check)].append(outcome)

    def last_run(self, gbm, fct, check):
        history = self._runs.get((gbm, fct, check))
        return history[-1] if history else None

    def consecutive_errors(self, gbm, fct, check):
        count = 0
        for outcome in reversed(self._runs.get((gbm, fct, check), [])):
            if outcome.status == "skipped":  # skipped는 투명 — 스트릭을 끊지 않는다(4a 미너)
                continue
            if outcome.status != "error":
                break
            count += 1
        return count

    def heartbeat(self, at):
        self._heartbeat_at = at

    def last_heartbeat(self):
        return self._heartbeat_at

    def runs(self, gbm, fct, check, limit=50):
        if limit <= 0:  # limit=0은 "0개" — -0 슬라이스가 전체를 돌려주는 함정을 피한다
            return []
        history = self._runs.get((gbm, fct, check), [])
        return list(reversed(history[-limit:]))

    def prune_runs_before(self, before):
        """before 이전에 기록된 실행 이력을 전부 삭제하고 삭제 건수를 반환한다."""
        deleted = 0
        for key, history in self._runs.items():
            kept = [o for o in history if o.observed_at >= before]
            deleted += len(history) - len(kept)
            self._runs[key] = kept
        return deleted

    def record_send(self, send_id, *, kind, target, at):
        if send_id in self._sends:                 # 이미 있으면 중복 억제 — False
            return False
        self._sends[send_id] = {"send_id": send_id, "kind": kind, "target": target,
                                "at": at, "sent": False}
        return True

    def mark_sent(self, send_id, at):
        record = self._sends.get(send_id)
        if record is not None:
            record["sent"] = True

    def pending_sends(self, limit=50):
        if limit <= 0:                              # limit=0은 "0개"(runs와 동일 관례)
            return []
        pending = [r for r in self._sends.values() if not r["sent"]]
        return [{"send_id": r["send_id"], "kind": r["kind"], "target": r["target"],
                 "at": r["at"]} for r in pending[:limit]]

    def prune_sends_before(self, before):
        """before 이전에 기록된 발송 이력(완료분 포함)을 전부 삭제하고 삭제 건수를 반환한다."""
        stale = [sid for sid, r in self._sends.items() if r["at"] < before]
        for sid in stale:
            del self._sends[sid]
        return len(stale)
