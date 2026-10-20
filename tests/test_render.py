import pytest

from btprintbridge import render


def test_empty_label_rejected():
    with pytest.raises(ValueError, match="nothing to print"):
        render.render_label({"text": "", "qr": None, "barcode": None}, 24)


def test_text_label_height_is_tape_dots():
    img = render.render_label({"text": "Hi", "qr": None, "barcode": None}, 24)
    assert img.size[1] == 128  # TAPE_DOTS[24]


def test_unsupported_width_rejected():
    with pytest.raises(ValueError):
        render.render_label({"text": "Hi", "qr": None, "barcode": None}, 16)


def test_whitespace_only_text_rejected():
    with pytest.raises(ValueError, match="nothing to print"):
        render.render_label({"text": "   \n\t", "qr": None, "barcode": None}, 12)


def test_text_only_label_is_monochrome():
    img = render.render_label({"text": "Hi", "qr": None, "barcode": None}, 24)
    assert img.mode == "1"
    assert img.getextrema()[0] == 0  # something dark was drawn


def _dark_columns(img):
    h = img.size[1]
    return [x for x in range(img.size[0]) if any(img.getpixel((x, y)) == 0 for y in range(h))]


def test_qr_is_square_left_at_full_height():
    height = render.TAPE_DOTS[24]
    img = render.render_label({"text": "Hi", "qr": {"value": "X"}, "barcode": None}, 24)
    assert img.size[1] == height
    left = _dark_columns(img)[0]
    # QR is the first element, full tape height, after the end margin
    assert img.getpixel((left, 0)) == 0
    assert img.getpixel((left, height - 1)) == 0


def test_barcode_is_last_and_dark():
    img = render.render_label({"text": "Hi", "qr": None, "barcode": {"value": "1234"}}, 24)
    dark = _dark_columns(img)
    assert dark, "no dark pixels from barcode"
    assert dark[-1] >= img.size[0] * 0.75  # barcode occupies the right end


def test_both_label_ends_have_blank_margin():
    img = render.render_label({"text": "Hi", "qr": {"value": "X"}, "barcode": {"value": "1234"}}, 12)
    dark = _dark_columns(img)
    assert dark[0] >= render.END_MARGIN                      # space before the QR
    assert img.size[0] - 1 - dark[-1] >= render.END_MARGIN    # space after the barcode


def test_codes_separated_from_text_by_gap():
    qr_only = render.render_label({"text": "", "qr": {"value": "X"}}, 12)
    qr_w = _dark_columns(qr_only)[-1] - _dark_columns(qr_only)[0] + 1
    img = render.render_label({"text": "Hi", "qr": {"value": "X"}}, 12)
    dark = _dark_columns(img)
    qr_end = dark[0] + qr_w - 1
    text_start = next(x for x in dark if x > qr_end)
    assert text_start - qr_end - 1 >= render.CODE_GAP


def test_label_has_minimum_length_and_content_is_centered():
    img = render.render_label({"text": "A"}, 12)
    assert img.size[0] >= render.MIN_LABEL_LENGTH
    dark = _dark_columns(img)
    left, right = dark[0], img.size[0] - 1 - dark[-1]
    assert abs(left - right) <= 4      # glyph box vs 1-bit ink: < 0.6 mm


def test_full_spec_composite():
    height = render.TAPE_DOTS[18]
    img = render.render_label({"text": "Hi\nok", "qr": {"value": "X"}, "barcode": {"value": "1234"}}, 18)
    assert img.size[1] == height
    assert img.mode == "1"


def test_qr_null_value_falls_back_to_label_text():
    height = render.TAPE_DOTS[24]
    img = render.render_label({"text": "Hi", "qr": {"value": None}, "barcode": None}, 24)
    assert img.size[1] == height
    # QR rendered from "Hi": first element, full printable height
    left = _dark_columns(img)[0]
    assert img.getpixel((left, 0)) == 0
    assert img.getpixel((left, height - 1)) == 0


def test_null_codes_with_empty_text_rejected():
    spec = {"text": "", "qr": {"value": None}, "barcode": {"value": None}}
    with pytest.raises(ValueError, match="nothing to print"):
        render.render_label(spec, 24)


