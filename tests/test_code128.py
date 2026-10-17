import pytest

from btprintbridge import code128


def test_module_count_symbols_and_stop():
    m = code128.code128_modules("HELLO")
    assert isinstance(m, list) and set(m) <= {0, 1}
    # Start B + 5 chars + checksum = 7 symbols @ 11 modules, then the stop symbol's 11 + 2 termination bars
    assert len(m) == 7 * 11 + 13


def test_checksum_known_value():
    # Start B = 104; codes: H=39, E=36, L=43, L=43, O=47
    # checksum = (104 + 1*39 + 2*36 + 3*43 + 4*43 + 5*47) % 103 = 602 % 103 = 87 → 8 symbols total
    m = code128.code128_modules("HELLO")
    # golden: length pins start + 8 symbols (7 body + checksum) + stop + 2 bars
    assert len(m) == 7 * 11 + 13


def test_h_matches_published_pattern():
    # Wikipedia table: Start B (104) = 11010010000; "H" = ASCII 72 -> Code-B value 40, pattern 11000101000;
    # checksum (104 + 40) % 103 = 41, pattern 11000100010; stop pattern (incl. 2-module bar) = 1100011101011
    m = code128.code128_modules("H")
    expected = list(map(int, "11010010000" + "11000101000" + "11000100010" + "1100011101011"))
    assert m == expected


def test_rejects_non_ascii():
    with pytest.raises(ValueError):
        code128.code128_modules("héllo")


def test_rejects_below_space():
    with pytest.raises(ValueError):
        code128.code128_modules("a\x1fb")


def test_empty_string_valid():
    # Start B + checksum 104 % 103 = 1 + stop
    m = code128.code128_modules("")
    assert len(m) == 2 * 11 + 13


def test_modules_to_image_dimensions():
    m = code128.code128_modules("H")
    img = code128.modules_to_image(m, bar_height=64, scale=3)
    assert img.size == (len(m) * 3, 64)


def test_modules_to_image_dark_modules_drawn():
    m = code128.code128_modules("")
    img = code128.modules_to_image(m, bar_height=10, scale=2)
    # first module of start-B is a bar (1): pixel (0, 0) dark
    assert img.getpixel((0, 0)) == 0
    # gap module: pixel black where module==1, white where 0 (mode "1": 0=black, 1=white)
    first_gap = m.index(0) * 2
    assert img.getpixel((first_gap, 0)) != 0


def test_modules_to_image_bars_full_height():
    m = code128.code128_modules("")
    img = code128.modules_to_image(m, bar_height=20, scale=1)
    for y in range(20):
        assert img.getpixel((0, y)) == 0