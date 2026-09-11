"""블록 — **숫자가 텍스트로 바뀌는 지점.** HTML은 보지 않는다.

여기서 보는 것 셋:
  ① 섹션을 더하거나 빼도 나머지가 모른다(`BLOCKS`가 단일 진실 소스)
  ② 데이터가 0건이어도 섹션이 사라지지 않는다
  ③ 증감을 **색 없이** 읽을 수 있다 — 메일 다크모드는 색을 강제로 반전시킨다
"""
from datetime import date

import pytest

from src.config.schema_report import Thresholds
from src.report.blocks import (BLOCKS, NBSP, Block, Cell, Column, EMDASH, Table,
                               build_blocks, delta, n, pct, tight, upper)
from src.report.facts import SiteOutcome
from src.report.rows import normalize

from tests.support import YESTERDAY, doc, facts_from

# 항상 남아야 하는 섹션. 사라지면 읽는 사람은 "그 항목은 원래 없는 리포트"로 읽는다.
# 처리 상태 분포는 뺐다 — 미해제율은 KPI 타일이 이미 말하고, 같은 숫자를 두 번
# 보여 주면 읽는 사람이 어느 쪽을 봐야 하는지 고민한다.
STANDING = {"header", "tiles", "comment", "gbm", "trend", "issues",
            "plant", "scenario", "line", "sites"}


def build(documents, *, source, window, thresholds=None, sites=()):
    rows, problems = normalize(documents, source=source, window=window, gbm="mx", fct="gumi")
    if not sites:
        sites = (SiteOutcome(gbm="mx", fct="gumi", status="ok", fetched=len(documents),
                             kept=len(rows), problems=problems),)
    return facts_from(rows, window=window, source=source, thresholds=thresholds, sites=sites)


def keys(facts) -> list[str]:
    return [b.key for b in build_blocks(facts)]


# ── ① BLOCKS가 단일 진실 소스인가 ───────────────────────────────────

def test_블록_키가_겹치지_않는다(source, window):
    facts = build([doc(YESTERDAY)], source=source, window=window)
    produced = keys(facts)
    assert len(produced) == len(set(produced))


def test_순서가_BLOCKS의_순서다(source, window):
    """렌더러는 순회만 한다 — 순서를 따로 들고 있으면 둘이 어긋난다."""
    facts = build([doc(YESTERDAY)], source=source, window=window)
    declared = [b(facts).key for b in BLOCKS]
    assert keys(facts) == [k for k in declared if k != "coverage"]


@pytest.mark.parametrize("builder", BLOCKS, ids=lambda b: b.__name__)
def test_블록은_혼자서도_만들어진다(builder, source, window):
    """블록 하나를 빼도 나머지가 안 깨지려면 서로를 모르고 있어야 한다."""
    facts = build([doc(YESTERDAY)] * 3, source=source, window=window)
    block = builder(facts)
    assert isinstance(block, Block) and block.key


def test_키가_겹치면_바로_실패한다(source, window):
    """겹친 키는 렌더러가 둘 중 하나만 그리거나 두 번 그리게 만든다."""
    import src.report.blocks as module

    facts = build([], source=source, window=window)
    original = module.BLOCKS
    module.BLOCKS = (original[0], original[0])
    try:
        with pytest.raises(ValueError, match="겹친다"):
            build_blocks(facts)
    finally:
        module.BLOCKS = original


# ── ② 비어 있어도 사라지지 않는다 ───────────────────────────────────

def test_데이터가_없어도_섹션이_남는다(source, window):
    facts = build([], source=source, window=window)
    assert STANDING <= set(keys(facts))


def test_비어_있는_섹션은_대체_문구를_갖는다(source, window):
    facts = build([], source=source, window=window)
    for block in build_blocks(facts):
        if not block.has_content:
            assert block.empty or block.key == "header", \
                f"{block.key}가 비어 있는데 보여 줄 문구가 없다"


def test_경고_배너는_정상일_때_사라진다(source, window):
    """있을 때만 의미가 있는 것이다 — 항상 있으면 사람이 안 읽는다."""
    clean = build([doc(YESTERDAY)], source=source, window=window)
    assert "coverage" not in keys(clean)


