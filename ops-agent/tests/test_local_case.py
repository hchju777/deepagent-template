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
    # 사내 모양(측정 #10): `_run`은 조립뿐이고 거르는 줄은 `get("X")`↔`register(component="X")`로 이은 컴포넌트에 있다. 미끼 — 사이트 config의 같은
    # 필드·값 줄과 다른 서비스의 dict 줄(둘 다 레포 전체 grep이 가짜로 잡던 것).
    from tools.local_case import CORE_FILES
    assert 'self.components.get("state_filter")' in CORE_FILES["batch/refresh.py"] and 'r.get("state") != "-"' not in CORE_FILES["batch/refresh.py"]
    # 등록은 등록 모음 파일에 호출형으로(R3-5 ④) — 컴포넌트 파일엔 데코레이터가 없고, 비교식은 이 사이트 전용 클래스에 있다.
    assert 'register(component="state_filter", gbm="mx", factory="gumi")(comp.GumiStateFilter)' in CORE_FILES["common/registry.py"]
    assert "@register" not in CORE_FILES["batch/components.py"] and 'r.get("state") != "-"' in CORE_FILES["batch/components.py"]
    assert CORE_FILES["config/factories/gumi/common.json"]["badge"] == {"empty_state": "-", "state": "-"}
    assert '{"state": "-"' in CORE_FILES["processor/handler.py"]


def test_palette_empty는_공용_팔레트_키가_빈_배열이고_그_쓴_쪽_클래스는_서비스_디렉터리_밖과_다른_레포에_같은_이름으로_있다():
    """측정 #10 P0 2의 모양 — 쓴 쪽(batch)이 읽는 공용 키의 쓴 클래스가 (1) 서비스 디렉터리 밖(`common/`)이라 `path` 없는 토폴로지로는 세
    서비스 중 어느 것인지 못 가르고, (2) 다른 레포(dt-api)에도 같은 이름으로 있다. 걷기가 레포 단위로 가르고 `repo=`를 넘기는지 재는 변형.
    dt-api는 그 키를 선언하지 않는다 — 선언으로 dt-core를 고른다."""
    import json
    from tools.local_case import API_FILES, CORE_FILES, VARIANTS, _EMPTY_DATA
    assert "palette-empty" in VARIANTS and "palette-empty" in _EMPTY_DATA
    s = _seeds(NOW, "palette-empty")
    palette = json.loads(s["redis"]["alarm:palette"])
    assert palette["colors"] == [] and palette["metadata"]["event_type"] == "PaletteLoader"
    assert json.loads(s["redis"]["palette:source"]) and json.loads(s["redis"]["alarm:stats:L1"])["record"][0]["data"] == []
    assert json.loads(_seeds(NOW, "data-empty")["redis"]["alarm:palette"])["colors"]
    assert "class PaletteLoader:" in CORE_FILES["common/palette.py"] and "class PaletteLoader:" in API_FILES["api/palette.py"]
    assert 'cfg["redis_key"]["alarm_palette"]' in CORE_FILES["batch/refresh.py"] and 'cfg["redis_key"]["palette_source"]' in CORE_FILES["common/palette.py"]
    assert CORE_FILES["config/gbm/mx.json"]["redis_key"]["alarm_palette"] == "alarm:palette"
    assert "alarm_palette" not in API_FILES["config/gbm/mx.json"]["redis_key"]


# ── R3-5 ④ — 사내 c-1·c-2의 사슬과 등록 모음 파일 ──

def test_chain_empty와_chain_filtered는_정답_홉_표의_사슬_모양이다():
    """측정 #11 뒤 사람이 준 정답 표의 모양. c-1: 요약(빈 배열) ← 도메인(빈 배열) ← 원천(빈 배열, 쓴 클래스는 메시지 입력이라 인덱스 자원 0).
    c-2: 요약(빈 배열) ← 도메인(행 전부 state·wg가 '-', 같은 값인 문자열 일곱 필드 뒤) ← 원천(같은 값) ← 입력 셋(정상)."""
    from tools.local_case import VARIANTS, _EMPTY_DATA
    assert {"chain-empty", "chain-filtered"} <= set(VARIANTS) & _EMPTY_DATA
    empty, filtered = _seeds(NOW, "chain-empty"), _seeds(NOW, "chain-filtered")
    for s in (empty, filtered):
        assert _value(s, "alarm:stats:L1")["record"][0]["data"] == []
    assert _value(empty, "alarm:domain:L1") == {"metadata": {"event_type": "DomainBuilder"}, "record": []}
    assert _value(empty, "alarm:raw:L1") == {"metadata": {"event_type": "SourceCollector"}, "rows": []}
    rows = _value(filtered, "alarm:domain:L1")["record"]
    same = [k for k, v in rows[0].items() if isinstance(v, str) and all(r[k] == v for r in rows)]
    assert len(rows) == 5 and all(r["state"] == r["wg"] == "-" for r in rows) and same.index("state") >= 7
    assert _value(filtered, "alarm:raw:L1") == {"metadata": {"event_type": "SourceMerger"}, "rows": rows}
    assert all(_value(filtered, k) for k in ("feed:events:L1", "feed:status:L1", "feed:ref"))
    # 다른 변형의 도메인·원천 묶음은 정상이다 — 걷기가 거기서 멈추면 다른 변형의 측정이 바뀐다
    healthy = _seeds(NOW, "healthy")
    assert _value(healthy, "alarm:domain:L1")["record"] and _value(healthy, "alarm:raw:L1")["rows"]


