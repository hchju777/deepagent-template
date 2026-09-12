"""블록 → 메일 본문 HTML. **Outlook이 제약을 전부 정한다.**

## 왜 table 레이아웃과 인라인 스타일만 쓰는가

Outlook 데스크톱은 HTML을 브라우저 엔진이 아니라 **Word 엔진**으로 그린다. flex,
grid, float, `position`, 외부 `<style>`의 클래스 선택자 상당수가 무시되거나 제멋대로
해석된다. 2026년에도 그렇다. 그래서:

- 배치는 중첩 `<table role="presentation">`
- 스타일은 전부 `style="..."` 인라인
- 폭은 `width="720"`을 **속성으로도** 준다(Word는 CSS width를 무시할 때가 있다)

이건 구식 HTML이 아니라 **대상 렌더러에 맞춘 코드**다. 브라우저에서 예쁘게 나오는
것을 고르면 정작 받는 사람의 Outlook에서 무너진다.

## 다크모드는 우리가 켜는 것이 아니다

메일 클라이언트의 다크모드는 **클라이언트가 강제로 색을 반전**시키는 것이고,
Outlook 데스크톱은 CSS로 막을 수 없다. `prefers-color-scheme` 미디어 쿼리는 상당수
클라이언트에서 지원되지 않고, 지원되는 곳에서도 강제 반전과 겹쳐 더 이상해진다.

그래서 이 렌더러는 **다크모드를 지원하려 하지 않는다.** 대신 반전돼도 망가지지
않게 만든다:

- 의미를 색에만 싣지 않는다 — 증감은 `▲`/`▼`와 부호가 말한다(`blocks.py`의 `delta`).
- 배경은 거의 흰색(`#fefefe`), 글자는 거의 검정 — 반전되면 거의 검정/거의 흰색이
  되어 대비가 유지된다. 중간 회색 배경에 중간 회색 글자를 쓰면 반전 후 뭉개진다.
- 투명 배경을 남기지 않는다. 칸마다 배경색을 명시하지 않으면 클라이언트가 칠한
  어두운 바탕 위에 어두운 글자가 올라간다.

## 주입

라인 이름·알람 항목 이름은 **대상 시스템의 데이터**다. 그대로 넣으면 `<`가 태그가
되고 `"`가 스타일 속성을 끊는다. 모든 텍스트는 `html.escape(quote=True)`를 거친다.
"""
from html import escape

from src.report.blocks import Banner, Block, Cell, Chart, ChartPanel, Table, Tile

# ── 팔레트 ──────────────────────────────────────────────────────────
# 검증된 값들이다(라이트 모드 대비 기준 통과). 계열색 넷은 GBM 구분용.
PAGE = "#fefefe"          # 거의 흰색 — 반전되면 거의 검정이 되어 대비가 유지된다
PANEL = "#f7f8fa"
HEAD_BG = "#f2f4f7"
INK = "#16181d"
BODY = "#2f3540"
DIM = "#5b6270"
FAINT = "#98a1b0"
RULE = "#e4e7ec"
ROW_RULE = "#eef1f5"
HEAD_RULE = "#d3d9e2"

BAD, BAD_BG = "#b42318", "#fef3f2"
GOOD, GOOD_BG = "#067647", "#eef7f1"
WARN, WARN_BG = "#b54708", "#fff3e6"

# GBM 구분용 계열색. **정체성**을 나타내는 색이고 의미(증감)를 나타내지 않는다 —
# 그래서 클라이언트가 다크모드로 색을 반전시켜 파랑이 주황이 돼도 문제가 없다.
# MX가 VD와 **다른 색**이라는 사실만 유지되면 되고, 이름 글자가 정체성을 함께 말한다.
# 네 개를 넘으면 돌려 쓴다(같은 색을 쓰는 두 GBM은 이름으로 구별된다).
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#7a5af8", "#0d9488")

