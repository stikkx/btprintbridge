"""RFCOMM transport, exclusive-operation lock, and status exchange
for the Brother PT-P710BT label printer.

No print logic here; the CLI and print pipeline live elsewhere.
"""

import fcntl
import socket
import time
from contextlib import contextmanager
from pathlib import Path

from btprintbridge.status import parse_status_packet

LOCK_PATH = Path.home() / ".cache" / "btprintbridge.lock"

STATUS_PACKET_LEN = 32
INVALIDATE = b"\x00" * 100
INIT = b"\x1b\x40"
STATUS_REQUEST = b"\x1b\x69\x53"


class PrinterError(Exception):
    """Connection/protocol/printer-state failures.

    ``code`` classifies the failure for API consumers: PRINTER_UNREACHABLE
    (default: connect/communication), NOT_READY (printer reports an error
    state), TAPE_MISMATCH (loaded tape differs from the requested width).
    """

    def __init__(self, message: str, code: str = "PRINTER_UNREACHABLE"):
        super().__init__(message)
        self.code = code


class PrinterBusy(Exception):
    """Another btprintbridge operation holds the lock."""


@contextmanager
def locked(wait: float = 0):
    """Exclusive fcntl.flock on LOCK_PATH; PrinterBusy if already held.

    With *wait* > 0 the lock is retried until *wait* seconds have passed
    (the HTTP API queues behind a running job instead of failing at once).
    """
    path = Path(LOCK_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if time.monotonic() >= deadline:
                    raise PrinterBusy("another btprintbridge operation is in progress") from e
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def open_rfcomm(mac: str, channel: int = 1, timeout: float = 5.0) -> socket.socket:
    """Open an RFCOMM socket to (mac, channel); OSError -> PrinterError."""
    sock = None
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(timeout)
        sock.connect((mac, channel))
    except OSError as e:
        if sock is not None:
            sock.close()
        raise PrinterError(f"connection failed: {e}") from e
    return sock


def recv_packet(sock) -> bytes:
    """Accumulate until one 32-byte status packet; OSError/short read -> PrinterError."""
    received = bytearray()
    while len(received) < STATUS_PACKET_LEN:
        try:
            chunk = sock.recv(STATUS_PACKET_LEN - len(received))
        except OSError as e:
            raise PrinterError(f"communication failed: {e}") from e
        if not chunk:
            raise PrinterError("short status response")
        received += chunk
    return bytes(received)


def query_status(sock) -> dict:
    """Send invalidate + init + status request; read one 32-byte status packet."""
    sock.send(INVALIDATE + INIT + STATUS_REQUEST)
    return parse_status_packet(recv_packet(sock))


def full_status(mac, channel, lock_wait: float = 0) -> dict:
    """locked() + open_rfcomm() + query_status(), socket closed afterwards."""
    with locked(wait=lock_wait):
        sock = open_rfcomm(mac, channel)
        try:
            return query_status(sock)
        finally:
            sock.close()