def test_등록은_모음_파일에_호출형_네_모양으로_있고_문서_문자열_속_예시는_등록이_아니다():
    """사내 등록 모음 파일의 모양(사람 확인 10-15): (a) 한 줄, (b) 사이트 인자(기본 클래스 위에 사이트 전용 클래스), (c) 점 표기 별칭, (d) 여러 줄.
    문서 문자열의 `@register` 예시는 세지 않는다 — 세면 이 사이트의 state_filter가 둘이 돼 못 이음이 된다."""
    from src.infrastructure.deployed_code import registrations
    from tools.local_case import CORE_FILES
    text = CORE_FILES["common/registry.py"]
    regs = registrations("common/registry.py", text)
    assert [(r.component, r.cls, r.module, r.site) for r in regs] == [
        ("state_filter", "StateFilter", None, {}),
        ("state_filter", "GumiStateFilter", "batch.components", {"gbm": "mx", "factory": "gumi"}),
        ("state_filter", "SevtStateFilter", "batch.sevt_filters", {"gbm": "mx", "factory": "sevt"}),
        ("row_parser", "RowParser", None, {}),
        ("ref_joiner", "RefJoiner", None, {}),
        ("row_filler", "RowFiller", "common.fillers", {})]
    assert '@register(component="state_filter"' in text and "register(\n    component=" in text
    assert not [p for p, t in CORE_FILES.items() if p.endswith(".py") and p != "common/registry.py" and "register(" in t]


def test_제목에_슬래시가_든_배지의_요약_키는_data가_커서_잘리고_쓴_쪽_이름은_data_밖에_있다():
    """측정 #12의 16행 모양(R3-5b ①) — data가 커서 입구의 후보 본문(기본 evidence_chars)이 잘려 좁혀 다시 읽고, 쓴 쪽 이름은 data의 형제 자리에
    있다. 제목에 '/'가 든다(행 9)."""
    import json
    from tools.local_case import API_FILES, BADGE_TITLES, CORE_FILES
    s = _seeds(NOW, "healthy")
    assert "Up/Down Rate" in BADGE_TITLES and _badge(s, "L1", "Up/Down Rate")
    value = _value(s, "alarm:rate:L1")
    assert value["record"][0]["summary_info"]["title"] == "Up/Down Rate" and value["metadata"]["event_type"] == "RateRefresh"
    assert len(json.dumps(value["record"][0]["data"])) > 2400
    assert "class RateRefresh:" in CORE_FILES["batch/rate.py"] and 'cfg["redis_key"]["alarm_rate"]["key"]' in API_FILES["api/alarms.py"]



def test_chain_objects와_palette_unread는_빈_객체_자기_출력뿐인_클래스_읽기_실패의_모양이다():
    """측정 #12의 걷기 틀린 사실 둘과 읽기 실패(R3-5b ②). chain-objects: 도메인 키 `record {}`(빈 객체) ← 원천 묶음 빈 배열, 원천의 쓴 쪽은 그 묶음을
    읽어 덧붙여 다시 쓴다(읽는 자원이 자기 출력뿐). palette-unread: palette-empty에 원천 색 키 읽기 실패(stub 신호 — 실서버의 WRONGTYPE 자리)."""
    from tools.local_case import CORE_FILES, VARIANTS, _EMPTY_DATA
    assert {"chain-objects", "palette-unread"} <= set(VARIANTS) & _EMPTY_DATA
    objects = _seeds(NOW, "chain-objects")
    assert _value(objects, "alarm:domain:L1") == {"metadata": {"event_type": "DomainBuilder"}, "record": {}}
    assert _value(objects, "alarm:raw:L1") == {"metadata": {"event_type": "RawAppender"}, "rows": []}
    src = CORE_FILES["common/appender.py"]
    assert src.count('cfg["redis_key"]["alarm_raw"]') == 2 and "self.redis.get(" in src and "self.redis.set(" in src
    unread = _seeds(NOW, "palette-unread")
    assert set(unread["redis"]["palette:source"]) == {"__error__"} and _value(unread, "alarm:palette")["colors"] == []