# 차트 이미지 전용 색. 본문 색을 그대로 쓰지 않는 이유: 차트는 **투명 배경**이라
# 라이트(#fefefe)와 다크(#16181d) 양쪽 바탕 위에 얹힌다. 본문 색은 라이트만 보고
# 고른 것이라 다크에서 무너진다(#5b6270은 라이트 6.08 / 다크 2.90).
#
# 측정해 보면 **양쪽에서 작은 글자 기준(4.5:1)을 넘는 색은 없다** — 순수 회색 최적이
# #7b7b7b의 4.20:1이다. 그래서 이 값들은 "양쪽에서 같은 정도로 읽히는" 타협점이고,
# 라이트에서만 보면 본문 글씨보다 연하게 느껴지는 것이 정상이다.
CHART_LABEL = "#7b7b7b"      # 점 위의 숫자 — 양쪽 4.20:1
CHART_GRID = "#9a9a9a"       # 점선 격자 — 격자는 낮은 대비가 맞다
CHART_BASELINE = "#7b7b7b"   # 0 기준선

WIDTH = 720
PAD = "28px"
FONT = ("'Malgun Gothic','맑은 고딕',-apple-system,'Segoe UI',"
        "Roboto,'Helvetica Neue',sans-serif")

# 한국어 줄바꿈. 브라우저의 기본값은 한글을 **음절 단위로** 끊어서 "7 평일에 걸쳐"가
# "7 평" / "일에 걸쳐"로 갈라진다. `keep-all`은 공백에서만 끊게 해서 한국어 조판을
# 맞추고, `break-word`는 공백 없는 아주 긴 낱말(`설비정지5분초과`)이 칸을 넘치는 것을
# 막는다 — keep-all만 쓰면 그 낱말이 표를 밀어낸다.
#
# Outlook의 Word 엔진은 이 속성들을 무시할 수 있다. 그래서 **끊기면 안 되는 짧은
# 구절은 blocks.py가 줄바꿈 금지 공백(U+00A0)으로 붙여 둔다** — CSS는 개선이고
# 그 문자가 보장이다.
WRAP = "word-break:keep-all;overflow-wrap:break-word;"

_TONE_COLOR = {"plain": INK, "strong": INK, "muted": DIM,
               "bad": BAD, "good": GOOD, "warn": WARN}
_CHIP_BG = {"plain": HEAD_BG, "strong": HEAD_BG, "muted": HEAD_BG,
            "bad": BAD_BG, "good": GOOD_BG, "warn": WARN_BG}
_BANNER = {"bad": (BAD, BAD_BG, "#7a271a"),
           "warn": (WARN, WARN_BG, "#7a3a07"),
           "good": (GOOD, GOOD_BG, "#054f30")}


def series_color(index: int | None) -> str | None:
    """계열색 번호 → 색. 블록은 번호만 알고 색 값은 여기만 안다."""
    return SERIES[index % len(SERIES)] if index is not None else None


def e(text) -> str:
    """모든 텍스트가 지나는 문 하나. `quote=True`여야 `"`가 속성을 끊지 못한다."""
    return escape(str(text), quote=True)


def _row(content: str, *, pad: str) -> str:
    return f'<tr><td style="padding:{pad};">{content}</td></tr>'


def _heading(block: Block) -> str:
    hint = (f'<span style="font-size:12px;font-weight:500;color:{DIM};"> {e(block.hint)}</span>'
            if block.hint else "")
    lead = (f'<div style="font-size:12px;color:{DIM};margin-top:7px;line-height:1.6;">'
            f'{e(block.lead)}</div>' if block.lead else "")
    return (f'<div style="font-size:16px;font-weight:700;color:{INK};'
            f'border-bottom:1px solid {RULE};padding-bottom:8px;">'
            f'{e(block.title)}{hint}</div>{lead}')


def _banner(banner: Banner) -> str:
    bar, background, text = _BANNER[banner.tone]
    # 굵게 강조는 본문에 `**...**`로 들어온다 — 마크다운을 쓰지 않으므로 여기서 벗긴다.
    body = e(banner.text).replace("**", "")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border-collapse:collapse;margin-top:14px;">'
            f'<tr><td width="4" style="background:{bar};font-size:0;line-height:0;">&nbsp;</td>'
            f'<td style="background:{background};padding:11px 14px;font-size:13px;'
            f'color:{text};line-height:1.55;{WRAP}">{body}</td></tr></table>')


