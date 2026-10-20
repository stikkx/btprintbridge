"""Label renderer: QR / multiline text / Code128 composed into a label image.

The image's x-axis runs along the label LENGTH; its tall axis is the tape's
raster height (= ``TAPE_DOTS[tape_width]`` pixels). User-facing layout in one
horizontal row: optional QR left, multiline text middle, optional vertical
Code128 last/right — elements are never stacked above or below each other.
Pure rendering only; printer I/O lives in the transport/raster tasks.
"""

import functools
import os
import subprocess

import qrcode
from PIL import Image, ImageDraw, ImageFont

from btprintbridge import code128

# Raster-height dots per tape width (mm). 12 mm: 76 checked with a
# calibration print (2026-10-20, frames of 84/76/70/62/54 dots); 9/18 mm are
# the values of BinaryStock's former agent, not yet checked here.
TAPE_DOTS: dict[int, int] = {9: 52, 12: 76, 18: 120, 24: 128}

# Nominal label-length budget used to auto-fit text; the extra length after
# the codes is the "remaining width" the widest text line must fit into.
_MAX_LABEL_LENGTH = 1024
# Spacing in dots (180 dpi: 7.1 dots/mm). END_MARGIN is blank tape before the
# first and after the last element (also the codes' quiet zone at the label
# ends); CODE_GAP separates QR / text / barcode; labels shorter than
# MIN_LABEL_LENGTH (1 inch, the printer's minimum) are padded and centered.
END_MARGIN = 21    # ~3 mm
CODE_GAP = 14      # ~2 mm
MIN_LABEL_LENGTH = 180
_MIN_FONT_SIZE = 6
# Text uses the letters' real ink height, not the font's line height (which
# adds empty ascender/descender room): V_PAD blank dots stay at the band's
# top and bottom; lines are LINE_GAP_RATIO of a line apart (min LINE_GAP).
V_PAD = 4
LINE_GAP = 2
LINE_GAP_RATIO = 0.25
MAX_FONT_SIZE = 200
DOTS_PER_MM = 180 / 25.4
_FONT_CANDIDATES = (
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
)


@functools.cache
def _font_path() -> str | None:
    """First installed bold sans font; fontconfig's choice as last resort."""
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out if out and os.path.exists(out) else None


def _load_font(size: int):
    path = _font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def _text_block(font, lines: list[str]) -> tuple[int, int, int]:
    """``(top, pitch, block_height)`` of *lines* drawn with *font*.

    All lines share one pitch (ink height of the tallest line + LINE_GAP) so
    baselines stay evenly spaced (gap ~1/4 line); *top* is the ink offset from the draw
    origin. Blank lines keep their slot.
    """
    boxes = [font.getbbox(line) for line in lines if line.strip()] or [font.getbbox("H")]
    top = min(b[1] for b in boxes)
    ink = max(b[3] for b in boxes) - top
    pitch = ink + max(LINE_GAP, round(ink * LINE_GAP_RATIO))
    return top, pitch, pitch * len(lines) - LINE_GAP


_QR_EC = {"L": qrcode.constants.ERROR_CORRECT_L, "M": qrcode.constants.ERROR_CORRECT_M,
          "Q": qrcode.constants.ERROR_CORRECT_Q, "H": qrcode.constants.ERROR_CORRECT_H}