def test_그룹과_제목이_같은_배지와_행_안의_클래스_이름_미끼():
    """측정 #12b P0 1·2의 모양(R3-5c ①) — 증감 배지 키의 행 안 화면 라벨이 dt-api의 스키마 클래스 이름과 같고, 그룹=제목인 배지(`ALL/ALL`)의 값이
    미끼 키(Caution 배지)에 한 번 든다."""
    from tools.local_case import API_FILES
    s = _seeds(NOW, "healthy")
    assert _badge(s, "ALL", "ALL")
    assert _value(s, "alarm:scan:L1")["record"][0]["summary_info"]["scope"] == "ALL"
    assert {r["view"] for r in _value(s, "alarm:rate:L1")["record"][0]["data"]} == {"Overview"} and "class Overview:" in API_FILES["api/schemas.py"]



def test_도메인_키를_쓰는_클래스는_읽기를_부모의_run에서_받는다():
    """측정 #13 추가분 3(R3-5d ③)의 모양 — 읽기는 부모(다른 파일)의 `_run`에, 쓰기는 자식에. 걷기가 DomainBuilder를 지나 원천 묶음으로 가려면
    `code.resources`가 부모의 읽기를 세어야 한다(측정판 걷기 테스트가 그걸 잰다)."""
    from tools.local_case import CORE_FILES
    child, base = CORE_FILES["batch/domain.py"], CORE_FILES["common/readers.py"]
    assert "class DomainBuilder(LineReader):" in child and "def _run" not in child and 'cfg["redis_key"]["alarm_domain"]' in child
    assert "class LineReader:" in base and "def _run(self" in base and 'cfg["redis_key"]["alarm_raw"]' in base



def test_message_변형_셋은_원천_묶음_키_값에_포인터가_있고_그_메시지가_비었거나_지워졌거나_행이_전부_자리_표시다():
    """측정 #13 추가분 2(R3-5d ④)의 모양 — `metadata.source`(토픽·파티션·오프셋)가 그 키를 만든 메시지 한 건을 가리킨다. message-gone은 발행 선언이
    있는 가공 토픽의 보존 앞 오프셋이다. 다른 변형엔 포인터가 없다(정답 표가 그대로)."""
    from tools.local_case import CORE_FILES, VARIANTS, _EMPTY_DATA, _MESSAGE_VARIANTS
    assert _MESSAGE_VARIANTS <= set(VARIANTS) & _EMPTY_DATA
    for variant in _MESSAGE_VARIANTS:
        raw = _value(_seeds(NOW, variant), "alarm:raw:L1")
        assert raw["rows"] == [] and raw["metadata"]["event_type"] == "SourceCollector"
        assert set(raw["metadata"]["source"]) == {"topic", "partition", "offset"} and raw["metadata"]["source"]["partition"] == 0
    empty, gone, blank = (_seeds(NOW, v) for v in ("message-empty", "message-gone", "message-blank"))
    ptr = _value(empty, "alarm:raw:L1")["metadata"]["source"]
    assert ptr["topic"] == "mx.alarm.raw" and empty["kafka"]["mx.alarm.raw"][ptr["offset"]]["rows"] == []
    ptr = _value(gone, "alarm:raw:L1")["metadata"]["source"]
    assert ptr["topic"] == "gumi.alarm.audit" and gone["kafka"]["gumi.alarm.audit"]["begin"] > ptr["offset"]
    # 그 토픽은 사이트 층만 선언한다 — 소비·발행 key_path 둘에 같은 값(측정 #15 P0-1의 모양). GBM 층엔 없다.
    site = CORE_FILES["config/factories/gumi/mx.json"]["infra"]["kafka"]
    assert site["consumer"]["topic"]["topic3"] == site["producer"]["topic"]["topic2"] == "gumi.alarm.audit"
    assert "gumi.alarm.audit" not in str(CORE_FILES["config/gbm/mx.json"])
    ptr = _value(blank, "alarm:raw:L1")["metadata"]["source"]
    rows = blank["kafka"]["mx.alarm.raw"][ptr["offset"]]["rows"]
    assert len(rows) == 5 and all(r["ws"] == "-" for r in rows) and 'row.get("ws") != "-"' in CORE_FILES["common/parsers.py"]
    assert "source" not in _value(_seeds(NOW, "chain-empty"), "alarm:raw:L1")["metadata"]
