import socket

import pytest
from btprintbridge import transport
from PIL import Image

from btprintbridge import print as print_module
from btprintbridge import raster as raster_module
from btprintbridge.print import _embed_in_head_band, print_spec
from btprintbridge.render import TAPE_DOTS, render_label


class FakeSock:
    """Replays pre-loaded 32-byte status packets; records all sent bytes."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.sent = b""
        self.chunks = []
        self.closed = False
        self.timeouts = []

    def send(self, data):
        self.sent += data
        self.chunks.append(data)

    def recv(self, n):
        if not self._replies:
            return b""
        return self._replies.pop(0)

    def close(self):
        self.closed = True

    def settimeout(self, t):
        self.timeouts.append(t)


def status_packet(width, status_type=0x01, err1=0, err2=0):
    pkt = bytearray(32)
    pkt[:3] = b"\x80\x20\x42"
    pkt[8] = err1
    pkt[9] = err2
    pkt[10] = width
    pkt[18] = status_type
    return bytes(pkt)


def completed_packet(width=12):
    return status_packet(width, status_type=0x01)


def error_packet(err1=0, err2=0):
    return status_packet(12, status_type=0x02, err1=err1, err2=err2)


def terminators(sock):
    """Page terminators (0x0c print-no-feed / 0x1a print+feed) in send order."""
    return [c for c in sock.chunks if c in (b"\x0c", b"\x1a")]


def make_png(path, width, height):
    """RGBA png: left half opaque black, right half transparent."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(width):
        for y in range(height):
            if x < width // 2:
                img.putpixel((x, y), (0, 0, 0, 255))
    img.save(path, format="PNG")


def fake_transport(monkeypatch, width, replies):
    sock = FakeSock(replies)
    monkeypatch.setattr(print_module, "open_printer", lambda mac, channel: sock)
    monkeypatch.setattr(
        print_module,
        "query_status",
        lambda s: {
            "media_type": "laminated",
            "media_width": width,
            "auto_cut": False,
            "status_type": "reply",
            "errors": [],
        },
    )
    return sock


def test_stream_contains_modes_and_n_print_commands(monkeypatch):
    # one "printing_completed" status packet per copy
    sock = fake_transport(monkeypatch, 12, [completed_packet(12) for _ in range(3)])
    result = print_spec(
        {"text": "Hi", "qr": False, "barcode": False, "copies": 3},
        "00:11:22:33:44:55",
    )
    stream = sock.sent
    assert terminators(sock) == [b"\x0c", b"\x0c", b"\x1a"]  # last page feeds + cuts now
    assert stream.count(b"\x1b\x69\x4d\x40") == 3           # auto-cut on every page
    assert stream.count(b"\x1b\x69\x4b\x00") == 2           # chaining on between pages
    assert stream.count(b"\x1b\x69\x4b\x08") == 1           # chaining OFF on the last page
    assert b"\x1b\x69\x7a\x84\x00\x0c" in stream            # media width 12mm from queried status
    assert result == {"copies_printed": 3, "media_width": 12}


def test_copies_repeat_from_print_info_without_reinitializing(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12) for _ in range(2)])
    print_spec({"text": "Hi", "copies": 2}, "00:11:22:33:44:55")
    stream = sock.sent
    assert b"\x00" * 100 not in stream                      # no invalidate re-sent
    assert b"\x1b\x40" not in stream                        # no init re-sent
    assert stream.startswith(b"\x1b\x69\x61\x01\x1b\x69\x21\x00")  # raster+notify first
    assert stream.count(b"\x1b\x69\x7a\x84\x00\x0c") == 2   # print info per copy
    assert stream.count(b"\x1b\x69\x4d\x40") == 2           # full settings per page
    assert b"\x1b\x69\x64\x00\x00" in stream                # margin 0


def test_error_status_raises_printer_error(monkeypatch):
    fake_transport(monkeypatch, 12, [error_packet(err2=0x10)])  # cover open
    with pytest.raises(transport.PrinterError, match="printer error"):
        print_spec({"text": "Hi"}, "00:11:22:33:44:55")


def test_turned_off_status_raises_printer_error(monkeypatch):
    fake_transport(monkeypatch, 12, [status_packet(12, status_type=0x04)])
    with pytest.raises(transport.PrinterError, match="turned off"):
        print_spec({"text": "Hi"}, "00:11:22:33:44:55")


def test_other_status_types_ignored_until_completed(monkeypatch):
    replies = [
        status_packet(12, status_type=0x06),   # phase change: ignored
        status_packet(12, status_type=0x05),   # notification: ignored
        completed_packet(12),
    ]
    sock = fake_transport(monkeypatch, 12, replies)
    result = print_spec({"text": "Hi"}, "00:11:22:33:44:55")
    assert result == {"copies_printed": 1, "media_width": 12}