def test_읽지_못한_법인이_있으면_맨_위에_경고가_붙는다(source, window):
    """아래 모든 숫자가 일부 법인만의 합이라는 사실을 숫자보다 먼저 봐야 한다."""
    facts = build([doc(YESTERDAY)], source=source, window=window, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="mx", fct="sevt", status="error", error="타임아웃")))
    produced = keys(facts)
    assert produced[1] == "coverage", "머리말 바로 다음이어야 한다"
    banner = build_blocks(facts)[1].banners[0]
    assert "1 / 2 법인" in banner.text and "MX/SEVT" in banner.text


def test_표본이_잘리면_하한이라고_경고한다(source, window):
    facts = build([doc(YESTERDAY)], source=source, window=window, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok", complete=False,
                    truncated_reason="limit=1에 걸림"),))
    banners = build_blocks(facts)[1].banners
    assert any("하한" in b.text for b in banners)


def test_조회_범위_표는_정상일_때도_남는다(source, window):
    """이상할 때만 나타나는 표는, 없을 때 "괜찮다"인지 "확인을 안 했다"인지
    구별해 주지 않는다."""
    facts = build([doc(YESTERDAY)], source=source, window=window)
    sites = next(b for b in build_blocks(facts) if b.key == "sites")
    assert sites.table is not None and len(sites.table.rows) == 1


# ── ③ 색 없이 읽히는가 ──────────────────────────────────────────────

def test_증감에_화살표가_붙는다():
    """메일 다크모드는 색을 강제로 반전시킨다 — 화살표는 반전돼도 화살표다."""
    up, tone = delta(150, 100)
    assert up == f"▲{NBSP}50.0%" and tone == "bad"
    down, tone = delta(50, 100)
    assert down == f"▼{NBSP}50.0%" and tone == "good"


def test_화살표와_숫자가_줄바꿈으로_갈라지지_않는다():
    """갈라지면 방향을 읽을 수 없고, 색이 반전되는 다크모드에서는 화살표가
    유일한 신호다. 한국어 줄바꿈은 기본적으로 아무 데서나 끊긴다."""
    text, _ = delta(150, 100)
    assert " " not in text, f"보통 공백이 남아 있다 — {text!r}"
    assert NBSP in text


def test_끊기면_안_되는_구절만_붙인다():
    assert tight("7 평일에 걸쳐") == f"7{NBSP}평일에{NBSP}걸쳐"
    assert tight("단어") == "단어"


def test_기준이_0이면_배수_대신_신규다():
    """`+∞%`를 리포트에 실을 수는 없다."""
    assert delta(20, 0) == ("신규", "bad")
    assert delta(0, 0) == (EMDASH, "muted")


def test_기준이_없으면_0이_아니라_모른다다():
    """0건과 "보존 기간 밖이라 모른다"는 완전히 다른 사실이다."""
    assert delta(20, None) == (EMDASH, "muted")
    assert n(None) == EMDASH and n(0) == "0"


def test_변화가_없으면_화살표를_붙이지_않는다():
    assert delta(100, 100) == ("0.0%", "muted")


def test_법인_이름은_대문자다():
    """문서의 값은 `gumi`처럼 소문자인데 GBM은 `MX`로 쓰므로, 한 표 안에서 두 층의
    표기가 어긋난다. 한국어 법인명에는 영향이 없다."""
    assert upper("gumi") == "GUMI" and upper("mx/sevt") == "MX/SEVT"
    assert upper("구미") == "구미"


def test_천단위_구분과_소수():
    assert n(1234567) == "1,234,567"
    assert n(12.0) == "12" and n(12.34) == "12.3"
    assert pct(1, 3) == "33.3%" and pct(1, 0) == EMDASH


# ── 표의 모양 ───────────────────────────────────────────────────────

def test_칸_수가_열_수와_다르면_즉시_실패한다():
    """어긋나면 표가 조용히 밀려서 **다른 열의 숫자**로 읽힌다."""
    with pytest.raises(ValueError, match="칸이"):
        Table(columns=(Column("a"), Column("b")), rows=((Cell("1"),),))


