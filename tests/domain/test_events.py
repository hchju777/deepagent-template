from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from src.domain.events import EVENT_SCHEMA_VERSION, EngineEvent

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_봉투는_어휘_밖_이벤트를_거부한다():
    e = EngineEvent(event="round_started", case_id="c-1", at=T, data={"round": 1})
    assert e.schema_version == EVENT_SCHEMA_VERSION == 1
    with pytest.raises(ValidationError):
        EngineEvent(event="node_finished", case_id="c-1", at=T)
    with pytest.raises(ValidationError):
        EngineEvent(event="round_started", case_id="c-1", at=T, extra="x")


def test_이벤트_스토어는_케이스별로_단조_seq를_부여한다():
    from src.domain.events import InMemoryEventStore
    store = InMemoryEventStore()
    a = store.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    b = store.append(EngineEvent(event="round_started", case_id="c-1", at=T))
    c = store.append(EngineEvent(event="round_started", case_id="c-2", at=T))
    assert (a.seq, b.seq, c.seq) == (1, 2, 1)
    assert [e.seq for e in store.since("c-1")] == [1, 2]
    assert [e.seq for e in store.since("c-1", after_seq=1)] == [2]


def test_보존_삭제_후에도_seq는_재사용되지_않는다():
    # seq를 len(history)+1로 계산하면 prune 후 이미 나간 번호로 되돌아간다 —
    # 구독자가 `?since=2`로 재접속했을 때 새 이벤트를 영원히 못 본다.
    from datetime import timedelta

    from src.domain.events import InMemoryEventStore
    store = InMemoryEventStore()
    store.append(EngineEvent(event="round_started", case_id="c-1", at=T - timedelta(days=40)))
    store.append(EngineEvent(event="round_started", case_id="c-1", at=T - timedelta(days=40)))
    assert store.prune_before(T) == 2
    assert store.append(EngineEvent(event="round_started", case_id="c-1", at=T)).seq == 3
