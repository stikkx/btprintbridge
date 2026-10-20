import pytest
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


def test_serve_builds_server_from_args(monkeypatch, tmp_path):
    from btprintbridge import server
    seen = {}
    keyfile = tmp_path / "key"
    keyfile.write_text("k" * 43 + "\n")

    class FakeSrv:
        server_address = ("127.0.0.1", 8799)
        def serve_forever(self):
            seen["served"] = True
        def server_close(self):
            seen["closed"] = True

    def fake_make(host, port, mac, channel, origins, api_key):
        seen.update(host=host, port=port, mac=mac, channel=channel, origins=list(origins),
                    api_key=api_key)
        return FakeSrv()

    monkeypatch.setattr(server, "make_server", fake_make)
    rc = cli.main(["serve", "--port", "8799", "--mac", "AA:BB:CC:DD:EE:FF",
                   "--channel", "2", "--origins", "https://a.example, http://localhost:3000,",
                   "--key-file", str(keyfile)])
    assert rc == 0
    assert seen == {"host": "127.0.0.1", "port": 8799, "mac": "AA:BB:CC:DD:EE:FF",
                    "channel": 2, "origins": ["https://a.example", "http://localhost:3000"],
                    "api_key": "k" * 43, "served": True, "closed": True}


def test_serve_port_in_use_fails_cleanly(monkeypatch, capsys, tmp_path):
    from btprintbridge import server
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

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


def test_serve_creates_key_in_config_dir(monkeypatch, tmp_path):
    from btprintbridge import server
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    seen = {}

    class FakeSrv:
        server_address = ("127.0.0.1", 8710)
        def serve_forever(self): pass
        def server_close(self): pass

    def fake_make(host, port, mac, channel, origins, api_key):
        seen["key"] = api_key
        return FakeSrv()

    monkeypatch.setattr(server, "make_server", fake_make)
    assert cli.main(["serve"]) == 0
    assert (tmp_path / "btprintbridge" / "api-key").read_text().strip() == seen["key"]


def test_serve_refuses_non_loopback_host(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 1
    assert "loopback" in capsys.readouterr().err


def test_api_key_command_prints_and_rotates(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["api-key"]) == 0
    first = capsys.readouterr().out.strip()
    assert len(first) >= 32
    assert cli.main(["api-key"]) == 0
    assert capsys.readouterr().out.strip() == first
    assert cli.main(["api-key", "--rotate"]) == 0
    assert capsys.readouterr().out.strip() not in ("", first)


@pytest.mark.parametrize("argv,expected", [([], None), (["--channel", "auto"], None), (["--channel", "2"], 2)])
def test_channel_defaults_to_auto(monkeypatch, capsys, argv, expected):
    seen = {}

    def fake_status(mac, channel, lock_wait=0):
        seen["channel"] = channel
        return {"media_width": 12, "errors": []}

    monkeypatch.setattr(cli, "full_status", fake_status)
    assert cli.main(["status", "--mac", "00:11:22:33:44:55", "--json", *argv]) == 0
    assert seen["channel"] == expected


def test_channel_rejects_garbage():
    with pytest.raises(SystemExit):
        cli.main(["status", "--channel", "zwei"])


def test_autodetect_prefers_the_connected_printer(monkeypatch):
    monkeypatch.setattr(cli, "paired_names", lambda: [
        "Device 00:11:22:33:44:55 PT-P710BT1234",
        "Device 66:77:88:99:AA:BB PT-P710BT5678",
    ])
    monkeypatch.setattr(cli, "bt_connected", lambda mac: mac == "66:77:88:99:AA:BB")
    assert cli.mac_autodetect() == "66:77:88:99:AA:BB"
    monkeypatch.setattr(cli, "bt_connected", lambda mac: False)
    assert cli.mac_autodetect() == "00:11:22:33:44:55"     # none connected: first


def test_autodetect_single_printer_skips_connection_check(monkeypatch):
    monkeypatch.setattr(cli, "paired_names", lambda: ["Device 00:11:22:33:44:55 PT-P710BT1234"])
    monkeypatch.setattr(cli, "bt_connected", lambda mac: (_ for _ in ()).throw(AssertionError))
    assert cli.mac_autodetect() == "00:11:22:33:44:55"



def test_preview_writes_png_and_reports_layout(tmp_path, capsys):
    from PIL import Image
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"vcard": {"name": "Max Stick", "phone": "0170 1"}}))
    out = tmp_path / "p.png"
    assert cli.main(["preview", "--spec", str(spec), "--tape", "12", "--out", str(out),
                     "--scale", "2"]) == 0
    info = json.loads(capsys.readouterr().out)
    with Image.open(out) as img:
        size = img.size
    assert size[1] == 76 * 2
    assert info["tape_width"] == 12 and info["font_size"] > 0
    assert info["length_mm"] == round(size[0] / 2 / 7.087, 1)


def test_preview_reports_spec_errors(tmp_path, capsys):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"vcard": {"email": "nope"}}))
    assert cli.main(["preview", "--spec", str(spec), "--out", str(tmp_path / "p.png")]) == 1
    assert "email" in capsys.readouterr().err
