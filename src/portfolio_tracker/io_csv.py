"""Wealthsimple activity CSV importer.

Reads an `activities-export-YYYY-MM-DD.csv` file and returns transactions
in the standard schema used by the rest of the package.

Activity-type mapping (Wealthsimple → standard type):
- Trade/BUY                            → BUY
- Trade/SELL                           → SELL  (units sign-flipped to negative)
- Dividend / Interest / ReturnOfCapital / BrokenDividend
                                       → DIV
- AdministrativePayment / BonusPayment → DIV (internal credits)
- Fee / Tax                            → FEE
- MoneyMovement/EFT or TRANSFER (+)    → CONTRIB
- MoneyMovement/EFT or TRANSFER (-)    → WITHDRAW
- MoneyMovement/TRANSFER_TF            → INTERNAL (between user's own accounts)
- CorporateAction/SUBDIVISION          → BUY at price=0 (stock split)
- StockDividend                        → BUY at price=0
- SecurityTransfer                     → BUY/SELL at price=0 plus an external
                                         in-kind contribution/withdrawal value
- NonCashDistribution / Switch / Correction / LegacyCorporateAction
                                       → warned + skipped (manual handling if needed)
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .symbols import SYMBOLS

_US_LISTED = SYMBOLS["us_listed"]
_TICKER_OVERRIDES = SYMBOLS["ticker_overrides"]
_WS_INTERNAL = SYMBOLS["internal_tickers"]


def normalize_ticker(symbol: str) -> str:
    """Map a Wealthsimple display symbol to a Yahoo-compatible ticker."""
    s = (symbol or "").strip()
    if not s:
        return ""
    if s in _TICKER_OVERRIDES:
        return _TICKER_OVERRIDES[s]
    if s in _US_LISTED:
        return s
    if s in _WS_INTERNAL:
        return s
    if "." in s:  # already has an exchange suffix (e.g. ZSP.TO)
        return s
    return f"{s}.TO"


# Activity type dispatch — returns the standard type or None to skip
def _map_activity(activity_type: str, sub_type: str, cash: float) -> str | None:
    at = (activity_type or "").strip()
    sub = (sub_type or "").strip()

    if at == "Trade":
        if sub == "BUY":
            return "BUY"
        if sub == "SELL":
            return "SELL"
        return None
    if at in {"Dividend", "Interest", "ReturnOfCapital", "BrokenDividend"}:
        return "DIV"
    if at in {"AdministrativePayment", "BonusPayment"}:
        return "DIV"
    if at in {"Fee", "Tax"}:
        return "FEE"
    if at == "MoneyMovement":
        if sub == "TRANSFER_TF":
            return "INTERNAL"
        if sub in {"EFT", "TRANSFER"}:
            return "CONTRIB" if cash >= 0 else "WITHDRAW"
        return None
    if at == "CorporateAction" and sub == "SUBDIVISION":
        return "BUY"
    if at == "StockDividend":
        return "BUY"
    if at == "SecurityTransfer":
        return "BUY"  # caller flips sign for outgoing transfers
    return None


_ZERO_CASH_TYPES = {"CorporateAction", "StockDividend", "SecurityTransfer"}


def read_wealthsimple_csv(source) -> tuple[pd.DataFrame, list[str]]:
    """Parse a Wealthsimple activities export.

    `source` may be a file path or any object pandas.read_csv accepts.
    Returns a (DataFrame, warnings) tuple. The DataFrame matches the
    standard Transactions schema used by io_excel.read_transactions.
    """
    raw = pd.read_csv(source, dtype=str).fillna("")

    # Wealthsimple's date column has been renamed twice: transaction_date ->
    # effective_at (single ISO timestamp) -> effective_date (split into a
    # separate effective_date/effective_time pair) in its 2026 exports.
    date_column = next(
        (c for c in ("transaction_date", "effective_at", "effective_date") if c in raw.columns),
        None,
    )
    if date_column is None:
        raise ValueError(
            "Wealthsimple CSV requires transaction_date, effective_at, or effective_date"
        )
    parsed_dates = pd.to_datetime(raw[date_column], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    raw = raw[parsed_dates.notna()].copy()
    raw["_transaction_date"] = parsed_dates[parsed_dates.notna()]

    warnings: list[str] = []
    out_rows: list[dict[str, Any]] = []

    for _, r in raw.iterrows():
        cash = _to_float(r.get("net_cash_amount"))
        units_raw = _to_float(r.get("quantity"))
        price = _to_float(r.get("unit_price"))
        fees = _to_float(r.get("commission"))
        symbol = _safe_str(r.get("symbol"))
        activity = _safe_str(r.get("activity_type"))
        sub = _safe_str(r.get("activity_sub_type"))

        std_type = _map_activity(activity, sub, cash)
        if std_type is None:
            warnings.append(
                f"Unsupported activity {activity}/{sub} on {r['_transaction_date']} ({symbol})"
            )
            continue

        # A security transfer moves units without moving account cash. Its
        # exported net amount is retained separately as an external in-kind
        # flow for money-weighted returns.
        external_flow = cash if std_type in {"CONTRIB", "WITHDRAW"} else 0.0
        if activity == "SecurityTransfer":
            direction = _safe_str(r.get("direction")).upper()
            is_outgoing = cash < 0 or units_raw < 0 or direction in {"OUT", "SHORT"}
            std_type = "SELL" if is_outgoing else "BUY"
            if cash != 0:
                external_flow = cash
            else:
                transfer_value = abs(units_raw * price)
                external_flow = -transfer_value if is_outgoing else transfer_value

        # SELL: flip sign so my schema's units are negative for outgoing
        if std_type == "SELL":
            units = -abs(units_raw)
        else:
            units = units_raw

        # Activity types that move units but no cash
        if activity in _ZERO_CASH_TYPES:
            cash = 0.0
            price = 0.0

        # Dividends and other cash-only events don't change units
        if std_type in {"DIV", "FEE", "CONTRIB", "WITHDRAW", "INTERNAL"}:
            units = 0.0

        ccy = _safe_str(r.get("currency")) or "CAD"
        out_rows.append({
            "date": pd.to_datetime(r["_transaction_date"]),
            "account": f"{_safe_str(r.get('account_type'))}|{_safe_str(r.get('account_id'))}",
            "ticker": normalize_ticker(symbol),
            "type": std_type,
            "units": float(units),
            "price_native": float(price),
            "currency": ccy.upper(),
            "cash_native": float(cash),
            "external_flow_native": float(external_flow),
            "fees": float(fees),
            "note": _safe_str(r.get("name")),
        })

    df = pd.DataFrame(out_rows)
    if df.empty:
        df = pd.DataFrame(columns=[
            "date", "account", "ticker", "type", "units",
            "price_native", "currency", "cash_native", "external_flow_native",
            "fees", "note",
        ])
    return df, warnings


def _safe_str(v) -> str:
    if v is None:
        return ""
    s = str(v)
    if s == "nan":
        return ""
    return s.strip()


def _to_float(v) -> float:
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0
