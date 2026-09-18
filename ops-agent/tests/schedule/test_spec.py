"""스케줄 설정 — 잘못된 값이 **기동에서** 걸리는가."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from src.config.schema_schedule import ScheduleSpec

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=KST)


def test_기본은_매일_8시다():
    spec = ScheduleSpec()
    assert (spec.kind, spec.cron) == ("cron", "0 8 * * *")
    assert spec.next_after(NOW, anchor=NOW).strftime("%m-%d %H:%M") == "09-15 08:00"


def test_interval은_기준점에_묶인다():
    """"직전 실행 + 간격"이면 실행 시간만큼 매번 밀린다."""
    spec = ScheduleSpec(kind="interval", interval_seconds=60)
    anchor = NOW

    assert spec.next_after(NOW, anchor=anchor) == NOW + timedelta(seconds=60)
    # 한 번이 90초 걸려 이미 한 슬롯이 지났다 — 몰아 쏘지 않고 다음 슬롯으로 간다.
    late = NOW + timedelta(seconds=90)
    assert spec.next_after(late, anchor=anchor) == NOW + timedelta(seconds=120)


def test_슬롯_경계에서는_다음_슬롯이다():
    """정확히 슬롯 위에서 물으면 같은 순간을 돌려주면 안 된다 — 즉시 재발사가 된다."""
    spec = ScheduleSpec(kind="interval", interval_seconds=60)

    assert spec.next_after(NOW + timedelta(seconds=60), anchor=NOW) == \
        NOW + timedelta(seconds=120)


def test_interval이어도_cron_식을_검사한다():
    """안 하면 잘못된 식이 **kind를 바꾸는 날**에야 드러나고, 그날은 보통 급할 때다."""
    with pytest.raises(ValidationError, match="cron"):
        ScheduleSpec(kind="interval", interval_seconds=60, cron="매일 8시")


def test_간격은_1초_이상이다():
    """0이면 발사 사이에 잠이 없어 한 코루틴이 루프를 독점한다."""
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="interval", interval_seconds=0)


def test_모르는_칸은_거부한다():
    with pytest.raises(ValidationError):
        ScheduleSpec(every="1분")


def test_사람이_읽을_한_줄():
    assert ScheduleSpec().describe() == "cron 0 8 * * *"
    assert ScheduleSpec(kind="interval", interval_seconds=60).describe() == "60초마다"
    assert ScheduleSpec(enabled=False).describe() == "꺼짐"
