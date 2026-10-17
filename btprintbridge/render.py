"""Label renderer: QR / multiline text / Code128 composed into a label image.

The image's x-axis runs along the label LENGTH; its tall axis is the tape's
raster height (= ``TAPE_DOTS[tape_width]`` pixels). User-facing layout in one
horizontal row: optional QR left, multiline text middle, optional vertical
Code128 last/right — elements are never stacked above or below each other.
Pure rendering only; printer I/O lives in the transport/raster tasks.
"""

import qrcode
from PIL import Image, ImageDraw, ImageFont

from btprintbridge import code128

# Raster-height dots per tape width (mm). VERIFY WITH REAL PRINTS (spec item #3).
TAPE_DOTS: dict[int, int] = {9: 32, 12: 54, 18: 86, 24: 128}

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
_FONT_PATH = "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"


def _load_font(size: int):
    try:
        return ImageFont.truetype(_FONT_PATH, size)
    except OSError:
        return ImageFont.load_default(size)


def _qr_image(data, height: int) -> Image.Image:
    """Render QR *data* as a borderless square image of *height* px per side."""
    qr = qrcode.QRCode(border=0)
    qr.add_data(str(data))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("1")
    return img.resize((height, height), Image.Resampling.NEAREST)


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


def _fit_text(lines: list[str], height: int, remaining_width: int):
    """Pick the largest font size whose lines fit height and remaining width.

    Starts at printable height / line count and shrinks (floor at
    ``_MIN_FONT_SIZE``) until every constraint holds; returns the final font.
    """
    size = max(_MIN_FONT_SIZE, height // max(1, len(lines)))
    font = _load_font(size)
    while size > _MIN_FONT_SIZE:
        ascent, descent = font.getmetrics()
        if (ascent + descent) * len(lines) > height:
            size -= 1
            font = _load_font(size)
            continue
        if max(font.getlength(line) for line in lines) <= remaining_width:
            break
        size -= 1
        font = _load_font(size)
    return font


def render_label(spec: dict, tape_width: int) -> Image.Image:
    """Render a label spec into a 1-bit image ``TAPE_DOTS[tape_width]`` px tall.

    ``spec`` keys: ``text`` (multiline string), ``qr`` / ``barcode`` as
    ``False | {"value": str|null}`` — a null value falls back to the label
    text. Raises ``ValueError`` for an unsupported tape width,
    ``ValueError("nothing to print")`` when text is empty/whitespace-only and
    every enabled code resolves empty, and ``ValueError("label does not fit
    on tape of N mm")`` when even the minimum font size cannot fit the text.
    """
    if tape_width not in TAPE_DOTS:
        raise ValueError(f"unsupported tape width: {tape_width}mm")
    height = TAPE_DOTS[tape_width]

    text = str(spec.get("text") or "")
    lines = text.split("\n") if text.strip() else []
    qr_data = qr_value(spec.get("qr"), text)
    barcode_data = barcode_value(spec.get("barcode"), text)
    if not lines and not qr_data and not barcode_data:
        raise ValueError("nothing to print")

    qr_img = _qr_image(qr_data, height) if qr_data else None
    barcode_img = _barcode_image(barcode_data, height) if barcode_data else None

    segments = [img for img in (qr_img, barcode_img) if img is not None]
    if lines:
        segments.insert(1 if qr_img is not None else 0, None)  # None marks the text slot
    reserved = sum(seg.size[0] if seg is not None else 0 for seg in segments)
    reserved += CODE_GAP * (len(segments) - 1) + 2 * END_MARGIN
    remaining_width = max(1, _MAX_LABEL_LENGTH - reserved)

    text_width = 0
    font = None
    if lines:
        font = _fit_text(lines, height, remaining_width)
        text_width = max(font.getlength(line) for line in lines)
        ascent, descent = font.getmetrics()
        if text_width > remaining_width or (ascent + descent) * len(lines) > height:
            raise ValueError(f"label does not fit on tape of {tape_width} mm")

    content = int(sum(
        seg.size[0] if seg is not None else text_width for seg in segments
    ) + CODE_GAP * (len(segments) - 1))
    width = max(content + 2 * END_MARGIN, MIN_LABEL_LENGTH)
    canvas = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(canvas)

    x = (width - content) // 2
    for seg in segments:
        if seg is not None:
            canvas.paste(seg, (x, 0))
            x += seg.size[0] + CODE_GAP
            continue
        ascent, descent = font.getmetrics()
        advance = ascent + descent
        y = (height - advance * len(lines)) // 2
        for i, line in enumerate(lines):
            draw.text((x, y + i * advance), line, font=font, fill=0)
        x += int(text_width) + CODE_GAP
    return canvas