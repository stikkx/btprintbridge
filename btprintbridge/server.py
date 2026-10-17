"""Local HTTP print API (``btprintbridge serve``).

A small stdlib server so web apps (BinaryStock) can print through the same
backend as the Omarchy bar plugin. Loopback by default; CORS answers only for
allow-listed origins. RFCOMM work stays serialized by ``transport.locked``;
API callers queue behind a running job for up to ``PRINT_LOCK_WAIT`` seconds.

Endpoints (API v1):

``GET /health``   fast liveness, BlueZ connection state only (no RFCOMM)
``GET /status``   cassette query over RFCOMM (``status --json`` shape + ok)
``POST /print``   label spec: ``text`` (+ qr/barcode) or base64 PNG ``image``

Errors are ``{"ok": false, "error": CODE, "message": ...}`` with CODE one of
VALIDATION (400), BUSY (409), NOT_READY / TAPE_MISMATCH (422),
PRINTER_UNREACHABLE (503).
"""

import base64
import binascii
import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from btprintbridge import cli
from btprintbridge.print import print_spec
from btprintbridge.transport import PrinterBusy, PrinterError, full_status

API_VERSION = 1
MAX_BODY_BYTES = 8 * 1024 * 1024
PRINT_LOCK_WAIT = 90.0
STATUS_LOCK_WAIT = 15.0

_CONNECTED_RE = re.compile(r"^\s*Connected:\s*yes\s*$", re.MULTILINE)


def bluez_connected(mac: str) -> bool:
    """BlueZ connection state of *mac* via ``bluetoothctl info``."""
    try:
        proc = subprocess.run(
            ["bluetoothctl", "info", mac], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(_CONNECTED_RE.search(proc.stdout or ""))


def resolve_mac(configured: str | None) -> str | None:
    """Configured MAC, else the first paired ``PT-`` device (per request, so a
    printer paired after the server started is still found)."""
    return configured or cli.mac_autodetect()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "btprintbridge/1"

    # ---- plumbing --------------------------------------------------------

    def log_message(self, fmt, *args):  # one compact line per request
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin in self.server.origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, err: ApiError) -> None:
        self._json(err.status, {"ok": False, "error": err.code, "message": err.message})

    def _mac(self) -> str | None:
        return resolve_mac(self.server.mac)

    # ---- routes ----------------------------------------------------------

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        if self.headers.get("Origin") in self.server.origins:
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            # Chromium Private Network Access: HTTPS page -> loopback.
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._health()
        elif path == "/status":
            self._status()
        else:
            self._error(ApiError(404, "NOT_FOUND", f"no route {path}"))

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/print":
            self._error(ApiError(404, "NOT_FOUND", f"no route {path}"))
            return
        try:
            self._json(200, self._print())
        except ApiError as e:
            self._error(e)

    def _health(self) -> None:
        mac = self._mac()
        self._json(200, {
            "ok": True,
            "service": "btprintbridge",
            "api": API_VERSION,
            "printer": {
                "mac": mac,
                "paired": mac is not None,
                "connected": bluez_connected(mac) if mac else False,
            },
        })

    def _status(self) -> None:
        mac = self._mac()
        if mac is None:
            out = cli._not_connected("no paired P-touch printer found")
        else:
            try:
                out = dict(full_status(mac, self.server.channel, lock_wait=STATUS_LOCK_WAIT))
                out["connected"] = True
                out["error"] = None
            except PrinterBusy as e:
                self._error(ApiError(409, "BUSY", str(e)))
                return
            except PrinterError as e:
                out = cli._not_connected(str(e))
        out["ok"] = True
        self._json(200, out)

    def _read_spec(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "VALIDATION", "invalid Content-Length") from None
        if length > MAX_BODY_BYTES:
            raise ApiError(400, "VALIDATION", "request body too large")
        try:
            spec = json.loads(self.rfile.read(length) or b"null")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "VALIDATION", "request body is not valid JSON") from None
        if not isinstance(spec, dict):
            raise ApiError(400, "VALIDATION", "spec must be a JSON object")
        image = spec.get("image")
        if image is not None:
            if not isinstance(image, str):
                raise ApiError(400, "VALIDATION", "'image' must be a base64-encoded PNG")
            try:
                spec["image"] = base64.b64decode(image, validate=True)
            except (binascii.Error, ValueError):
                raise ApiError(400, "VALIDATION", "'image' is not valid base64") from None
        return spec

    def _print(self) -> dict:
        spec = self._read_spec()
        mac = self._mac()
        if mac is None:
            raise ApiError(503, "PRINTER_UNREACHABLE", "no paired P-touch printer found")
        try:
            result = print_spec(spec, mac, channel=self.server.channel,
                                lock_wait=PRINT_LOCK_WAIT)
        except PrinterBusy as e:
            raise ApiError(409, "BUSY", str(e)) from e
        except PrinterError as e:
            status = 503 if e.code == "PRINTER_UNREACHABLE" else 422
            raise ApiError(status, e.code, str(e)) from e
        except ValueError as e:
            raise ApiError(400, "VALIDATION", str(e)) from e
        return {"ok": True, "printed": result["copies_printed"],
                "media_width": result["media_width"]}


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, mac, channel, origins):
        self.mac = mac
        self.channel = channel
        self.origins = frozenset(o for o in origins if o and o != "*")
        super().__init__(addr, ApiHandler)


def make_server(host: str, port: int, mac: str | None, channel: int,
                origins) -> ApiServer:
    return ApiServer((host, port), mac, channel, list(origins))
