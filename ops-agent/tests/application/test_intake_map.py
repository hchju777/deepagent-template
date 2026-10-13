"""R3-0 ① — 접수 끝점의 대상 행에서 대상 키로. **코드가 고르고, 못 고르면 고르지 않는다.**"""
from src.application.intake_map import map_target
from src.application.runner_probe import detail
from src.application.state import CaseState
from src.domain.case import EvidenceRef
from src.knowledge.schema import IntakeLink

LINK = IntakeLink(field="link", token="/summary/badge/([^/]+)/")
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


def test_대상_행의_링크_토큰이_든_선언_키_하나를_고르고_케이스_줄로_말한다(case):
    """사내 측정 #5 c-1: 이름이 비슷한 다른 배지의 키를 읽었다. 토큰은 데이터 자신이 선언된 필드에 싣는 식별자다 — 증상 문장의
    단어가 아니다. 고른 키는 순서가 아니라 **문장**으로 리드에게 간다(순서는 리드가 못 읽는 신호다)."""
    got = map_target(_state(case, _rows()), link=LINK, declared_keys=KEYS)
    assert (got.token, got.key, got.prefer) == ("stats", "alarm:stats:{line}", ("stats",))
    assert got.line() == "대상 키: alarm:stats:{line} (대상 행 t-1.e1 response[3]의 링크 토큰 'stats'에서)"


def test_고르지_못하면_고르지_않고_그_사실을_적는다(case):
    assert map_target(CaseState(case=case), link=LINK, declared_keys=KEYS).line() == ""     # 접수 응답을 아직 안 봤다
    assert map_target(_state(case, _rows()), link=None, declared_keys=KEYS).line() == ""     # 선언이 없다
    rows = _rows()
    rows[3].pop("link")
    got = map_target(_state(case, rows), link=LINK, declared_keys=KEYS)
    assert got.key is None and "링크 필드 'link'" in got.line() and got.prefer == ()
    got = map_target(_state(case, _rows("/other/")), link=LINK, declared_keys=KEYS)
    assert got.key is None and "토큰을 못 뽑았다" in got.line()
    got = map_target(_state(case, _rows("/summary/badge/nothing/")), link=LINK, declared_keys=KEYS)
    assert got.key is None and got.token == "nothing" and "선언 키가 없다" in got.line() and got.prefer == ("nothing",)
    got = map_target(_state(case, _rows("/summary/badge/alarm/")), link=LINK, declared_keys=KEYS)   # 둘에 든다
    assert got.key is None and "2개" in got.line() and "alarm:scan:{line}" in got.line() and got.keys == KEYS[:2]


def test_잘린_대상_행은_못_읽었다고_말한다(case):
    got = map_target(_state(case, _rows(note="x" * 2000), limit=300), link=LINK, declared_keys=KEYS)
    assert got.key is None and "잘려" in got.line()
