"""Raster engine for the Brother PT-P710BT: PackBits compression and raster line generation.

Raster lines are always 16 bytes = the 128-dot print head width, regardless of
tape size; narrower tapes' printable bands (``TAPE_DOTS`` dots) are embedded
centered into the head lines by ``print._embed_in_head_band`` before they get
here. Each line is prefixed with ``0x47`` + 2-byte little-endian length +
PackBits-compressed payload, or the single-byte ``0x5A`` zero-line command.
The compression mode ``0x4D 0x02`` is selected by the print task before the
raster data (see task 7).
"""

from PIL import Image

BYTES_PER_LINE = 16


def packbits_encode(data: bytes) -> bytes:
    """PackBits-compress *data* (TIFF PackBits, compression mode 0x02).

    Runs of >= 2 identical bytes: control (257 - n), n in 2..128, then the
    repeated byte. Literals of n bytes, n in 1..128: control (n - 1), then
    the raw bytes. Empty input returns empty output.
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        run = 1
        while run < 128 and i + run < n and data[i + run] == data[i]:
            run += 1
        if run >= 2:
            out.append((257 - run) & 0xFF)
            out.append(data[i])
            i += run
            continue
        start = i
        while i < n and i - start < 128 and not (i + 1 < n and data[i] == data[i + 1]):
            i += 1
        out.append(i - start - 1)
        out += data[start:i]
    return bytes(out)


def pil_image_to_raster_bits(image: Image.Image) -> bytes:
    """Convert a PIL image's ink pixels to packed raster bits, 1 bpp MSB-first.

    The ink rule is mode-aware: images with an alpha channel (RGBA/LA) keep
    the transparent-PNG contract ``alpha > 0 -> ink``; opaque monochrome
    images (mode '1'/'L', as produced by ``render_label``) use dark-on-light
    ``pixel value < 128 -> ink``. Rows are transposed to columns (rotate 90)
    so each line runs along the tape: output line *x* is image column *x*.
    """
    if "A" in image.getbands():
        ink = image.convert("RGBA").getchannel("A")
    else:
        ink = image.convert("L").point(lambda v: 255 if v < 128 else 0)
    w, h = ink.size
    bytes_per_line = (h + 7) // 8
    alpha = ink.tobytes()
    out = bytearray(w * bytes_per_line)
    for y in range(h):
        row = y * w
        shift = 0x80 >> (y % 8)
        byte = y // 8
        for x in range(w):
            if alpha[row + x]:
                out[x * bytes_per_line + byte] |= shift
    return bytes(out)


def png_to_alpha_raster(path, height: int) -> bytes:
    """Load a PNG (path or file object) and return its packed raster bits.

    Expects *height* pixels. Transparent PNGs: alpha > 0 is ink; PNGs without
    a meaningful alpha channel (uniform alpha): luminance < 128 is ink.
    """
    with Image.open(path) as im:
        if im.height != height:
            raise ValueError(f"image height is {im.height}, expected {height} pixels")
        rgba = im.convert("RGBA")
        low, high = rgba.getchannel("A").getextrema()
        if low == high:
            # No meaningful alpha (opaque or uniform): dark-on-light ink.
            return pil_image_to_raster_bits(rgba.convert("L"))
        return pil_image_to_raster_bits(rgba)


def rasterize(data: bytes, bytes_per_line: int = BYTES_PER_LINE) -> list[bytes]:
    """Split *data* into raster lines and produce the 0x47/0x5A commands.

    The trailing remainder of the last line is zero-padded before packing.
    All-zero lines emit the ``0x5A`` command; other lines emit ``0x47`` +
    2-byte little-endian compressed length + PackBits payload.
    """
    padded = data + b"\x00" * (-len(data) % bytes_per_line)
    out = []
    for off in range(0, len(padded), bytes_per_line):
        chunk = padded[off : off + bytes_per_line]
        if not any(chunk):
            out.append(b"\x5A")
            continue
        compressed = packbits_encode(chunk)
        out.append(b"\x47" + len(compressed).to_bytes(2, "little") + compressed)
    return out