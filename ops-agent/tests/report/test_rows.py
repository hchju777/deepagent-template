"""문서 → 행. **버린 것을 세는가**가 이 파일의 주제다.

어제 건수가 0인 게 "현장이 조용해서"인지 "필드 이름이 바뀌어서"인지 구별하려면,
버린 문서를 종류별로 세야 한다. 조용히 버리면 그 구별이 영원히 불가능해진다.
"""
from datetime import date, datetime

from src.report.rows import MISSING, normalize, projection

from .conftest import YESTERDAY, doc


def run(documents, *, source, window, gbm="mx", fct="gumi"):
    return normalize(documents, source=source, window=window, gbm=gbm, fct=fct)


def test_정상_문서가_행이_된다(source, window):
    rows, problems = run([doc(YESTERDAY)], source=source, window=window)
    assert len(rows) == 1
    row = rows[0]
    assert row.day == YESTERDAY
    assert row.occurred_at == datetime(2026, 9, 4, 9)
    assert row.line == ("P222", "조립2라인")
    assert row.scenario == ("S01", "재고 불일치")
    assert row.unresolved is True, "status=0은 미해제다"
    assert problems.describe() == []


def test_해제된_알람은_미해제가_아니다(source, window):
    rows, _ = run([doc(YESTERDAY, status=40)], source=source, window=window)
    assert rows[0].unresolved is False


def test_사이트가_정한_gbm이_이긴다(source, window):
    """문서의 gbm을 믿으면, 한 법인의 Mongo에 다른 법인 데이터가 섞여 있을 때
    그대로 따라간다. 어긋난 것은 세기만 한다."""
    rows, problems = run([doc(YESTERDAY, gbm="vd")], source=source, window=window)
    assert rows[0].gbm == "mx"
    assert problems.gbm_mismatch == 1


def test_주말_문서는_버리고_센다(source, window):
    saturday = date(2026, 8, 29)
    rows, problems = run([doc(saturday)], source=source, window=window)
    assert rows == []
    assert problems.not_wanted == 1
    assert problems.describe() == [], "주말은 정상이므로 이슈로 올리지 않는다"


def test_전주_동요일_문서는_남긴다(source, window):
    """`selected`로 거르면 전주 비교값이 통째로 사라진다 — 비교 칸이 항상 빈다."""
    rows, _ = run([doc(date(2026, 8, 28))], source=source, window=window)
    assert len(rows) == 1


def test_범위_밖_문서는_신호다(source, window):
    """범위 필터가 서버에서 걸렀는데도 범위 밖이 오면, 문자열 날짜 비교가
    의도대로 안 되고 있다는 뜻이다."""
    rows, problems = run([doc(date(2026, 1, 5))], source=source, window=window)
    assert rows == []
    assert problems.outside_range == 1
    assert "조회 범위 밖" in problems.describe()[0]


def test_날짜를_못_읽으면_행이_아니라_숫자가_된다(source, window):
    docs = [{"occ_date": "2026-08-25 25:99:99", "status": 0},   # 사전순으론 범위 안
            {"occ_date": "어제", "status": 0},
            {"status": 0}]
    rows, problems = run(docs, source=source, window=window)
    assert rows == []
    assert problems.unreadable_date == 2
    assert problems.missing_date == 1
    assert any("date_format" in line for line in problems.describe())


def test_필드가_없으면_표시를_남기고_센다(source, window):
    rows, problems = run([{"occ_date": "2026-09-04 09:00:00"}], source=source, window=window)
    assert rows[0].line_code == MISSING and rows[0].scenario_name == MISSING
    assert problems.missing_fields["line_code"] == 1
    assert rows[0].plant == "gumi", "plant가 없으면 사이트 이름으로 대체한다"


def test_status가_문자열이어도_읽는다(source, window):
    """문서에 "0"처럼 문자열로 들어 있는 경우가 있다."""
    rows, problems = run([doc(YESTERDAY, status="10")], source=source, window=window)
    assert rows[0].status == 10 and rows[0].unresolved is True
    assert problems.unreadable_status == 0


def test_status가_정수가_아니면_미해제로_치지_않는다(source, window):
    rows, problems = run([doc(YESTERDAY, status="?")], source=source, window=window)
    assert rows[0].status is None
    assert rows[0].unresolved is False, "읽을 수 없는 것을 미해제로 세면 숫자가 늘어난다"
    assert problems.unreadable_status == 1


def test_bool은_status가_아니다(source, window):
    """파이썬에서 True는 int의 하위형이라 그대로 두면 status=1(조치시작)이 된다."""
    rows, problems = run([doc(YESTERDAY, status=True)], source=source, window=window)
    assert rows[0].status is None and problems.unreadable_status == 1


def test_문서가_아닌_것이_섞여도_죽지_않는다(source, window):
    rows, problems = run(["이건 문자열", None, doc(YESTERDAY)], source=source, window=window)
    assert len(rows) == 1 and problems.missing_date == 2


def test_투영은_normalize가_읽는_필드를_전부_담는다(source, window):
    """두 벌로 적으면 필드를 추가할 때 한쪽만 고쳐서 값이 항상 (없음)이 된다."""
    fields = projection(source)
    sample = doc(YESTERDAY)
    narrowed = {k: v for k, v in sample.items() if k in set(fields)}
    rows, problems = run([narrowed], source=source, window=window)
    assert problems.missing_fields == {}, f"투영에서 빠진 필드가 있다 — {fields}"
    assert rows[0].line_code == "P222"


def test_필드_이름을_바꾸면_그대로_따라간다(window):
    """법인마다 필드명이 달라도 config만 고치면 된다."""
    from src.config.schema_report import SourceSpec

    other = SourceSpec(collection="event", date_field="created_at",
                       fields={"scenario_name": "event_name", "line_code": "eq_id"})
    rows, problems = normalize(
        [{"created_at": "2026-09-04 09:00:00", "event_name": "신호 끊김", "eq_id": "EQ7"}],
        source=other, window=window, gbm="mx", fct="gumi")
    assert rows[0].scenario_name == "신호 끊김" and rows[0].line_code == "EQ7"
    assert "created_at" in projection(other)