def test_null_code_with_text_present_renders():
    height = render.TAPE_DOTS[24]
    spec = {"text": "x", "qr": {"value": None}, "barcode": None}
    img = render.render_label(spec, 24)
    assert img.size[1] == height
    assert img.getpixel((_dark_columns(img)[0], 0)) == 0  # QR with value "x" was drawn


def test_false_codes_are_disabled():
    img = render.render_label({"text": "Hi", "qr": False, "barcode": False}, 24)
    plain = render.render_label({"text": "Hi"}, 24)
    assert img.tobytes() == plain.tobytes()            # no QR/barcode block


def test_pathological_text_overflow_raises():
    with pytest.raises(ValueError, match="does not fit"):
        render.render_label({"text": "a" * 5000, "qr": None, "barcode": None}, 24)


def test_normal_label_stays_within_max_length():
    spec = {"text": "word " * 30, "qr": {"value": "X"}, "barcode": {"value": "1234"}}
    img = render.render_label(spec, 24)
    assert img.size[0] <= render._MAX_LABEL_LENGTH


def test_pathological_text_overflow_raises_also_with_codes():
    spec = {"text": "a" * 5000, "qr": {"value": "X"}, "barcode": {"value": "1234"}}
    with pytest.raises(ValueError, match="does not fit on tape of 24 mm"):
        render.render_label(spec, 24)

def test_code_values_resolution():
    text = "Shelf A-3\n  Drawer 2 \n\n"
    assert render.qr_value({"value": None}, text) == text          # QR keeps lines
    assert render.barcode_value({"value": None}, text) == "Shelf A-3 Drawer 2"
    assert render.barcode_value({"value": "X1"}, text) == "X1"      # override untouched
    assert render.barcode_value(False, text) is None
    assert render.qr_value(None, text) is None


def test_multiline_label_with_both_codes_renders():
    img = render.render_label({"text": "Shelf A-3\nDrawer 2", "qr": {"value": None},
                               "barcode": {"value": None}}, 12)
    assert img.size[1] == render.TAPE_DOTS[12]



# ---- vertical space: band per tape, ink-based line pitch, font fallback ------

def _ink_rows(img):
    w, h = img.size
    return [y for y in range(h) if any(img.getpixel((x, y)) == 0 for x in range(w))]


def test_12mm_band_is_76_dots():
    # hardware-checked 2026-10-20 (calibration label): 76 dots fit 12 mm tape
    assert render.TAPE_DOTS[12] == 76


def test_two_line_12mm_text_fills_the_band():
    img = render.render_label({"text": "GOP-243\nGOP-BN-002", "qr": False, "barcode": False}, 12)
    rows = _ink_rows(img)
    height = render.TAPE_DOTS[12]
    # ink spans most of the band but keeps the padding free
    assert rows[0] >= render.V_PAD and rows[-1] <= height - 1 - render.V_PAD
    assert rows[-1] - rows[0] >= height * 0.8


def test_two_lines_do_not_touch():
    img = render.render_label({"text": "Hgjpy\nHgjpy", "qr": False, "barcode": False}, 12)
    rows = set(_ink_rows(img))
    first, last = min(rows), max(rows)
    gap = [y for y in range(first, last + 1) if y not in rows]
    assert len(gap) >= render.LINE_GAP          # blank rows between the lines


def test_single_line_text_is_bigger_than_two_lines():
    one = render._fit_text(["GOP-243"], render.TAPE_DOTS[12], 900)
    two = render._fit_text(["GOP-243", "GOP-BN-002"], render.TAPE_DOTS[12], 900)
    assert one.size > two.size


def test_blank_line_keeps_its_slot():
    img = render.render_label({"text": "AB\n\nCD", "qr": False, "barcode": False}, 24)
    rows = _ink_rows(img)
    gap = max(b - a for a, b in zip(rows, rows[1:]))
    assert gap > 20                              # empty middle line stays empty


def test_font_falls_back_when_preferred_font_is_missing(monkeypatch):
    monkeypatch.setattr(render, "_FONT_CANDIDATES", ("/nonexistent/a.ttf",))
    render._font_path.cache_clear()
    try:
        font = render._load_font(20)
        assert font.getlength("Hi") > 0
    finally:
        render._font_path.cache_clear()
