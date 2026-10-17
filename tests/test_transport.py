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
    pkt = bytearray(32); pkt[10] = 12; pkt[18] = 0x00
    fake = FakeSock(bytes(pkt))
    d = transport.query_status(fake)
    assert fake.sent == b"\x00" * 100 + b"\x1b\x40" + b"\x1b\x69\x53"
    assert d["media_width"] == 12


def test_query_status_accumulates_short_reads():
    pkt = bytearray(32); pkt[10] = 24
    first, rest = bytes(pkt[:12]), bytes(pkt[12:])
    fake = FakeSock(b"")
    fake.remaining = [first, rest]
    fake.recv = lambda n: fake.remaining.pop(0)
    d = transport.query_status(fake)
    assert d["media_width"] == 24


def test_query_status_raises_on_empty_reads_before_32_bytes():
    pkt = bytearray(32); pkt[10] = 24
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
