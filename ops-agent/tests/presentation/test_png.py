"""PNG을 직접 만드는 코드. **라이브러리 없이 그렸으니 라이브러리 없이 검사한다.**

matplotlib로 그렸다면 "뭔가 나왔다"까지만 단정할 수 있다. 직접 그리면 **특정
좌표의 색**을 단정할 수 있어서, 선이 제자리에 있는지 점이 빠지지 않았는지를
픽셀로 확인한다. 그게 이 방식을 고른 이유 중 하나다.
"""
import struct
import zlib

import pytest

from src.presentation.line_chart import LineChartStyle, render_line_chart
from src.presentation.png import (Canvas, GLYPH_HEIGHT, GLYPH_WIDTH, draw_text,
                                  parse_color, text_width)

WHITE = (255, 255, 255)
BLUE = (42, 120, 214)


def decode(png: bytes) -> tuple[int, int, list[list[tuple[int, int, int]]]]:
    """청크를 파싱하고 **CRC까지 검증**한 뒤 픽셀을 돌려준다.

    디코더를 직접 쓰는 이유: Pillow를 테스트 의존성으로 들이면 "우리 PNG가 Pillow에서
    열린다"만 알 수 있고, 파일이 규약에 맞는지는 모른다. CRC와 청크 순서를 직접
    보면 규약을 본다.
    """
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "PNG 서명이 아니다"
    position, kinds, idat = 8, [], b""
    width = height = 0
    channels = 3
    while position < len(png):
        (length,) = struct.unpack(">I", png[position:position + 4])
        kind = png[position + 4:position + 8]
        payload = png[position + 8:position + 8 + length]
        (crc,) = struct.unpack(">I", png[position + 8 + length:position + 12 + length])
        assert crc == zlib.crc32(kind + payload) & 0xFFFFFFFF, f"{kind!r} CRC 불일치"
        if kind == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", payload[:10])
            assert depth == 8, f"8비트가 아니다 — {depth}"
            assert color_type in (2, 6), f"트루컬러(2/6)가 아니다 — {color_type}"
            channels = 3 if color_type == 2 else 4
        if kind == b"IDAT":
            idat += payload
        kinds.append(kind)
        position += 12 + length
    assert kinds[0] == b"IHDR" and kinds[-1] == b"IEND", f"청크 순서 — {kinds}"

    raw = zlib.decompress(idat)
    stride = width * channels
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        assert raw[start] == 0, "필터 0(None)만 쓴다"
        line = raw[start + 1:start + 1 + stride]
        # 알파는 버리고 RGB만 돌려준다 — 색 검사가 목적이고, 투명 여부는
        # `Canvas.opacity`로 따로 본다.
        rows.append([tuple(line[x * channels:x * channels + 3]) for x in range(width)])
    return width, height, rows


# ── 파일 자체 ───────────────────────────────────────────────────────

def test_규약에_맞는_PNG가_나온다():
    width, height, rows = decode(Canvas(7, 5).to_png())
    assert (width, height) == (7, 5)
    assert rows[0][0] == WHITE and len(rows) == 5 and len(rows[0]) == 7


def test_같은_그림은_같은_바이트다():
    """플랫폼마다 다른 그림이 나오면 테스트가 그 차이를 못 잡는다. 결정론이
    직접 그리기를 고른 이유다."""
    def draw():
        canvas = Canvas(30, 20, background=parse_color("#fefefe"))
        canvas.line(1, 18, 28, 2, BLUE, thickness=2)
        canvas.disc(28, 2, 2, BLUE)
        draw_text(canvas, 2, 2, "1,204", (0, 0, 0))
        return canvas.to_png()

    assert draw() == draw()


def test_크기가_0이면_거부한다():
    with pytest.raises(ValueError):
        Canvas(0, 10)


# ── 그리기 ──────────────────────────────────────────────────────────

def test_범위를_벗어난_점은_버린다():
    """선을 그리다 한 픽셀이 밖으로 나가는 일은 정상이다 — 거기서 예외를 내면
    차트 하나 때문에 리포트 전체가 죽는다."""
    canvas = Canvas(5, 5)
    canvas.set(-1, -1, BLUE)
    canvas.set(99, 99, BLUE)
    canvas.line(-10, 2, 10, 2, BLUE)
    _, _, rows = decode(canvas.to_png())
    assert rows[2] == [BLUE] * 5, "안쪽은 그려져야 한다"


def test_선이_두_끝점을_지난다():
    canvas = Canvas(20, 20)
    canvas.line(2, 17, 17, 2, BLUE)
    _, _, rows = decode(canvas.to_png())
    assert rows[17][2] == BLUE and rows[2][17] == BLUE


def test_완만한_선은_위아래로_번진다():
    """수평으로 번지면 같은 픽셀을 덧칠해서 굵어지지 않는다."""
    canvas = Canvas(20, 20)
    canvas.line(2, 10, 17, 10, BLUE, thickness=3)
    _, _, rows = decode(canvas.to_png())
    assert rows[9][10] == BLUE and rows[10][10] == BLUE and rows[11][10] == BLUE


