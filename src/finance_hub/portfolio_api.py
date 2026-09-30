"""Background portfolio-pipeline refresh and payload cache access.

The portfolio pipeline (portfolio_tracker.cli.main) is slow and needs LSEG
Workspace running, so the hub never runs it automatically. Instead it reads
the last cached payload written by that pipeline (see
portfolio_tracker/cli.py's `_write_payload_cache`) and exposes a background
Refresh action that reruns the pipeline on a worker thread, capturing its
console output as a status log the Portfolio tab can poll and display.
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PortfolioRefreshJob:
    """Tracks the state of a single in-flight (or most recent) pipeline run."""

    def __init__(self, workbook_path: str | Path, payload_path: str | Path):
        self._workbook_path = Path(workbook_path)
        self._payload_path = Path(payload_path)
        self._lock = threading.Lock()
        self._state = "idle"  # idle | running | error
        self._log: list[str] = []
        self._error: str | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            state, log, error = self._state, list(self._log), self._error
        cached = read_cache(self._payload_path)
        return {
            "state": state,
            "log": log,
            "error": error,
            "generated_at": cached["generated_at"] if cached else None,
        }

    def start(self, extra_args: list[str] | None = None) -> None:
        with self._lock:
            if self._state == "running":
                raise RuntimeError("a portfolio refresh is already running")
            self._state = "running"
            self._log = []
            self._error = None
        thread = threading.Thread(target=self._run, args=(extra_args or [],), daemon=True)
        thread.start()

    def _run(self, extra_args: list[str]) -> None:
        from portfolio_tracker.cli import main as portfolio_main  # deferred: heavy import

        buffer = io.StringIO()
        argv = [
            "--workbook", str(self._workbook_path),
            "--payload-out", str(self._payload_path),
            *extra_args,
        ]
        try:
            with contextlib.redirect_stdout(buffer):
                code = portfolio_main(argv)
            with self._lock:
                self._log = buffer.getvalue().splitlines()
                if code == 0:
                    self._state = "idle"
                else:
                    self._state = "error"
                    self._error = f"portfolio-tracker exited with status {code}"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            with self._lock:
                self._log = buffer.getvalue().splitlines()
                self._state = "error"
                self._error = str(exc)


def read_cache(payload_path: str | Path) -> dict[str, Any] | None:
    """Read the JSON document `{"generated_at": ..., "payload": {...}}`.

    Returns None if the file doesn't exist yet (no pipeline run so far) or is
    unreadable/corrupt (e.g. truncated by a run that died mid-write).
    """
    path = Path(payload_path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def stale_days(generated_at: str | None) -> float | None:
    if not generated_at:
        return None
    generated = datetime.fromisoformat(generated_at)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - generated).total_seconds() / 86400
