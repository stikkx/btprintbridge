import json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "bin" / "btprintbridge"

def run_launcher(*args):
    return subprocess.run([sys.executable, str(LAUNCHER), *args], capture_output=True, text=True, cwd=REPO)

def test_no_args_prints_usage_and_exit_2():
    r = run_launcher()
    assert r.returncode == 2
    assert "usage" in (r.stderr + r.stdout).lower()

def test_print_missing_spec_file_exit_1_and_stderr(tmp_path):
    r = run_launcher("print", "--spec", str(tmp_path / "absent.json"), "--mac", "00:00:00:00:00:00")
    assert r.returncode == 1
    assert r.stderr.startswith("btprintbridge:")

def test_print_invalid_spec_exit_1_and_stderr(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"text": "Hi", "copies": 0}))
    r = run_launcher("print", "--spec", str(spec), "--mac", "00:00:00:00:00:00")
    assert r.returncode == 1
    assert "copies" in r.stderr

def test_print_binary_spec_exit_1_and_stderr(tmp_path):
    spec = tmp_path / "spec.bin"
    spec.write_bytes(b"\x00\xff\xfe\x9c\x80" * 16)
    r = run_launcher("print", "--spec", str(spec), "--mac", "00:00:00:00:00:00")
    assert r.returncode == 1
    assert r.stderr.startswith("btprintbridge:")

def test_importing_main_module_does_not_execute_cli():
    r = subprocess.run([sys.executable, "-c", "import btprintbridge.__main__"], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    assert "usage" not in (r.stdout + r.stderr).lower()