def test_spec_tape_width_mismatch_raises_unless_forced(monkeypatch):
    fake_transport(monkeypatch, 12, [completed_packet(12)])
    with pytest.raises(transport.PrinterError, match="tape"):
        print_spec({"text": "Hi", "tape_width": 18}, "00:11:22:33:44:55")
    result = print_spec(
        {"text": "Hi", "tape_width": 18}, "00:11:22:33:44:55", force=True
    )
    assert result["media_width"] == 12


def test_image_height_mismatch_rejected(monkeypatch, tmp_path):
    make_png(tmp_path / "img.png", 8, 124)
    fake_transport(monkeypatch, 24, [completed_packet(24)])
    with pytest.raises(ValueError, match="expected 128"):
        print_spec({"image": str(tmp_path / "img.png")}, "00:11:22:33:44:55")


def test_image_spec_prints_with_matching_height(monkeypatch, tmp_path):
    make_png(tmp_path / "img.png", 8, TAPE_DOTS[12])
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    result = print_spec({"image": str(tmp_path / "img.png")}, "00:11:22:33:44:55")
    assert result == {"copies_printed": 1, "media_width": 12}
    assert b"\x1a" in sock.sent


def test_text_and_image_are_mutually_exclusive(monkeypatch, tmp_path):
    make_png(tmp_path / "img.png", 8, 32)
    with pytest.raises(ValueError, match="exactly one"):
        print_spec(
            {"text": "Hi", "image": str(tmp_path / "img.png")},
            "00:11:22:33:44:55",
        )


def test_empty_or_missing_label_raises():
    with pytest.raises(ValueError, match="exactly one"):
        print_spec({}, "00:11:22:33:44:55")
    with pytest.raises(ValueError, match="non-empty"):
        print_spec({"text": ""}, "00:11:22:33:44:55")


def test_image_spec_requires_png_path():
    with pytest.raises(ValueError, match="\\.png"):
        print_spec({"image": "img.jpg"}, "00:11:22:33:44:55")


def test_copies_must_be_int_ge_one():
    with pytest.raises(ValueError, match="copies"):
        print_spec({"text": "Hi"}, "00:11:22:33:44:55", copies=0)
    with pytest.raises(ValueError, match="copies"):
        print_spec({"text": "Hi", "copies": "2"}, "00:11:22:33:44:55")


def test_copies_param_overrides_spec_copies(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12) for _ in range(5)])
    result = print_spec(
        {"text": "Hi", "copies": 5}, "00:11:22:33:44:55", copies=3
    )
    assert result == {"copies_printed": 3, "media_width": 12}
    assert terminators(sock) == [b"\x0c", b"\x0c", b"\x1a"]


def test_multiline_text_with_qr_fallback_prints(monkeypatch):
    # Regression: multi-line label + QR (empty override) was rejected.
    fake_transport(monkeypatch, 12, [completed_packet(12)])
    result = print_spec({"text": "Shelf A-3\nDrawer 2", "qr": {"value": None}},
                        "00:11:22:33:44:55")
    assert result["copies_printed"] == 1


def test_multiline_text_with_barcode_fallback_prints(monkeypatch):
    fake_transport(monkeypatch, 12, [completed_packet(12)])
    result = print_spec({"text": "Shelf A-3\nDrawer 2", "barcode": {"value": None}},
                        "00:11:22:33:44:55")
    assert result["copies_printed"] == 1


def test_qr_accepts_unicode_override(monkeypatch):
    fake_transport(monkeypatch, 12, [completed_packet(12)])
    result = print_spec({"text": "x", "qr": {"value": "Büro\nRegal 3"}}, "00:11:22:33:44:55")
    assert result["copies_printed"] == 1


def test_barcode_override_with_unencodable_char_rejected():
    with pytest.raises(ValueError, match=r"barcode cannot encode '\\x00'"):
        print_spec({"text": "Hi", "barcode": {"value": "h\x00llo"}}, "00:11:22:33:44:55")


def test_barcode_fallback_with_umlaut_names_the_character():
    with pytest.raises(ValueError, match="barcode.*'ü'"):
        print_spec({"text": "Büro", "barcode": {"value": None}}, "00:11:22:33:44:55")


