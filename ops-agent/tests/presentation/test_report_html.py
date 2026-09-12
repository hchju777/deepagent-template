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

from tests.support import YESTERDAY, doc, facts_from

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
    assert "MX/SEVT" in html and "연결 거부" in html
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


# ── GBM 정체성 색 ───────────────────────────────────────────────────

def test_GBM_글씨에_계열색이_나간다(source, window):
    """색은 **정체성**이다 — 다크모드로 반전돼도 "MX가 VD와 다른 색"만 유지되면 된다."""
    from src.presentation.report_html import SERIES
    from src.report.facts import SiteOutcome as SO

    mx, _ = normalize([doc(YESTERDAY, plant="gumi")] * 3, source=source,
                      window=window, gbm="mx", fct="gumi")
    da, _ = normalize([doc(YESTERDAY, plant="gwangju")] * 2, source=source,
                      window=window, gbm="da", fct="gwangju")
    facts = facts_from(mx + da, window=window, source=source,
                       sites=(SO(gbm="mx", fct="gumi", status="ok"),
                              SO(gbm="da", fct="gwangju", status="ok")),
                       gbms=("mx", "da"))
    html = html_of(facts)
    assert f"color:{SERIES[0]};" in html and f"color:{SERIES[1]};" in html
    assert re_search(html, SERIES[0], "MX"), "MX 글씨에 첫 계열색이 붙어야 한다"
    assert re_search(html, SERIES[1], "DA")


def re_search(html: str, color: str, label: str) -> bool:
    import re
    return bool(re.search(rf"color:{re.escape(color)};[^>]*>{label}<", html))


def test_묶음_경계에_윗선이_그어진다(source, window):
    """GBM 이름을 둘째 행부터 비우므로, 선이 없으면 빈 칸이 "값 없음"으로 읽힌다."""
    from src.report.blocks import Block, Cell, Column, Table
    from src.presentation.report_html import HEAD_RULE

    table = Table(columns=(Column("a"), Column("b")),
                  rows=((Cell("MX"), Cell("1")), (Cell(""), Cell("2")),
                        (Cell("DA"), Cell("3"))),
                  group_starts=frozenset({0, 2}))
    html = render([Block(key="x", title="x", table=table)], title="t", generated_at="x")
    assert html.count(f"border-top:2px solid {HEAD_RULE}") == 2, \
        "첫 행은 머리글 아래라 선이 필요 없고, 둘째 묶음에만 그어진다"


def test_계열색이_의미색을_이긴다():
    """GBM 이름 칸은 정체성이 의미보다 앞선다 — tone과 겹치면 series가 이긴다."""
    from src.report.blocks import Block, Cell, Column, Table
    from src.presentation.report_html import BAD, SERIES

    table = Table(columns=(Column("a"),), rows=((Cell("MX", tone="bad", series=0),),))
    html = render([Block(key="x", title="x", table=table)], title="t", generated_at="x")
    assert f"color:{SERIES[0]};" in html and f"color:{BAD};" not in html


# ── 한국어 줄바꿈 ───────────────────────────────────────────────────

def test_칸에_한국어_줄바꿈_규칙이_붙는다(source, window):
    """브라우저 기본값은 한글을 음절 단위로 끊어서 "7 평일에 걸쳐"가 "7 평"/"일에
    걸쳐"로 갈라진다. `keep-all`로 공백에서만 끊고, `break-word`로 공백 없는 아주
    긴 낱말이 칸을 넘치는 것을 막는다."""
    html = html_of(build([doc(YESTERDAY)], source=source, window=window))
    assert "word-break:keep-all" in html
    assert "overflow-wrap:break-word" in html


def test_줄바꿈_금지_공백이_그대로_살아_나간다(source, window):
    """`html.escape`가 U+00A0을 건드리면 `&amp;nbsp;`가 되거나 보통 공백으로 뭉개진다.
    Outlook의 Word 엔진은 CSS를 무시할 수 있으므로 이 문자가 유일한 보장이다."""
    earlier = [d for d in window.days if d != YESTERDAY]
    documents = [doc(YESTERDAY)] * 30 + [doc(d) for d in earlier for _ in range(5)]
    html = html_of(build(documents, source=source, window=window))
    assert "\u00a0" in html, "줄바꿈 금지 공백이 사라졌다"
    assert "&nbsp;amp;" not in html and "&amp;nbsp;" not in html


