"""Detect transfers between the user's own accounts: a matching amount with
opposite sign across two different accounts within a short date window
(e.g. a credit-card payment leaving chequing and arriving on the card).

Matches are suggestions only — the UI confirms or unlinks each one before
it affects posting. Confirmed pairs post as a single Journal line instead
of two separate categorized rows, avoiding double-counting the same money
movement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

_DEFAULT_WINDOW_DAYS = 3


@dataclass(frozen=True)
class TransferMatch:
    outgoing: dict[str, Any]  # negative amount: money leaving outgoing['account']
    incoming: dict[str, Any]  # positive amount: money arriving in incoming['account']

    @property
    def amount(self) -> float:
        return abs(self.outgoing["amount"])


def _parse(iso_date: str) -> date:
    year, month, day = (int(part) for part in iso_date.split("-"))
    return date(year, month, day)


def find_matches(
    transactions: list[dict[str, Any]], *, window_days: int = _DEFAULT_WINDOW_DAYS
) -> list[TransferMatch]:
    """Pair opposite-sign, equal-magnitude rows across different accounts.

    Only rows still eligible for review (status 'new' or 'categorized') are
    considered. Each row is used in at most one match; ties are broken by
    the smallest date gap so the most plausible pairing wins.
    """
    eligible = [t for t in transactions if t.get("status") in ("new", "categorized")]
    outgoing = [t for t in eligible if t["amount"] < 0]
    incoming = [t for t in eligible if t["amount"] > 0]

    candidates: list[tuple[int, str, str, dict[str, Any], dict[str, Any]]] = []
    for out in outgoing:
        for inc in incoming:
            if out["account"] == inc["account"]:
                continue
            if round(-out["amount"], 2) != round(inc["amount"], 2):
                continue
            gap = abs((_parse(inc["txn_date"]) - _parse(out["txn_date"])).days)
            if gap > window_days:
                continue
            candidates.append((gap, out["fingerprint"], inc["fingerprint"], out, inc))

    candidates.sort(key=lambda c: c[0])
    used_out: set[str] = set()
    used_in: set[str] = set()
    matches: list[TransferMatch] = []
    for _gap, out_fp, in_fp, out, inc in candidates:
        if out_fp in used_out or in_fp in used_in:
            continue
        used_out.add(out_fp)
        used_in.add(in_fp)
        matches.append(TransferMatch(outgoing=out, incoming=inc))
    return matches
