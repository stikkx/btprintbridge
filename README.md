# btprintbridge

Bluetooth bridge for the Brother PT-P710BT (P-touch CUBE Plus) label printer.
Backend of the Omarchy bar plugin
[OmaLabel (`stikkx.labelprint`)](https://github.com/stikkx/omarchy-labelprint),
and a local HTTP print API that web apps can print through.

Python, no compile step. Talks RFCOMM directly (no CUPS, no driver).

## Install

```bash
sudo pacman -S python-pillow python-qrcode
./install.sh          # symlinks bin/btprintbridge to ~/.local/bin/btprintbridge
```

Pair the printer once (`bluetoothctl` → `pair` / `trust`). Without `--mac`,
the first paired device whose name starts with `PT-` is used.

## CLI

```bash
btprintbridge status [--mac M] [--channel auto|N] [--json]
btprintbridge print --spec label.json [--copies N] [--cut each|end] [--force]
btprintbridge serve [--port 8710] [--origins https://app.example,...]
btprintbridge api-key [--rotate]
```

Label spec (`print --spec`), exactly one of `text` / `image`:

```json
{
  "text": "Shelf A-3\nDrawer 2",
  "qr": {"value": null},            // false = off; null value = use the text
  "barcode": false,                 // Code128, rendered vertically on the right
  "copies": 1,                      // 1..100
  "cut": "each",                    // "each" = cut every copy, "end" = one strip
  "tape_width": 12                  // optional; refuse if another tape is loaded
}
```

`image` is a PNG path (CLI) whose height equals the tape's printable dots:
9 mm 32, 12 mm 54, 18 mm 86, 24 mm 128. Transparent PNGs: alpha > 0 is ink;
opaque PNGs: dark pixels are ink. At most 4096 px long.

## HTTP API (`serve`)

Loopback only (`127.0.0.1:8710` by default). The OmaLabel popup has a
**Print API** switch that runs `serve` as the transient user unit
`btprintbridge-api`; you can also run it by hand.

### API key

Every website you open can reach a loopback port, so `/status` and `/print`
need the API key:

```
Authorization: Bearer <key>
```

The key is created on first start in `~/.config/btprintbridge/api-key`
(mode 0600). Show it with `btprintbridge api-key`, replace it with
`btprintbridge api-key --rotate` (restart `serve` afterwards). Paste it into
the web app that prints (BinaryStock: print dialog → "API key").

The server also refuses requests whose `Host` is not a loopback name (DNS
rebinding), requests from origins not passed with `--origins` (403), and
`POST` bodies that are not `application/json` (415). `/health` needs no key
and does not reveal the printer address.

| Method | Path      | Response |
|--------|-----------|----------|
| GET    | `/health` | `{"ok":true,"service":"btprintbridge","api":1,"auth":"bearer","printer":{"paired","connected"}}` — fast, BlueZ state only, no key needed |
| GET    | `/status` | cassette query over Bluetooth: `{"ok":true,"connected":true,"media_width":12,...}` or `connected:false` + `error` |
| POST   | `/print`  | body = label spec above; `image` is a **base64** PNG. → `{"ok":true,"printed":N,"media_width":12}` |

Errors: `{"ok":false,"error":CODE,"message":"…"}`

| CODE                  | HTTP | Meaning |
|-----------------------|------|---------|
| `VALIDATION`          | 400  | bad JSON / spec |
| `UNAUTHORIZED`        | 401  | missing or wrong API key |
| `FORBIDDEN`           | 403  | foreign `Host` or `Origin` |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | body is not `application/json` |
| `BUSY`                | 409  | another job held the printer for > 90 s |
| `TAPE_MISMATCH`       | 422  | loaded tape ≠ `tape_width` |
| `NOT_READY`           | 422  | printer reports an error (cover open, no tape, …) |
| `PRINTER_UNREACHABLE` | 503  | printer off / out of range / not paired |

CORS: only origins passed with `--origins` get `Access-Control-Allow-Origin`;
preflights also answer `Access-Control-Allow-Private-Network: true` so an
HTTPS page may call the loopback API in Chromium.

```bash
curl -s localhost:8710/health
curl -s -X POST localhost:8710/print -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $(btprintbridge api-key)" \
  -d '{"text":"TOOL-000123\nCordless drill","qr":{"value":"TOOL-000123"},"copies":2}'
```

Print jobs are serialized by a lock file (`~/.cache/btprintbridge.lock`);
API calls queue up to 90 s behind a running job, the CLI fails fast.

## Tests

```bash
python -m pytest -q
```

Hardware-verified on a PT-P710BT with 12 mm TZe tape.

## License

MIT — see [LICENSE](LICENSE).
