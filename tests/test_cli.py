"""CLI contract tests for `btprintbridge status` / `print` + PT- autodetect."""

import json

from btprintbridge import cli, transport


def test_status_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "full_status", lambda mac, ch: {"connected": True, "media_width": 12})
    rc = cli.main(["status", "--json", "--mac", "00:00:00:00:00:00"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out["connected"] is True and out["media_width"] == 12


def test_status_connection_failure_reports_not_connected(monkeypatch, capsys):
    monkeypatch.setattr(cli, "paired_names", lambda: ["Device 00:11:22:33:44:55 PT-P710BT1234"])
    monkeypatch.setattr(cli, "full_status", lambda mac, ch: (_ for _ in ()).throw(transport.PrinterError("off")))
    rc = cli.main(["status", "--json"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out == {"connected": False, "media_width": 0, "media_type": None, "errors": [], "error": "off"}


def test_status_without_paired_printer_reports_friendly_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "paired_names", lambda: [])
    rc = cli.main(["status", "--json"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out == {"connected": False, "media_width": 0, "media_type": None, "errors": [], "error": "no paired P-touch printer found"}


def test_autodetect_pt_device_from_paired_list(monkeypatch, capsys):
    monkeypatch.setattr(cli, "paired_names", lambda: [
        "Device 00:11:22:33:44:55 PT-P710BT1234",
        "Device AA:BB:CC:DD:EE:FF BT-Something",
    ])
    assert cli.mac_autodetect() == "00:11:22:33:44:55"


def test_autodetect_real_world_line_exact(monkeypatch, capsys):
    monkeypatch.setattr(cli, "paired_names", lambda: ["Device 00:11:22:33:44:55 PT-P710BT1234"])
    assert cli.mac_autodetect() == "00:11:22:33:44:55"


def test_autodetect_skips_non_pt_device(monkeypatch, capsys):
    monkeypatch.setattr(cli, "paired_names", lambda: ["Device AA:BB:CC:DD:EE:FF Galaxy Buds"])
    assert cli.mac_autodetect() is None


def test_print_error_exit_code_and_stderr(monkeypatch, capsys, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"text": "Hi"}))
    monkeypatch.setattr(cli, "print_spec", lambda *a, **k: (_ for _ in ()).throw(transport.PrinterBusy("printer busy")))
    rc = cli.main(["print", "--spec", str(spec), "--mac", "00:00:00:00:00:00"])
    cap = capsys.readouterr()
    assert rc == 1 and "printer busy" in cap.err


def test_serve_builds_server_from_args(monkeypatch):
    from btprintbridge import server
    seen = {}

    class FakeSrv:
        server_address = ("127.0.0.1", 8799)
        def serve_forever(self):
            seen["served"] = True
        def server_close(self):
            seen["closed"] = True

    def fake_make(host, port, mac, channel, origins):
        seen.update(host=host, port=port, mac=mac, channel=channel, origins=list(origins))
        return FakeSrv()

    monkeypatch.setattr(server, "make_server", fake_make)
    rc = cli.main(["serve", "--port", "8799", "--mac", "AA:BB:CC:DD:EE:FF",
                   "--channel", "2", "--origins", "https://a.example, http://localhost:3000,"])
    assert rc == 0
    assert seen == {"host": "127.0.0.1", "port": 8799, "mac": "AA:BB:CC:DD:EE:FF",
                    "channel": 2, "origins": ["https://a.example", "http://localhost:3000"],
                    "served": True, "closed": True}


def test_serve_port_in_use_fails_cleanly(monkeypatch, capsys):
    from btprintbridge import server

    def boom(*a):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(server, "make_server", boom)
    rc = cli.main(["serve", "--port", "8710"])
    assert rc == 1
    assert "Address already in use" in capsys.readouterr().err


def test_print_cut_flag_passed(monkeypatch, capsys, tmp_path):
    spec = tmp_path / "s.json"
    spec.write_text('{"text": "x"}')
    seen = {}

    def fake_print(spec, mac, channel=1, copies=None, force=False, cut=None):
        seen["cut"] = cut
        return {"copies_printed": 1, "media_width": 12}

    monkeypatch.setattr(cli, "print_spec", fake_print)
    rc = cli.main(["print", "--spec", str(spec), "--mac", "AA:BB:CC:DD:EE:FF", "--cut", "end"])
    assert rc == 0 and seen["cut"] == "end"
