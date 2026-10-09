from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import socket
from pathlib import Path

from finance_hub.cli import _parser, main


def test_main_returns_1_when_workbook_missing(tmp_path, capsys):
    missing = tmp_path / "Nope.xlsx"
    assert main(["--workbook", str(missing)]) == 1
    assert "not found" in capsys.readouterr().out


def test_defaults_match_the_single_launcher_convention():
    args = _parser().parse_args([])
    assert args.workbook.name == "Personal Budget.xlsx"
    assert args.portfolio_workbook.name == "portfolio.xlsx"
    assert args.portfolio_payload.name == "portfolio_payload.json"
    assert args.port == 8765


def _load_launcher():
    path = Path(__file__).parents[1] / "launch.pyw"
    # .pyw isn't a recognised source suffix, so the loader must be explicit.
    loader = importlib.machinery.SourceFileLoader("noface_launch", str(path))
    spec = importlib.util.spec_from_file_location("noface_launch", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


launch = _load_launcher()


def test_lock_acquired_when_absent(tmp_path):
    lock = tmp_path / ".noface.lock"
    assert launch.acquire_lock(lock, now=1000.0, hostname="pc-a", pid=1) is None
    held = json.loads(lock.read_text(encoding="utf-8"))
    assert held == {"host": "pc-a", "pid": 1, "updated": 1000.0}


def test_fresh_foreign_lock_is_refused(tmp_path):
    lock = tmp_path / ".noface.lock"
    launch.acquire_lock(lock, now=1000.0, hostname="pc-b", pid=7)
    assert launch.acquire_lock(lock, now=1060.0, hostname="pc-a", pid=1) == "pc-b"
    assert json.loads(lock.read_text(encoding="utf-8"))["host"] == "pc-b"


def test_stale_foreign_lock_is_taken_over(tmp_path):
    lock = tmp_path / ".noface.lock"
    launch.acquire_lock(lock, now=1000.0, hostname="pc-b", pid=7)
    assert launch.acquire_lock(lock, now=1000.0 + 121, hostname="pc-a", pid=1) is None
    assert json.loads(lock.read_text(encoding="utf-8"))["host"] == "pc-a"


def test_same_host_lock_is_taken_over(tmp_path):
    lock = tmp_path / ".noface.lock"
    launch.acquire_lock(lock, now=1000.0, hostname="pc-a", pid=7)
    assert launch.acquire_lock(lock, now=1001.0, hostname="pc-a", pid=1) is None
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == 1


def test_release_removes_only_our_lock(tmp_path):
    lock = tmp_path / ".noface.lock"
    launch.acquire_lock(lock, now=1000.0, hostname="pc-b", pid=7)
    launch.release_lock(lock, hostname="pc-a", pid=1)
    assert lock.exists()
    launch.release_lock(lock, hostname="pc-b", pid=7)
    assert not lock.exists()


def test_port_probe_on_unused_port_returns_false():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert launch.server_running(port, timeout=0.2) is False


def test_stamp_staleness(tmp_path):
    nas, local = tmp_path / "nas.txt", tmp_path / "local.txt"
    assert launch.stamp_is_stale(nas, local) is False  # no NAS stamp: nothing to compare
    nas.write_text("abc\n", encoding="utf-8")
    assert launch.stamp_is_stale(nas, local) is True  # mirror never synced
    local.write_text("abc", encoding="utf-8")
    assert launch.stamp_is_stale(nas, local) is False
    local.write_text("old", encoding="utf-8")
    assert launch.stamp_is_stale(nas, local) is True


def test_runtime_dir_ignores_non_noface_python(tmp_path):
    exe = tmp_path / "runtime" / "python" / "pythonw.exe"
    exe.parent.mkdir(parents=True)
    assert launch.runtime_dir(str(exe)) is None
    (tmp_path / "runtime" / "site-packages").mkdir()
    assert launch.runtime_dir(str(exe)) == (tmp_path / "runtime").resolve()
