"""Memoized access to the budget dashboard payload.

Rebuilding the payload means reparsing the whole Journal sheet with openpyxl,
which is noticeable on a large workbook. The hub re-renders the Budget view
far more often than the workbook actually changes (posting from the Journal
tab, or editing it in Excel), so this caches the payload against the
workbook's mtime and only rebuilds when it moves. `invalidate()` is called
right after a Journal post so the next Budget-tab activation picks it up
immediately rather than waiting on mtime resolution.
"""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

from budget_dashboard.workbook import (
    ASSET_ACCOUNTS,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    LIABILITY_ACCOUNTS,
    build_dashboard_data,
)


class BudgetPayloadCache:
    def __init__(self, workbook_path: str | Path):
        self._workbook_path = Path(workbook_path)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._payload: dict[str, Any] | None = None
        self._transactions: list[dict[str, Any]] | None = None

    def _refresh_if_needed(self) -> None:
        mtime = self._workbook_path.stat().st_mtime
        if self._payload is None or self._transactions is None or mtime != self._mtime:
            self._payload, self._transactions = build_dashboard_data(self._workbook_path)
            self._mtime = mtime

    def get(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_if_needed()
            assert self._payload is not None
            return self._payload

    def query_transactions(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        category: str | None = None,
        account: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a filtered, newest-first page of read-only Journal rows."""
        if start is not None:
            date.fromisoformat(start)
        if end is not None:
            date.fromisoformat(end)
        if start is not None and end is not None and start > end:
            raise ValueError("start cannot be after end")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        category = category.strip() if category else None
        account = account.strip() if account else None
        kind = kind.strip().casefold() if kind else None
        if kind not in (None, "income", "expense", "investment"):
            raise ValueError("kind must be income, expense, or investment")
        search = q.strip().casefold() if q else ""
        with self._lock:
            self._refresh_if_needed()
            assert self._transactions is not None
            matches = []
            for transaction in self._transactions:
                if start is not None and transaction["date"] < start:
                    continue
                if end is not None and transaction["date"] > end:
                    continue
                if category is not None and category not in (
                    transaction["debit"], transaction["credit"]
                ):
                    continue
                if category is None and kind is not None and not _matches_kind(
                    transaction, kind
                ):
                    continue
                if account is not None and account not in (
                    transaction["debit"], transaction["credit"]
                ):
                    continue
                if search and search not in " ".join((
                    transaction["date"], transaction["debit"],
                    transaction["credit"], transaction["note"],
                )).casefold():
                    continue
                row = {
                    key: transaction[key]
                    for key in ("row_number", "date", "debit", "credit", "amount", "note")
                }
                row["impact_cad"] = _impact(
                    transaction, category=category, account=account, kind=kind
                )
                matches.append(row)

            matches.sort(key=lambda row: (row["date"], row["row_number"]), reverse=True)
            return {
                "items": matches[offset:offset + limit],
                "total": len(matches),
                "limit": limit,
                "offset": offset,
            }

    def invalidate(self) -> None:
        with self._lock:
            self._mtime = None


def _impact(
    transaction: dict[str, Any], *, category: str | None,
    account: str | None, kind: str | None
) -> float:
    """Signed contribution to the selected dashboard category or account."""
    amount = float(transaction["amount"])
    debit, credit = transaction["debit"], transaction["credit"]
    if category is not None:
        signed = (amount if credit == category else 0.0) - (
            amount if debit == category else 0.0
        )
        return -signed if category in EXPENSE_CATEGORIES else signed
    if kind is not None:
        if kind == "income":
            categories = INCOME_CATEGORIES
            multiplier = 1.0
        elif kind == "expense":
            categories = EXPENSE_CATEGORIES
            multiplier = -1.0
        else:
            categories = ("Investment Income",)
            multiplier = 1.0
        signed = sum(
            (amount if credit == name else 0.0) - (amount if debit == name else 0.0)
            for name in categories
        )
        return multiplier * signed
    if account is not None:
        debit_effect = amount if debit == account else 0.0
        credit_effect = amount if credit == account else 0.0
        if account in LIABILITY_ACCOUNTS:
            return credit_effect - debit_effect
        if account in ASSET_ACCOUNTS:
            return debit_effect - credit_effect
        return debit_effect - credit_effect
    return amount


def _matches_kind(transaction: dict[str, Any], kind: str) -> bool:
    if kind == "income":
        categories = INCOME_CATEGORIES
    elif kind == "expense":
        categories = EXPENSE_CATEGORIES
    else:
        categories = ("Investment Income",)
    return transaction["debit"] in categories or transaction["credit"] in categories
