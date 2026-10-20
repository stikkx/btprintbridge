"""Print pipeline for the Brother PT-P710BT: label-spec validation, raster
streaming and per-copy status handling.

Consumes transport (socket, lock, status query), raster (packed bits,
zero-line/0x47 commands), render (text/QR/Code128 label composition) and the
status parser; produces ``print_spec``. The wire sequence follows the
community reference tool ``pt-p710bt-label-maker``.
"""

import io

from btprintbridge import raster
from btprintbridge.render import TAPE_DOTS, barcode_problem, barcode_value, render_label
from btprintbridge.status import parse_status_packet
from btprintbridge.transport import (
    PrinterError,
    locked,
    open_printer,
    query_status,
    recv_packet,
)

# Per page, after the status query supplied invalidate + init once.
RASTER_MODE = b"\x1b\x69\x61\x01"  # dynamic command mode: raster
STATUS_NOTIFY = b"\x1b\x69\x21\x00"  # automatic status notification: notify

# Mode settings, re-sent on every page (see page pattern below).
VARIOUS_MODES = b"\x1b\x69\x4d\x40"  # auto-cut
ADVANCED_MODES = b"\x1b\x69\x4b\x00"  # chain mode on (non-final pages only)
MARGIN = b"\x1b\x69\x64\x00\x00"  # feed margin 0
COMPRESSION = b"\x4d\x02"  # TIFF packbits

# Per copy: print info (1B 69 7A 84 00 <width> 00 <length 4B> 00 00), raster
# lines, print command. Copies repeat from the print info on.
PRINT_INFO_PREFIX = b"\x1b\x69\x7a\x84\x00"
HEAD_BPL = 16  # raster lines are always full head width: 128 dots = 16 bytes
PRINT = b"\x1a"

# Page pattern (BinaryStock's hardware-proven sequence) for every job: each
# page carries its full settings; non-final pages keep chaining ON and end in
# 0x0c (print, no feed); the FINAL page switches chaining OFF and ends in 0x1a
# (print + feed + cut). With chaining left ON on the last page the printer
# holds the label until the next job arrives — the cut came one job late
# (user-observed 2026-10-15). cut="each" keeps auto-cut on for every page;
# cut="end" disables it on non-final pages (one strip, single final cut).
VARIOUS_MODES_NO_CUT = b"\x1b\x69\x4d\x00"
ADVANCED_MODES_CHAIN_OFF = b"\x1b\x69\x4b\x08"
PRINT_NO_FEED = b"\x0c"

CUT_MODES = ("each", "end")
MAX_COPIES = 100


def _validate_spec(spec: dict, copies, cut=None) -> tuple[int, str]:
    """Validate the spec at entry (before any socket opens).

    Returns ``(copies, cut)``; the *copies*/*cut* parameters override the
    spec's own fields. ``image`` is a ``.png`` path (CLI) or raw PNG bytes
    (HTTP API).
    """
    text = spec.get("text")
    image = spec.get("image")
    if (text is None) == (image is None):
        raise ValueError("spec must contain exactly one of 'text' or 'image'")
    if text is not None:
        if not isinstance(text, str) or not text:
            raise ValueError("'text' must be a non-empty string")
        for key in ("qr", "barcode"):
            entry = spec.get(key)
            if isinstance(entry, dict) and not isinstance(entry.get("value"), (str, type(None))):
                raise ValueError(f"{key} value must be a string or null")
        barcode = barcode_value(spec.get("barcode"), text)
        if barcode is not None:
            problem = barcode_problem(barcode)
            if problem:
                raise ValueError(problem)
    elif isinstance(image, (bytes, bytearray)):
        if not image:
            raise ValueError("'image' must not be empty")
    elif not isinstance(image, str) or not image.endswith(".png"):
        raise ValueError("'image' must be a path to a .png file")

    total = copies if copies is not None else spec.get("copies", 1)
    if (isinstance(total, bool) or not isinstance(total, int)
            or not 1 <= total <= MAX_COPIES):
        raise ValueError(f"copies must be an integer between 1 and {MAX_COPIES}")
    mode = cut if cut is not None else spec.get("cut", "each")
    if mode not in CUT_MODES:
        raise ValueError("cut must be 'each' or 'end'")
    return total, mode


def _raster_data(spec: dict, media_width: int) -> bytes:
    """Packed raster bits for the spec at the queried media width.

    Image specs must match ``TAPE_DOTS[media_width]`` pixels high
    (``png_to_alpha_raster`` rejects with the exact expected count).
    """
    if spec.get("image") is not None:
        if media_width not in TAPE_DOTS:
            raise ValueError(f"unsupported tape width: {media_width}mm")
        image = spec["image"]
        if isinstance(image, (bytes, bytearray)):
            image = io.BytesIO(image)
        try:
            return raster.png_to_alpha_raster(image, TAPE_DOTS[media_width])
        except OSError as e:  # PIL.UnidentifiedImageError is an OSError
            raise ValueError(f"image is not a decodable PNG: {e}") from e
    return raster.pil_image_to_raster_bits(render_label(spec, media_width))


def _send(sock, data: bytes) -> None:
    """Send wrapper: OSError/timeout -> PrinterError (same contract as recv)."""
    try:
        sock.send(data)
    except OSError as e:
        raise PrinterError(f"communication failed: {e}") from e


