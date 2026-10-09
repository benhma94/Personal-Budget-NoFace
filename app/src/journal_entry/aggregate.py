"""Turn categorized transactions and confirmed transfers into aggregated
Journal posting lines, grouped by (Debit, Credit, note) and dated with a
single user-chosen posting date — matching how entries have always been made
by hand (one line per category/account/purpose per session). The note is part
of the key so differently-described transactions (e.g. "Eric Ikea" vs
"Groceries") never get summed under one line carrying only one of the notes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from .xlsx_append import JournalLine


def build_posting_lines(
    categorized: list[dict[str, Any]],
    confirmed_transfers: list[dict[str, Any]],
    posting_date: date,
) -> tuple[list[JournalLine], list[dict[str, Any]]]:
    """Build posting lines plus any rows that couldn't be aggregated.

    `confirmed_transfers` is a list of dicts shaped
    ``{"outgoing": row, "incoming": row, "note": str}``.
    Returns `(lines, skipped)`; `skipped` holds categorized rows with no
    category or a zero amount, surfaced so the UI can flag them instead of
    silently dropping money from the batch.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped: list[dict[str, Any]] = []

    for row in categorized:
        category = (row.get("category") or "").strip()
        amount = row["amount"]
        if not category or amount == 0:
            skipped.append(row)
            continue
        account = row["account"]
        note = (row.get("note") or "").strip()
        if amount < 0:
            key = (category, account, note)
        else:
            key = (account, category, note)
        groups[key].append(row)

    lines = [
        JournalLine(
            posting_date=posting_date,
            debit=debit,
            credit=credit,
            components=[abs(row["amount"]) for row in rows],
            note=note or rows[0]["category"].strip(),
        )
        for (debit, credit, note), rows in groups.items()
    ]
    for transfer in confirmed_transfers:
        outgoing, incoming = transfer["outgoing"], transfer["incoming"]
        lines.append(
            JournalLine(
                posting_date=posting_date,
                debit=incoming["account"],
                credit=outgoing["account"],
                components=[abs(outgoing["amount"])],
                note=transfer.get("note") or "Transfer",
            )
        )
    return lines, skipped