# ── 차트가 이미지로 나가는가 ────────────────────────────────────────

def chart_html(source, window):
    from src.report.facts import SiteOutcome as SO

    mx, _ = normalize([doc(YESTERDAY, plant="gumi")] * 30, source=source,
                      window=window, gbm="mx", fct="gumi")
    da, _ = normalize([doc(YESTERDAY, plant="gwangju")] * 3, source=source,
                      window=window, gbm="da", fct="gwangju")
    facts = facts_from(mx + da, window=window, source=source,
                       sites=(SO(gbm="mx", fct="gumi", status="ok"),
                              SO(gbm="da", fct="gwangju", status="ok")),
                       gbms=("mx", "da"))
    return html_of(facts)


def test_차트가_base64_PNG로_들어간다(source, window):
    """인라인 SVG는 Outlook에서 안 보이고, 외부 이미지는 차단된다 — data URI는
    사내 메일 경로에서 동작하는 것이 확인됐다."""
    import base64
    import re

    html = chart_html(source, window)
    found = re.findall(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"', html)
    assert len(found) == 2, f"판 2개에 이미지 2개가 나와야 한다 — {len(found)}개"
    for encoded in found:
        raw = base64.b64decode(encoded)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n", "PNG 서명이 아니다"


def test_이미지_크기를_속성으로도_준다(source, window):
    """Word 엔진은 CSS 크기를 무시할 때가 있다 — 그러면 2배로 그린 그림이
    2배 크기로 표시돼서 본문을 밀어낸다."""
    from src.presentation.report_html import PLOT_W

    html = chart_html(source, window)
    assert f'width="{PLOT_W}"' in html and f"width:{PLOT_W}px" in html
    assert 'height="92"' in html and "height:92px" in html


def test_이미지가_막혀도_읽을_것이_남는다(source, window):
    """alt와 바로 아래 숫자 표가 남는다. alt가 비면 차단된 자리에 아무것도 없다."""
    html = chart_html(source, window)
    assert 'alt="MX 일별 추이' in html
    assert "30" in html, "같은 숫자가 표에도 있어야 한다"


def test_판마다_최댓값이_찍힌다(source, window):
    """y축이 판마다 다르므로, 최댓값이 없으면 높이를 읽을 근거가 없다."""
    html = chart_html(source, window)
    assert "최대 30" in html and "최대 3" in html


def test_y축_경고가_본문에_실린다(source, window):
    assert "비교하면 안 된다" in chart_html(source, window)


def test_차트가_있어도_태그_짝이_맞는다(source, window):
    checker = Balance()
    checker.feed(chart_html(source, window))
    assert checker.problems == [] and checker.stack == []


def test_이미지에_테두리가_안_생긴다(source, window):
    """일부 클라이언트는 img에 파란 테두리를 붙인다 — 링크처럼 보인다."""
    html = chart_html(source, window)
    assert "border:0;outline:none;text-decoration:none;" in html


def test_본문에_들어가는_차트는_투명하다(source, window):
    """불투명이면 클라이언트가 강제하는 다크모드에서 어두운 본문 위에 **흰 판**이
    뜬다 — 이미지는 반전되지 않기 때문이다."""
    import base64
    import re

    html = chart_html(source, window)
    for encoded in re.findall(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"', html):
        png = base64.b64decode(encoded)
        assert png[25] == 6, "알파 트루컬러(6)가 아니다 — 배경이 불투명하다"


def test_차트_색은_본문_색을_그대로_쓰지_않는다():
    """본문 색은 라이트 바탕만 보고 고른 것이라 다크에서 무너진다 —
    #5b6270은 라이트 6.08 / 다크 2.90이다."""
    from src.presentation.report_html import CHART_LABEL, DIM, FAINT, HEAD_RULE

    assert CHART_LABEL != DIM and CHART_LABEL not in (FAINT, HEAD_RULE)
