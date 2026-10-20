import socket

import pytest
from btprintbridge import transport


class FakeSock:
    def __init__(self, reply):
        self.sent = b""; self.reply = reply; self.closed = False
    def send(self, b): self.sent += b
    def recv(self, n): out = self.reply; self.reply = b""; return out
    def close(self): self.closed = True


def test_query_status_sends_invalidate_init_request_and_parses_reply(monkeypatch):
    pkt = bytearray(32); pkt[:3] = transport.STATUS_HEADER; pkt[10] = 12; pkt[18] = 0x00
    fake = FakeSock(bytes(pkt))
    d = transport.query_status(fake)
    assert fake.sent == b"\x00" * 100 + b"\x1b\x40" + b"\x1b\x69\x53"
    assert d["media_width"] == 12


def test_query_status_accumulates_short_reads():
    pkt = bytearray(32); pkt[:3] = transport.STATUS_HEADER; pkt[10] = 24
    first, rest = bytes(pkt[:12]), bytes(pkt[12:])
    fake = FakeSock(b"")
    fake.remaining = [first, rest]
    fake.recv = lambda n: fake.remaining.pop(0)
    d = transport.query_status(fake)
    assert d["media_width"] == 24


def test_query_status_raises_on_empty_reads_before_32_bytes():
    pkt = bytearray(32); pkt[:3] = transport.STATUS_HEADER; pkt[10] = 24
    fake = FakeSock(bytes(pkt))
    fake.recv = lambda n: b""  # connection closed mid-response
    with pytest.raises(transport.PrinterError, match="short status response"):
        transport.query_status(fake)


def test_query_status_wraps_read_timeout():
    def raise_timeout(n):
        raise socket.timeout()
    fake = FakeSock(b"")
    fake.recv = raise_timeout
    with pytest.raises(transport.PrinterError, match="communication failed"):
        transport.query_status(fake)


def test_lock_blocks_second_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "LOCK_PATH", tmp_path / "lock")
    with transport.locked():
        with pytest.raises(transport.PrinterBusy):
            with transport.locked():
                pass


def test_lock_message_names_in_progress_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "LOCK_PATH", tmp_path / "lock")
    with transport.locked():
        with pytest.raises(transport.PrinterBusy, match="another btprintbridge operation is in progress"):
            with transport.locked():
                pass


def test_lock_released_after_context_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "LOCK_PATH", tmp_path / "lock")
    with transport.locked():
        pass
    with transport.locked():
        pass


def test_open_rfcomm_wraps_oserror():
    with pytest.raises(transport.PrinterError, match="connection failed"):
        transport.open_rfcomm("00:11:22:33:44:55", 1, timeout=0.2)


def test_lock_wait_acquires_after_release(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(transport, "LOCK_PATH", tmp_path / "lock")
    release = threading.Event()

    def holder():
        with transport.locked():
            release.wait(2)

    t = threading.Thread(target=holder)
    t.start()
    import time
    time.sleep(0.1)
    threading.Timer(0.3, release.set).start()
    with transport.locked(wait=3):
        pass
    t.join()


def test_lock_wait_times_out_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "LOCK_PATH", tmp_path / "lock")
    with transport.locked():
        with pytest.raises(transport.PrinterBusy):
            with transport.locked(wait=0.3):
                pass


# ---- wrong channel / channel autodetection ------------------------------------

IAP2 = bytes.fromhex("ff550200ee10") * 6  # what an iPhone (iAP2) channel answers


def good_packet(width=12):
    pkt = bytearray(32); pkt[:3] = transport.STATUS_HEADER; pkt[10] = width; pkt[11] = 0x01
    return bytes(pkt)


def test_query_status_rejects_non_brother_reply():
    with pytest.raises(transport.PrinterError, match="wrong RFCOMM channel"):
        transport.query_status(FakeSock(IAP2[:32]))


@pytest.fixture
def channels(monkeypatch, tmp_path):
    """open_rfcomm fake: {channel: reply bytes | None (refused)}; records attempts."""
    cache = tmp_path / "channels.json"
    monkeypatch.setattr(transport, "CHANNEL_CACHE", cache)
    state = {"replies": {}, "tried": [], "socks": {}}

    def fake_open(mac, channel, timeout=5.0):
        state["tried"].append(channel)
        if state.get("down"):
            raise transport.PrinterError("connection failed: timed out")
        reply = state["replies"].get(channel)
        if reply is None:
            raise transport.PrinterError("connection failed: [Errno 111] Connection refused")
        sock = FakeSock(reply)
        sock.settimeout = lambda t: None
        state["socks"][channel] = sock
        return sock

    monkeypatch.setattr(transport, "open_rfcomm", fake_open)
    state["cache"] = cache
    return state


def test_auto_channel_skips_iap2_and_remembers_the_printer_channel(channels):
    import json
    channels["replies"] = {1: IAP2[:32], 2: good_packet()}
    sock = transport.open_printer("66:77:88:99:AA:BB", None)
    assert sock is channels["socks"][2]
    assert channels["socks"][1].closed and not sock.closed
    assert json.loads(channels["cache"].read_text()) == {"66:77:88:99:AA:BB": 2}

    channels["tried"].clear()
    transport.open_printer("66:77:88:99:AA:BB", None)
    assert channels["tried"] == [2]                 # cached channel first


def test_auto_channel_default_printer_on_channel_1(channels):
    channels["replies"] = {1: good_packet()}
    assert transport.open_printer("00:11:22:33:44:55", None) is channels["socks"][1]
    assert channels["tried"] == [1]


def test_stale_cached_channel_falls_back_to_scan(channels):
    import json
    channels["cache"].write_text(json.dumps({"00:11:22:33:44:55": 5}))
    channels["replies"] = {1: good_packet()}
    assert transport.open_printer("00:11:22:33:44:55", None) is channels["socks"][1]
    assert channels["tried"][:2] == [5, 1]
    assert json.loads(channels["cache"].read_text()) == {"00:11:22:33:44:55": 1}


def test_corrupt_cache_is_ignored(channels):
    channels["cache"].write_text("{nope")
    channels["replies"] = {1: good_packet()}
    assert transport.open_printer("00:11:22:33:44:55", None) is channels["socks"][1]


def test_explicit_channel_is_not_probed_or_cached(channels):
    channels["replies"] = {3: b""}
    sock = transport.open_printer("00:11:22:33:44:55", 3)
    assert sock is channels["socks"][3] and channels["tried"] == [3]
    assert not channels["cache"].exists()


def test_auto_channel_none_found(channels):
    channels["replies"] = {1: IAP2[:32]}
    with pytest.raises(transport.PrinterError, match="no Brother printer service"):
        transport.open_printer("00:11:22:33:44:55", None)
    assert channels["tried"] == list(transport.AUTO_CHANNELS)


def test_auto_channel_stops_at_once_when_printer_is_off(channels):
    channels["down"] = True
    with pytest.raises(transport.PrinterError, match="timed out"):
        transport.open_printer("00:11:22:33:44:55", None)
    assert channels["tried"] == [1]                 # no 10 x connect timeout