def test_합계_행도_열_수를_지킨다():
    with pytest.raises(ValueError, match="합계"):
        Table(columns=(Column("a"),), rows=(), total=(Cell("1"), Cell("2")))


def test_GBM_요약의_합계가_행들의_합과_맞는다(source, window):
    documents = ([doc(YESTERDAY, plant="gumi")] * 4
                 + [doc(YESTERDAY, plant="sevt")] * 6)
    facts = build(documents, source=source, window=window)
    table = next(b for b in build_blocks(facts) if b.key == "gbm").table
    assert table.total[1].text == "10"


def test_일별_추이는_0건인_날도_행으로_남는다(source, window):
    """없는 행을 빼면 읽는 사람은 "그날이 존재하지 않았다"고 본다."""
    facts = build([doc(YESTERDAY)], source=source, window=window)
    trend = next(b for b in build_blocks(facts) if b.key == "trend")
    assert len(trend.table.rows) == len(window.days)


def test_데이터_품질은_조회_범위_섹션의_목록으로_간다(source, window):
    facts = build([doc(YESTERDAY, status="?")], source=source, window=window)
    sites = next(b for b in build_blocks(facts) if b.key == "sites")
    assert any("status" in line for line in sites.bullets)


# ── GBM별 TOP과 정체성 색 ───────────────────────────────────────────

def two_gbm(source, window, *, mx: int, da: int):
    """MX가 압도적으로 많은 상황. 전사 TOP이면 DA가 한 줄도 안 들어왔다."""
    mx_rows, _ = normalize([doc(YESTERDAY, plant="gumi", line="P1", line_name="조립1")] * mx,
                           source=source, window=window, gbm="mx", fct="gumi")
    da_rows, _ = normalize([doc(YESTERDAY, plant="gwangju", line="G1",
                                line_name="냉장1")] * da,
                           source=source, window=window, gbm="da", fct="gwangju")
    return facts_from(mx_rows + da_rows, window=window, source=source, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="da", fct="gwangju", status="ok")), gbms=("mx", "da"))


@pytest.mark.parametrize("key", ["plant", "scenario", "line"])
def test_TOP_표에_모든_GBM이_들어온다(key, source, window):
    facts = two_gbm(source, window, mx=100, da=2)
    table = next(b for b in build_blocks(facts) if b.key == key).table
    shown = [c.text for row in table.rows for c in row[:1] if c.text]
    assert shown == ["MX", "DA"], f"{key}: 적은 GBM이 밀려났다 — {shown}"


def test_라인_TOP에_작은_GBM의_법인이_나온다(source, window):
    """전사 TOP 10이었을 때 gwangju가 11위 밖으로 밀려 한 줄도 못 나왔다."""
    facts = two_gbm(source, window, mx=100, da=2)
    table = next(b for b in build_blocks(facts) if b.key == "line").table
    assert any("GWANGJU" in row[1].text for row in table.rows)


def test_GBM_이름에만_계열색이_붙는다(source, window):
    """색은 정체성(어느 GBM)을 나타내고 의미(증감)를 나타내지 않는다."""
    facts = two_gbm(source, window, mx=3, da=2)
    table = next(b for b in build_blocks(facts) if b.key == "plant").table
    first, second = table.rows[0], table.rows[1]
    assert first[0].series == 0 and second[0].series == 1
    assert all(cell.series is None for cell in first[1:]), "숫자 칸에는 색을 붙이지 않는다"


def test_같은_GBM의_둘째_행부터는_이름을_비운다(source, window):
    """같은 이름을 다섯 번 반복하면 눈이 그걸 읽느라 다른 열의 차이를 못 본다."""
    rows, _ = normalize([doc(YESTERDAY, plant="gumi")] * 3
                        + [doc(YESTERDAY, plant="sevt")] * 2,
                        source=source, window=window, gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, gbms=("mx",))
    table = next(b for b in build_blocks(facts) if b.key == "plant").table
    assert table.rows[0][0].text == "MX" and table.rows[1][0].text == ""
    assert [r[1].text for r in table.rows] == ["GUMI", "SEVT"]
    assert 0 in table.group_starts, "묶음 경계를 표시해야 빈 칸이 '값 없음'으로 안 읽힌다"


