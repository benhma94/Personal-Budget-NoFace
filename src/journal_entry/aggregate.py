"""Turn categorized transactions and confirmed transfers into aggregated
Journal posting lines, grouped by (Debit, Credit) account pair and dated
with a single user-chosen posting date — matching how entries have always
been made by hand (one line per category/account per session).
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
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped: list[dict[str, Any]] = []

    for row in categorized:
        category = (row.get("category") or "").strip()
        amount = row["amount"]
        if not category or amount == 0:
            skipped.append(row)
            continue
        account = row["account"]
        if amount < 0:
            key = (category, account)
        else:
            key = (account, category)
        groups[key].append(row)

    lines = [
        _line_from_group(debit, credit, rows, posting_date)
        for (debit, credit), rows in groups.items()
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


def _line_from_group(
    debit: str, credit: str, rows: list[dict[str, Any]], posting_date: date
) -> JournalLine:
    components = [abs(row["amount"]) for row in rows]
    return JournalLine(
        posting_date=posting_date,
        debit=debit,
        credit=credit,
        components=components,
        note=_representative_note(rows),
    )


def _representative_note(rows: list[dict[str, Any]]) -> str:
    """The most common non-blank note among the group, or the category name."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for row in rows:
        note = (row.get("note") or "").strip()
        if not note:
            continue
        if note not in counts:
            order.append(note)
        counts[note] = counts.get(note, 0) + 1
    if not counts:
        return rows[0].get("category") or ""
    return max(order, key=lambda note: counts[note])