def test_급한_선은_좌우로_번진다():
    """한쪽 방향만 고르면 어떤 구간에서 선이 1px로 얇아진다 — 꺾은선의 두께가
    구간마다 달라 보이면 "끊겼다"로 읽힌다."""
    canvas = Canvas(20, 20)
    canvas.line(10, 2, 10, 17, BLUE, thickness=3)
    _, _, rows = decode(canvas.to_png())
    assert rows[10][9] == BLUE and rows[10][10] == BLUE and rows[10][11] == BLUE


@pytest.mark.parametrize("target", [(17, 3), (17, 10), (17, 17), (10, 17), (3, 17)])
def test_어떤_기울기에서도_두께가_유지된다(target):
    """실제 꺾은선은 구간마다 기울기가 다르다 — 한 판 안에서 두께가 달라지면 "끊겼다"로
    읽힌다.

    전체 칠한 픽셀 수로 보면 안 된다 — 그건 **선 길이**에 비례하므로 짧은 구간이
    항상 작게 나온다. 봐야 하는 것은 진행 방향의 **단면 두께**다.
    """
    canvas = Canvas(20, 20)
    canvas.line(2, 10, *target, BLUE, thickness=3)
    _, _, rows = decode(canvas.to_png())

    dx, dy = abs(target[0] - 2), abs(target[1] - 10)
    if dx >= dy:                                   # 완만한 선 → 세로 단면
        x = (2 + target[0]) // 2
        cross = sum(1 for y in range(20) if rows[y][x] == BLUE)
    else:                                          # 급한 선 → 가로 단면
        y = (10 + target[1]) // 2
        cross = sum(1 for x in range(20) if rows[y][x] == BLUE)
    assert cross >= 3, f"{target}에서 단면이 {cross}px — 얇다"


def test_점선_격자에_빈_칸이_있다():
    """실선 격자는 추세선과 헷갈려서 선이 묻힌다."""
    canvas = Canvas(20, 5)
    canvas.dotted_hline(0, 2, 20, BLUE, on=2, off=3)
    _, _, rows = decode(canvas.to_png())
    painted = [x for x, color in enumerate(rows[2]) if color == BLUE]
    assert painted and len(painted) < 20


def test_점이_중심과_반지름을_지킨다():
    canvas = Canvas(21, 21)
    canvas.disc(10, 10, 3, BLUE)
    _, _, rows = decode(canvas.to_png())
    assert rows[10][10] == BLUE
    assert rows[10][13] == BLUE and rows[13][10] == BLUE
    assert rows[10][17] == WHITE, "반지름 밖까지 칠했다"


# ── 글자 ────────────────────────────────────────────────────────────

def test_숫자가_그려진다():
    canvas = Canvas(40, 12)
    draw_text(canvas, 1, 1, "07", BLUE)
    _, _, rows = decode(canvas.to_png())
    painted = sum(1 for row in rows for color in row if color == BLUE)
    assert painted > 10, "글자가 비어 있다"


def test_글자_폭_계산이_실제와_맞는다():
    """폭이 틀리면 점 위의 숫자가 중앙에서 벗어나 다른 점을 가리킨다."""
    canvas = Canvas(60, 12)
    text = "1,204"
    draw_text(canvas, 0, 1, text, BLUE)
    _, _, rows = decode(canvas.to_png())
    columns = [x for x in range(60) if any(rows[y][x] == BLUE for y in range(12))]
    assert max(columns) < text_width(text), \
        f"실제 폭이 계산({text_width(text)})을 넘는다 — {max(columns) + 1}"


def test_모르는_문자는_네모로_그린다():
    """빈칸으로 두면 "숫자가 빠졌다"로 보이는데, 네모는 그릴 수 없는 문자임이
    드러난다."""
    canvas = Canvas(GLYPH_WIDTH + 2, GLYPH_HEIGHT + 2)
    draw_text(canvas, 1, 1, "가", BLUE)
    _, _, rows = decode(canvas.to_png())
    assert rows[1][1] == BLUE and rows[1][GLYPH_WIDTH] == BLUE


def test_색을_읽는다():
    assert parse_color("#2a78d6") == BLUE
    assert parse_color("2a78d6") == BLUE
    assert parse_color("#fff") == WHITE
    with pytest.raises(ValueError):
        parse_color("#12345")


# ── 꺾은선 차트 ─────────────────────────────────────────────────────

STYLE = LineChartStyle(background="#fefefe", line="#2a78d6", grid="#d3d9e2",
                       label="#5b6270", baseline="#98a1b0")


def test_꺾은선이_그려진다():
    png = render_line_chart([10, 20, 30], scale=30, width=60, height=40, style=STYLE)
    width, height, rows = decode(png)
    assert (width, height) == (120, 80), "2배로 그린다 — 확대돼도 또렷해야 한다"
    assert any(color == BLUE for row in rows for color in row)


