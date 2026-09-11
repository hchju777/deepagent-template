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

from src.report.blocks import Banner, Block, Cell, Table, Tile

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

SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")

WIDTH = 720
PAD = "28px"
FONT = ("'Malgun Gothic','맑은 고딕',-apple-system,'Segoe UI',"
        "Roboto,'Helvetica Neue',sans-serif")

_TONE_COLOR = {"plain": INK, "strong": INK, "muted": DIM,
               "bad": BAD, "good": GOOD, "warn": WARN}
_CHIP_BG = {"plain": HEAD_BG, "strong": HEAD_BG, "muted": HEAD_BG,
            "bad": BAD_BG, "good": GOOD_BG, "warn": WARN_BG}
_BANNER = {"bad": (BAD, BAD_BG, "#7a271a"),
           "warn": (WARN, WARN_BG, "#7a3a07"),
           "good": (GOOD, GOOD_BG, "#054f30")}


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
            f'color:{text};line-height:1.55;">{body}</td></tr></table>')


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
            f'color:{_TONE_COLOR[tile.tone]};">{e(tile.value)}{unit}</div>{note}</td>')
    rows = ["".join(cells[i:i + 3]) for i in range(0, len(cells), 3)]
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border-collapse:separate;border-spacing:8px 8px;">'
            + "".join(f"<tr>{row}</tr>" for row in rows) + "</table>")


def _cell(cell: Cell, *, last: bool, total: bool = False) -> str:
    border = "" if last or total else f"border-bottom:1px solid {ROW_RULE};"
    weight = "font-weight:600;" if cell.tone == "strong" or total else ""
    if total:
        weight = "font-weight:700;"
    color = _TONE_COLOR[cell.tone]
    if cell.chip:
        inner = (f'<span style="background:{_CHIP_BG[cell.tone]};color:{color};'
                 f'font-size:10.5px;padding:2px 7px;font-weight:600;white-space:nowrap;">'
                 f'{e(cell.text)}</span>')
    else:
        inner = e(cell.text)
    hint = (f'<span style="font-size:11px;color:{FAINT};"> · {e(cell.hint)}</span>'
            if cell.hint else "")
    return (f'<td align="{cell.align}" style="padding:9px 10px;{border}'
            f'color:{color};{weight}vertical-align:top;">{inner}{hint}</td>')


def _table(table: Table) -> str:
    header = "".join(
        f'<th align="{column.align}" style="padding:9px 10px;'
        f'border-bottom:1px solid {HEAD_RULE};font-size:11px;color:{DIM};'
        f'font-weight:600;letter-spacing:.3px;white-space:nowrap;">{e(column.label)}</th>'
        for column in table.columns)
    body = []
    for index, row in enumerate(table.rows):
        last = index == len(table.rows) - 1 and table.total is None
        body.append("<tr>" + "".join(_cell(c, last=last) for c in row) + "</tr>")
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
        f'<td style="font-size:12.5px;color:{BODY};line-height:1.65;padding:3px 0;">'
        f'{e(line)}</td></tr>' for line in lines)
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
                          f'margin-top:8px;">{e(block.footnote)}</div>', pad=f"0 {PAD}"))
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