def _tiles(tiles: tuple[Tile, ...]) -> str:
    """3열 격자. `border-spacing`으로 간격을 주는 이유: margin은 Word 엔진에서
    무시된다."""
    cells = []
    for tile in tiles:
        hint = (f'&nbsp;<span style="color:{FAINT};">{e(tile.hint)}</span>'
                if tile.hint else "")
        unit = (f'<span style="font-size:14px;font-weight:500;color:{DIM};">'
                f' {e(tile.unit)}</span>' if tile.unit else "")
        note = (f'<div style="font-size:12px;color:{DIM};margin-top:3px;line-height:1.5;">'
                f'{e(tile.note)}</div>' if tile.note else "")
        cells.append(
            f'<td width="33%" style="background:{PANEL};border:1px solid {RULE};'
            f'padding:13px 15px;vertical-align:top;">'
            f'<div style="font-size:11px;color:{DIM};letter-spacing:.4px;">'
            f'{e(tile.label)}{hint}</div>'
            f'<div style="font-size:26px;font-weight:700;line-height:1.15;margin-top:5px;'
            f'color:{series_color(tile.series) or _TONE_COLOR[tile.tone]};">'
            f'{e(tile.value)}{unit}</div>{note}</td>')
    rows = ["".join(cells[i:i + 3]) for i in range(0, len(cells), 3)]
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border-collapse:separate;border-spacing:8px 8px;">'
            + "".join(f"<tr>{row}</tr>" for row in rows) + "</table>")


# 차트 치수. 픽셀로 고정하는 이유: Word 엔진은 이미지의 `%` 폭을 제대로 안 다루고,
# 메일 본문 폭(720px)이 고정이라 상대 단위의 이점이 없다.
AXIS_W = 46          # 왼쪽 GBM 이름 칸
MAX_W = 52           # 오른쪽 최댓값 칸
PLOT_W = 560         # 꺾은선 그림의 표시 폭


def _chart_style(color: str) -> "LineChartStyle":
    """차트 이미지는 **투명 배경**이다.

    불투명(거의 흰색)으로 두면 클라이언트가 강제하는 다크모드에서 이미지만 반전되지
    않아서 어두운 본문 위에 **흰 판 네 개**가 뜬다 — 읽는 사람에게는 깨진 것으로
    보인다. 그 대가로 글자·격자 대비가 내려가는 것은 `CHART_LABEL` 주석에 적혀 있다.
    """
    from src.presentation.line_chart import LineChartStyle

    return LineChartStyle(background=None, line=color, grid=CHART_GRID,
                          label=CHART_LABEL, baseline=CHART_BASELINE)


def _panel(panel: ChartPanel) -> str:
    """GBM 한 판: 이름 | 꺾은선 그림 | 최댓값.

    그림 안에는 **숫자만** 들어간다. 이름·최댓값·날짜는 HTML이 그리므로 PNG 폰트가
    글리프 11개로 끝난다(`png.py` 참고). 한글을 그림에 넣는 순간 그 구조가 무너진다.

    `alt`를 채우는 이유: 이미지를 막는 클라이언트가 있고, 그때 남는 것이 이 문장과
    바로 아래 숫자 표다.
    """
    import base64

    from src.presentation.line_chart import render_line_chart

    values = [bar.value for column in panel.columns for bar in column.bars]
    color = series_color(panel.series) or INK
    png = render_line_chart(values, scale=panel.scale, width=PLOT_W,
                            height=panel.height, style=_chart_style(color))
    encoded = base64.b64encode(png).decode("ascii")
    alt = f"{panel.title or ''} 일별 추이 {', '.join(f'{v:,}' for v in values)}".strip()

    title = ""
    if panel.title:
        title = (f'<span style="color:{color};font-weight:700;font-size:13px;">'
                 f'{e(panel.title)}</span>')
    return (f'<tr>'
            f'<td width="{AXIS_W}" valign="middle" '
            f'style="padding:0 6px 0 0;vertical-align:middle;">{title}</td>'
            f'<td style="padding:0;line-height:0;">'
            f'<img src="data:image/png;base64,{encoded}" width="{PLOT_W}" '
            f'height="{panel.height}" alt="{e(alt)}" '
            f'style="display:block;width:{PLOT_W}px;height:{panel.height}px;'
            f'border:0;outline:none;text-decoration:none;"></td>'
            f'<td width="{MAX_W}" align="right" valign="middle" '
            f'style="padding:0 0 0 8px;font-size:11px;color:{FAINT};'
            f'vertical-align:middle;white-space:nowrap;">'
            f'{e(f"최대 {panel.scale:,}")}</td>'
            f'</tr>'
            f'<tr><td colspan="3" style="font-size:0;line-height:0;height:8px;">'
            f'&nbsp;</td></tr>')


