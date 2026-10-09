"""NOFACE launcher, run by NoFace.lnk as `<local runtime>\\python\\pythonw.exe launch.pyw`.

The shortcut sits in the repo root; this file and everything it uses live in
the app\\ subfolder (ROOT below), which is also the shortcut's working directory.

Replaces run.bat / run_hidden.vbs / msgbox.vbs. pythonw has no console, so
nothing flashes on screen, and because the only executable started is the
local pythonw.exe (this file is merely read by it), Windows never shows the
network-zone security prompts that .bat/.vbs files on the NAS share trigger.

Dependencies come from the per-PC runtime mirror built by setup.ps1, so this
file must stay stdlib-only. Running it with any other Python (e.g. the dev
venv: `python launch.pyw`) skips the mirror checks.
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765
LOCK_STALE_SECONDS = 120
LOCK_REFRESH_SECONDS = 30
LOG_PATH = ROOT / "logs" / "finance-hub.log"


def show_error(message: str) -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, "NOFACE", 0x10)


def runtime_dir(executable: str = sys.executable) -> Path | None:
    """The NoFace runtime containing `executable`, or None if it isn't one."""
    candidate = Path(executable).resolve().parent.parent
    return candidate if (candidate / "site-packages").is_dir() else None


def read_stamp(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def stamp_is_stale(nas_stamp: Path, local_stamp: Path) -> bool:
    nas = read_stamp(nas_stamp)
    return nas is not None and nas != read_stamp(local_stamp)


def server_running(port: int = PORT, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_lock(path: Path) -> dict | None:
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return held if isinstance(held, dict) else None


def _write_lock(path: Path, host: str, pid: int, now: float) -> None:
    tmp = path.with_name(f"{path.name}.{host}.tmp")
    tmp.write_text(json.dumps({"host": host, "pid": pid, "updated": now}), encoding="utf-8")
    os.replace(tmp, path)


def acquire_lock(path: Path, *, now: float | None = None, hostname: str | None = None,
                 pid: int | None = None) -> str | None:
    """Take the cross-PC lock. Returns None on success, else the holder's host."""
    now = time.time() if now is None else now
    host = hostname or socket.gethostname()
    held = _read_lock(path)
    # Same-host locks are always taken over: a live same-host instance was
    # already caught by the port probe, so any such lock is left from a crash.
    if held and held.get("host") != host:
        try:
            fresh = now - float(held.get("updated")) < LOCK_STALE_SECONDS
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            return str(held.get("host"))
    _write_lock(path, host, os.getpid() if pid is None else pid, now)
    return None


def _owns_lock(path: Path, host: str, pid: int) -> bool:
    held = _read_lock(path)
    return bool(held) and held.get("host") == host and held.get("pid") == pid


def release_lock(path: Path, *, hostname: str | None = None, pid: int | None = None) -> None:
    host = hostname or socket.gethostname()
    if _owns_lock(path, host, os.getpid() if pid is None else pid):
        path.unlink(missing_ok=True)


def _keep_lock_fresh(path: Path, stop: threading.Event) -> None:
    host, pid = socket.gethostname(), os.getpid()
    while not stop.wait(LOCK_REFRESH_SECONDS):
        try:
            if _owns_lock(path, host, pid):
                _write_lock(path, host, pid, time.time())
        except OSError:
            pass  # NAS hiccup; try again next tick


def load_lseg_key() -> None:
    if os.environ.get("LSEG_APP_KEY"):
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "LSEG_APP_KEY")
    except (ImportError, OSError):
        return
    if value:
        os.environ["LSEG_APP_KEY"] = str(value)


def _run(argv: list[str]) -> int:
    os.chdir(ROOT)
    # Every import probes sys.path entries in order, and each probe of a NAS
    # folder is a network round trip. Drop this script's folder and put the
    # NAS-hosted src last, so stdlib and third-party imports stay local.
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != ROOT]
    runtime = runtime_dir()
    if runtime is not None:
        sys.path.append(str(runtime / "site-packages"))
    sys.path.append(str(ROOT / "src"))
    if runtime is not None:
        if stamp_is_stale(ROOT / ".runtime" / "stamp.txt", runtime / "stamp.txt"):
            show_error("The NOFACE runtime was updated. Run setup.ps1 on this PC, then try again.")
            return 1

    if server_running():
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return 0

    if not (ROOT / "data" / "Personal Budget.xlsx").is_file():
        show_error(r"app\data\Personal Budget.xlsx was not found. Place your workbook there first.")
        return 1

    lock = ROOT / "data" / ".noface.lock"
    holder = acquire_lock(lock)
    if holder is not None:
        show_error(f"NOFACE is open on {holder}. Close it there first.")
        return 1
    stop = threading.Event()
    atexit.register(release_lock, lock)
    threading.Thread(target=_keep_lock_fresh, args=(lock, stop), daemon=True).start()
    try:
        load_lseg_key()
        LOG_PATH.parent.mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
        from finance_hub.cli import main as hub_main

        code = hub_main(argv)
    finally:
        stop.set()
        release_lock(lock)
    if code:
        show_error(r"Finance Hub exited with an error. See app\logs\finance-hub.log for details.")
    return code


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(sys.argv[1:] if argv is None else argv)
    except Exception:
        text = traceback.format_exc()
        try:
            if sys.stderr is not None:  # the log after redirection, or a dev console
                sys.stderr.write(text)
            else:
                LOG_PATH.parent.mkdir(exist_ok=True)
                with open(LOG_PATH, "a", encoding="utf-8") as log:
                    log.write(text)
        except OSError:
            pass
        show_error(f"NOFACE failed to start:\n\n{text.strip().splitlines()[-1]}\n\n"
                   r"See app\logs\finance-hub.log for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
