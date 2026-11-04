"""PNG을 직접 만든다. **의존성 없이, 어디서나 같은 바이트로.**

## 왜 matplotlib/Pillow를 안 쓰는가

셋 다 이유가 있다:

1. **사내 PyPI에 없을 위험.** 7단계에서 langchain으로 이미 겪었다. 리포트 하나를
   위해 배포를 막을 수 있는 의존성을 들이지 않는다.
2. **플랫폼마다 다른 그림.** matplotlib는 버전에 따라 안티에일리어싱과 기본 치수가
   바뀐다. 개발에서 본 그림과 사내에서 나가는 그림이 다르면, 그 차이를 테스트가
   못 잡는다.
3. **검사할 수 없다.** 저 둘로 그린 PNG는 "뭔가 나왔다"까지만 단정할 수 있다.
   직접 그리면 **특정 좌표의 색**을 단정할 수 있다 — 선이 제자리에 있는지, 점이
   빠지지 않았는지를 픽셀로 확인한다.

## 한글이 없어서 가능해진 일

차트에 들어가는 글자는 **숫자뿐**이다(GBM 이름·날짜·최댓값은 HTML이 그린다).
그래서 글리프 11개짜리 5×7 비트맵 폰트로 끝난다 — 한글이 섞이면 글리프가 수천 개라
폰트 파일을 번들해야 하고, 그 순간 이 방법이 무너진다.

## 필터 0만 쓴다

PNG의 스캔라인 필터(Sub/Up/Average/Paeth)는 압축률을 높이지만, 우리 이미지는
작고(560×180) 단색 면이 많아 zlib만으로 충분하다. 필터를 쓰면 구현이 네 배가 되고
버그가 숨을 곳이 생긴다.
"""
import struct
import zlib

RGB = tuple[int, int, int]


def parse_color(value: str) -> RGB:
    """`#2a78d6` → (42, 120, 214). 축약형(`#abc`)도 받는다."""
    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"색을 읽을 수 없다 — {value!r}")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


