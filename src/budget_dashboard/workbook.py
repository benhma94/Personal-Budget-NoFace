"""Read the personal-budget workbook without modifying it."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, NamedTuple
import warnings

from openpyxl import load_workbook

from .profile import PROFILE

# OpenPyXL cannot preserve an extended validation object, but this reader never
# saves the workbook, so the warning does not apply to this read-only workflow.
warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported.*",
    module="openpyxl.worksheet._reader",
)


INCOME_CATEGORIES = PROFILE["income_categories"]
EXPENSE_CATEGORIES = PROFILE["expense_categories"]
CATEGORY_ALIASES = PROFILE["category_aliases"]
ASSET_ACCOUNTS = PROFILE["asset_accounts"]
LIABILITY_ACCOUNTS = PROFILE["liability_accounts"]
_CANONICAL_NAMES = {
    value.casefold(): value
    for value in (
        *INCOME_CATEGORIES, *EXPENSE_CATEGORIES, "Investment Income",
        *ASSET_ACCOUNTS, *LIABILITY_ACCOUNTS,
    )
}
_CANONICAL_NAMES.update({key.casefold(): value for key, value in CATEGORY_ALIASES.items()})


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _month_key(value: date | datetime) -> str:
    return value.strftime("%Y-%m")


def _canonical(value: str) -> str:
    """Match account/category names the same case-insensitive way Excel does."""
    return _CANONICAL_NAMES.get(value.casefold(), value)


def _month_range(first: str, last: str) -> list[str]:
    year, month = map(int, first.split("-"))
    end_year, end_month = map(int, last.split("-"))
    result: list[str] = []
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


class JournalData(NamedTuple):
    """Result of `_read_journal`, named so callers can't silently swap two
    same-typed fields (several are `dict[tuple[str, str], float]`) by
    unpacking them in the wrong positional order."""
    transactions: list[dict[str, Any]]
    actuals: dict[tuple[str, str], float]
    account_debits: dict[tuple[str, str], float]
    account_credits: dict[tuple[str, str], float]
    savings_by_month: dict[str, float]
    espp_by_month: dict[str, float]


def _read_journal(workbook: Any) -> JournalData:
    """Parse the Journal sheet into transactions plus signed per-(month,
    category) aggregates. Shared by build_dashboard_data and the budget
    planner's reader so "actual" can never drift between the two features."""
    journal = workbook["Journal"]
    transactions: list[dict[str, Any]] = []
    actuals: dict[tuple[str, str], float] = defaultdict(float)
    account_debits: dict[tuple[str, str], float] = defaultdict(float)
    account_credits: dict[tuple[str, str], float] = defaultdict(float)
    savings_by_month: dict[str, float] = defaultdict(float)
    espp_by_month: dict[str, float] = defaultdict(float)

    for row_number, values in enumerate(
        journal.iter_rows(min_row=3, values_only=True), start=3
    ):
        raw_date, debit, credit, raw_amount, note, *_ = values
        if not isinstance(raw_date, (date, datetime)) or not isinstance(raw_amount, (int, float)):
            continue
        amount = float(raw_amount)
        key = _month_key(raw_date)
        debit = _canonical(str(debit).strip()) if debit is not None else ""
        credit = _canonical(str(credit).strip()) if credit is not None else ""
        note = str(note).strip() if note is not None else ""
        actuals[(key, debit)] -= amount
        actuals[(key, credit)] += amount
        account_debits[(key, debit)] += amount
        account_credits[(key, credit)] += amount
        if note.casefold() == "savings":
            savings_by_month[key] += amount
        elif note.casefold() == "savings usage":
            savings_by_month[key] -= amount
        if debit.casefold().endswith("espp") and note.casefold() == debit.casefold():
            espp_by_month[key] += amount
        transactions.append({
            "row_number": row_number,
            "date": raw_date.strftime("%Y-%m-%d"), "period": key,
            "debit": debit, "credit": credit, "amount": amount, "note": note,
        })

    return JournalData(
        transactions, actuals, account_debits, account_credits, savings_by_month, espp_by_month
    )


def _read_budget_grid(workbook: Any) -> dict[str, dict[str, float | None]]:
    """Parse the Budget sheet's month columns (rows 16-47, keyed by the
    month-end dates in row 13) into {month: {category: raw signed value}}.
    A blank cell stays None here (unlike build_dashboard_data's 0.0-filled
    view) so the budget planner can tell "no entry yet" from "entered as
    zero"."""
    budget_sheet = workbook["Budget"]
    # Random ``cell()`` access on a read-only worksheet reparses its XML.  Read
    # this wide sheet once so a real workbook remains quick to process.
    budget_values = list(budget_sheet.iter_rows(min_row=13, max_row=48, values_only=True))
    budget_rows = {
        row[0].strip(): row for row in budget_values[3:]
        if row and isinstance(row[0], str)
    }
    budget_by_month: dict[str, dict[str, float | None]] = {}
    for column, month_end in enumerate(budget_values[0]):
        if not isinstance(month_end, (date, datetime)):
            continue
        key = _month_key(month_end)
        budget_by_month[key] = {
            category: _raw_number(row[column] if column < len(row) else None)
            for category, row in budget_rows.items()
        }
    return budget_by_month


