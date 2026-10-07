"""로컬 측정판의 시드 변형 — 고장이 **어느 홉**에 있는지가 변형마다 다르다.

sink-stopped(기본)는 컬렉션 상류가 깨진 것이라 배지와 컬렉션이 **일치**하는 게 정상이고, cache-stale은
컬렉션과 화면 사이가 깨진 것이라 **불일치**가 나야 한다. healthy는 대조군이다. 11b 커밋 4의 T3가 이 셋으로
recompute의 일치/불일치를 잰다.
"""
from datetime import datetime, timedelta

import pytest

from tools.local_case import LINES, _seeds

NOW = datetime(2026, 9, 30, 10, 0, 0)


def _badge(seeds, line, title="Alarm"):
    """배지 응답은 사내 모양의 행 목록이다 — `group`·`title`로 고른다."""
    return next(r for r in seeds["rest"]["summary_badge"] if r["group"] == line and r["title"] == title)


def _recent(seeds, line):
    since = (NOW - timedelta(minutes=60)).isoformat()
    return [d for d in seeds["mongo"]["alarm_events"] if d["line"] == line and d["occ_date"] >= since]


def test_기본은_sink_정지다_컬렉션에_최근_문서가_없고_lag가_쌓이고_배지는_0이다():
    s = _seeds(NOW)
    assert s == _seeds(NOW, "sink-stopped")
    assert all(not _recent(s, l) for l in LINES)
    assert s["lags"]["gumi-mx-core"]["mx.alarm.main"] > 0
    assert all(_badge(s, l)["alarm"] == 0 for l in LINES)


def test_cache_stale은_컬렉션은_최신인데_캐시와_배지가_옛것이다():
    s = _seeds(NOW, "cache-stale")
    assert len(_recent(s, "L1")) >= 2
    assert s["lags"]["gumi-mx-core"]["mx.alarm.main"] == 0
    assert _badge(s, "L1")["alarm"] == 0                              # 화면은 옛 캐시를 보여 준다
    assert "0" in s["redis"]["alarm:stats:L1"]                      # 캐시도 옛것 그대로


def test_healthy는_배지가_창_안_문서_수와_같다():
    s = _seeds(NOW, "healthy")
    for l in LINES:
        assert _badge(s, l)["alarm"] == len(_recent(s, l)) > 0
    assert s["lags"]["gumi-mx-core"]["mx.alarm.main"] == 0


def test_모르는_변형은_거부한다():
    with pytest.raises(ValueError):
        _seeds(NOW, "nope")


def test_cache_missing은_캐시_키가_아예_없고_배지는_0이다():
    """사내 실측 c-1의 모양 — 요약 키가 Redis에 없다. 선언됐는데 없는 키가 r1 브리핑에 실리는지 재는 변형."""
    s = _seeds(NOW, "cache-missing")
    assert not [k for k in s["redis"] if k.startswith("alarm:stats:")]
    assert "hb:sink" in s["redis"]
    assert all(_badge(s, l)["alarm"] == 0 for l in LINES)
