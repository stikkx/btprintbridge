import pytest
import qrcode

from btprintbridge import render, vcard
from btprintbridge.print import _validate_spec, expand_preset


def test_payload_has_name_email_phone():
    p = vcard.payload("Max Mustermann", "max@example.com", "+49 170 1234567")
    assert p == ("BEGIN:VCARD\r\nVERSION:3.0\r\nN:Mustermann;Max\r\nFN:Max Mustermann\r\n"
                 "EMAIL:max@example.com\r\nTEL:+491701234567\r\nEND:VCARD")


def test_payload_escapes_special_characters():
    p = vcard.payload("Doe, John; Jr\\", "", "")
    assert "FN:Doe\\, John\; Jr\\\\\r\n" in p
    assert "EMAIL" not in p and "TEL" not in p


def test_single_word_name_and_umlauts():
    p = vcard.payload("Jürgen", "", "0170 123")
    assert "N:Jürgen;\r\n" in p and "FN:Jürgen\r\n" in p and "TEL:0170123\r\n" in p


def test_without_name_fn_falls_back():
    p = vcard.payload("", "a@b.de", "")
    assert "FN:a@b.de\r\n" in p and "N:;\r\n" in p


@pytest.mark.parametrize("name,email,phone,msg", [
    ("", "", "", "at least one"),
    ("Max", "no-at-sign", "", "email"),
    ("Max", "a b@c.de", "", "email"),
    ("Max", "", "+49 abc", "phone"),
    ("Line\nbreak", "", "", "name"),
    ("x" * 101, "", "", "name"),
])
def test_invalid_cards_are_rejected(name, email, phone, msg):
    with pytest.raises(ValueError, match=msg):
        vcard.validate({"name": name, "email": email, "phone": phone})


def test_validate_rejects_unknown_or_non_string_fields():
    with pytest.raises(ValueError, match="unknown"):
        vcard.validate({"name": "Max", "street": "x"})
    with pytest.raises(ValueError, match="string"):
        vcard.validate({"name": 5})
    with pytest.raises(ValueError, match="object"):
        vcard.validate("Max")


def test_expand_preset_builds_text_and_qr():
    spec = expand_preset({"vcard": {"name": "Max Mustermann", "email": "max@example.com",
                                    "phone": "+49 170 1234567"}, "copies": 2})
    assert spec["text"] == "Max Mustermann\nmax@example.com\n+49 170 1234567"
    assert spec["qr"]["value"].startswith("BEGIN:VCARD")
    assert spec["qr"]["ec"] == "L"
    assert spec["barcode"] is False and spec["copies"] == 2
    assert "vcard" not in spec
    _validate_spec(spec, None)                      # a normal text spec now


def test_expand_preset_keeps_custom_text_and_skips_empty_fields():
    spec = expand_preset({"vcard": {"name": "Max", "phone": "0170"}, "text": "Ruf an!"})
    assert spec["text"] == "Ruf an!"
    assert expand_preset({"vcard": {"name": "Max", "phone": "0170"}})["text"] == "Max\n0170"


def test_vcard_and_image_are_exclusive():
    with pytest.raises(ValueError, match="vcard"):
        expand_preset({"vcard": {"name": "Max"}, "image": b"x"})


def test_spec_without_preset_is_unchanged():
    spec = {"text": "x", "qr": False}
    assert expand_preset(spec) is spec


# ---- QR rendering ---------------------------------------------------------------

def _qr_modules(data, ec):
    q = qrcode.QRCode(border=0, error_correction=ec)
    q.add_data(data)
    q.make(fit=True)
    return q.modules_count


def test_qr_uses_whole_dot_modules_when_possible():
    img = render._qr_image("TOOL-1", 76)
    n = _qr_modules("TOOL-1", qrcode.constants.ERROR_CORRECT_M)
    scale = 76 // n
    assert img.size == (n * scale, 76)          # as wide as the code, band tall
    rows = [y for y in range(76) if any(img.getpixel((x, y)) == 0 for x in range(img.size[0]))]
    assert rows[-1] - rows[0] + 1 == n * scale and abs(rows[0] - (75 - rows[-1])) <= 1


def test_vcard_label_renders_on_12mm_with_two_dot_modules():
    spec = expand_preset({"vcard": {"name": "Max Stick", "email": "max@stikkx.de",
                                    "phone": "+49 170 1234567"}})
    img = render.render_label(spec, 12)
    assert img.size[1] == render.TAPE_DOTS[12]
    data = spec["qr"]["value"]
    assert 76 // _qr_modules(data, qrcode.constants.ERROR_CORRECT_L) >= 2