def _raw_number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def build_dashboard_data(
    workbook_path: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build aggregate and transaction models without modifying the workbook."""
    path = Path(workbook_path)
    if not path.is_file():
        raise FileNotFoundError(f"Budget workbook not found: {path}")

    workbook = load_workbook(path, read_only=True, data_only=True)
    missing = {"Journal", "Budget", "Ledger"}.difference(workbook.sheetnames)
    if missing:
        workbook.close()
        raise ValueError(f"Workbook is missing required sheet(s): {', '.join(sorted(missing))}")

    journal_data = _read_journal(workbook)
    transactions = journal_data.transactions
    actuals = journal_data.actuals
    account_debits = journal_data.account_debits
    account_credits = journal_data.account_credits
    savings_by_month = journal_data.savings_by_month
    espp_by_month = journal_data.espp_by_month

    if not transactions:
        workbook.close()
        raise ValueError("The Journal sheet contains no dated transactions")

    raw_budget_by_month = _read_budget_grid(workbook)
    budget_by_month: dict[str, dict[str, float]] = {
        period: {category: (value if value is not None else 0.0) for category, value in row.items()}
        for period, row in raw_budget_by_month.items()
    }

    actual_periods = [tx["period"] for tx in transactions]
    first_period = min(actual_periods)
    last_period = max(max(actual_periods), max(budget_by_month, default=max(actual_periods)))
    periods = _month_range(first_period, last_period)
    latest_period = max(actual_periods)
    categories = [*INCOME_CATEGORIES, *EXPENSE_CATEGORIES]

    def values_for(period_keys: list[str]) -> dict[str, Any]:
        rows = []
        for category in categories:
            actual_signed = sum(actuals[(key, category)] for key in period_keys)
            budget_signed = sum(
                budget_by_month.get(key, {}).get(category, 0.0) for key in period_keys
            )
            kind = "income" if category in INCOME_CATEGORIES else "expense"
            actual = actual_signed if kind == "income" else -actual_signed
            budget = budget_signed if kind == "income" else -budget_signed
            variance = actual - budget if kind == "income" else budget - actual
            rows.append({
                "category": category, "kind": kind, "actual": actual,
                "budget": budget, "variance": variance,
            })
        investment_income = sum(
            actuals[(key, "Investment Income")] for key in period_keys
        )
        rows.append({
            "category": "Investment Income", "kind": "investment",
            "actual": investment_income, "budget": None, "variance": None,
        })
        income = sum(row["actual"] for row in rows if row["kind"] == "income")
        income_budget = sum(row["budget"] for row in rows if row["kind"] == "income")
        spending = sum(row["actual"] for row in rows if row["kind"] == "expense")
        spending_budget = sum(row["budget"] for row in rows if row["kind"] == "expense")
        savings = sum(savings_by_month[key] + espp_by_month[key] for key in period_keys)
        return {
            "income": income, "income_budget": income_budget,
            "spending": spending, "spending_budget": spending_budget,
            "surplus": income - spending, "surplus_budget": income_budget - spending_budget,
            "savings": savings, "savings_rate": savings / income if income else None,
            "investment_income": investment_income,
            "categories": rows,
        }

    monthly: dict[str, Any] = {}
    ytd: dict[str, Any] = {}
    for period in periods:
        ytd_keys = [key for key in periods if key[:4] == period[:4] and key <= period]
        monthly[period] = values_for([period])
        ytd[period] = values_for(ytd_keys)

    ledger = workbook["Ledger"]
    opening = {
        str(account).strip(): _number(balance)
        for account, balance in ledger.iter_rows(
            min_row=3, max_row=34, max_col=2, values_only=True
        ) if account
    }
    balances: dict[str, Any] = {}
    running_debits: dict[str, float] = defaultdict(float)
    running_credits: dict[str, float] = defaultdict(float)
    accounts = set(ASSET_ACCOUNTS) | set(LIABILITY_ACCOUNTS)
    for period in periods:
        for account in accounts:
            running_debits[account] += account_debits[(period, account)]
            running_credits[account] += account_credits[(period, account)]
        assets = [{
            "account": account,
            "balance": opening.get(account, 0.0) + running_debits[account] - running_credits[account],
        } for account in ASSET_ACCOUNTS]
        liabilities = [{
            "account": account,
            "balance": opening.get(account, 0.0) - running_debits[account] + running_credits[account],
        } for account in LIABILITY_ACCOUNTS]
        total_assets = sum(row["balance"] for row in assets)
        total_liabilities = sum(row["balance"] for row in liabilities)
        balances[period] = {
            "assets": assets, "liabilities": liabilities,
            "total_assets": total_assets, "total_liabilities": total_liabilities,
            "net_worth": total_assets - total_liabilities,
        }

    workbook.close()
    payload = {
        "workbook": path.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "latest_transaction_date": max(tx["date"] for tx in transactions),
        "latest_period": latest_period, "periods": periods,
        "monthly": monthly, "ytd": ytd, "balances": balances,
    }
    return payload, transactions


def build_dashboard_payload(workbook_path: str | Path) -> dict[str, Any]:
    """Build the compact dashboard payload; raw transactions stay internal."""
    payload, _transactions = build_dashboard_data(workbook_path)
    return payload