def _qr_image(data, height: int, ec: str = "M") -> Image.Image:
    """QR *data* in a *height* px tall image. Modules are whole dots (2, 3,
    ...) with the code centered vertically when that fits — evenly sized modules scan far
    better than a code stretched to the band; denser codes are scaled."""
    qr = qrcode.QRCode(border=0, error_correction=_QR_EC.get(ec, _QR_EC["M"]))
    qr.add_data(str(data))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("1")
    n = qr.modules_count
    scale = height // n
    if scale < 2:
        return img.resize((height, height), Image.Resampling.NEAREST)
    side = n * scale
    out = Image.new("1", (side, height), 1)     # only as wide as the code
    out.paste(img.resize((side, side), Image.Resampling.NEAREST), (0, (height - side) // 2))
    return out


def _barcode_image(data, height: int) -> Image.Image:
    """Render Code128 *data* rotated 90° so bars read along the label length.

    The code image (bars spanning ``bar_height=height``) is rotated so its
    long axis lies across the tape width; the module scale is capped so the
    rotated image still fits the tape height.
    """
    modules = code128.code128_modules(str(data))
    # keep the rotated image (tall = len(modules) * scale) within the tape height
    scale = max(1, min(2, height // len(modules)))
    img = code128.modules_to_image(modules, bar_height=height, scale=scale)
    img = img.transpose(Image.Transpose.ROTATE_90)
    if img.size[1] > height:
        img = img.resize((img.size[0], height), Image.Resampling.NEAREST)
    return img


def _resolve_code(value, fallback: str | None) -> str | None:
    """Resolve a ``qr``/``barcode`` spec entry: ``False | {"value": str|null}``.

    Absent/``None``/``False`` → disabled; a dict with a string value → that
    value; a dict with a null value → *fallback* (the label text).
    """
    if isinstance(value, dict):
        return value.get("value") if value.get("value") is not None else fallback
    return None


def qr_value(entry, text: str) -> str | None:
    """QR payload: the override, else the label text verbatim (QR encodes any
    text — line breaks and non-ASCII included)."""
    return _resolve_code(entry, text)


def barcode_value(entry, text: str) -> str | None:
    """Code128 payload: the override verbatim, else the label text with its
    lines joined by single spaces (Code128-B cannot encode a line break)."""
    if isinstance(entry, dict) and entry.get("value") is None:
        return " ".join(line.strip() for line in text.splitlines() if line.strip())
    return _resolve_code(entry, text)


def barcode_problem(value: str) -> str | None:
    """Why *value* cannot be a Code128-B barcode, or None if it can."""
    for ch in value:
        if not 32 <= ord(ch) <= 127:
            return (f"barcode cannot encode {ch!r} — Code128 allows plain ASCII "
                    "only; set an ASCII barcode value or turn the barcode off")
    return None


def _text_extent(font, lines: list[str]) -> tuple[int, int]:
    """``(left, width)`` of the lines' ink along the label (left-aligned)."""
    boxes = [font.getbbox(line) for line in lines if line.strip()] or [font.getbbox("H")]
    left = min(b[0] for b in boxes)
    return left, max(b[2] for b in boxes) - left


def _fit_text(lines: list[str], height: int, remaining_width: int, max_size: int | None = None):
    """Largest font (at most *max_size*) whose text block fits the band
    (minus V_PAD) and the remaining width; ``_MIN_FONT_SIZE`` when nothing
    fits (caller checks)."""
    usable = height - 2 * V_PAD

    def fits(size):
        font = _load_font(size)
        if _text_block(font, lines)[2] > usable:
            return False
        return _text_extent(font, lines)[1] <= remaining_width

    lo, hi = _MIN_FONT_SIZE, max(_MIN_FONT_SIZE, max_size or height * 2)
    if not fits(lo):
        return _load_font(lo)
    while lo < hi:                      # largest size that fits
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return _load_font(lo)


def render_label(spec: dict, tape_width: int) -> Image.Image:
    """Render a label spec into a 1-bit image ``TAPE_DOTS[tape_width]`` px tall.

    ``spec`` keys: ``text`` (multiline string), ``qr`` / ``barcode`` as
    ``False | {"value": str|null}`` — a null value falls back to the label
    text; ``font_size`` ``"auto"`` (default: as large as fits) or a size in
    dots, shrunk if it does not fit. ``image.info["font_size"]`` is the size
    used. Raises ``ValueError`` for an unsupported tape width,
    ``ValueError("nothing to print")`` when text is empty/whitespace-only and
    every enabled code resolves empty, and ``ValueError("label does not fit
    on tape of N mm")`` when even the minimum font size cannot fit the text.
    """
    if tape_width not in TAPE_DOTS:
        raise ValueError(f"unsupported tape width: {tape_width}mm")
    height = TAPE_DOTS[tape_width]

    text = str(spec.get("text") or "")
    font_size = spec.get("font_size")
    max_size = font_size if isinstance(font_size, int) and not isinstance(font_size, bool) else None
    lines = text.split("\n") if text.strip() else []
    qr_data = qr_value(spec.get("qr"), text)
    barcode_data = barcode_value(spec.get("barcode"), text)
    if not lines and not qr_data and not barcode_data:
        raise ValueError("nothing to print")

    qr_entry = spec.get("qr")
    qr_ec = qr_entry.get("ec", "M") if isinstance(qr_entry, dict) else "M"
    qr_img = _qr_image(qr_data, height, qr_ec) if qr_data else None
    barcode_img = _barcode_image(barcode_data, height) if barcode_data else None

    segments = [img for img in (qr_img, barcode_img) if img is not None]
    if lines:
        segments.insert(1 if qr_img is not None else 0, None)  # None marks the text slot
    reserved = sum(seg.size[0] if seg is not None else 0 for seg in segments)
    reserved += CODE_GAP * (len(segments) - 1) + 2 * END_MARGIN
    remaining_width = max(1, _MAX_LABEL_LENGTH - reserved)

    text_width = text_left = 0
    font = None
    if lines:
        font = _fit_text(lines, height, remaining_width, max_size)
        text_left, text_width = _text_extent(font, lines)
        if text_width > remaining_width or _text_block(font, lines)[2] > height - 2 * V_PAD:
            raise ValueError(f"label does not fit on tape of {tape_width} mm")

    content = int(sum(
        seg.size[0] if seg is not None else text_width for seg in segments
    ) + CODE_GAP * (len(segments) - 1))
    width = max(content + 2 * END_MARGIN, MIN_LABEL_LENGTH)
    canvas = Image.new("1", (width, height), 1)
    canvas.info["font_size"] = font.size if font is not None else 0
    draw = ImageDraw.Draw(canvas)

    x = (width - content) // 2
    for seg in segments:
        if seg is not None:
            canvas.paste(seg, (x, 0))
            x += seg.size[0] + CODE_GAP
            continue
        top, pitch, block = _text_block(font, lines)
        y = (height - block) // 2 - top
        for i, line in enumerate(lines):
            draw.text((x - text_left, y + i * pitch), line, font=font, fill=0)
        x += int(text_width) + CODE_GAP
    return canvas