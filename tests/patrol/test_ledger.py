"""MetricsSinkPort — LedgerPort 3분할의 마지막 조각(계획 15/P8).

저장은 이미 갈라져 있었다(ledger_runs/sends/ledger_meta, retention knob도 따로).
메트릭은 네 번째 컬렉션이고, 인터페이스가 따라 갈라진다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.patrol.ledger import InMemoryLedger

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger():
    return InMemoryLedger()


def test_메트릭은_이름별로_최신순으로_읽힌다(ledger):
    ledger.record_metric("investigation.duration_s", 3.5,
                         tags={"gbm": "mx", "fct": "gumi", "outcome": "closed"}, at=T)
    ledger.record_metric("investigation.duration_s", 9.0, tags={"gbm": "mx"}, at=T + timedelta(minutes=1))
    ledger.record_metric("other.metric", 1.0, tags={}, at=T)
    rows = ledger.metrics("investigation.duration_s")
    assert [r["value"] for r in rows] == [9.0, 3.5]
    assert rows[0]["tags"] == {"gbm": "mx"} and rows[1]["at"] == T
    assert ledger.metrics("없는.이름") == []


def test_메트릭_조회는_limit을_지킨다(ledger):
    for i in range(5):
        ledger.record_metric("m", float(i), tags={}, at=T + timedelta(seconds=i))
    assert [r["value"] for r in ledger.metrics("m", limit=2)] == [4.0, 3.0]
    assert ledger.metrics("m", limit=0) == []          # -0 슬라이스 함정(runs와 같은 규약)


def test_오래된_메트릭만_걷힌다(ledger):
    ledger.record_metric("m", 1.0, tags={}, at=T - timedelta(days=40))
    ledger.record_metric("m", 2.0, tags={}, at=T - timedelta(days=30))   # 경계 = 남는다
    ledger.record_metric("m", 3.0, tags={}, at=T)
    assert ledger.prune_metrics_before(T - timedelta(days=30)) == 1
    assert [r["value"] for r in ledger.metrics("m")] == [3.0, 2.0]


def test_LedgerPort는_세_책임의_합집합이다():
    # 소비자(데몬 조립·retention)는 여전히 하나를 받는다 — 갈라진 것은 ABC다.
    from src.patrol.ledger import CheckLedgerPort, LedgerPort, MetricsSinkPort, SendLedgerPort
    assert issubclass(LedgerPort, (CheckLedgerPort, SendLedgerPort, MetricsSinkPort))
    assert not issubclass(CheckLedgerPort, MetricsSinkPort)     # 융착돼 있으면 분할이 아니다
