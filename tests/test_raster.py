import pytest
from PIL import Image

from btprintbridge import raster


def make_png(path, width, height):
    """RGBA png: left half opaque black, right half transparent."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(width):
        for y in range(height):
            if x < width // 2:
                img.putpixel((x, y), (0, 0, 0, 255))
    img.save(path, format="PNG")


def test_png_height_mismatch_rejected(tmp_path):
    make_png(tmp_path / "small.png", 4, 4)
    with pytest.raises(ValueError, match="expected 128"):
        raster.png_to_alpha_raster(tmp_path / "small.png", 128)


def test_all_zero_line_emits_zero_command():
    out = raster.rasterize(b"\x00" * 16)
    assert out == [b"\x5A"]


def test_packbits_simple_run_of_repeats():
    assert raster.packbits_encode(b"A" * 5) == b"\xFC\x41"  # control byte 257-5=252, then the repeated byte


def test_packbits_empty_input():
    assert raster.packbits_encode(b"") == b""


def test_packbits_literal_split_at_128():
    data = bytes(range(256))
    out = raster.packbits_encode(data)
    assert out == b"\x7f" + bytes(range(128)) + b"\x7f" + bytes(range(128, 256))


def test_packbits_mixed_literal_and_run():
    # "AAAAB" -> run of 4 (control 257-4=253), then literal 1
    assert raster.packbits_encode(b"AAAAB") == b"\xFD\x41\x00\x42"


def test_png_to_alpha_raster_transposes_left_half_dark(tmp_path):
    path = tmp_path / "dot.png"
    make_png(path, 4, 4)
    bits = raster.png_to_alpha_raster(path, 4)
    # height 4 -> 1 byte per line; column x becomes line x, MSB-first down y
    assert bits == b"\xF0\xF0\x00\x00"


def test_alpha_above_zero_is_dark():
    img = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    img.putpixel((0, 0), (0, 0, 0, 1))  # alpha 1 counts as dark
    img.putpixel((1, 0), (0, 0, 0, 0))
    assert raster.pil_image_to_raster_bits(img) == b"\x80\x00"


def test_rasterize_chunked_with_le_length_and_zero_lines():
    data = b"\x01\x02\x03" + b"\x00" * 3
    out = raster.rasterize(data)
    # one padded 16-byte chunk: literal 3 + run of 13 zeros, then \x5A for... none (single chunk)
    assert out == [b"\x47\x06\x00\x02\x01\x02\x03\xF4\x00"]


def test_rasterize_zero_line_after_data_line():
    out = raster.rasterize(b"\x01" + b"\x00" * 16)
    # first chunk: literal 1 + run of 15 zeros; second chunk all zero
    assert out == [b"\x47\x04\x00\x00\x01\xF2\x00", b"\x5A"]


def test_rasterize_pads_last_line_before_packing():
    out = raster.rasterize(b"\xff")
    # 0xff + 15 zero pad -> literal 1, run of 15 zeros
    assert out == [b"\x47\x04\x00\x00\xff\xf2\x00"]

def test_l_mode_image_only_dark_pixels_are_ink():
    # rendered labels are opaque monochrome: ink = value < 128, not alpha
    img = Image.new("L", (8, 8), 255)
    for x in range(2):
        for y in range(2):
            img.putpixel((x, y), 0)
    img.putpixel((6, 6), 127)   # just below the threshold: ink
    img.putpixel((5, 5), 128)   # at the threshold: not ink
    bits = raster.pil_image_to_raster_bits(img)
    # output line x = image column x; y=0 -> 0x80, y=6 -> 0x02
    assert bits == b"\xc0\xc0\x00\x00\x00\x00\x02\x00"


def test_rgba_transparent_background_keeps_alpha_ink_rule():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((1, 1), (0, 0, 0, 255))
    bits = raster.pil_image_to_raster_bits(img)
    # only the dark square's pixel: column 1, y=1 -> 0x40
    assert bits == b"\x00\x40\x00\x00"


def test_png_to_alpha_raster_still_checks_height(tmp_path):
    make_png(tmp_path / "img.png", 4, 4)
    with pytest.raises(ValueError, match="expected 128"):
        raster.png_to_alpha_raster(tmp_path / "img.png", 128)
