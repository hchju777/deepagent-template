"""팩트시트 — **비어 있으면 안 되는 칸**과 **비어 있어야 정상인 칸**.

이 파일이 지키는 것은 렌더링이 아니라 계약이다: 문제가 있으면 반드시 이슈 절에
나타나고, 없으면 그 줄이 아예 없어야 한다. "표본이 잘렸다"가 빠지면 리포트가
"어제 5만 건"이라고 단정하는데 실제로는 그 이상이다.
"""
from src.report.facts import SiteOutcome
from src.report.rows import RowProblems, normalize
from src.report.sheet import fact_sheet

from tests.support import YESTERDAY, doc, facts_from

SECTIONS = ["① 총 알람", "② 미해제", "③ GBM별", "④ 법인별", "⑤ 일별 추세",
            "⑥ 법인 TOP", "⑦ 라인 TOP", "⑧ 알람 항목 TOP", "⑨ status 분포",
            "⑩ 급증(전주 동요일 대비)", "⑪ 반복 알람", "⑫ 알람 항목 신규·소멸", "⑬ 이슈"]


def build(documents, *, source, window, sites=()):
    rows, problems = normalize(documents, source=source, window=window,
                               gbm="mx", fct="gumi")
    if not sites:
        sites = (SiteOutcome(gbm="mx", fct="gumi", status="ok", fetched=len(documents),
                             kept=len(rows), problems=problems),)
    return facts_from(rows, window=window, source=source, sites=sites)


def test_모든_섹션이_나온다(source, window):
    sheet = fact_sheet(build([doc(YESTERDAY)] * 3, source=source, window=window))
    assert list(sheet)[1:] == SECTIONS


def test_데이터가_하나도_없어도_섹션이_사라지지_않는다(source, window):
    """섹션이 사라지면 읽는 사람은 "그 항목은 원래 없는 리포트"라고 읽는다."""
    sheet = fact_sheet(build([], source=source, window=window))
    assert list(sheet)[1:] == SECTIONS
    assert sheet["① 총 알람"]["건수"] == 0
    assert len(sheet["⑤ 일별 추세"]) == len(window.days)


def test_문제가_없으면_이슈_절이_거의_비어_있다(source, window):
    issues = fact_sheet(build([doc(YESTERDAY)], source=source, window=window))["⑬ 이슈"]
    assert list(issues) == ["읽은 양"], "정상일 때 이슈가 붙으면 진짜 이슈가 묻힌다"


def test_잘린_표본은_하한이라고_말한다(source, window):
    facts = build([doc(YESTERDAY)], source=source, window=window, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok", complete=False,
                    truncated_reason="limit=1에 걸림 — 더 있다"),))
    issues = fact_sheet(facts)["⑬ 이슈"]
    key = next(k for k in issues if "잘린" in k)
    assert "하한" in key
    assert issues[key][0]["사이트"] == "mx/gumi"
    assert fact_sheet(facts)["기준"]["표본이 완전한가"] is False


def test_읽지_못한_법인이_이름으로_남는다(source, window):
    facts = build([], source=source, window=window, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="mx", fct="sevt", status="error", error="타임아웃")))
    listed = fact_sheet(facts)["⑬ 이슈"]["읽지 못한 법인"]
    assert listed == [{"사이트": "mx/sevt", "상태": "error", "이유": "타임아웃"}]


def test_데이터_품질_문제가_이슈로_올라간다(source, window):
    """건수만이 아니라 **실제 값**이 함께 실려야 다음 행동이 정해진다."""
    facts = build([doc(YESTERDAY, status="?")], source=source, window=window)
    quality = fact_sheet(facts)["⑬ 이슈"]["데이터 품질"]
    assert quality == ["status가 정수가 아닌 문서 1건 — 실제 값: '?'"]


def test_읽은_양에_버린_이유가_붙는다(source, window):
    """"받아온 13,179 − 쓴 973"의 차이를 사람이 직접 맞춰 볼 수 있어야 한다."""
    facts = build([doc(YESTERDAY), {"occ_date": "2026-09-04T09:00:00"}],
                  source=source, window=window)
    volume = fact_sheet(facts)["⑬ 이슈"]["읽은 양"][0]
    assert volume["받아온 문서"] == 2 and volume["집계에 쓴 행"] == 1
    assert volume["버린 이유"] == {"날짜 형식이 안 맞음": 1}
    assert "설명되지 않은 차이" not in volume


def test_설명되지_않는_차이는_숨기지_않는다(source, window):
    """세지 않는 탈락 경로가 생기면 드러나야 다음 사람이 그것을 찾는다."""
    facts = build([doc(YESTERDAY)], source=source, window=window, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok", fetched=100, kept=1,
                    problems=RowProblems(unreadable_date=5, date_samples=("'x'",))),))
    volume = fact_sheet(facts)["⑬ 이슈"]["읽은 양"][0]
    assert volume["설명되지 않은 차이"] == 94


def test_읽은_양은_항상_남는다(source, window):
    """받아온 문서와 집계에 쓴 행이 다르면 그 차이가 설명되어야 한다."""
    read = fact_sheet(build([doc(YESTERDAY)] * 3, source=source, window=window))
    assert read["⑬ 이슈"]["읽은 양"] == [
        {"사이트": "mx/gumi", "받아온 문서": 3, "집계에 쓴 행": 3}]


def test_JSON으로_찍힌다(source, window):
    """CLI가 json.dumps로 내보낸다 — 여기서 직렬화가 안 되면 명령이 죽는다."""
    import json

    sheet = fact_sheet(build([doc(YESTERDAY)] * 2, source=source, window=window))
    assert json.loads(json.dumps(sheet, ensure_ascii=False, default=str))