def test_code_value_multiline_text_with_disabled_code_ok(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    result = print_spec({"text": "a\nb", "qr": False}, "00:11:22:33:44:55")
    assert result == {"copies_printed": 1, "media_width": 12}
    assert b"\x1a" in sock.sent


def test_send_timeout_wrapped_as_printer_error(monkeypatch):
    timeout_sock = FakeSock([completed_packet(12)])

    def raise_timeout(data):
        raise socket.timeout()

    timeout_sock.send = raise_timeout
    monkeypatch.setattr(print_module, "open_printer", lambda mac, channel: timeout_sock)
    monkeypatch.setattr(
        print_module,
        "query_status",
        lambda s: {
            "media_type": "laminated",
            "media_width": 12,
            "auto_cut": False,
            "status_type": "reply",
            "errors": [],
        },
    )
    with pytest.raises(transport.PrinterError, match="communication failed"):
        print_spec({"text": "Hi"}, "00:11:22:33:44:55")


def _png_raster_bytes(width_px, height_px):
    """Packed bit length for an opacity-left-half png of this size."""
    return width_px * ((height_px + 7) // 8)


def test_12mm_embeds_band_centered_in_16_byte_lines(monkeypatch, tmp_path):
    # 12 mm: TAPE_DOTS[12]=54-dot printable band embedded CENTERED into each
    # 128-dot head line; raster lines are always 16 bytes (head width).
    make_png(tmp_path / "img.png", 8, TAPE_DOTS[12])
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    print_spec({"image": str(tmp_path / "img.png")}, "00:11:22:33:44:55")
    stream = sock.sent
    canvas_len = 8 * 16                    # canvas: 16 bytes per column
    n_lines = canvas_len // 16             # raster number = canvas lines
    info = (
        b"\x1b\x69\x7a\x84\x00\x0c\x00"
        + n_lines.to_bytes(4, "little")
        + b"\x00\x00"
    )
    assert info in stream
    # full-dark column, centered: off=(128-54)//2=37 -> bits 37..90 set:
    # blank 4 bytes, \x07, \xff*6, \xe0, blank 4 bytes
    dark_col = bytes(4) + b"\x07" + b"\xff" * 6 + b"\xe0" + bytes(4)
    payload = raster_module.packbits_encode(dark_col)
    dark_cmd = b"\x47" + len(payload).to_bytes(2, "little") + payload
    assert stream.count(dark_cmd) == 4
    assert stream.count(b"\x5a") == 4      # blank columns -> zero commands


def test_embed_in_head_band_centers_54_dot_band():
    inner = b"\x80\x00\x00\x00\x00\x00\x00"  # lone dot at band y = 0
    canvas = _embed_in_head_band(inner, 54)
    assert len(canvas) == 16               # one full head line
    assert canvas[4] == 0x04               # head bit (128-54)//2 = 37 -> 0x80>>5
    assert canvas[:4] == bytes(4)
    assert canvas[5:] == bytes(11)


def test_embed_in_head_band_24mm_identity():
    inner = bytes(range(16)) * 2           # w=2, already 16 bytes per line
    assert _embed_in_head_band(inner, 128) == inner


def test_24mm_still_16_byte_lines_and_raster_number(monkeypatch, tmp_path):
    # 24 mm tape: TAPE_DOTS[24]=128 dots -> bytes_per_line = ceil(128/8) = 16
    make_png(tmp_path / "img.png", 8, TAPE_DOTS[24])
    sock = fake_transport(monkeypatch, 24, [completed_packet(24)])
    print_spec({"image": str(tmp_path / "img.png")}, "00:11:22:33:44:55")
    stream = sock.sent
    data_len = _png_raster_bytes(8, TAPE_DOTS[24])  # 128 bytes
    n_lines = data_len // 16  # 8, identical to data_len >> 4
    info = (
        b"\x1b\x69\x7a\x84\x00\x18\x00"
        + n_lines.to_bytes(4, "little")
        + b"\x00\x00"
    )
    assert info in stream
    # dark column = one 16-byte line: packbits run 257-16=0xF1 + \xff
    assert stream.count(b"\x47\x02\x00\xf1\xff") == 4
    assert stream.count(b"\x5a") == 4


def test_wait_timeout_passed_value_set_on_socket(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    print_spec({"text": "Hi"}, "00:11:22:33:44:55", wait_timeout=17.5)
    assert sock.timeouts == [17.5]


def test_wait_timeout_default_is_30(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    print_spec({"text": "Hi"}, "00:11:22:33:44:55")
    assert sock.timeouts == [30.0]


def test_rendered_12mm_label_raster_is_mostly_white():
    img = render_label({"text": "Hi", "qr": False, "barcode": False}, 12)
    bits = raster_module.pil_image_to_raster_bits(img)
    total = len(bits) * 8
    dark = sum(bin(byte).count("1") for byte in bits)
    assert dark > 0              # the text itself is ink
    assert dark < total // 2     # background must stay blank, not all-dark


# ---- print API additions: cut modes, gating, error codes, image bytes -------

def fake_transport_status(monkeypatch, width, replies, errors):
    sock = FakeSock(replies)
    monkeypatch.setattr(print_module, "open_printer", lambda mac, channel: sock)
    monkeypatch.setattr(
        print_module,
        "query_status",
        lambda s: {"media_type": "laminated", "media_width": width,
                   "status_type": "reply", "errors": list(errors)},
    )
    return sock


def test_cut_each_is_default_sequence(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12) for _ in range(2)])
    print_spec({"text": "Hi", "copies": 2}, "00:11:22:33:44:55")
    assert terminators(sock) == [b"\x0c", b"\x1a"]
    assert b"\x1b\x69\x4d\x00" not in sock.sent             # each page cut


def test_cut_end_prints_one_strip_with_single_final_cut(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12) for _ in range(3)])
    result = print_spec({"text": "Hi", "copies": 3, "cut": "end"}, "00:11:22:33:44:55")
    stream = sock.sent
    assert result == {"copies_printed": 3, "media_width": 12}
    assert terminators(sock) == [b"\x0c", b"\x0c", b"\x1a"]  # one feed+cut, at the end
    assert stream.count(b"\x1b\x69\x4d\x00") == 2           # non-final pages: no auto-cut
    assert stream.count(b"\x1b\x69\x4d\x40") == 1           # final page: auto-cut
    assert stream.count(b"\x1b\x69\x4b\x08") == 1           # final page: chaining off
    assert stream.count(b"\x1b\x69\x4b\x00") == 2           # non-final pages: chaining on
    assert stream.count(b"\x1b\x69\x7a\x84\x00\x0c") == 3   # print info per page


