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
    """요약 키가 Redis에 아예 없다 — "선언됐는데 없는 키"가 r1 브리핑에 실리는지 재는 변형. (측정 #6 정정: 사내 c-1형이 **아니다** —
    c-1형은 키가 있고 data가 빈 배열이다, 아래 `data-empty`.)"""
    s = _seeds(NOW, "cache-missing")
    assert not [k for k in s["redis"] if k.startswith("alarm:stats:")]
    assert "hb:sink" in s["redis"]
    assert all(_badge(s, l)["alarm"] == 0 for l in LINES)


# ── R3-1 ①-b — 요약 키 값에 identity, c-1형, 미끼 ──

def _value(seeds, key):
    import json
    return json.loads(seeds["redis"][key])


def test_data_empty는_요약_키가_있고_data가_빈_배열이며_값_안에_대상_identity가_그대로_있다():
    """사내 c-1형(측정 #6 정정): 대상 요약 키는 있고 data가 비었다. 값의 `record[0].summary_info`에 배지 행의 group/title이 그대로 —
    입구 노드(R3-1 ①)가 그 자리로 키를 고른다."""
    from src.application.intake_map import holds_identity

    s = _seeds(NOW, "data-empty")
    value = _value(s, "alarm:stats:L1")
    assert value["record"][0]["data"] == [] and value["count_1h"] == 0
    assert holds_identity(value, ("L1", "Alarm")) == "record[0].summary_info"
    assert all(_badge(s, l)["alarm"] == 0 for l in LINES)
    assert len(_recent(s, "L1")) >= 2                                   # 컬렉션엔 최근 문서가 있다 — 깨진 곳은 요약 키 쪽
    healthy = _value(_seeds(NOW, "healthy"), "alarm:stats:L1")
    assert healthy["record"][0]["data"] and holds_identity(healthy, ("L1", "Alarm")) == "record[0].summary_info"


def test_미끼_키는_이름이_비슷하고_같은_라벨을_달았지만_identity가_다르다():
    """측정 #6 c-2의 모양 — 라벨(`source`)은 여러 키가 공유하고 이름도 비슷하다. identity로만 가르면 미끼는 빠진다."""
    from src.application.intake_map import holds_identity

    s = _seeds(NOW, "data-empty")
    stats, decoy = _value(s, "alarm:stats:L1"), _value(s, "alarm:scan:L1")
    assert stats["record"][0]["summary_info"]["source"] == decoy["record"][0]["summary_info"]["source"] == "MES"
    assert holds_identity(decoy, ("L1", "Alarm")) is None and holds_identity(decoy, ("L1", "Caution")) == "record[0].summary_info"
    assert [k for k in ("alarm:scan:L1", "alarm:stats:L1") if holds_identity(_value(s, k), ("L1", "Alarm"))] == ["alarm:stats:L1"]


# ── R3-1b ② — 측정판: 요약 키를 명령에 실어 보내는 배치 서비스 ──

def test_batch는_요약_키를_직접_쓰지_않고_명령에_실어_보낸다():
    """사내 10-07 진단표 #8의 모양 — 배치 파이프라인이 요약 키 이름을 kafka 명령에 실어 보낸다(직접 set 안 함). 토폴로지의 `batch`가
    그 서비스다 — `code uses <요약 키>`의 쓰기에 sink와 함께 나와야 P0 2의 다음 홉이 쓸모 있다."""
    from tools.local_case import BOARD_SERVICES, CORE_FILES, _board_topology
    src = CORE_FILES["batch/refresh.py"]
    assert 'cfg["redis_key"]["alarm_stats"]' in src and "producer.send(" in src and "redis.set" not in src
    # 사내 모양(측정 #8): 쓰는 쪽은 `_run`을 가진 클래스이고, 요약 키 값의 `metadata.event_type`이 그 클래스 이름이다. 입력 키를 읽는다.
    assert "class SummaryRefresh:" in src and "def _run(self" in src and 'cfg["redis_key"]["alarm_source"]' in src
    s = _seeds(NOW, "data-empty")
    assert _value(s, "alarm:stats:L1")["metadata"]["event_type"] == "SummaryRefresh"
    assert _value(s, "alarm:scan:L1")["metadata"]["event_type"] == "refresh-job"            # 미끼 — 클래스 이름이 아니다
    assert "alarm:source:L1" in s["redis"]
    # 리포의 knowledge/topology/mx.json은 사내에서 실제 서비스 지도로 바뀌는 파일이다 — 측정판은 자기 사본의 services만 바꾼다(핫픽스 10-14).
    assert BOARD_SERVICES["batch"] == {"repo": "dt-core", "role": "주기적으로 요약을 다시 만들라는 명령을 낸다",
                                       "selects": {"SERVICE_ROLE": "batch"}}
    made = _board_topology({"_설명": ["x"], "services": {"real-svc": {"repo": "real-repo"}}, "flow": {"sources": []}, "config_paths": ["c"]})
    assert made["services"] == BOARD_SERVICES and made["flow"] == {"sources": []} and made["config_paths"] == ["c"] and made["_설명"] == ["x"]


def test_source_missing은_원천_키가_없고_source_blank은_빈_배열이며_둘_다_요약_키의_data는_빈_배열이다():
    """R3-3 ② 걷기가 나쁜 입력에서 멈추는 두 모양 — 없음(키가 없다)과 0행(빈 배열). 요약 키는 c-1형(`data-empty`) 그대로."""
    import json
    missing, blank = _seeds(NOW, "source-missing"), _seeds(NOW, "source-blank")
    assert not [k for k in missing["redis"] if k.startswith("alarm:source:")]
    assert json.loads(blank["redis"]["alarm:source:L1"]) == []
    for s in (missing, blank):
        assert json.loads(s["redis"]["alarm:stats:L1"])["record"][0]["data"] == []
    assert json.loads(_seeds(NOW, "data-empty")["redis"]["alarm:source:L1"])
    # c-2형 — 원천 행은 있는데 모든 행의 state가 '-'. batch 코드에 그 필드와 값이 한 줄에 리터럴로 있다(③의 데이터 링크).
    rows = json.loads(_seeds(NOW, "source-filtered")["redis"]["alarm:source:L1"])
    assert rows and all(r["state"] == "-" for r in rows)
    from tools.local_case import CORE_FILES
    assert 'r.get("state") != "-"' in CORE_FILES["batch/refresh.py"]
