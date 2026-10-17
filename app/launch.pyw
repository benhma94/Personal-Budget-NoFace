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

The same file is the entry point of the installed NOFACE.exe (PyInstaller, see
packaging\\noface.spec). When frozen, logs live in Documents\\NOFACE (or
NOFACE_HOME) and the workbooks in its data\\ subfolder, unless the user picked
another data folder (saved in SETTINGS_PATH). The folder picker opens when the
workbook is missing, on `NOFACE.exe --choose-folder`, or when Shift is held
during launch. The default folder is seeded from the bundled examples, and
Velopack updates the app from GitHub Releases.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# Read-only files shipped with the app: this folder, or the PyInstaller bundle.
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UPDATE_REPOSITORY = "https://github.com/benhma94/Personal-Budget-NoFace"
UPDATE_CHANNEL = "win-x64"  # must match --channel in scripts\build_windows.ps1
WORKBOOK = "Personal Budget.xlsx"
EXAMPLES = {
    WORKBOOK: "Personal Budget Template.xlsx",
    "portfolio.xlsx": "Portfolio Template.xlsx",
}
# Per PC, since each PC may map the shared data folder to a different drive.
# Not in %LOCALAPPDATA%\NOFACE: that is Velopack's install folder (paths are
# case-insensitive), which a reinstall or uninstall wipes.
_LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home())
SETTINGS_PATH = _LOCAL / "NOFACE Settings" / "settings.json"
LEGACY_SETTINGS_PATH = _LOCAL / "NoFace" / "settings.json"  # 0.1.2
MB_YESNOCANCEL, MB_YESNO, MB_ICONQUESTION = 0x3, 0x4, 0x20
IDCANCEL, IDYES, IDNO = 2, 6, 7


def documents_dir() -> Path:
    """The user's Documents folder, following OneDrive/folder redirection."""
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "Personal")
        if value:
            return Path(os.path.expandvars(str(value)))
    except (ImportError, OSError):
        pass
    return Path.home() / "Documents"


def home_dir(environ: dict[str, str] | None = None) -> Path:
    """Where the installed app keeps data\\ and logs\\."""
    override = (os.environ if environ is None else environ).get("NOFACE_HOME")
    return Path(override) if override else documents_dir() / "NOFACE"


def load_settings(path: Path = SETTINGS_PATH, legacy: Path | None = LEGACY_SETTINGS_PATH) -> dict:
    if legacy is not None and not path.is_file():
        path = legacy
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def save_settings(settings: dict, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def data_dir(environ: dict[str, str] | None = None, settings: dict | None = None) -> Path:
    """Where the installed app reads the workbooks: NOFACE_HOME, then the saved choice."""
    if (os.environ if environ is None else environ).get("NOFACE_HOME"):
        return home_dir(environ) / "data"
    saved = (load_settings() if settings is None else settings).get("data_dir")
    return Path(saved) if saved else home_dir(environ) / "data"


ROOT = home_dir() if FROZEN else Path(__file__).resolve().parent
PORT = 8765
LOCK_STALE_SECONDS = 120
LOCK_REFRESH_SECONDS = 30
LOG_PATH = ROOT / "logs" / "finance-hub.log"


def show_error(message: str) -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, "NOFACE", 0x10)


def ask(message: str, buttons: int = MB_YESNO) -> int:
    import ctypes

    return ctypes.windll.user32.MessageBoxW(None, message, "NOFACE", buttons | MB_ICONQUESTION)


def shift_held() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)  # VK_SHIFT


def choose_folder(title: str) -> Path | None:
    """The Windows folder picker; None if cancelled. ctypes because tkinter isn't bundled."""
    import ctypes
    from ctypes import wintypes

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize(None)  # the resizable new-style dialog needs COM
    display = ctypes.create_unicode_buffer(260)
    # BIF_RETURNONLYFSDIRS | BIF_EDITBOX | BIF_NEWDIALOGSTYLE
    info = BROWSEINFOW(None, None, ctypes.cast(display, wintypes.LPWSTR), title,
                       0x1 | 0x10 | 0x40, None, 0, 0)
    pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
    if not pidl:
        return None
    path = ctypes.create_unicode_buffer(260)
    try:
        found = shell32.SHGetPathFromIDListW(pidl, path)
    finally:
        ole32.CoTaskMemFree(pidl)
    return Path(path.value) if found and path.value else None


def resolve_data_dir(choose: bool, examples: Path) -> Path | None:
    """The folder to serve, asking the user to pick one when needed. None means quit."""
    settings = load_settings()
    data = data_dir(settings=settings)
    if not choose:
        if (data / WORKBOOK).is_file():
            return data
        saved = settings.get("data_dir")
        if not saved or data != Path(saved):  # the default folder: first run
            seed_examples(data, examples)
            return data
        if ask(f"{WORKBOOK} wasn't found in\n{data}\n\nChoose another folder?") != IDYES:
            return None
    while True:
        picked = choose_folder(f"Choose the folder that holds {WORKBOOK}")
        if picked is None:  # keep the current folder when it still works
            return data if choose and (data / WORKBOOK).is_file() else None
        if not (picked / WORKBOOK).is_file():
            answer = ask(f"{WORKBOOK} isn't in\n{picked}\n\n"
                         "Start with the example workbooks there?", MB_YESNOCANCEL)
            if answer == IDCANCEL:
                return None
            if answer == IDNO:
                continue
            seed_examples(picked, examples)
        settings["data_dir"] = str(picked)
        save_settings(settings)
        return picked


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


