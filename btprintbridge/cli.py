"""Command-line interface for the Brother PT-P710BT label printer.

Subcommands:

``status [--mac M] [--channel N] [--json]``
    Query the printer; ``--json`` prints one JSON line (always exit 0 unless
    usage error).  Without ``--json`` the fields are printed human-readable.

``print --spec spec.json [--mac M] [--channel N] [--force] [--copies N] [--cut each|end]``
    Print a label; stdout carries the summary JSON, errors go to stderr as
    ``btprintbridge: <message>`` with exit code 1.

``serve [--port P] [--origins a,b] [--mac M] [--channel N] [--key-file F]``
    Run the local HTTP print API on 127.0.0.1 (see ``btprintbridge.server``).
    Callers need the API key (``Authorization: Bearer <key>``).

``api-key [--rotate] [--key-file F]``
    Print the API key (created on first use); ``--rotate`` makes a new one.

``--mac`` defaults to autodetection via ``bluetoothctl devices Paired``
(first device whose name starts with ``PT-``). ``--channel`` defaults to
``auto``: the printer's RFCOMM channel is found and remembered per MAC.
"""

import argparse
import json
import re
import subprocess
import sys

from btprintbridge.print import print_spec
from btprintbridge.transport import PrinterBusy, PrinterError, full_status

PT_NAME_RE = re.compile(r"^PT-")

# Shape of the failure report when the printer is unreachable.
NOT_CONNECTED = {
    "connected": False,
    "media_width": 0,
    "media_type": None,
    "errors": [],
    "error": None,
}


def paired_names() -> list[str]:
    """One line per paired device, as ``bluetoothctl devices Paired`` prints."""
    try:
        proc = subprocess.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return proc.stdout.splitlines()


def _channel(value: str) -> int | None:
    """``--channel``: 'auto' -> None (find it), else 1..30."""
    if value.strip().lower() == "auto":
        return None
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("channel must be 'auto' or a number") from None
    if not 1 <= n <= 30:
        raise argparse.ArgumentTypeError("channel must be between 1 and 30")
    return n


