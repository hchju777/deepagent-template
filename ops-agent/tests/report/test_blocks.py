"""블록 — **숫자가 텍스트로 바뀌는 지점.** HTML은 보지 않는다.

여기서 보는 것 셋:
  ① 섹션을 더하거나 빼도 나머지가 모른다(`BLOCKS`가 단일 진실 소스)
  ② 데이터가 0건이어도 섹션이 사라지지 않는다
  ③ 증감을 **색 없이** 읽을 수 있다 — 메일 다크모드는 색을 강제로 반전시킨다
"""
from datetime import date

import pytest

from src.config.schema_report import Thresholds
from src.report.blocks import (BLOCKS, Block, Cell, Column, EMDASH, Table, build_blocks,
                               delta, n, pct)
from src.report.facts import SiteOutcome
from src.report.rows import normalize

from .conftest import YESTERDAY, doc, facts_from

# 항상 남아야 하는 섹션. 사라지면 읽는 사람은 "그 항목은 원래 없는 리포트"로 읽는다.
STANDING = {"header", "tiles", "comment", "gbm", "trend", "issues",
            "plant", "scenario", "line", "status", "sites"}


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
    assert "1 / 2 법인" in banner.text and "mx/sevt" in banner.text


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
    assert up == "▲ 50.0%" and tone == "bad"
    down, tone = delta(50, 100)
    assert down == "▼ 50.0%" and tone == "good"


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