def test_묶음_경계가_GBM이_바뀌는_자리다(source, window):
    facts = two_gbm(source, window, mx=3, da=2)
    table = next(b for b in build_blocks(facts) if b.key == "plant").table
    assert table.group_starts == frozenset({0, 1}), "GBM이 둘이고 각 1행"


def test_처리_상태_분포_블록은_없다(source, window):
    facts = two_gbm(source, window, mx=3, da=2)
    assert "status" not in keys(facts)


# ── 이슈 표의 열 ────────────────────────────────────────────────────

def test_이슈_표가_GBM과_법인을_따로_갖는다(source, window):
    """한 칸에 "gumi · P222 조립2라인"처럼 합쳐 넣으면 GBM이 안 보인다."""
    facts = two_gbm(source, window, mx=3, da=2)
    issues = next(b for b in build_blocks(facts) if b.key == "issues")
    assert [c.label for c in issues.table.columns] == [
        "유형", "GBM", "법인", "대상", "어제", "평균", "증감"]


def test_반복_행이_GBM_법인_대상으로_나뉜다(source, window):
    from src.config.schema_report import Thresholds

    rows, _ = normalize([doc(d, plant="gumi", line="P222", line_name="조립2라인")
                         for d in window.days for _ in range(2)],
                        source=source, window=window, gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, gbms=("mx",),
                       thresholds=Thresholds(repeat_min_count=5, repeat_min_days=3))
    table = next(b for b in build_blocks(facts) if b.key == "issues").table
    row = next(r for r in table.rows if r[0].text == "반복")
    assert row[1].text == "MX" and row[2].text == "GUMI"
    assert "P222" in row[3].text and "재고 불일치" in row[3].text
    assert row[3].hint == f"S01 · 7{NBSP}평일에{NBSP}걸쳐", \
        "항목 id가 먼저 오고, 짧은 구절은 갈라지지 않는다"


def test_급증_행이_GBM_법인_항목을_갖는다(source, window):
    earlier = [d for d in window.days if d != YESTERDAY]
    documents = ([doc(YESTERDAY, plant="gumi", scen_name="설비 신호 끊김")] * 40
                 + [doc(d, plant="gumi", scen_name="설비 신호 끊김") for d in earlier])
    rows, _ = normalize(documents, source=source, window=window, gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, gbms=("mx",))
    table = next(b for b in build_blocks(facts) if b.key == "issues").table
    row = next(r for r in table.rows if r[0].text == "급증")
    assert row[1].text == "MX" and row[2].text == "GUMI"
    assert row[3].text == "설비 신호 끊김"
    assert row[3].hint == "S01", "리포트를 받은 사람이 다음에 하는 일이 이 id로 조회다"


# ── 항목 id와 유형별 상한 ───────────────────────────────────────────

def issue_table(facts):
    return next(b for b in build_blocks(facts) if b.key == "issues")


def test_이슈_표의_모든_항목_행이_항목_id를_갖는다(source, window):
    """이름만 있으면 받은 사람이 대상 시스템에서 찾을 키가 없다. 이름이 같고 id가
    다른 두 항목이 한 줄로 합쳐지는 것도 막는다."""
    from src.config.schema_report import Thresholds

    earlier = [d for d in window.days if d != YESTERDAY]
    documents = (
        [doc(YESTERDAY, scen="S02", scen_name="급증항목")] * 40          # 급증
        + [doc(d, scen="S02", scen_name="급증항목") for d in earlier]
        + [doc(YESTERDAY, scen="S09", scen_name="신규항목")]             # 신규
        + [doc(d, scen="S08", scen_name="소멸항목") for d in earlier]     # 소멸
        + [doc(d, scen="S03", scen_name="반복항목", line="P9",
               line_name="9라인") for d in window.days for _ in range(2)])
    rows, _ = normalize(documents, source=source, window=window, gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, gbms=("mx",),
                       thresholds=Thresholds(repeat_min_count=5, repeat_min_days=3))
    table = issue_table(facts).table

    for row in table.rows:
        kind = row[0].text
        if kind == "데이터":
            continue          # 시나리오가 없는 유형이다
        assert row[3].hint, f"{kind} 행에 항목 id가 없다 — {row[3].text!r}"
        assert row[3].hint.startswith("S"), f"{kind}: {row[3].hint!r}"


