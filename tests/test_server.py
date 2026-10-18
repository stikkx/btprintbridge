import base64
import json
import threading
import urllib.error
import urllib.request

import pytest

from btprintbridge import server as server_module
from btprintbridge.transport import PrinterBusy, PrinterError

MAC = "00:11:22:33:44:55"
ORIGIN = "https://bs.example"
KEY = "test-key-0123456789abcdef0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture
def api(monkeypatch):
    calls = {"print": [], "status": []}
    state = {"print": None, "status": None, "connected": True}

    def fake_print(spec, mac, channel=1, copies=None, force=False, cut=None, lock_wait=0, **kw):
        calls["print"].append({"spec": spec, "mac": mac, "channel": channel,
                               "copies": copies, "cut": cut, "lock_wait": lock_wait})
        if isinstance(state["print"], Exception):
            raise state["print"]
        n = copies if copies is not None else spec.get("copies", 1)
        return {"copies_printed": n, "media_width": 12}

    def fake_status(mac, channel, lock_wait=0):
        calls["status"].append((mac, channel))
        if isinstance(state["status"], Exception):
            raise state["status"]
        return {"media_width": 12, "media_type": "laminated", "errors": []}

    monkeypatch.setattr(server_module, "print_spec", fake_print)
    monkeypatch.setattr(server_module, "full_status", fake_status)
    monkeypatch.setattr(server_module, "bluez_connected", lambda mac: state["connected"])
    srv = server_module.make_server("127.0.0.1", 0, MAC, 1, [ORIGIN], api_key=KEY)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, calls, state
    srv.shutdown()
    srv.server_close()


def request(url, method="GET", body=None, headers=None, auth=True):
    data = None
    hdrs = dict(AUTH if auth else {})
    hdrs.update(headers or {})
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, dict(r.headers), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, dict(e.headers), (json.loads(raw) if raw else None)


def test_health_is_fast_and_identifies_service(api):
    base, calls, state = api
    status, _, body = request(base + "/health")
    assert status == 200
    assert body["ok"] is True
    assert body["service"] == "btprintbridge"
    assert body["api"] == 1
    assert body["printer"] == {"paired": True, "connected": True}   # no MAC leak
    assert body["auth"] == "bearer"
    assert calls["status"] == []                     # no RFCOMM for health


def test_health_reports_disconnected(api):
    base, _, state = api
    state["connected"] = False
    _, _, body = request(base + "/health")
    assert body["printer"]["connected"] is False


def test_status_queries_cassette(api):
    base, calls, _ = api
    status, _, body = request(base + "/status")
    assert status == 200
    assert body["ok"] is True and body["connected"] is True
    assert body["media_width"] == 12
    assert calls["status"] == [(MAC, 1)]


def test_status_unreachable_is_reported_not_raised(api):
    base, _, state = api
    state["status"] = PrinterError("connection failed: [Errno 112] Host is down")
    status, _, body = request(base + "/status")
    assert status == 200
    assert body["connected"] is False
    assert "Host is down" in body["error"]


def test_print_text_spec(api):
    base, calls, _ = api
    spec = {"text": "TOOL-1\nDrill", "qr": {"value": "TOOL-1"}, "barcode": False,
            "copies": 2, "cut": "end", "tape_width": 12}
    status, _, body = request(base + "/print", "POST", spec)
    assert status == 200
    assert body == {"ok": True, "printed": 2, "media_width": 12}
    call = calls["print"][0]
    assert call["mac"] == MAC and call["spec"]["text"] == "TOOL-1\nDrill"
    assert call["spec"]["cut"] == "end"
    assert call["lock_wait"] > 0                     # API queues behind running jobs


def test_print_image_base64_is_decoded(api):
    base, calls, _ = api
    png = b"\x89PNG\r\n\x1a\nfake"
    status, _, _ = request(base + "/print", "POST",
                           {"image": base64.b64encode(png).decode(), "copies": 1})
    assert status == 200
    assert calls["print"][0]["spec"]["image"] == png


def test_print_rejects_bad_base64(api):
    base, calls, _ = api
    status, _, body = request(base + "/print", "POST", {"image": "%%%not-base64"})
    assert status == 400 and body["error"] == "VALIDATION"
    assert calls["print"] == []


def test_print_rejects_invalid_json(api):
    base, _, _ = api
    status, _, body = request(base + "/print", "POST", b"{nope")
    assert status == 400
    assert body == {"ok": False, "error": "VALIDATION", "message": "request body is not valid JSON"}


def test_print_rejects_non_object(api):
    base, _, _ = api
    status, _, body = request(base + "/print", "POST", [1, 2])
    assert status == 400 and body["error"] == "VALIDATION"


@pytest.mark.parametrize("exc,http,code", [
    (ValueError("copies must be an integer between 1 and 100"), 400, "VALIDATION"),
    (PrinterBusy("another btprintbridge operation is in progress"), 409, "BUSY"),
    (PrinterError("printer not ready: cover_open", code="NOT_READY"), 422, "NOT_READY"),
    (PrinterError("loaded tape is 12 mm", code="TAPE_MISMATCH"), 422, "TAPE_MISMATCH"),
    (PrinterError("connection failed"), 503, "PRINTER_UNREACHABLE"),
])
def test_print_error_mapping(api, exc, http, code):
    base, _, state = api
    state["print"] = exc
    status, _, body = request(base + "/print", "POST", {"text": "x"})
    assert status == http
    assert body == {"ok": False, "error": code, "message": str(exc)}


def test_unknown_path_404(api):
    base, _, _ = api
    status, _, body = request(base + "/nope")
    assert status == 404 and body["ok"] is False


