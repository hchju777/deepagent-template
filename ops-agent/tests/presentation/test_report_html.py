"""HTML 렌더링 — **Outlook이 제약을 정하고, 대상 시스템의 데이터가 위협이다.**

여기서 보는 것 넷:
  ① 주입 — 라인 이름에 `<script>`나 `"`가 와도 깨지지 않는가
  ② Outlook 제약 — 외부 style·class·flex·grid가 섞여 들지 않았는가
  ③ 배경이 투명한 칸이 없는가 (클라이언트가 칠한 어두운 바탕에 어두운 글자가 앉는다)
  ④ 경고가 본문에 **반드시** 실리는가
"""
from html.parser import HTMLParser

import pytest

from src.presentation.report_html import PAGE, e, render
from src.report.blocks import (Banner, Block, Cell, Column, Table, Tile, build_blocks)
from src.report.facts import SiteOutcome
from src.report.rows import normalize

from ..report.conftest import YESTERDAY, doc, facts_from

VOID = {"meta", "br", "img", "hr", "input", "link"}


class Balance(HTMLParser):
    """태그 짝이 맞는가. Word 엔진은 안 닫힌 태그에서 표를 통째로 잃는다."""

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.problems.append(f"여분의 </{tag}>")
        elif self.stack[-1] != tag:
            self.problems.append(f"<{self.stack[-1]}> vs </{tag}>")
        else:
            self.stack.pop()


def html_of(facts) -> str:
    return render(build_blocks(facts), title="일일 알람 리포트",
                  generated_at="2026-09-07 08:00")


def build(documents, *, source, window, sites=()):
    rows, problems = normalize(documents, source=source, window=window, gbm="mx", fct="gumi")
    if not sites:
        sites = (SiteOutcome(gbm="mx", fct="gumi", status="ok", fetched=len(documents),
                             kept=len(rows), problems=problems),)
    return facts_from(rows, window=window, source=source, sites=sites)


# ── ① 주입 ──────────────────────────────────────────────────────────

MALICIOUS = '<script>alert(1)</script>" style="display:none'


def test_데이터의_꺾쇠가_태그가_되지_않는다(source, window):
    """라인 이름은 **대상 시스템의 데이터**다. 그대로 넣으면 태그가 된다."""
    facts = build([doc(YESTERDAY, line_name=MALICIOUS)], source=source, window=window)
    html = html_of(facts)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_데이터의_따옴표가_속성을_끊지_않는다(source, window):
    """`escape(quote=False)`면 `"`가 살아서 style 속성을 닫아 버린다 —
    그 뒤의 모든 스타일이 데이터로 조종된다."""
    facts = build([doc(YESTERDAY, line_name=MALICIOUS)], source=source, window=window)
    html = html_of(facts)
    assert 'style="display:none' not in html
    assert "&quot;" in html


def test_태그_짝이_맞는다(source, window):
    facts = build([doc(YESTERDAY, scen_name=MALICIOUS)] * 3, source=source, window=window)
    checker = Balance()
    checker.feed(html_of(facts))
    assert checker.problems == [] and checker.stack == []


def test_빈_데이터에서도_태그_짝이_맞는다(source, window):
    checker = Balance()
    checker.feed(html_of(build([], source=source, window=window)))
    assert checker.problems == [] and checker.stack == []


def test_이스케이프_문이_하나다():
    assert e('<a href="x">') == "&lt;a href=&quot;x&quot;&gt;"
    assert e(None) == "None", "숫자·None도 문자열로 바뀌어야 한다"


# ── ② Outlook 제약 ──────────────────────────────────────────────────

@pytest.mark.parametrize("forbidden, why", [
    ("<style", "외부 style 블록은 Word 엔진에서 무시될 수 있다"),
    ('class="', "클래스 선택자는 인라인이 아니라 외부 규칙을 전제한다"),
    ("display:flex", "Word 엔진은 flex를 모른다"),
    ("display:grid", "Word 엔진은 grid를 모른다"),
    ("float:", "Word 엔진의 float 해석이 브라우저와 다르다"),
    ("position:", "Word 엔진은 position을 무시한다"),
    ("@media", "상당수 클라이언트가 미디어 쿼리를 지우고 보낸다"),
])
def test_Word_엔진이_모르는_것을_쓰지_않는다(forbidden, why, source, window):
    html = html_of(build([doc(YESTERDAY)], source=source, window=window))
    assert forbidden not in html, why


