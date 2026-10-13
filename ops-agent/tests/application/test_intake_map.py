"""R3-0 ①·④ — 접수 끝점의 대상 행에서 대상 키로. **선언 없이, 행 자신의 값으로, 정확히 하나일 때만.**"""
from src.application.intake_map import map_target, row_tokens
from src.application.runner_probe import detail
from src.application.state import CaseState
from src.domain.case import EvidenceRef

KEYS = ("alarm:scan:{line}", "alarm:stats:{line}", "hb:{service}")


def _rows(target_link="/summary/badge/stats/", **target_over):
    rows = [{"group": g, "title": t, "alarm": 0, "link": f"/summary/badge/{t.lower()}/"}
            for t in ("Setup", "Alarm") for g in ("L1", "L2", "L3")]
    target = rows[3]                                                   # L1/Alarm — 사내처럼 대상 행이 앞이 아니다
    target["link"] = target_link
    target.update(target_over)
    return rows


def _state(case, rows, *, limit=1200):
    """실행기가 실제로 쓰는 본문(`detail`) — 손으로 흉내 내면 형식이 갈려도 모른다."""
    body, _ = detail({"request": {}, "status": 200, "response": rows}, limit=limit, focus=("L1", "Alarm"))
    ref = EvidenceRef(id="t-1.e1", source="rest.query entry='summary_badge'", summary="x", body="\n".join(body))
    return CaseState(case=case, evidence=[ref])


def test_행의_값은_영숫자_토큰_3자_이상이고_숫자만인_것은_뺀다():
    assert row_tokens({"group": "L1", "title": "Alarm", "alarm": 0, "ts": "2026-10-13", "link": "/summary/badge/stats/"}) == [
        ("title", "Alarm"), ("link", "summary"), ("link", "badge"), ("link", "stats")]


def test_대상_행의_값이_선언_키_하나에만_들면_그_키를_고르고_케이스_줄로_말한다(case):
    """사내 측정 #5 c-1: 이름이 비슷한 다른 배지의 키를 읽었다. 증상 문장이 아니라 **대상 행 자신의 값**으로 고른다 — 선언은 두지
    않는다(장기 유지보수의 짐, 10-13 결정). 고른 키는 순서가 아니라 문장으로 리드에게 간다."""
    got = map_target(_state(case, _rows()), declared_keys=KEYS)
    assert (got.token, got.field, got.key, got.prefer) == ("stats", "link", "alarm:stats:{line}", ("stats",))
    assert got.line() == "대상 키: alarm:stats:{line} (대상 행 t-1.e1 response[3]의 link 값 토큰 'stats'가 이 키에만 든다)"


def test_고르지_못하면_고르지_않고_그_사실을_적는다(case):
    assert map_target(CaseState(case=case), declared_keys=KEYS).line() == ""              # 접수 응답을 아직 안 봤다
    assert map_target(_state(case, _rows()), declared_keys=()).line() == ""              # 고를 재료(선언 키)가 없다
    got = map_target(_state(case, _rows("/other/")), declared_keys=KEYS)                 # 어느 키에도 안 든다
    assert got.key is None and "든 것이 없다" in got.line() and got.prefer == ()
    got = map_target(_state(case, _rows("/summary/badge/alarm/")), declared_keys=KEYS)   # 둘에 든다 — 고르지 않는다
    assert got.key is None and got.prefer == () and "2개" in got.line() and "alarm:scan:{line}" in got.line()
    assert set(got.keys) == set(KEYS[:2])
    rows = _rows()
    rows[3].pop("link")                                                               # 링크 없이 identity만 — 대소문자 그대로라 안 맞는다
    assert map_target(_state(case, rows), declared_keys=KEYS).key is None


def test_잘린_대상_행은_못_읽었다고_말한다(case):
    got = map_target(_state(case, _rows(note="x" * 2000), limit=300), declared_keys=KEYS)
    assert got.key is None and "잘려" in got.line()