def _read_status(sock) -> dict:
    return parse_status_packet(recv_packet(sock))


def _await_completed(sock) -> None:
    """Wait for ``printing_completed``; error/turned_off raise, rest ignored."""
    while True:
        status = _read_status(sock)
        status_type = status["status_type"]
        if status_type == "printing_completed":
            return
        if status_type == "error":
            errors = ", ".join(status["errors"]) or "unspecified error"
            raise PrinterError(f"printer error: {errors}", code="NOT_READY")
        if status_type == "turned_off":
            raise PrinterError("printer turned off")
        # reply / notification / phase_change / unknown: ignored


def _embed_in_head_band(inner: bytes, dots: int, head_bpl: int = 16) -> bytes:
    """Embed centered printable-band bits into full-width head lines.

    The PT-P710BT head is always ``head_bpl * 8`` dots wide and raster lines
    are always ``head_bpl`` bytes regardless of tape size; the printable band
    of a narrower tape (``dots`` = ``TAPE_DOTS[width]``, ``ceil(dots/8)``
    bytes per inner line) is centered inside each head line: band dot ``y``
    lands on head bit ``(head_dots - dots) // 2 + y`` (verified live-fire).
    Identity for a band that already fills the head (24 mm). MSB-first.
    """
    head_dots = head_bpl * 8
    bpl_inner = (dots + 7) // 8
    if dots == head_dots and bpl_inner == head_bpl:
        return inner
    lines = len(inner) // bpl_inner
    offset = (head_dots - dots) // 2
    canvas = bytearray(lines * head_bpl)
    for line_idx in range(lines):
        src = inner[line_idx * bpl_inner : (line_idx + 1) * bpl_inner]
        dst = bytearray(head_bpl)
        for i in range(dots):
            if src[i // 8] & (0x80 >> (i % 8)):
                y = offset + i
                dst[y // 8] |= 0x80 >> (y % 8)
        canvas[line_idx * head_bpl : (line_idx + 1) * head_bpl] = dst
    return bytes(canvas)


def _print_page(sock, media_width: int, canvas: bytes, cut: bool, final: bool) -> None:
    """Stream one page: settings, raster lines, terminator.

    ``canvas`` is 16-byte head lines from ``_embed_in_head_band``; the
    print-info field carries the number of raster lines.
    """
    _send(sock, RASTER_MODE)
    _send(sock, STATUS_NOTIFY)
    _send(
        sock,
        PRINT_INFO_PREFIX
        + bytes([media_width])
        + b"\x00"
        + (len(canvas) // HEAD_BPL).to_bytes(4, "little")
        + b"\x00\x00",
    )
    _send(sock, VARIOUS_MODES if cut else VARIOUS_MODES_NO_CUT)
    _send(sock, ADVANCED_MODES_CHAIN_OFF if final else ADVANCED_MODES)
    _send(sock, MARGIN)
    _send(sock, COMPRESSION)
    for line in raster.rasterize(canvas, bytes_per_line=HEAD_BPL):
        _send(sock, line)
    _send(sock, PRINT if final else PRINT_NO_FEED)


def print_spec(spec: dict, mac, channel=None, copies: int | None = None, force=False,
               wait_timeout: float = 30.0, cut: str | None = None,
               lock_wait: float = 0) -> dict:
    """Validate *spec*, then print it as N cut labels over one RFCOMM session.

    The status query opens the socket and initializes the printer; each copy
    is one page (see the page pattern above), the last page feeds and cuts
    immediately. The socket read
    timeout is raised to *wait_timeout* after connecting (keep *open_printer*'s
    short connect timeout) because printing + cutting one label takes several
    seconds. ``spec.tape_width``, when present, must match the queried media
    width unless *force*. *cut* ``"each"`` (default) cuts every copy;
    ``"end"`` prints one strip cut once at the end. A printer reporting error
    flags is refused before anything is streamed (``NOT_READY``).
    *lock_wait* lets a caller queue behind a running job. *channel* None
    finds the printer's RFCOMM channel (see ``open_printer``).
    Returns ``{"copies_printed": n, "media_width": mm}``.
    """
    total, mode = _validate_spec(spec, copies, cut)
    with locked(wait=lock_wait):
        sock = open_printer(mac, channel)
        try:
            sock.settimeout(wait_timeout)
            status = query_status(sock)
            media_width = status["media_width"]
            if status.get("errors"):
                raise PrinterError(
                    "printer not ready: " + ", ".join(status["errors"]),
                    code="NOT_READY",
                )
            if media_width not in TAPE_DOTS:
                raise PrinterError(
                    f"no supported tape loaded (reported width {media_width} mm)",
                    code="NOT_READY",
                )
            tape_width = spec.get("tape_width")
            if tape_width is not None and tape_width != media_width and not force:
                raise PrinterError(
                    f"loaded tape is {media_width} mm but spec requests {tape_width} mm"
                    " (use force to override)",
                    code="TAPE_MISMATCH",
                )
            inner = _raster_data(spec, media_width)
            canvas = _embed_in_head_band(inner, TAPE_DOTS[media_width])
            for copy in range(total):
                final = copy == total - 1
                _print_page(sock, media_width, canvas,
                            cut=(mode == "each" or final), final=final)
                _await_completed(sock)
        finally:
            sock.close()
    return {"copies_printed": total, "media_width": media_width}