def test_폭을_속성으로도_준다(source, window):
    """Word는 CSS width를 무시할 때가 있다 — 속성이 있어야 720px이 지켜진다."""
    html = html_of(build([doc(YESTERDAY)], source=source, window=window))
    assert 'width="720"' in html and "max-width:720px" in html


def test_배치가_table로_되어_있다(source, window):
    html = html_of(build([doc(YESTERDAY)], source=source, window=window))
    assert html.count('role="presentation"') >= 5


# ── ③ 다크모드에서 뭉개지지 않는가 ──────────────────────────────────

def test_본문_배경이_명시돼_있다(source, window):
    """투명하게 두면 클라이언트가 칠한 어두운 바탕 위에 어두운 글자가 앉는다."""
    html = html_of(build([doc(YESTERDAY)], source=source, window=window))
    assert f'background:{PAGE}' in html
    assert f'<body style="margin:0;padding:0;background:{PAGE};">' in html


def test_색이_유일한_신호가_아니다(source, window):
    """증감 칸에 화살표가 들어 있어야 색이 반전돼도 방향을 읽을 수 있다."""
    earlier = [d for d in window.days if d != YESTERDAY]
    documents = [doc(YESTERDAY)] * 30 + [doc(d) for d in earlier for _ in range(5)]
    html = html_of(build(documents, source=source, window=window))
    assert "▲" in html or "▼" in html


def test_color_scheme_메타를_넣는다(source, window):
    """막아 주지는 않지만, 이걸 보는 클라이언트는 자체 반전을 덜 공격적으로 한다."""
    html = html_of(build([doc(YESTERDAY)], source=source, window=window))
    assert 'name="color-scheme"' in html and 'name="supported-color-schemes"' in html


# ── ④ 경고가 본문에 실리는가 ────────────────────────────────────────

def test_읽지_못한_법인이_본문에_이름으로_실린다(source, window):
    """메일에 안 실리면 관리자는 그 숫자를 완전하다고 믿는다."""
    facts = build([doc(YESTERDAY)], source=source, window=window, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok"),
        SiteOutcome(gbm="mx", fct="sevt", status="error", error="연결 거부")))
    html = html_of(facts)
    assert "mx/sevt" in html and "연결 거부" in html
    assert "1 / 2 법인" in html


def test_표본이_잘렸다는_사실이_본문에_실린다(source, window):
    facts = build([doc(YESTERDAY)], source=source, window=window, sites=(
        SiteOutcome(gbm="mx", fct="gumi", status="ok", complete=False,
                    truncated_reason="limit=1에 걸림 — 더 있다"),))
    assert "하한" in html_of(facts)


def test_데이터_품질_문제가_본문에_실린다(source, window):
    facts = build([doc(YESTERDAY, status="?")], source=source, window=window)
    assert "status가 정수가 아닌" in html_of(facts)


def test_LLM이_숫자를_만들지_않는다는_사실을_본문이_말한다(source, window):
    """읽는 사람이 숫자의 출처를 의심할 여지를 남기지 않는다."""
    html = html_of(build([doc(YESTERDAY)], source=source, window=window))
    assert "LLM은 숫자를 만들지 않습니다" in html


# ── 조각 단위 ───────────────────────────────────────────────────────

def test_타일은_세_개씩_한_줄이다():
    tiles = tuple(Tile(label=f"L{i}", value=str(i)) for i in range(6))
    html = render([Block(key="t", tiles=tiles)], title="t", generated_at="x")
    assert html.count("<tr>") >= 2


def test_칸_정렬이_속성으로_나간다():
    table = Table(columns=(Column("a"), Column("b", "right")),
                  rows=((Cell("1"), Cell("2", "right")),))
    html = render([Block(key="x", title="x", table=table)], title="t", generated_at="x")
    assert 'align="right"' in html


def test_배너_문구의_별표를_벗긴다():
    """마크다운을 쓰지 않으므로 `**`가 그대로 보이면 안 된다."""
    block = Block(key="b", banners=(Banner(tone="bad", text="이건 **중요**합니다"),))
    html = render([block], title="t", generated_at="x")
    assert "**" not in html and "이건 중요합니다" in html