def _chart(chart: Chart) -> str:
    """꺾은선 판들 + 공유 x축 라벨.

    x축 라벨을 **판마다 반복하지 않고 맨 아래 한 번만** 둔다 — 판이 네 개면 날짜가
    네 번 찍혀서 정작 선보다 글자가 많아진다. 같은 시간축을 공유한다는 사실도 그
    배치가 말해 준다.

    라벨 칸의 좌우 여백(10px)은 `line_chart`의 `pad_side`와 같은 값이다. 안 맞으면
    날짜가 점과 어긋나서 "그날이 아닌 날"을 가리킨다.
    """
    panels = "".join(_panel(panel) for panel in chart.panels)

    labels = ""
    if chart.axis:
        cells = "".join(
            f'<td align="center" style="font-size:10.5px;color:{DIM};'
            f'white-space:nowrap;">{e(label)}</td>' for label in chart.axis)
        labels = (f'<tr><td width="{AXIS_W}" style="font-size:0;line-height:0;">'
                  f'&nbsp;</td>'
                  f'<td style="padding:2px 10px 0;">'
                  f'<table role="presentation" cellpadding="0" cellspacing="0" '
                  f'border="0" width="100%" style="width:100%;'
                  f'border-collapse:collapse;table-layout:fixed;">'
                  f'<tr>{cells}</tr></table></td>'
                  f'<td width="{MAX_W}" style="font-size:0;line-height:0;">'
                  f'&nbsp;</td></tr>')

    warning = ""
    if chart.warning:
        warning = (f'<div style="font-size:11px;color:{FAINT};line-height:1.6;'
                   f'padding:8px 0 0;{WRAP}">{e(chart.warning)}</div>')

    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="width:100%;border-collapse:collapse;">'
            f'{panels}{labels}</table>{warning}')


def _cell(cell: Cell, *, last: bool, total: bool = False,
          group_start: bool = False) -> str:
    border = "" if last or total else f"border-bottom:1px solid {ROW_RULE};"
    if group_start:
        # 묶음 경계를 **선으로** 긋는다. GBM 이름을 둘째 행부터 비워 두므로,
        # 선이 없으면 빈 칸이 "값이 없다"로 읽힌다.
        border += f"border-top:2px solid {HEAD_RULE};"
    weight = "font-weight:600;" if cell.tone == "strong" or total else ""
    if total:
        weight = "font-weight:700;"
    # 계열색이 tone을 이긴다 — GBM 이름 칸은 정체성이 의미보다 앞선다.
    color = series_color(cell.series) or _TONE_COLOR[cell.tone]
    if cell.chip:
        inner = (f'<span style="background:{_CHIP_BG[cell.tone]};color:{color};'
                 f'font-size:10.5px;padding:2px 7px;font-weight:600;white-space:nowrap;">'
                 f'{e(cell.text)}</span>')
    else:
        inner = e(cell.text)
    hint = (f'<span style="font-size:11px;color:{FAINT};"> · {e(cell.hint)}</span>'
            if cell.hint else "")
    return (f'<td align="{cell.align}" style="padding:9px 10px;{border}'
            f'color:{color};{weight}vertical-align:top;{WRAP}">{inner}{hint}</td>')


def _table(table: Table) -> str:
    header = "".join(
        f'<th align="{column.align}" style="padding:9px 10px;'
        f'border-bottom:1px solid {HEAD_RULE};font-size:11px;'
        f'color:{series_color(column.series) or DIM};'
        f'font-weight:600;letter-spacing:.3px;white-space:nowrap;">{e(column.label)}</th>'
        for column in table.columns)
    body = []
    for index, row in enumerate(table.rows):
        last = index == len(table.rows) - 1 and table.total is None
        start = index in table.group_starts and index != 0
        body.append("<tr>" + "".join(_cell(c, last=last, group_start=start)
                                     for c in row) + "</tr>")
    if table.total is not None:
        body.append(f'<tr style="background:{PANEL};">'
                    + "".join(_cell(c, last=True, total=True) for c in table.total)
                    + "</tr>")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border-collapse:collapse;margin-top:12px;'
            f'font-size:13px;color:{INK};">'
            f'<tr style="background:{HEAD_BG};">{header}</tr>' + "".join(body) + "</table>")