def seed_examples(data: Path, examples: Path) -> list[str]:
    """Copy the example workbooks into `data` where none exist yet."""
    data.mkdir(parents=True, exist_ok=True)
    seeded = []
    for target, example in EXAMPLES.items():
        if not (data / target).exists() and (examples / example).is_file():
            shutil.copyfile(examples / example, data / target)
            seeded.append(target)
    return seeded


class Updater:
    """Background Velopack check and download; the update installs once NOFACE exits.

    Every failure (not installed, offline, rate-limited) is logged and ignored:
    updates must never stop the app from running.
    """

    def __init__(self, repository: str = UPDATE_REPOSITORY) -> None:
        self.repository = repository
        self.manager = None
        self.update = None
        self.thread = threading.Thread(target=self._check, daemon=True)

    def _check(self) -> None:
        try:
            import velopack

            manager = velopack.UpdateManager(
                velopack.GithubSource(self.repository),
                velopack.UpdateOptions(
                    AllowVersionDowngrade=False,
                    MaximumDeltasBeforeFallback=-1,
                    ExplicitChannel=UPDATE_CHANNEL,
                ),
            )
            update = manager.check_for_updates()
            if update is None:
                return
            manager.download_updates(update)
            self.manager, self.update = manager, update
            print(f"NOFACE {update.TargetFullRelease.Version} downloaded; it installs on exit.")
        except Exception as error:
            print(f"Update check skipped: {error}")

    def apply_on_exit(self) -> None:
        if self.update is None:
            return
        try:
            self.manager.wait_exit_then_apply_updates(self.update, silent=True, restart=False)
        except Exception as error:
            print(f"Update install skipped: {error}")


def smoke_test() -> int:
    """Packaging check for CI: deferred imports and bundled files are present."""
    import finance_hub.server
    import lseg.data  # noqa: F401  (imported lazily by the Portfolio refresh)
    import portfolio_tracker.cli  # noqa: F401

    required = [BUNDLE / "examples" / name for name in EXAMPLES.values()]
    required.append(finance_hub.server._ASSET_DIR / "shell.html")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print("Missing bundled files: " + ", ".join(missing))
        return 1
    print("NOFACE smoke test passed")
    return 0


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


def data_env(data: Path) -> dict[str, str]:
    """Env vars pointing at the optional profile/symbols files that exist in `data`.

    Setting them makes the files required, so a missing one is left unset and
    the built-in defaults load instead.
    """
    files = {"FINANCE_PROFILE_PATH": "profile.json", "PORTFOLIO_SYMBOLS_PATH": "symbols.json"}
    return {name: str(data / file) for name, file in files.items() if (data / file).is_file()}


def _run(argv: list[str]) -> int:
    if FROZEN:
        # Read before the slow startup below, so a quick Shift press still counts.
        shift = shift_held()
        import velopack

        # Runs Velopack's install/update/uninstall hooks, which may exit.
        velopack.App().run()
        if argv[:1] == ["--smoke-test"]:
            return smoke_test()
        choose = argv[:1] == ["--choose-folder"]
        if choose:
            argv = argv[1:]
        ROOT.mkdir(parents=True, exist_ok=True)
        os.chdir(ROOT)
        choose = choose or shift
        data = resolve_data_dir(choose, BUNDLE / "examples")
        if data is None:
            return 0
        if choose and server_running():
            show_error("NOFACE is already open. Close it (or wait for it to shut down), "
                       f"then open it again to use\n{data}")
            return 0
        return _serve(argv, data)

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
    return _serve(argv, ROOT / "data")


def _serve(argv: list[str], data: Path) -> int:
    if server_running():
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return 0

    workbook = data / WORKBOOK
    if not workbook.is_file():
        show_error(f"{workbook} was not found. Place your workbook there first.")
        return 1

    lock = data / ".noface.lock"
    holder = acquire_lock(lock)
    if holder is not None:
        show_error(f"NOFACE is open on {holder}. Close it there first.")
        return 1
    stop = threading.Event()
    atexit.register(release_lock, lock)
    threading.Thread(target=_keep_lock_fresh, args=(lock, stop), daemon=True).start()
    try:
        load_lseg_key()
        # Read at import time by budget_dashboard.profile and portfolio_tracker.symbols.
        for name, value in data_env(data).items():
            os.environ.setdefault(name, value)
        LOG_PATH.parent.mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
        updater = Updater() if FROZEN else None
        if updater is not None:
            updater.thread.start()
        from finance_hub.cli import main as hub_main

        code = hub_main([
            "--workbook", str(workbook),
            "--portfolio-workbook", str(data / "portfolio.xlsx"),
            "--portfolio-payload", str(data / "portfolio_payload.json"),
            "--db", str(data / "journal_entry.sqlite"),
            *argv,
        ])
        if updater is not None:
            updater.apply_on_exit()
    finally:
        stop.set()
        release_lock(lock)
    if code:
        show_error(f"Finance Hub exited with an error. See {LOG_PATH} for details.")
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
                   f"See {LOG_PATH} for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