def test_유형별로_상한을_넘으면_생략하고_그_사실을_말한다(source, window):
    """조용히 자르면 "이슈가 이것뿐"이라는 거짓이 된다."""
    from src.config.schema_report import Thresholds

    earlier = [d for d in window.days if d != YESTERDAY]
    documents = []
    for index in range(9):                     # 신규 항목 9개 — 상한 5를 넘긴다
        documents.append(doc(YESTERDAY, scen=f"S{index + 20}", scen_name=f"신규{index}"))
    documents += [doc(d) for d in earlier]
    rows, _ = normalize(documents, source=source, window=window, gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, gbms=("mx",),
                       thresholds=Thresholds(top_n=5))
    block = issue_table(facts)

    shown = [r for r in block.table.rows if r[0].text == "신규"]
    assert len(shown) == 5, "유형별 상한이 안 걸렸다"
    assert block.lead and "신규 4건" in block.lead and "생략" in block.lead
    assert "report aggregate" in block.lead, "전체를 어디서 보는지 말해야 한다"


def test_한_유형이_표를_독차지하지_않는다(source, window):
    """법인이 늘면 반복 행이 법인 수에 비례해 불어난다 — 그것이 급증·신규를
    밀어내면 "오늘 뭐가 이상한가"의 답이 한쪽으로 기울어진다."""
    from src.config.schema_report import Thresholds

    earlier = [d for d in window.days if d != YESTERDAY]
    documents = [doc(YESTERDAY, scen="S02", scen_name="급증항목")] * 40
    documents += [doc(d, scen="S02", scen_name="급증항목") for d in earlier]
    for index in range(12):                    # 반복 후보 12개
        documents += [doc(d, line=f"L{index}", line_name=f"{index}라인",
                          scen="S03", scen_name="반복항목")
                      for d in window.days for _ in range(2)]
    rows, _ = normalize(documents, source=source, window=window, gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, gbms=("mx",),
                       thresholds=Thresholds(top_n=5, repeat_min_count=5,
                                             repeat_min_days=3))
    table = issue_table(facts).table
    kinds = [r[0].text for r in table.rows]
    assert kinds.count("반복") == 5
    assert "급증" in kinds, "반복이 표를 독차지했다"


def test_상한_안에_들어오면_생략_문구가_없다(source, window):
    facts = two_gbm(source, window, mx=3, da=2)
    assert issue_table(facts).lead is None


def test_제목의_건수는_생략된_것까지_센다(source, window):
    """표에 보이는 줄 수만 적으면 "5건"으로 보여서 생략을 못 알아챈다."""
    from src.config.schema_report import Thresholds

    earlier = [d for d in window.days if d != YESTERDAY]
    documents = [doc(YESTERDAY, scen=f"S{i + 30}", scen_name=f"신규{i}") for i in range(8)]
    documents += [doc(d) for d in earlier]
    rows, _ = normalize(documents, source=source, window=window, gbm="mx", fct="gumi")
    facts = facts_from(rows, window=window, source=source, gbms=("mx",),
                       thresholds=Thresholds(top_n=5))
    block = issue_table(facts)
    # 손으로 센 숫자를 박지 않는다 — 임계값을 건드리면 그 숫자가 낡고, 테스트가
    # 무엇을 지키려 했는지 알 수 없게 된다. 성질만 단정한다.
    visible = len(block.table.rows)
    counted = int(block.hint.removesuffix("건"))
    assert counted > visible, f"보이는 {visible}줄만 세고 있다 — {block.hint}"
    assert counted == visible + 3, "생략한 신규 3건이 빠졌다"
