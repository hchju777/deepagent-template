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