def test_cors_allowed_origin_echoed(api):
    base, _, _ = api
    _, headers, _ = request(base + "/health", headers={"Origin": ORIGIN})
    assert headers.get("Access-Control-Allow-Origin") == ORIGIN


def test_cors_foreign_origin_not_echoed(api):
    base, _, _ = api
    _, headers, _ = request(base + "/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in headers


def test_preflight_allows_private_network(api):
    base, _, _ = api
    status, headers, _ = request(base + "/print", "OPTIONS", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Private-Network": "true",
    })
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") == ORIGIN
    assert headers.get("Access-Control-Allow-Private-Network") == "true"
    assert "POST" in headers.get("Access-Control-Allow-Methods", "")


def test_print_without_mac_autodetect_miss(monkeypatch):
    monkeypatch.setattr(server_module, "resolve_mac", lambda configured: None)
    monkeypatch.setattr(server_module, "bluez_connected", lambda mac: False)
    srv = server_module.make_server("127.0.0.1", 0, None, 1, [], api_key=KEY)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        status, _, body = request(base + "/print", "POST", {"text": "x"})
        assert status == 503 and body["error"] == "PRINTER_UNREACHABLE"
        _, _, health = request(base + "/health")
        assert health["printer"] == {"paired": False, "connected": False}
    finally:
        srv.shutdown()
        srv.server_close()


def test_bluez_connected_parses_bluetoothctl(monkeypatch):
    class P:
        stdout = "Device 00:11\n\tName: PT\n\tConnected: yes\n"
    monkeypatch.setattr(server_module.subprocess, "run", lambda *a, **k: P())
    assert server_module.bluez_connected(MAC) is True
    P.stdout = "\tConnected: no\n"
    assert server_module.bluez_connected(MAC) is False


# ---- security: API key, Host, Origin, Content-Type ---------------------------

def test_health_needs_no_key(api):
    base, _, _ = api
    status, _, body = request(base + "/health", auth=False)
    assert status == 200 and body["ok"] is True


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": KEY},                          # missing scheme
    {"Authorization": f"Basic {KEY}"},
])
def test_print_requires_api_key(api, headers):
    base, calls, _ = api
    status, _, body = request(base + "/print", "POST", {"text": "x"}, headers=headers, auth=False)
    assert status == 401
    assert body["error"] == "UNAUTHORIZED"
    assert calls["print"] == []


def test_status_requires_api_key(api):
    base, calls, _ = api
    status, _, body = request(base + "/status", auth=False)
    assert status == 401 and body["error"] == "UNAUTHORIZED"
    assert calls["status"] == []


def test_simple_cross_site_post_is_rejected(api):
    """A website can send a no-cors text/plain POST without preflight."""
    base, calls, _ = api
    status, _, body = request(base + "/print", "POST", b'{"text": "x"}', auth=False,
                              headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
    assert status in (401, 403)
    assert calls["print"] == []


def test_post_requires_json_content_type(api):
    base, calls, _ = api
    status, _, body = request(base + "/print", "POST", b'{"text": "x"}',
                              headers={"Content-Type": "text/plain"})
    assert status == 415 and body["error"] == "UNSUPPORTED_MEDIA_TYPE"
    assert calls["print"] == []


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8710", "192.168.1.5:8710"])
def test_foreign_host_header_is_rejected(api, host):
    """DNS rebinding: a hostile domain resolving to 127.0.0.1."""
    base, calls, _ = api
    for path, method, body in [("/health", "GET", None), ("/print", "POST", {"text": "x"})]:
        status, _, out = request(base + path, method, body, headers={"Host": host})
        assert status == 403 and out["error"] == "FORBIDDEN"
    assert calls["print"] == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_loopback_host_headers_are_accepted(api, host):
    base, _, _ = api
    port = base.rsplit(":", 1)[1]
    status, _, _ = request(base + "/health", headers={"Host": f"{host}:{port}"})
    assert status == 200


def test_foreign_origin_is_rejected_even_with_key(api):
    base, calls, _ = api
    status, _, body = request(base + "/print", "POST", {"text": "x"},
                              headers={"Origin": "https://evil.example"})
    assert status == 403 and body["error"] == "FORBIDDEN"
    assert calls["print"] == []


def test_allowed_origin_with_key_prints(api):
    base, calls, _ = api
    status, headers, _ = request(base + "/print", "POST", {"text": "x"}, headers={"Origin": ORIGIN})
    assert status == 200 and len(calls["print"]) == 1
    assert headers.get("Access-Control-Allow-Origin") == ORIGIN


def test_preflight_allows_authorization_header(api):
    base, _, _ = api
    _, headers, _ = request(base + "/print", "OPTIONS", auth=False, headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    allowed = headers.get("Access-Control-Allow-Headers", "").lower()
    assert "authorization" in allowed and "content-type" in allowed


def test_server_refuses_to_start_without_key():
    with pytest.raises(ValueError):
        server_module.make_server("127.0.0.1", 0, MAC, 1, [], api_key="")


# ---- key file -----------------------------------------------------------------

def test_key_file_is_created_private_and_stable(tmp_path):
    import os
    import stat
    path = tmp_path / "conf" / "api-key"
    key = server_module.load_or_create_key(path)
    assert len(key) >= 32
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert server_module.load_or_create_key(path) == key
    new = server_module.load_or_create_key(path, rotate=True)
    assert new != key and server_module.load_or_create_key(path) == new


def test_key_file_with_loose_permissions_is_tightened(tmp_path):
    import os
    import stat
    path = tmp_path / "api-key"
    path.write_text("x" * 40 + "\n")
    os.chmod(path, 0o644)
    assert server_module.load_or_create_key(path) == "x" * 40
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
