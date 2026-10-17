"""Pure parser for the Brother PT-P710BT 32-byte status reply packet.

No I/O; takes raw bytes and returns a JSON-serializable dict.
"""

MEDIA_TYPES = {
    0x00: "none",
    0x01: "laminated",
    0x03: "non_laminated",
    0x11: "heat_shrink",
    0xFF: "incompatible",
}

STATUS_TYPES = {
    0x00: "reply",
    0x01: "printing_completed",
    0x02: "error",
    0x04: "turned_off",
    0x05: "notification",
    0x06: "phase_change",
}

PHASE_TYPES = {
    0x00: "editing",
    0x01: "printing",
}

ERROR_INFO_1 = {
    0x01: "no_media",
    0x02: "cutter_jam",
    0x04: "weak_batteries",
    0x08: "high_voltage_adapter",
}

ERROR_INFO_2 = {
    0x01: "wrong_media",
    0x10: "cover_open",
    0x20: "overheating",
}

AUTO_CUT_FLAG = 0x40

# Byte offsets in the 32-byte status reply
ERROR_INFORMATION_1 = 8
ERROR_INFORMATION_2 = 9
MEDIA_WIDTH = 10
MEDIA_TYPE = 11
MODE = 15
MEDIA_LENGTH = 17
STATUS_TYPE = 18
PHASE_TYPE = 19
PHASE_NUMBER = 20
NOTIFICATION_NUMBER = 22
TAPE_COLOR = 24
TEXT_COLOR = 25


def _flag_names(error_dict, offset, packet):
    value = packet[offset]
    return [name for bit, name in error_dict.items() if value & bit]


def parse_status_packet(pkt: bytes) -> dict:
    """Parse a 32-byte status reply into a JSON-serializable dict."""
    if len(pkt) != 32:
        raise ValueError(f"status packet must be exactly 32 bytes, got {len(pkt)}")

    media_width = pkt[MEDIA_WIDTH]
    phase_type = PHASE_TYPES.get(pkt[PHASE_TYPE], "unknown")

    return {
        "media_type": MEDIA_TYPES.get(pkt[MEDIA_TYPE], "unknown"),
        "media_width": media_width,
        "auto_cut": bool(pkt[MODE] & AUTO_CUT_FLAG),
        "status_type": STATUS_TYPES.get(pkt[STATUS_TYPE], "unknown"),
        "phase_type": phase_type,
        "errors": _flag_names(ERROR_INFO_1, ERROR_INFORMATION_1, pkt)
        + _flag_names(ERROR_INFO_2, ERROR_INFORMATION_2, pkt),
        "media_length": pkt[MEDIA_LENGTH],
        "tape_color": pkt[TAPE_COLOR],
        "text_color": pkt[TEXT_COLOR],
    }
