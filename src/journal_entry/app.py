"""In-process application façade.

Wires store + csv_import + rules + transfers + aggregate + xlsx_append +
accounts together behind one object. `server.py` is a thin HTTP adapter over
this class; tests exercise `JournalApp` directly without needing a running
server.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import csv_import
from . import rules as rules_mod
from . import transfers as transfers_mod
from . import xlsx_read
from .accounts import account_kind, read_ledger_accounts
from .aggregate import build_posting_lines
from .month_close import build_preview as build_month_close_preview
from .month_close import stage_adjustments
from .store import Store
from .xlsx_append import JournalLine, append_journal_rows
from .xlsx_append import update_journal_row as _write_updated_row


class RowConflictError(RuntimeError):
    """Raised when a posted row's current values don't match what the
    caller last saw, so the edit is refused rather than silently clobbering
    a concurrent change (e.g. a hand-edit made directly in Excel)."""


class JournalApp:
    def __init__(self, workbook_path: str | Path, store: Store):
        self.workbook_path = Path(workbook_path)
        self.store = store

    @classmethod
    def create(cls, workbook_path: str | Path, db_path: str | Path) -> "JournalApp":
        store = Store(db_path)
        rules_mod.seed_rules(store)
        return cls(workbook_path, store)

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------
    # accounts
    # ------------------------------------------------------------------

    def accounts(self) -> dict[str, Any]:
        names = read_ledger_accounts(self.workbook_path)
        return {"accounts": names, "kinds": {name: account_kind(name) for name in names}}

    # ------------------------------------------------------------------
    # import
    # ------------------------------------------------------------------

    def sniff(self, text: str) -> dict[str, Any]:
        """Identify a CSV's layout: headers, signature, remembered mapping
        (if any), and a best-effort date column/format guess for brand-new
        layouts (skipped once a mapping is already known)."""
        headers = csv_import.sniff_headers(text)
        signature = csv_import.header_signature(headers) if headers else None
        source = self.store.get_source(signature) if signature else None
        guessed_date_col = None
        guessed_date_format = None
        if headers and not source:
            guessed_date_col = csv_import.guess_date_column(headers)
            if guessed_date_col:
                guessed_date_format = csv_import.guess_date_format(text, guessed_date_col)
        return {
            "headers": headers,
            "header_signature": signature,
            "known_source": source,
            "guessed_date_col": guessed_date_col,
            "guessed_date_format": guessed_date_format,
        }

    def preview_dates(self, text: str, date_col: str, date_format: str, sample_size: int = 5) -> dict[str, Any]:
        """Show how `date_format` parses a few real values from `date_col`,
        so the mapping form can give live feedback instead of failing silently
        at import time."""
        samples = csv_import.sample_column_values(text, date_col, sample_size)
        return {"samples": csv_import.preview_date_parses(samples, date_format)}

    def save_mapping(
        self,
        header_signature: str,
        label: str,
        default_account: str,
        mapping: dict[str, Any],
        date_format: str,
    ) -> None:
        # Validate the mapping shape early so a bad UI payload fails loudly, not on next import.
        csv_import.ColumnMapping(**mapping)
        self.store.save_source(header_signature, label, default_account, mapping, date_format)

    def import_csv(
        self,
        text: str,
        *,
        header_signature: str | None = None,
        account: str | None = None,
        mapping: dict[str, Any] | None = None,
        date_format: str | None = None,
    ) -> dict[str, Any]:
        """Parse and store new rows from `text`; already-seen rows are skipped."""
        if header_signature:
            source = self.store.get_source(header_signature)
            if source:
                account = account or source["default_account"]
                mapping = mapping or source["mapping"]
                date_format = date_format or source["date_format"]
        if not (account and mapping and date_format):
            raise ValueError("no known column mapping for this file; provide one")

        col_mapping = csv_import.ColumnMapping(**mapping)
        parsed = csv_import.parse_csv(text, account, col_mapping, date_format, known_fingerprints=set())
        known = self.store.known_fingerprints(row.fingerprint for row in parsed)

        now = datetime.now().isoformat(timespec="seconds")
        to_insert = []
        rows_out = []
        for row in parsed:
            duplicate = row.fingerprint in known
            rows_out.append({**asdict(row), "duplicate": duplicate})
            if not duplicate:
                to_insert.append(
                    dict(
                        fingerprint=row.fingerprint, account=row.account, txn_date=row.txn_date,
                        description=row.description, amount=row.amount, category=None, note=None,
                        status="new", transfer_peer=None, batch_id=None, imported_at=now,
                    )
                )
        inserted = self.store.insert_transactions(to_insert)
        self._apply_suggestions([row["fingerprint"] for row in to_insert])
        return {
            "read": len(parsed),
            "new": inserted,
            "duplicate": len(parsed) - inserted,
            "rows": rows_out,
        }

    def _apply_suggestions(self, fingerprints: list[str]) -> None:
        """Pre-fill category/note for newly imported rows from learned rules.

        Status stays 'new' — a suggestion is not a confirmation. The UI shows
        it with a "suggested" badge; confirming it calls `categorize`, which
        both commits it and reinforces the rule.
        """
        if not fingerprints:
            return
        all_rules = self.store.list_rules()
        if not all_rules:
            return
        for fingerprint in fingerprints:
            tx = self.store.get_transaction(fingerprint)
            if tx is None:
                continue
            suggestion = rules_mod.suggest(tx["description"], all_rules)
            if suggestion:
                self.store.update_transaction(
                    fingerprint, category=suggestion.category, note=suggestion.note
                )

    def undo_import(self, fingerprints: list[str]) -> dict[str, Any]:
        """Delete rows from a just-completed import, skipping any that have
        already been reviewed (status changed away from 'new').

        Repeated fingerprints are collapsed first: two identical rows in one
        CSV share a fingerprint, and counting them twice would under-report
        `deleted` and invent a phantom `kept`. `dict.fromkeys` preserves order.
        """
        unique = list(dict.fromkeys(fingerprints))
        deleted = self.store.delete_new_transactions(unique)
        return {"deleted": deleted, "kept": len(unique) - deleted}

    # ------------------------------------------------------------------
    # review
    # ------------------------------------------------------------------

    def list_transactions(
        self, *, status: str | list[str] | None = None, account: str | None = None
    ) -> list[dict[str, Any]]:
        return self.store.list_transactions(status=status, account=account)

    def categorize(
        self, fingerprints: list[str], category: str, note: str, *, learn: bool = True
    ) -> None:
        if not fingerprints:
            return
        self.store.bulk_update(fingerprints, category=category, note=note, status="categorized")
        if learn:
            seen_patterns: set[str] = set()
            for fingerprint in fingerprints:
                tx = self.store.get_transaction(fingerprint)
                if tx is None:
                    continue
                pattern = rules_mod.merchant_pattern(tx["description"])
                if pattern in seen_patterns:
                    continue
                seen_patterns.add(pattern)
                rules_mod.learn(self.store, pattern, category, note)

    def set_status(self, fingerprints: list[str], status: str) -> None:
        self.store.bulk_update(fingerprints, status=status)

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------

    def rules(self) -> list[dict[str, Any]]:
        return self.store.list_rules()

    def add_rule(self, pattern: str, category: str, note: str, match_type: str = "contains") -> None:
        rules_mod.learn(self.store, pattern, category, note, match_type=match_type)

    def delete_rule(self, rule_id: int) -> None:
        self.store.delete_rule(rule_id)

    # ------------------------------------------------------------------
    # transfers
    # ------------------------------------------------------------------

    def suggested_transfers(self) -> list[dict[str, Any]]:
        txs = self.store.list_transactions(status=["new", "categorized"])
        matches = transfers_mod.find_matches(txs)
        return [
            {"outgoing": match.outgoing, "incoming": match.incoming, "amount": match.amount}
            for match in matches
        ]

    def confirm_transfer(self, outgoing_fingerprint: str, incoming_fingerprint: str, note: str) -> None:
        self.store.update_transaction(
            outgoing_fingerprint, status="transfer", transfer_peer=incoming_fingerprint, note=note
        )
        self.store.update_transaction(
            incoming_fingerprint, status="transfer", transfer_peer=outgoing_fingerprint, note=note
        )

    def unlink_transfer(self, fingerprint: str) -> None:
        tx = self.store.get_transaction(fingerprint)
        if not tx or tx["status"] != "transfer":
            return
        peer_fingerprint = tx["transfer_peer"]
        self.store.update_transaction(fingerprint, status="new", transfer_peer=None)
        if peer_fingerprint:
            self.store.update_transaction(peer_fingerprint, status="new", transfer_peer=None)

    def _confirmed_transfers(self) -> list[dict[str, Any]]:
        rows = self.store.list_transactions(status="transfer")
        by_fingerprint = {row["fingerprint"]: row for row in rows}
        seen: set[str] = set()
        pairs = []
        for row in rows:
            if row["fingerprint"] in seen:
                continue
            peer = by_fingerprint.get(row["transfer_peer"])
            if not peer:
                continue
            seen.add(row["fingerprint"])
            seen.add(peer["fingerprint"])
            outgoing, incoming = (row, peer) if row["amount"] < 0 else (peer, row)
            pairs.append({"outgoing": outgoing, "incoming": incoming, "note": row.get("note") or "Transfer"})
        return pairs

    # ------------------------------------------------------------------
    # month close
    # ------------------------------------------------------------------

    def month_close_preview(
        self,
        month: str,
        portfolio_snapshot: dict[str, Any] | None,
        selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_month_close_preview(
            self.workbook_path,
            self.store,
            self._confirmed_transfers(),
            month,
            portfolio_snapshot,
            selection,
        )

    def stage_month_close(
        self,
        month: str,
        portfolio_snapshot: dict[str, Any] | None,
        selection: dict[str, Any],
    ) -> dict[str, Any]:
        return stage_adjustments(
            self.workbook_path,
            self.store,
            self._confirmed_transfers(),
            month,
            portfolio_snapshot,
            selection,
        )

    # ------------------------------------------------------------------
    # post
    # ------------------------------------------------------------------

    def _validate_month_close_posting_date(self, posting_date: date) -> None:
        close_rows = self.store.list_month_close_transactions(
            statuses=["categorized"]
        )
        mismatched = [
            row for row in close_rows if row.get("txn_date") != posting_date.isoformat()
        ]
        if mismatched:
            required = ", ".join(sorted({row["txn_date"] for row in mismatched}))
            raise ValueError(
                f"staged month-close adjustments must be posted on {required}"
            )

    def _build_lines(self, posting_date: date) -> tuple[list, list[dict[str, Any]], list[dict[str, Any]]]:
        self._validate_month_close_posting_date(posting_date)
        categorized = self.store.list_transactions(status="categorized")
        confirmed_transfers = self._confirmed_transfers()
        lines, skipped = build_posting_lines(categorized, confirmed_transfers, posting_date)
        return lines, skipped, confirmed_transfers

    def post_preview(self, posting_date: date) -> dict[str, Any]:
        lines, skipped, _ = self._build_lines(posting_date)
        return {"lines": [_line_summary(line) for line in lines], "skipped": skipped}

    def post(self, posting_date: date) -> dict[str, Any]:
        lines, skipped, confirmed_transfers = self._build_lines(posting_date)
        if not lines:
            raise ValueError("nothing to post")

        result = append_journal_rows(self.workbook_path, lines)

        batch_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        skipped_fingerprints = {row["fingerprint"] for row in skipped}
        categorized_fingerprints = [
            row["fingerprint"]
            for row in self.store.list_transactions(status="categorized")
            if row["fingerprint"] not in skipped_fingerprints
        ]
        transfer_fingerprints = [
            row["fingerprint"]
            for pair in confirmed_transfers
            for row in (pair["outgoing"], pair["incoming"])
        ]
        self.store.bulk_update(
            categorized_fingerprints + transfer_fingerprints, status="posted", batch_id=batch_id
        )
        self.store.record_batch(
            batch_id, posting_date.isoformat(), now, result.first_row, result.last_row,
            str(result.backup_path), len(lines),
        )
        return {
            "batch_id": batch_id,
            "first_row": result.first_row,
            "last_row": result.last_row,
            "backup_path": str(result.backup_path),
            "line_count": len(lines),
            "skipped": skipped,
        }

    def batches(self) -> list[dict[str, Any]]:
        return self.store.list_batches()

    def batch_lines(self, batch_id: str) -> list[dict[str, Any]]:
        """Current Journal-sheet contents for one posted batch's row range."""
        batch = self.store.get_batch(batch_id)
        if batch is None:
            raise ValueError(f"unknown batch: {batch_id}")
        return xlsx_read.read_journal_rows(self.workbook_path, batch["first_row"], batch["last_row"])

    def update_journal_row(
        self, row_number: int, *, posting_date: date, debit: str, credit: str,
        amount: float, note: str, expected: dict[str, Any],
    ) -> dict[str, Any]:
        """Correct one already-posted Journal row in place. Refuses rows
        this app never posted (row_number outside every recorded batch's
        range) and refuses if the row's current values don't match
        `expected` -- e.g. it was hand-edited in Excel since the edit form
        was opened.
        """
        if amount == 0:
            raise ValueError("amount must not be zero")
        if not any(
            batch["first_row"] <= row_number <= batch["last_row"]
            for batch in self.store.list_batches()
        ):
            raise ValueError(f"row {row_number} was not posted by this app")

        current = xlsx_read.read_journal_rows(self.workbook_path, row_number, row_number)
        if not current:
            raise ValueError(f"row {row_number} not found in the Journal sheet")
        actual = current[0]
        amount_matches = (
            actual["amount"] is not None
            and expected.get("amount") is not None
            and abs(actual["amount"] - expected["amount"]) < 1e-9
        )
        fields_match = (
            amount_matches
            and actual["posting_date"] == expected.get("posting_date")
            and actual["debit"] == expected.get("debit")
            and actual["credit"] == expected.get("credit")
            and actual["note"] == expected.get("note")
        )
        if not fields_match:
            raise RowConflictError(
                "this row has changed since it was loaded — reopen it to see the current values"
            )

        line = JournalLine(posting_date, debit, credit, [amount], note)
        backup_path = _write_updated_row(self.workbook_path, row_number, line)
        return {"row_number": row_number, "backup_path": str(backup_path)}


def _line_summary(line: Any) -> dict[str, Any]:
    return {
        "posting_date": line.posting_date.isoformat(),
        "debit": line.debit,
        "credit": line.credit,
        "components": list(line.components),
        "amount": line.amount,
        "note": line.note,
    }