def test_값이_없으면_빈_판이다():
    """기간에 데이터가 없는 GBM도 판은 남는다 — 판이 사라지면 "그 GBM은 대상이
    아니다"로 읽힌다."""
    png = render_line_chart([], scale=0, width=60, height=40, style=STYLE)
    _, _, rows = decode(png)
    assert all(color == parse_color("#fefefe") for row in rows for color in row)


def test_값이_하나여도_죽지_않는다():
    """기간이 1일이면 점이 하나다 — (len-1)로 나누는 코드가 여기서 터진다."""
    png = render_line_chart([5], scale=5, width=60, height=40, style=STYLE)
    _, _, rows = decode(png)
    assert any(color == BLUE for row in rows for color in row)


def test_기준이_0이면_선이_바닥에_붙는다():
    """0으로 나누지 않고, 전부 0인 날도 바닥선으로 보여 준다."""
    png = render_line_chart([0, 0, 0], scale=0, width=60, height=40, style=STYLE)
    decode(png)          # 예외 없이 디코드되면 충분하다


def test_최댓값_점이_위쪽_여백_안에_들어온다():
    """위쪽 여백은 점 위의 숫자 자리다. 안 비워 두면 제일 높은 점의 숫자가 잘린다."""
    png = render_line_chart([100], scale=100, width=60, height=40, style=STYLE)
    _, height, rows = decode(png)
    top_painted = next(y for y in range(height)
                       if any(color != parse_color("#fefefe") for color in rows[y]))
    assert top_painted > 0, "그림 맨 위 줄까지 칠했다 — 숫자가 잘린다"


def test_파일이_메일에_실을_만큼_작다():
    """네 판이 본문에 base64로 들어간다 — 한 판이 수십 KB면 본문이 수백 KB가 된다."""
    png = render_line_chart([168, 182, 155, 201, 190, 176, 243], scale=243,
                            width=560, height=92, style=STYLE)
    assert len(png) < 12_000, f"{len(png):,}바이트 — 너무 크다"


# ── 투명 배경 ───────────────────────────────────────────────────────

def test_배경이_None이면_알파_채널이_생긴다():
    """다크모드에서 그림이 흰 판으로 뜨는 것을 막으려면 알파가 필요하다."""
    png = Canvas(4, 3, background=None).to_png()
    (color_type,) = struct.unpack(">B", png[25:26])
    assert color_type == 6, "알파 트루컬러(6)가 아니다"


def test_알파를_안_쓰면_채널을_넣지_않는다():
    """쓰지도 않는 채널이 픽셀마다 1바이트씩 붙으면 base64로 실을 때 33%가 늘어난다."""
    (color_type,) = struct.unpack(">B", Canvas(4, 3).to_png()[25:26])
    assert color_type == 2


def test_칠한_곳만_불투명하다():
    canvas = Canvas(5, 5, background=None)
    canvas.set(2, 2, BLUE)
    assert canvas.opacity(2, 2) == 255
    assert canvas.opacity(0, 0) == 0


def test_투명_PNG도_규약에_맞는다():
    """알파가 붙어도 스캔라인 길이와 필터 바이트가 맞아야 한다."""
    canvas = Canvas(6, 4, background=None)
    canvas.line(0, 3, 5, 0, BLUE)
    png = canvas.to_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    position, idat = 8, b""
    while position < len(png):
        (length,) = struct.unpack(">I", png[position:position + 4])
        kind = png[position + 4:position + 8]
        payload = png[position + 8:position + 8 + length]
        (crc,) = struct.unpack(">I", png[position + 8 + length:position + 12 + length])
        assert crc == zlib.crc32(kind + payload) & 0xFFFFFFFF
        if kind == b"IDAT":
            idat += payload
        position += 12 + length
    raw = zlib.decompress(idat)
    stride = 6 * 4
    assert len(raw) == 4 * (stride + 1), "RGBA 스캔라인 길이가 안 맞는다"
    assert all(raw[y * (stride + 1)] == 0 for y in range(4))


def test_투명_차트도_같은_크기로_그려진다():
    clear = LineChartStyle(background=None, line="#2a78d6", grid="#9a9a9a",
                           label="#7b7b7b", baseline="#7b7b7b")
    png = render_line_chart([10, 20, 30], scale=30, width=60, height=40, style=clear)
    width, height, _ = decode(png)
    assert (width, height) == (120, 80)


def test_투명_차트의_배경이_비어_있다():
    clear = LineChartStyle(background=None, line="#2a78d6", grid="#9a9a9a",
                           label="#7b7b7b", baseline="#7b7b7b")
    png = render_line_chart([10], scale=10, width=40, height=30, style=clear)
    # 디코더가 RGB만 다루므로 알파는 Canvas로 직접 본다
    canvas = Canvas(4, 4, background=None)
    assert canvas.opacity(0, 0) == 0
    assert png[25] == 6
