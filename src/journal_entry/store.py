"""SQLite storage for the journal-entry app.

Holds imported bank transactions, remembered CSV column mappings, learned
categorization rules, and a record of posted batches. No business logic
lives here — callers decide what to import, how to categorize, and when to
post; this module only persists what they decide.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    header_signature TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    default_account TEXT NOT NULL,
    mapping TEXT NOT NULL,
    date_format TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    fingerprint TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    txn_date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    transfer_peer TEXT,
    batch_id TEXT,
    imported_at TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'csv',
    close_month TEXT,
    close_kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_transactions_account_date ON transactions(account, txn_date);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'contains',
    category TEXT NOT NULL,
    note TEXT,
    hits INTEGER NOT NULL DEFAULT 0,
    last_used TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_pattern ON rules(pattern, match_type);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    posting_date TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    first_row INTEGER NOT NULL,
    last_row INTEGER NOT NULL,
    backup_path TEXT NOT NULL,
    line_count INTEGER NOT NULL
);
"""

# Valid transaction lifecycle states.
STATUSES = ("new", "categorized", "ignored", "transfer", "posted")


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Store:
    """Owns the SQLite connection. One instance per process is expected."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._migrate_transaction_metadata()
        self._conn.commit()

    def _migrate_transaction_metadata(self) -> None:
        """Add month-close metadata to databases created by older releases.

        ``CREATE TABLE IF NOT EXISTS`` does not alter an existing SQLite
        table, so each column is added explicitly and idempotently.  Existing
        imported rows inherit ``origin='csv'`` and otherwise remain untouched.
        """
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(transactions)").fetchall()
        }
        if "origin" not in columns:
            self._conn.execute(
                "ALTER TABLE transactions ADD COLUMN origin TEXT NOT NULL DEFAULT 'csv'"
            )
        if "close_month" not in columns:
            self._conn.execute("ALTER TABLE transactions ADD COLUMN close_month TEXT")
        if "close_kind" not in columns:
            self._conn.execute("ALTER TABLE transactions ADD COLUMN close_kind TEXT")
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_transactions_month_close
               ON transactions(origin, close_month, close_kind, status)"""
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # sources: remembered CSV column mappings, keyed by header signature
    # ------------------------------------------------------------------

    def get_source(self, header_signature: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE header_signature = ?", (header_signature,)
        ).fetchone()
        result = _row(row)
        if result is not None:
            result["mapping"] = json.loads(result["mapping"])
        return result

    def save_source(
        self,
        header_signature: str,
        label: str,
        default_account: str,
        mapping: dict[str, Any],
        date_format: str,
    ) -> None:
        self._conn.execute(
            """INSERT INTO sources (header_signature, label, default_account, mapping, date_format)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(header_signature) DO UPDATE SET
                 label = excluded.label,
                 default_account = excluded.default_account,
                 mapping = excluded.mapping,
                 date_format = excluded.date_format""",
            (header_signature, label, default_account, json.dumps(mapping), date_format),
        )
        self._conn.commit()

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM sources ORDER BY label").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["mapping"] = json.loads(d["mapping"])
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # transactions
    # ------------------------------------------------------------------

    def known_fingerprints(self, fingerprints: Iterable[str]) -> set[str]:
        fingerprints = list(fingerprints)
        if not fingerprints:
            return set()
        placeholders = ",".join("?" * len(fingerprints))
        rows = self._conn.execute(
            f"SELECT fingerprint FROM transactions WHERE fingerprint IN ({placeholders})",
            fingerprints,
        ).fetchall()
        return {r["fingerprint"] for r in rows}

    def insert_transactions(self, rows: list[dict[str, Any]]) -> int:
        """Insert new rows, silently skipping fingerprints already present. Returns count inserted."""
        if not rows:
            return 0
        before = self._conn.total_changes
        self._conn.executemany(
            """INSERT OR IGNORE INTO transactions
               (fingerprint, account, txn_date, description, amount, category, note,
                status, transfer_peer, batch_id, imported_at)
               VALUES (:fingerprint, :account, :txn_date, :description, :amount, :category,
                       :note, :status, :transfer_peer, :batch_id, :imported_at)""",
            rows,
        )
        self._conn.commit()
        return self._conn.total_changes - before

    def list_transactions(
        self,
        *,
        status: str | Iterable[str] | None = None,
        account: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM transactions WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            placeholders = ",".join("?" * len(statuses))
            query += f" AND status IN ({placeholders})"
            params.extend(statuses)
        if account is not None:
            query += " AND account = ?"
            params.append(account)
        query += " ORDER BY txn_date, account, description"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_transaction(self, fingerprint: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM transactions WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return _row(row)

    def update_transaction(self, fingerprint: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{key} = ?" for key in fields)
        self._conn.execute(
            f"UPDATE transactions SET {cols} WHERE fingerprint = ?",
            (*fields.values(), fingerprint),
        )
        self._conn.commit()

    def bulk_update(self, fingerprints: Iterable[str], **fields: Any) -> None:
        fingerprints = list(fingerprints)
        if not fingerprints or not fields:
            return
        cols = ", ".join(f"{key} = ?" for key in fields)
        placeholders = ",".join("?" * len(fingerprints))
        self._conn.execute(
            f"UPDATE transactions SET {cols} WHERE fingerprint IN ({placeholders})",
            (*fields.values(), *fingerprints),
        )
        self._conn.commit()

    def delete_new_transactions(self, fingerprints: Iterable[str]) -> int:
        """Delete rows by fingerprint, but only while still status='new' —
        reviewed rows (categorized/ignored/transfer/posted) are left alone.
        Returns the number of rows actually deleted."""
        fingerprints = list(fingerprints)
        if not fingerprints:
            return 0
        placeholders = ",".join("?" * len(fingerprints))
        cursor = self._conn.execute(
            f"DELETE FROM transactions WHERE status = 'new' AND fingerprint IN ({placeholders})",
            fingerprints,
        )
        self._conn.commit()
        return cursor.rowcount

    def list_month_close_transactions(
        self,
        *,
        close_month: str | None = None,
        statuses: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return month-close rows, optionally narrowed by month and status."""
        query = "SELECT * FROM transactions WHERE origin = 'month_close'"
        params: list[Any] = []
        if close_month is not None:
            query += " AND close_month = ?"
            params.append(close_month)
        if statuses is not None:
            status_values = list(statuses)
            if not status_values:
                return []
            placeholders = ",".join("?" * len(status_values))
            query += f" AND status IN ({placeholders})"
            params.extend(status_values)
        query += " ORDER BY txn_date, close_kind, imported_at"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def replace_month_close_transactions(
        self, close_month: str, rows_by_kind: dict[str, dict[str, Any] | None]
    ) -> None:
        """Replace active close rows for a month while preserving posted history.

        A ``None`` value removes the active adjustment for that kind.  Every
        replacement happens in one transaction so the Post queue never sees a
        half-updated close.
        """
        try:
            for close_kind, row in rows_by_kind.items():
                self._conn.execute(
                    """DELETE FROM transactions
                       WHERE origin = 'month_close' AND close_month = ?
                         AND close_kind = ? AND status IN ('new', 'categorized')""",
                    (close_month, close_kind),
                )
                if row is not None:
                    self._conn.execute(
                        """INSERT INTO transactions
                           (fingerprint, account, txn_date, description, amount, category,
                            note, status, transfer_peer, batch_id, imported_at,
                            origin, close_month, close_kind)
                           VALUES (:fingerprint, :account, :txn_date, :description,
                                   :amount, :category, :note, :status,
                                   :transfer_peer, :batch_id, :imported_at,
                                   'month_close', :close_month, :close_kind)""",
                        row,
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # rules: merchant pattern -> category/note, learned as-you-go
    # ------------------------------------------------------------------

    def list_rules(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM rules ORDER BY hits DESC, pattern").fetchall()
        return [dict(r) for r in rows]

    def upsert_rule(self, pattern: str, match_type: str, category: str, note: str | None) -> None:
        self._conn.execute(
            """INSERT INTO rules (pattern, match_type, category, note, hits, last_used)
               VALUES (?, ?, ?, ?, 0, NULL)
               ON CONFLICT(pattern, match_type) DO UPDATE SET
                 category = excluded.category, note = excluded.note""",
            (pattern, match_type, category, note),
        )
        self._conn.commit()

    def touch_rule(self, rule_id: int, used_at: str) -> None:
        self._conn.execute(
            "UPDATE rules SET hits = hits + 1, last_used = ? WHERE id = ?", (used_at, rule_id)
        )
        self._conn.commit()

    def delete_rule(self, rule_id: int) -> None:
        self._conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # batches: record of what was posted to the workbook
    # ------------------------------------------------------------------

    def record_batch(
        self,
        batch_id: str,
        posting_date: str,
        posted_at: str,
        first_row: int,
        last_row: int,
        backup_path: str,
        line_count: int,
    ) -> None:
        self._conn.execute(
            """INSERT INTO batches
               (batch_id, posting_date, posted_at, first_row, last_row, backup_path, line_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, posting_date, posted_at, first_row, last_row, backup_path, line_count),
        )
        self._conn.commit()

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return _row(row)

    def list_batches(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM batches ORDER BY posted_at DESC").fetchall()
        return [dict(r) for r in rows]
