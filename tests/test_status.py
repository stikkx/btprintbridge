import pytest
from btprintbridge.status import parse_status_packet

# Synthetic 32-byte status reply: 24mm laminated tape, auto-cut, OK
HEADER = bytes(8)
def make_packet(status_type=0x00, media_width=24, media_type=0x01, err1=0, err2=0, mode=0x40):
    pkt = bytearray(32)
    pkt[8] = err1; pkt[9] = err2; pkt[10] = media_width; pkt[11] = media_type
    pkt[15] = mode; pkt[18] = status_type
    return bytes(pkt)

def test_parses_media_width_and_type():
    d = parse_status_packet(make_packet())
    assert d["media_width"] == 24
    assert d["media_type"] == "laminated"
    assert d["auto_cut"] is True
    assert d["status_type"] == "reply"

def test_no_media():
    d = parse_status_packet(make_packet(err1=0x01, media_width=0, media_type=0x00))
    assert d["media_width"] == 0
    assert d["media_type"] == "none"
    assert "no_media" in d["errors"]


def test_error_info_2_reference_flags():
    assert parse_status_packet(make_packet(err2=0x01))["errors"] == ["wrong_media"]
    # cover_open only for 0x10, never for the non-reference bits 0x02/0x04
    assert "cover_open" in parse_status_packet(make_packet(err2=0x10))["errors"]
    assert "cover_open" in parse_status_packet(make_packet(err2=0x30))["errors"]
    assert "cover_open" not in parse_status_packet(make_packet(err2=0x02))["errors"]
    assert "overheating" in parse_status_packet(make_packet(err2=0x20))["errors"]

def test_wrong_length_raises():
    with pytest.raises(ValueError):
        parse_status_packet(b"\x00" * 31)
