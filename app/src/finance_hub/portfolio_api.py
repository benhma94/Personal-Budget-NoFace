"""Background portfolio-pipeline refresh, payload cache access, and the
Journal tab's portfolio-transactions CSV upload.

The portfolio pipeline (portfolio_tracker.cli.main) is slow and needs LSEG
Workspace running, so the hub never runs it automatically. Instead it reads
the last cached payload written by that pipeline (see
portfolio_tracker/cli.py's `_write_payload_cache`) and exposes a background
Refresh action that reruns the pipeline on a worker thread, capturing its
console output as a status log the Portfolio tab can poll and display.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import threading
from datetime import date, datetime, timezone
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

    @property
    def workbook_path(self) -> Path:
        return self._workbook_path

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


def detect_portfolio_format(headers: list[str]) -> str | None:
    """Name the broker export these CSV headers belong to, or None.

    Wealthsimple is the only format the pipeline reads today. The date
    columns mirror the three generations read_wealthsimple_csv accepts
    (portfolio_tracker/io_csv.py).
    """
    names = {h.strip() for h in headers}
    if {"activity_type", "net_cash_amount"} <= names and names & {
        "transaction_date", "effective_at", "effective_date",
    }:
        return "wealthsimple"
    return None


def preview_portfolio_import(text: str) -> dict[str, Any]:
    """Parse an uploaded portfolio CSV and summarise what the pipeline will see."""
    headers = next(csv.reader(io.StringIO(text)), [])
    fmt = detect_portfolio_format(headers)
    if fmt is None:
        raise ValueError(
            "Unrecognised portfolio file — only Wealthsimple activities exports are supported"
        )
    from portfolio_tracker.io_csv import read_wealthsimple_csv  # deferred: heavy import

    df, warnings = read_wealthsimple_csv(io.StringIO(text))
    dates = df["date"]
    return {
        "format": fmt,
        "rows": len(df),
        "first_date": dates.min().date().isoformat() if len(df) else None,
        "last_date": dates.max().date().isoformat() if len(df) else None,
        "by_type": {str(k): int(v) for k, v in df["type"].value_counts().sort_index().items()},
        "skipped": len(warnings),
        "warnings": warnings[:20],
    }


def save_portfolio_import(text: str, data_dir: str | Path, today: date | None = None) -> dict[str, Any]:
    """Validate the upload and save it where the pipeline auto-detects it.

    portfolio_tracker.cli._load_transactions reads the newest
    activities-export-*.csv by filename next to the workbook, so naming the
    file by today's date makes this upload the one the next Refresh uses.
    A same-day upload replaces the earlier one.
    """
    summary = preview_portfolio_import(text)
    data_dir = Path(data_dir)
    target = data_dir / f"activities-export-{(today or date.today()).isoformat()}.csv"
    tmp = target.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, target)
    return {**summary, "saved_as": target.name}


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
