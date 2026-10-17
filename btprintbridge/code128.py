"""Code 128-B encoder: text to 0/1 bar modules, and modules to a PIL image.

Encodes ASCII 32-127 using Code Set B (start code 104) with the mandatory
mod-103 check symbol and the 13-module stop pattern (stop symbol + 2-module
termination bar). The 107-entry pattern table is copied verbatim from the
"Code 128" article's bar pattern table on Wikipedia (matching ISO/IEC 15417);
it is standard published data and is deliberately not derived here.
"""

from PIL import Image, ImageDraw

_START_B = 104
_CHECK_MOD = 103
_STOP_PATTERN = "1100011101011"  # stop symbol (11 modules) + 2-module termination bar

# Code 128 patterns, values 0-106 (103 data + 3 starts), verbatim from the
# Wikipedia "Code 128" article bar-pattern table (11 modules per symbol).
_PATTERNS = (
    "11011001100",  # 0
    "11001101100",  # 1
    "11001100110",  # 2
    "10010011000",  # 3
    "10010001100",  # 4
    "10001001100",  # 5
    "10011001000",  # 6
    "10011000100",  # 7
    "10001100100",  # 8
    "11001001000",  # 9
    "11001000100",  # 10
    "11000100100",  # 11
    "10110011100",  # 12
    "10011011100",  # 13
    "10011001110",  # 14
    "10111001100",  # 15
    "10011101100",  # 16
    "10011100110",  # 17
    "11001110010",  # 18
    "11001011100",  # 19
    "11001001110",  # 20
    "11011100100",  # 21
    "11001110100",  # 22
    "11101101110",  # 23
    "11101001100",  # 24
    "11100101100",  # 25
    "11100100110",  # 26
    "11101100100",  # 27
    "11100110100",  # 28
    "11100110010",  # 29
    "11011011000",  # 30
    "11011000110",  # 31
    "11000110110",  # 32
    "10100011000",  # 33
    "10001011000",  # 34
    "10001000110",  # 35
    "10110001000",  # 36
    "10001101000",  # 37
    "10001100010",  # 38
    "11010001000",  # 39
    "11000101000",  # 40
    "11000100010",  # 41
    "10110111000",  # 42
    "10110001110",  # 43
    "10001101110",  # 44
    "10111011000",  # 45
    "10111000110",  # 46
    "10001110110",  # 47
    "11101110110",  # 48
    "11010001110",  # 49
    "11000101110",  # 50
    "11011101000",  # 51
    "11011100010",  # 52
    "11011101110",  # 53
    "11101011000",  # 54
    "11101000110",  # 55
    "11100010110",  # 56
    "11101101000",  # 57
    "11101100010",  # 58
    "11100011010",  # 59
    "11101111010",  # 60
    "11001000010",  # 61
    "11110001010",  # 62
    "10100110000",  # 63
    "10100001100",  # 64
    "10010110000",  # 65
    "10010000110",  # 66
    "10000101100",  # 67
    "10000100110",  # 68
    "10110010000",  # 69
    "10110000100",  # 70
    "10011010000",  # 71
    "10011000010",  # 72
    "10000110100",  # 73
    "10000110010",  # 74
    "11000010010",  # 75
    "11001010000",  # 76
    "11110111010",  # 77
    "11000010100",  # 78
    "10001111010",  # 79
    "10100111100",  # 80
    "10010111100",  # 81
    "10010011110",  # 82
    "10111100100",  # 83
    "10011110100",  # 84
    "10011110010",  # 85
    "11110100100",  # 86
    "11110010100",  # 87
    "11110010010",  # 88
    "11011011110",  # 89
    "11011110110",  # 90
    "11110110110",  # 91
    "10101111000",  # 92
    "10100011110",  # 93
    "10001011110",  # 94
    "10111101000",  # 95
    "10111100010",  # 96
    "11110101000",  # 97
    "11110100010",  # 98
    "10111011110",  # 99
    "10111101110",  # 100
    "11101011110",  # 101
    "11110101110",  # 102
    "11010000100",  # 103 Start Code A
    "11010010000",  # 104 Start Code B
    "11010011100",  # 105 Start Code C
)


def code128_modules(text: str) -> list[int]:
    """Encode *text* as Code 128-B and return the 0/1 module sequence.

    Dark bars are 1 and light gaps are 0. The output is Start Code B, one
    symbol (11 modules) per character, the mod-103 check symbol, then the
    13-module stop pattern. Raises ``ValueError`` on characters outside
    ASCII 32-127.
    """
    values = []
    for ch in text:
        code = ord(ch)
        if not 32 <= code <= 127:
            raise ValueError(f"character outside ASCII 32-127: {ch!r} (U+{code:04X})")
        values.append(code - 32)
    checksum = (_START_B + sum((i + 1) * v for i, v in enumerate(values))) % _CHECK_MOD
    symbols = [_START_B] + values + [checksum]
    modules = []
    for value in symbols:
        modules.extend(int(bit) for bit in _PATTERNS[value])
    modules.extend(int(bit) for bit in _STOP_PATTERN)
    return modules


def modules_to_image(modules, bar_height: int, scale: int = 2) -> Image.Image:
    """Render a 0/1 module sequence as a monochrome PIL image.

    Each dark module becomes a ``scale``-pixel-wide black bar spanning the
    full ``bar_height``; light modules stay white gaps of the same width.
    """
    img = Image.new("1", (len(modules) * scale, bar_height), 1)
    draw = ImageDraw.Draw(img)
    for i, module in enumerate(modules):
        if module:
            draw.rectangle([i * scale, 0, (i + 1) * scale - 1, bar_height - 1], fill=0)
    return img