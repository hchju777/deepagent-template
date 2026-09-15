"""꺾은선 차트 한 판을 PNG로. **2배 크기로 그리고 절반으로 표시한다.**

## 왜 꺾은선인가

막대는 "그날 얼마였나"를 말하고, 꺾은선은 **"어떻게 움직였나"**를 말한다. 추세를
보는 것이 이 차트의 목적이므로 선이 맞다. 대신 선은 대각선이라 `<td>`로는 못 그린다
— 그래서 PNG다(인라인 SVG는 Outlook에서 안 보인다).

## 2배로 그리는 이유

메일 클라이언트와 고해상도 화면은 이미지를 확대한다. `<img width="560">`에 560px
그림을 넣으면 흐려지고, 1120px을 넣으면 또렷하다. 파일은 커지지만 한 판이 수 KB다.

## 숫자만 PNG에 들어간다

점 위의 값만 그림 안에 있고, GBM 이름·날짜·최댓값은 **HTML이 그린다.** 그래서
폰트가 글리프 11개로 끝난다(`png.py` 참고). 한글을 그림에 넣는 순간 이 구조가
무너지므로, 여기에 한국어 문자열을 넘기지 마라.
"""
from dataclasses import dataclass

from src.presentation.png import (Canvas, GLYPH_HEIGHT, RGB, Canvas as _Canvas,
                                  draw_text, parse_color, text_width)

SCALE = 2                 # 실제 픽셀 / 표시 픽셀


@dataclass(frozen=True)
class LineChartStyle:
    """`background=None`이면 투명하다.

    투명으로 가면 **모든 요소의 색이 양쪽 바탕에서 읽혀야 한다.** 측정해 보면 라이트
    (#fefefe)와 다크(#16181d) 양쪽에서 작은 글자 기준(4.5:1)을 넘는 색은 없다 —
    순수 회색 최적이 #7b7b7b의 4.20:1이다. 그래서 투명은 "다크에서 흰 판이 뜨는 것"과
    "양쪽에서 숫자 대비가 4.2로 내려가는 것"을 맞바꾸는 선택이고, 공짜가 아니다.
    """
    background: str | None
    line: str
    grid: str
    label: str
    baseline: str


def _thousands(value: int) -> str:
    return f"{value:,}"


def render_line_chart(values: list[int], *, scale: int, width: int, height: int,
                      style: LineChartStyle, highlight_last: bool = True) -> bytes:
    """값들을 꺾은선으로. `width`/`height`는 **표시 크기**(실제는 2배)다.

    `scale`은 y축 최댓값이다. 0이면 빈 판을 돌려준다 — 이 함수가 스스로 최댓값을
    정하지 않는 이유: 판마다 y축이 다르다는 사실을 호출부가 알고 있어야 그 경고를
    본문에 적을 수 있다.
    """
    canvas = _Canvas(width * SCALE, height * SCALE,
                     background=None if style.background is None
                     else parse_color(style.background))
    if not values:
        return canvas.to_png()

    grid = parse_color(style.grid)
    ink = parse_color(style.line)
    label_color = parse_color(style.label)
    baseline = parse_color(style.baseline)

    # 위쪽 여백은 **점 위의 숫자**가 들어갈 자리다. 안 비워 두면 제일 높은 점의
    # 숫자가 그림 밖으로 잘린다.
    pad_top = (GLYPH_HEIGHT + 5) * SCALE
    pad_bottom = 3 * SCALE
    pad_side = 10 * SCALE
    plot_top = pad_top
    plot_bottom = canvas.height - pad_bottom
    plot_height = max(1, plot_bottom - plot_top)
    plot_left = pad_side
    plot_right = canvas.width - pad_side
    plot_width = max(1, plot_right - plot_left)

    # 격자 — 점선이다. 실선이면 추세선과 헷갈린다.
    for fraction in (0.0, 0.5, 1.0):
        y = plot_top + round(plot_height * fraction)
        if fraction == 1.0:
            canvas.hline(plot_left, y, plot_width, baseline)
        else:
            canvas.dotted_hline(plot_left, y, plot_width, grid,
                                on=2 * SCALE, off=4 * SCALE)

    def point(index: int, value: int) -> tuple[int, int]:
        x = (plot_left if len(values) == 1
             else plot_left + round(index * plot_width / (len(values) - 1)))
        ratio = 0.0 if scale <= 0 else min(1.0, value / scale)
        return x, plot_bottom - round(plot_height * ratio)

    points = [point(index, value) for index, value in enumerate(values)]

    for start, end in zip(points, points[1:]):
        canvas.line(*start, *end, ink, thickness=2 * SCALE)

    for index, (x, y) in enumerate(points):
        last = index == len(points) - 1
        radius = (3 if last and highlight_last else 2) * SCALE
        canvas.disc(x, y, radius, ink)

        text = _thousands(values[index])
        half = text_width(text) * SCALE // 2
        # 마지막 점만 색을 입힌다 — 어제가 어디인지 한눈에 보여야 하고, 전부
        # 색칠하면 그 강조가 사라진다.
        draw_text(canvas, x - half, y - (GLYPH_HEIGHT + 4) * SCALE, text,
                  ink if last and highlight_last else label_color,
                  spacing=SCALE)
    return canvas.to_png()
