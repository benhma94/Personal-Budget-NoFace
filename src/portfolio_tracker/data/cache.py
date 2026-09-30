"""SQLite-backed, provider-isolated market-data cache."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd


_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_v2 (
    provider TEXT NOT NULL,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL NOT NULL,
    currency TEXT NOT NULL,
    PRIMARY KEY (provider, ticker, date)
);
CREATE TABLE IF NOT EXISTS fx_v2 (
    provider TEXT NOT NULL,
    pair TEXT NOT NULL,
    date TEXT NOT NULL,
    rate REAL NOT NULL,
    PRIMARY KEY (provider, pair, date)
);
CREATE TABLE IF NOT EXISTS classifications_v2 (
    provider TEXT NOT NULL,
    ticker TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (provider, ticker)
);
CREATE TABLE IF NOT EXISTS fund_snapshots (
    provider TEXT NOT NULL,
    ticker TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (provider, ticker)
);
"""


class Cache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ----- prices -----

    def put_prices(self, ticker: str, series: pd.Series, currency: str, provider: str = "default") -> None:
        rows = [
            (provider, ticker, ts.date().isoformat(), float(v), currency)
            for ts, v in series.items()
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO prices_v2 (provider, ticker, date, close, currency) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def get_prices(
        self, ticker: str, start: date, end: date, provider: str = "default", max_edge_gap_days: int = 0
    ) -> pd.Series | None:
        """Return cached prices if FULL [start, end] range is covered, else None."""
        cur = self._conn.execute(
            "SELECT date, close FROM prices_v2 WHERE provider = ? AND ticker = ? AND date BETWEEN ? AND ? ORDER BY date",
            (provider, ticker, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        first = date.fromisoformat(rows[0][0])
        last = date.fromisoformat(rows[-1][0])
        if (first - start).days > max_edge_gap_days or (end - last).days > max_edge_gap_days:
            return None
        idx = pd.to_datetime([r[0] for r in rows])
        return pd.Series([r[1] for r in rows], index=idx)

    # ----- fx -----

    def put_fx(self, pair: str, series: pd.Series, provider: str = "default") -> None:
        rows = [(provider, pair, ts.date().isoformat(), float(v)) for ts, v in series.items()]
        self._conn.executemany(
            "INSERT OR REPLACE INTO fx_v2 (provider, pair, date, rate) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def get_fx(
        self, pair: str, start: date, end: date, provider: str = "default", max_edge_gap_days: int = 0
    ) -> pd.Series | None:
        cur = self._conn.execute(
            "SELECT date, rate FROM fx_v2 WHERE provider = ? AND pair = ? AND date BETWEEN ? AND ? ORDER BY date",
            (provider, pair, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        first = date.fromisoformat(rows[0][0])
        last = date.fromisoformat(rows[-1][0])
        if (first - start).days > max_edge_gap_days or (end - last).days > max_edge_gap_days:
            return None
        idx = pd.to_datetime([r[0] for r in rows])
        return pd.Series([r[1] for r in rows], index=idx)

    # ----- classifications -----

    def put_classification(
        self,
        ticker: str,
        payload: dict,
        fetched_at: datetime | None = None,
        provider: str = "default",
    ) -> None:
        ts = (fetched_at or datetime.now()).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO classifications_v2 (provider, ticker, fetched_at, payload_json) VALUES (?, ?, ?, ?)",
            (provider, ticker, ts, json.dumps(payload)),
        )
        self._conn.commit()

    def get_classification(self, ticker: str, max_age_days: int, provider: str = "default") -> dict | None:
        cur = self._conn.execute(
            "SELECT fetched_at, payload_json FROM classifications_v2 WHERE provider = ? AND ticker = ?",
            (provider, ticker),
        )
        row = cur.fetchone()
        if not row:
            return None
        fetched_at = datetime.fromisoformat(row[0])
        if datetime.now() - fetched_at > timedelta(days=max_age_days):
            return None
        return json.loads(row[1])

    # ----- fund snapshots -----

    def put_fund_snapshot(self, ticker: str, payload: dict, fetched_at: datetime, provider: str = "lseg") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO fund_snapshots (provider, ticker, fetched_at, payload_json) VALUES (?, ?, ?, ?)",
            (provider, ticker, fetched_at.isoformat(), json.dumps(payload)),
        )
        self._conn.commit()

    def get_fund_snapshot(
        self, ticker: str, max_age_days: int | None = None, provider: str = "lseg"
    ) -> tuple[datetime, dict] | None:
        row = self._conn.execute(
            "SELECT fetched_at, payload_json FROM fund_snapshots WHERE provider = ? AND ticker = ?",
            (provider, ticker),
        ).fetchone()
        if not row:
            return None
        fetched_at = datetime.fromisoformat(row[0])
        if max_age_days is not None and datetime.now() - fetched_at > timedelta(days=max_age_days):
            return None
        return fetched_at, json.loads(row[1])