class Canvas:
    """픽셀 버퍼. 좌표는 (x, y)이고 원점은 **왼쪽 위**다(PNG 규약).

    내부는 항상 RGBA로 들고, `to_png`이 알파가 필요한지에 따라 트루컬러(타입 2)와
    알파 트루컬러(타입 6) 중에 고른다. **알파를 안 쓰면 파일에 알파 채널을 넣지 않는
    이유**: 쓰지도 않는 채널이 픽셀마다 1바이트씩 붙으면 base64로 실을 때 33%가
    그냥 늘어난다.
    """

    def __init__(self, width: int, height: int, *,
                 background: RGB | None = (255, 255, 255)):
        """`background=None`이면 **투명**하다 — 알파 채널이 생긴다."""
        if width <= 0 or height <= 0:
            raise ValueError(f"크기가 0 이하다 — {width}x{height}")
        self.width = width
        self.height = height
        self.alpha = background is None
        fill = (0, 0, 0, 0) if background is None else (*background, 255)
        self._pixels = bytearray(bytes(fill) * (width * height))

    def set(self, x: int, y: int, color: RGB) -> None:
        """범위를 벗어난 좌표는 **조용히 버린다.**

        일부러 그렇게 한다: 선을 그리다 한 픽셀이 밖으로 나가는 일은 정상이고,
        거기서 예외를 내면 차트 하나 때문에 리포트 전체가 죽는다(무raise 규율).

        칠한 픽셀은 **항상 불투명**이 된다. 반투명을 지원하지 않는 이유: 투명 배경의
        목적이 "바탕색을 그대로 보이게"이고, 선과 글자는 어느 바탕에서든 또렷해야
        하므로 섞일 일이 없다.
        """
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 4
            self._pixels[offset:offset + 4] = bytes((*color[:3], 255))

    def get(self, x: int, y: int) -> RGB:
        offset = (y * self.width + x) * 4
        return tuple(self._pixels[offset:offset + 3])           # type: ignore[return-value]

    def opacity(self, x: int, y: int) -> int:
        offset = (y * self.width + x) * 4
        return self._pixels[offset + 3]

    def rect(self, x: int, y: int, width: int, height: int, color: RGB) -> None:
        for dy in range(height):
            for dx in range(width):
                self.set(x + dx, y + dy, color)

    def hline(self, x: int, y: int, length: int, color: RGB) -> None:
        self.rect(x, y, length, 1, color)

    def vline(self, x: int, y: int, length: int, color: RGB) -> None:
        self.rect(x, y, 1, length, color)

    def dotted_hline(self, x: int, y: int, length: int, color: RGB, *,
                     on: int = 2, off: int = 3) -> None:
        """점선 격자. 실선 격자는 선 자체와 헷갈려서 추세선이 묻힌다."""
        step = on + off
        for dx in range(length):
            if dx % step < on:
                self.set(x + dx, y, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: RGB, *,
             thickness: int = 1) -> None:
        """Bresenham. 굵게 그릴 때 **진행 방향의 수직으로** 번진다.

        번지는 방향을 기울기로 정하는 이유: 완만한 선(dx ≥ dy)을 수평으로 번지게
        하면 같은 픽셀을 덧칠해서 굵어지지 않고, 급한 선(dy > dx)을 수직으로 번지게
        하면 같은 문제가 생긴다. 꺾은선의 가치가 "이어져 있고 두께가 일정하다"는
        것이라 한쪽만 고르면 어떤 구간에서 선이 1px로 얇아진다.
        """
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        error = dx - dy
        spread = range(-(thickness // 2), thickness - thickness // 2)
        vertical = dx >= dy          # 완만한 선은 위아래로 번진다
        while True:
            for offset in spread:
                if vertical:
                    self.set(x0, y0 + offset, color)
                else:
                    self.set(x0 + offset, y0, color)
            if x0 == x1 and y0 == y1:
                break
            doubled = error * 2
            if doubled > -dy:
                error -= dy
                x0 += sx
            if doubled < dx:
                error += dx
                y0 += sy

    def disc(self, cx: int, cy: int, radius: int, color: RGB) -> None:
        """점 표식. 반지름이 작아 정수 원으로 충분하다."""
        limit = radius * radius + radius
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= limit:
                    self.set(cx + dx, cy + dy, color)

    def to_png(self) -> bytes:
        """필터 0(None)만 쓰는 최소 PNG. 알파가 필요하면 타입 6, 아니면 타입 2."""
        channels = 4 if self.alpha else 3
        raw = bytearray()
        for y in range(self.height):
            raw.append(0)                                   # 스캔라인 필터 = None
            base = y * self.width * 4
            if self.alpha:
                raw += self._pixels[base:base + self.width * 4]
            else:
                for x in range(self.width):
                    offset = base + x * 4
                    raw += self._pixels[offset:offset + 3]

        def chunk(kind: bytes, payload: bytes) -> bytes:
            body = kind + payload
            return (struct.pack(">I", len(payload)) + body
                    + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

        header = struct.pack(">IIBBBBB", self.width, self.height, 8,
                             6 if self.alpha else 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                + chunk(b"IEND", b""))


# ── 5×7 비트맵 폰트 ──────────────────────────────────────────────────
# 숫자와 쉼표뿐이다. 차트의 나머지 글자(GBM 이름·날짜)는 HTML이 그리므로 여기
# 필요한 것이 이것뿐이고, 덕분에 폰트 파일이 필요 없다.

GLYPH_WIDTH, GLYPH_HEIGHT = 5, 7

_GLYPHS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "-": ("00000", "00000", "00000", "01110", "00000", "00000", "00000"),
    " ": ("00000",) * 7,
}

# 글리프가 없는 문자는 이것으로 그린다. 빈칸으로 두면 "숫자가 빠졌다"로 보이는데,
# 네모는 "그릴 수 없는 문자"임이 드러난다.
_FALLBACK = ("11111", "10001", "10001", "10001", "10001", "10001", "11111")


def text_width(text: str, *, spacing: int = 1) -> int:
    if not text:
        return 0
    return len(text) * GLYPH_WIDTH + (len(text) - 1) * spacing


def draw_text(canvas: Canvas, x: int, y: int, text: str, color: RGB, *,
              spacing: int = 1) -> None:
    """왼쪽 위를 (x, y)로 해서 그린다. 범위를 벗어난 부분은 잘린다."""
    cursor = x
    for character in text:
        rows = _GLYPHS.get(character, _FALLBACK)
        for dy, row in enumerate(rows):
            for dx, bit in enumerate(row):
                if bit == "1":
                    canvas.set(cursor + dx, y + dy, color)
        cursor += GLYPH_WIDTH + spacing
