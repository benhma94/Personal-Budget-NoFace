"""Adjusted Cost Base (ACB) computation using average-cost method."""
from __future__ import annotations

from typing import Callable

import pandas as pd


_EPSILON = 1e-6


def compute_acb(
    transactions: pd.DataFrame,
    fx_callables: dict[str, Callable[[pd.Timestamp], float]],
) -> dict[str, dict[str, float]]:
    """Compute ACB per ticker from the full transaction log.

    Returns {ticker: {"acb_per_unit": float, "total_acb": float, "total_units": float}}.
    Only tickers with current holdings (total_units > 1e-6) are included.
    Uses average-cost method (standard Canadian ACB). BUY at price=0 (stock splits,
    stock dividends) increases units without adding cost, correctly reducing ACB per unit.
    """
    state: dict[str, dict[str, float]] = {}

    txns = transactions.copy()
    txns["date"] = pd.to_datetime(txns["date"])
    txns = txns.sort_values("date").reset_index(drop=True)

    for _, row in txns.iterrows():
        ticker = str(row["ticker"]).strip()
        if not ticker:
            continue
        txn_type = str(row["type"])
        if txn_type not in {"BUY", "SELL"}:
            continue

        if ticker not in state:
            state[ticker] = {"total_units": 0.0, "total_cost": 0.0}

        s = state[ticker]
        units = float(row["units"])          # positive for BUY, negative for SELL
        price_native = float(row["price_native"])
        currency = str(row["currency"])
        date_ts = pd.Timestamp(row["date"])

        fx = fx_callables.get(currency, lambda _: 1.0)
        price_cad = price_native * fx(date_ts)

        if txn_type == "BUY":
            s["total_cost"] += units * price_cad   # 0 for splits
            s["total_units"] += units
        else:  # SELL — units is negative
            sell_units = abs(units)
            if s["total_units"] > _EPSILON:
                acb_per_unit = s["total_cost"] / s["total_units"]
                s["total_cost"] -= sell_units * acb_per_unit
            s["total_units"] += units   # subtracts

        # Guard floating-point drift
        s["total_units"] = max(0.0, s["total_units"])
        s["total_cost"] = max(0.0, s["total_cost"])

    result: dict[str, dict[str, float]] = {}
    for ticker, s in state.items():
        if s["total_units"] < _EPSILON:
            continue
        acb_per_unit = s["total_cost"] / s["total_units"] if s["total_units"] > 0 else 0.0
        result[ticker] = {
            "acb_per_unit": acb_per_unit,
            "total_acb": s["total_cost"],
            "total_units": s["total_units"],
        }
    return result
