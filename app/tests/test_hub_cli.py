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


def test_home_dir_prefers_noface_home(tmp_path):
    assert launch.home_dir({"NOFACE_HOME": str(tmp_path)}) == tmp_path
    assert launch.home_dir({}) == launch.documents_dir() / "NOFACE"


def test_seed_examples_copies_only_missing_workbooks(tmp_path):
    examples = Path(__file__).parents[1] / "examples"
    data = tmp_path / "data"
    data.mkdir()
    (data / "portfolio.xlsx").write_bytes(b"mine")
    assert launch.seed_examples(data, examples) == ["Personal Budget.xlsx"]
    assert (data / "Personal Budget.xlsx").read_bytes() == (
        examples / "Personal Budget Template.xlsx").read_bytes()
    assert (data / "portfolio.xlsx").read_bytes() == b"mine"
    assert launch.seed_examples(data, examples) == []


def test_updater_without_an_update_does_nothing():
    launch.Updater().apply_on_exit()  # no download yet: must not raise


def test_data_dir_prefers_noface_home_then_saved_choice(tmp_path):
    saved = {"data_dir": str(tmp_path / "picked")}
    assert launch.data_dir({"NOFACE_HOME": str(tmp_path)}, saved) == tmp_path / "data"
    assert launch.data_dir({}, saved) == tmp_path / "picked"
    assert launch.data_dir({}, {}) == launch.documents_dir() / "NOFACE" / "data"


def test_settings_round_trip_and_tolerate_bad_files(tmp_path):
    path = tmp_path / "NoFace" / "settings.json"
    assert launch.load_settings(path) == {}
    launch.save_settings({"data_dir": "Z:\data"}, path)
    assert launch.load_settings(path) == {"data_dir": "Z:\data"}
    path.write_text("not json", encoding="utf-8")
    assert launch.load_settings(path) == {}
    path.write_text("[]", encoding="utf-8")
    assert launch.load_settings(path) == {}


def test_load_settings_falls_back_to_legacy_path(tmp_path):
    path, legacy = tmp_path / "new" / "settings.json", tmp_path / "old" / "settings.json"
    launch.save_settings({"data_dir": "old"}, legacy)
    assert launch.load_settings(path, legacy) == {"data_dir": "old"}
    launch.save_settings({"data_dir": "new"}, path)
    assert launch.load_settings(path, legacy) == {"data_dir": "new"}


def test_data_env_names_only_existing_files(tmp_path):
    assert launch.data_env(tmp_path) == {}
    (tmp_path / "profile.json").write_text("{}", encoding="utf-8")
    assert launch.data_env(tmp_path) == {"FINANCE_PROFILE_PATH": str(tmp_path / "profile.json")}


def test_resolve_data_dir_uses_saved_folder_with_workbook(tmp_path, monkeypatch):
    picked = tmp_path / "picked"
    picked.mkdir()
    (picked / launch.WORKBOOK).write_bytes(b"mine")
    monkeypatch.delenv("NOFACE_HOME", raising=False)
    monkeypatch.setattr(launch, "load_settings", lambda: {"data_dir": str(picked)})
    assert launch.resolve_data_dir(False, tmp_path / "examples") == picked


def test_resolve_data_dir_saves_picked_folder(tmp_path, monkeypatch):
    picked = tmp_path / "picked"
    picked.mkdir()
    (picked / launch.WORKBOOK).write_bytes(b"mine")
    saved = {}
    monkeypatch.delenv("NOFACE_HOME", raising=False)
    monkeypatch.setattr(launch, "load_settings", lambda: {})
    monkeypatch.setattr(launch, "save_settings", saved.update)
    monkeypatch.setattr(launch, "choose_folder", lambda title: picked)
    assert launch.resolve_data_dir(True, tmp_path / "examples") == picked
    assert saved == {"data_dir": str(picked)}


def test_resolve_data_dir_asks_when_saved_folder_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("NOFACE_HOME", raising=False)
    monkeypatch.setattr(launch, "load_settings", lambda: {"data_dir": str(tmp_path / "gone")})
    monkeypatch.setattr(launch, "ask", lambda message, buttons=launch.MB_YESNO: launch.IDNO)
    assert launch.resolve_data_dir(False, tmp_path / "examples") is None