def _bullets(lines: tuple[str, ...]) -> str:
    items = "".join(
        f'<tr><td width="12" style="font-size:13px;color:{FAINT};vertical-align:top;'
        f'padding:3px 0;">·</td>'
        f'<td style="font-size:12.5px;color:{BODY};line-height:1.65;padding:3px 0;'
        f'{WRAP}">{e(line)}</td></tr>' for line in lines)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border-collapse:collapse;margin-top:10px;">'
            f'{items}</table>')


def _block(block: Block) -> str:
    parts = []
    if block.title:
        parts.append(_row(_heading(block), pad=f"24px {PAD} 0"))
    elif block.lead:
        parts.append(_row(f'<div style="font-size:13px;color:{DIM};line-height:1.6;">'
                          f'{e(block.lead)}</div>', pad=f"0 {PAD}"))
    for banner in block.banners:
        parts.append(_row(_banner(banner), pad=f"0 {PAD}"))
    if block.tiles:
        parts.append(_row(_tiles(block.tiles), pad=f"16px {PAD} 0"))
    if block.chart is not None and block.chart.has_panels:
        parts.append(_row(_chart(block.chart), pad=f"14px {PAD} 0"))
    if block.table is not None:
        parts.append(_row(_table(block.table), pad=f"0 {PAD}"))
    elif not block.has_content and block.empty:
        # 섹션을 감추지 않는다 — 감추면 "그 항목은 원래 없는 리포트"로 읽힌다.
        parts.append(_row(f'<div style="font-size:13px;color:{FAINT};'
                          f'padding:14px 0 2px;">{e(block.empty)}</div>', pad=f"0 {PAD}"))
    if block.bullets:
        parts.append(_row(_bullets(block.bullets), pad=f"0 {PAD}"))
    if block.footnote:
        parts.append(_row(f'<div style="font-size:11.5px;color:{FAINT};line-height:1.6;'
                          f'margin-top:8px;{WRAP}">{e(block.footnote)}</div>',
                          pad=f"0 {PAD}"))
    return "".join(parts)


def render(blocks: list[Block], *, title: str, generated_at: str,
           subtitle: str = "DAILY ALARM REPORT") -> str:
    """메일 본문 HTML. `<html>`/`<head>`까지 포함한 완전한 문서를 돌려준다 —
    발송 API가 본문을 그대로 싣기 때문이다.

    `<meta name="color-scheme">`을 넣는 이유: 이걸 보는 클라이언트는 **자체 반전을
    덜 공격적으로** 한다. 막아 주지는 않지만 넣는 비용이 0이다.
    """
    header_block = next((b for b in blocks if b.key == "header"), None)
    lead = header_block.lead if header_block else ""
    body = "".join(_block(b) for b in blocks if b.key != "header")

    return (
        '<!DOCTYPE html>\n<html lang="ko"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        f'<title>{e(title)}</title></head>'
        f'<body style="margin:0;padding:0;background:{PAGE};">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{PAGE};border-collapse:collapse;">'
        '<tr><td align="center" style="padding:0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="{WIDTH}" style="width:{WIDTH}px;max-width:{WIDTH}px;'
        f'border-collapse:collapse;font-family:{FONT};color:{INK};background:{PAGE};">'
        f'<tr><td style="padding:26px {PAD} 16px;border-bottom:3px solid {INK};">'
        f'<div style="font-size:11px;letter-spacing:2px;color:{DIM};">{e(subtitle)}</div>'
        f'<div style="font-size:23px;font-weight:700;margin-top:7px;letter-spacing:-0.3px;'
        f'color:{INK};">{e(title)}</div>'
        f'<div style="font-size:13px;color:{DIM};margin-top:7px;line-height:1.6;">'
        f'{e(lead)} · 생성 {e(generated_at)}</div>'
        '</td></tr>'
        f'{body}'
        f'<tr><td style="padding:22px {PAD} 26px;border-top:1px solid {RULE};'
        f'font-size:11.5px;color:{FAINT};line-height:1.6;">'
        '숫자는 집계 코드가 센 것이고 LLM은 숫자를 만들지 않습니다. '
        '임계값과 대상 법인은 시나리오 config가 정합니다.'
        '</td></tr>'
        '</table></td></tr></table></body></html>')