def test_cut_end_single_copy_is_plain_cut_label(monkeypatch):
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    print_spec({"text": "Hi", "cut": "end"}, "00:11:22:33:44:55")
    assert terminators(sock) == [b"\x1a"]
    assert b"\x1b\x69\x4d\x00" not in sock.sent


def test_cut_param_overrides_spec_and_is_validated():
    with pytest.raises(ValueError, match="cut"):
        print_spec({"text": "Hi", "cut": "middle"}, "00:11:22:33:44:55")
    with pytest.raises(ValueError, match="cut"):
        print_spec({"text": "Hi"}, "00:11:22:33:44:55", cut="sometimes")


def test_copies_capped_at_100():
    with pytest.raises(ValueError, match="copies"):
        print_spec({"text": "Hi", "copies": 101}, "00:11:22:33:44:55")


def test_status_errors_block_printing_with_not_ready(monkeypatch):
    sock = fake_transport_status(monkeypatch, 12, [completed_packet(12)], ["cover_open"])
    with pytest.raises(transport.PrinterError, match="cover_open") as exc:
        print_spec({"text": "Hi"}, "00:11:22:33:44:55")
    assert exc.value.code == "NOT_READY"
    assert b"\x1a" not in sock.sent                         # nothing streamed


def test_tape_mismatch_has_code(monkeypatch):
    fake_transport(monkeypatch, 12, [completed_packet(12)])
    with pytest.raises(transport.PrinterError) as exc:
        print_spec({"text": "Hi", "tape_width": 24}, "00:11:22:33:44:55")
    assert exc.value.code == "TAPE_MISMATCH"


def test_printer_error_default_code_is_unreachable():
    assert transport.PrinterError("x").code == "PRINTER_UNREACHABLE"


def test_image_bytes_accepted(monkeypatch):
    import io
    buf = io.BytesIO()
    Image.new("L", (40, TAPE_DOTS[12]), 0).save(buf, format="PNG")
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    result = print_spec({"image": buf.getvalue()}, "00:11:22:33:44:55")
    assert result["copies_printed"] == 1


def test_opaque_png_uses_luminance_not_alpha(tmp_path):
    path = tmp_path / "opaque.png"
    img = Image.new("RGBA", (10, 8), (255, 255, 255, 255))  # white, fully opaque
    img.putpixel((0, 0), (0, 0, 0, 255))
    img.save(path)
    bits = raster_module.png_to_alpha_raster(str(path), 8)
    assert bits[0] == 0x80                                   # only the black pixel is ink
    assert not any(bits[1:])


def test_print_lock_waits_when_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, "LOCK_PATH", tmp_path / "lock")
    fake_transport(monkeypatch, 12, [completed_packet(12)])
    seen = {}
    real_locked = transport.locked

    def spy(wait=0):
        seen["wait"] = wait
        return real_locked(wait=wait)

    monkeypatch.setattr(print_module, "locked", spy)
    print_spec({"text": "Hi"}, "00:11:22:33:44:55", lock_wait=12)
    assert seen["wait"] == 12


def test_single_copy_cuts_immediately_chaining_off(monkeypatch):
    # Regression: with chaining ON the printer held the last label until the
    # next job arrived (cut came one job late).
    sock = fake_transport(monkeypatch, 12, [completed_packet(12)])
    print_spec({"text": "Hi"}, "00:11:22:33:44:55")
    assert terminators(sock) == [b"\x1a"]
    assert b"\x1b\x69\x4b\x08" in sock.sent
    assert b"\x1b\x69\x4b\x00" not in sock.sent