def bt_connected(mac: str) -> bool:
    """BlueZ connection state of *mac* via ``bluetoothctl info``."""
    try:
        proc = subprocess.run(["bluetoothctl", "info", mac],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(re.search(r"^\s*Connected:\s*yes\s*$", proc.stdout or "", re.MULTILINE))


def mac_autodetect() -> str | None:
    """MAC of a paired device whose name starts with ``PT-``, or None.

    With several paired printers the connected one wins, else the first.
    ``bluetoothctl devices Paired`` lines have the form ``Device <MAC> <name...>``.
    """
    found = []
    for line in paired_names():
        parts = line.split(None, 2)
        if len(parts) == 3 and PT_NAME_RE.match(parts[2].strip()):
            found.append(parts[1])
    if len(found) > 1:
        for mac in found:
            if bt_connected(mac):
                return mac
    return found[0] if found else None


def _fail(message: str) -> int:
    print(f"btprintbridge: {message}", file=sys.stderr)
    return 1


def _not_connected(reason: str) -> dict:
    packet = dict(NOT_CONNECTED)
    packet["error"] = reason
    return packet


def cmd_status(args) -> int:
    """Status inquiry; exit 0 even when the printer cannot be reached.

    Without ``--mac``, autodetection scanning ``bluetoothctl devices Paired``
    for the first device whose name starts with ``PT-``; a miss reports a
    friendly ``connected: false`` result without attempting the connection.
    """
    mac = args.mac or mac_autodetect()
    result = None
    reason = None
    if mac is None:
        reason = "no paired P-touch printer found"
    else:
        try:
            result = full_status(mac, args.channel)
        except (PrinterError, PrinterBusy) as e:
            reason = str(e)

    if result is not None:
        out = dict(result)
        out["connected"] = True
        out["error"] = None
    else:
        out = _not_connected(reason)

    if args.json:
        print(json.dumps(out))
    else:
        if out["connected"]:
            for key in ("status_type", "phase_type", "media_type",
                        "media_width", "media_length", "tape_color",
                        "text_color", "auto_cut", "errors"):
                print(f"{key}: {out.get(key)}")
        else:
            print(f"not connected: {out['error']}", file=sys.stderr)
    return 0


def cmd_print(args) -> int:
    try:
        with open(args.spec, "r", encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, UnicodeDecodeError) as e:
        return _fail(f"cannot read spec: {e}")
    except json.JSONDecodeError as e:
        return _fail(f"spec is not valid JSON: {e}")
    if not isinstance(spec, dict):
        return _fail("spec must be a JSON object")

    mac = args.mac or mac_autodetect()
    if mac is None:
        return _fail("no paired P-touch printer found")

    try:
        result = print_spec(
            spec, mac, channel=args.channel, copies=args.copies, force=args.force,
            cut=args.cut,
        )
    except (PrinterError, PrinterBusy, ValueError) as e:
        return _fail(str(e))
    print(json.dumps({
        "printed": result["copies_printed"],
        "media_width": result["media_width"],
    }))
    return 0


def _key_path(args):
    from btprintbridge import server
    return args.key_file or server.default_key_path()


def cmd_api_key(args) -> int:
    from btprintbridge import server
    try:
        print(server.load_or_create_key(_key_path(args), rotate=args.rotate))
    except OSError as e:
        return _fail(f"cannot access API key file: {e}")
    return 0


def cmd_serve(args) -> int:
    from btprintbridge import server

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        return _fail("the print API only listens on a loopback address")
    origins = [o.strip() for o in (args.origins or "").split(",") if o.strip()]
    try:
        key = server.load_or_create_key(_key_path(args))
    except OSError as e:
        return _fail(f"cannot access API key file: {e}")
    try:
        srv = server.make_server(args.host, args.port, args.mac, args.channel, origins, key)
    except OSError as e:
        return _fail(f"cannot listen on {args.host}:{args.port}: {e}")
    host, port = srv.server_address[:2]
    print(f"btprintbridge API listening on http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btprintbridge",
        description="Bluetooth bridge for the Brother PT-P710BT label printer.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser(
        "status", help="Show printer status; exit 0 even when unreachable."
    )
    status_parser.add_argument("--mac", help="printer MAC address (default: autodetect)")
    status_parser.add_argument("--channel", type=_channel, default=None, help="RFCOMM channel or 'auto' (default: auto)")
    status_parser.add_argument("--json", action="store_true", help="output a single JSON line")
    status_parser.set_defaults(func=cmd_status)

    print_parser = subparsers.add_parser("print", help="Print a label from a spec JSON file.")
    print_parser.add_argument("--spec", required=True, help="label spec JSON file")
    print_parser.add_argument("--mac", help="printer MAC address (default: autodetect)")
    print_parser.add_argument("--channel", type=_channel, default=None, help="RFCOMM channel or 'auto' (default: auto)")
    print_parser.add_argument("--force", action="store_true", help="print even if tape width mismatches spec")
    print_parser.add_argument("--copies", type=int, default=None, help="number of copies (overrides spec)")
    print_parser.add_argument("--cut", choices=("each", "end"), default=None,
                              help="cut after each copy (default) or once at the end")
    print_parser.set_defaults(func=cmd_print)

    serve_parser = subparsers.add_parser("serve", help="Run the local HTTP print API.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="loopback bind address (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8710, help="port (default: 8710)")
    serve_parser.add_argument("--origins", default="", help="comma-separated CORS origins allowed to call the API")
    serve_parser.add_argument("--mac", help="printer MAC address (default: autodetect per request)")
    serve_parser.add_argument("--channel", type=_channel, default=None, help="RFCOMM channel or 'auto' (default: auto)")
    serve_parser.add_argument("--key-file", help="API key file (default: ~/.config/btprintbridge/api-key)")
    serve_parser.set_defaults(func=cmd_serve)

    key_parser = subparsers.add_parser("api-key", help="Print the print-API key (created on first use).")
    key_parser.add_argument("--rotate", action="store_true", help="replace the key with a new one")
    key_parser.add_argument("--key-file", help="API key file (default: ~/.config/btprintbridge/api-key)")
    key_parser.set_defaults(func=cmd_api_key)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
