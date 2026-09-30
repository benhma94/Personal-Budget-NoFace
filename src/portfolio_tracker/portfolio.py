"""Reconstruct daily holdings and cash flows from a transactions log."""
from __future__ import annotations

from datetime import date
from typing import Callable, Mapping

import pandas as pd


_EXTERNAL_TYPES = {"CONTRIB", "WITHDRAW"}


def reconstruct_daily_units(
    transactions: pd.DataFrame, start: date, end: date
) -> pd.DataFrame:
    """Daily units held by ticker over [start, end] inclusive.

    Units change on BUY/SELL only; forward-filled between transactions.
    Returns an empty-column DataFrame (indexed by date) if no ticker positions exist.
    """
    idx = pd.date_range(start, end, freq="D")

    ticker_txns = transactions[transactions["type"].isin({"BUY", "SELL"})]
    if ticker_txns.empty:
        return pd.DataFrame(index=idx)

    deltas = (
        ticker_txns.assign(
            date=pd.to_datetime(ticker_txns["date"]),
            units=ticker_txns["units"].astype(float),
        )
        .groupby(["date", "ticker"])["units"]
        .sum()
        .unstack(fill_value=0.0)
    )

    units = deltas.reindex(idx, fill_value=0.0).cumsum()
    return units


def daily_external_cash_flows_cad(
    transactions: pd.DataFrame,
    start: date,
    end: date,
    fx_to_cad: Mapping[str, Callable[[pd.Timestamp], float]],
) -> pd.Series:
    """Net external cash flow per day, in CAD.

    Cash contributions/withdrawals and explicitly valued in-kind security
    transfers are external. BUY/SELL/DIV/FEE are otherwise internal account
    movements. If ``external_flow_native`` is absent, the legacy type-based
    classification is used.
    """
    idx = pd.date_range(start, end, freq="D")
    if "external_flow_native" in transactions.columns:
        ext = transactions[transactions["external_flow_native"].astype(float) != 0].copy()
        amount_column = "external_flow_native"
    else:
        ext = transactions[transactions["type"].isin(_EXTERNAL_TYPES)].copy()
        amount_column = "cash_native"
    if ext.empty:
        return pd.Series(0.0, index=idx)

    ext["date"] = pd.to_datetime(ext["date"])
    ext["cad"] = [
        row[amount_column] * fx_to_cad[row["currency"]](row["date"])
        for _, row in ext.iterrows()
    ]

    daily = ext.groupby("date")["cad"].sum()
    return daily.reindex(idx, fill_value=0.0)


def interpolated_prices_from_transactions(
    transactions: pd.DataFrame,
    tickers: list[str],
    start: date,
    end: date,
    fx_to_cad: Mapping[str, Callable[[pd.Timestamp], float]],
) -> pd.DataFrame:
    """CAD price series for tickers with no market or manual-override price.

    Built from each ticker's own non-zero BUY/SELL prices (some providers emit
    $0.00 filler rows for automatic unit issuances; those are ignored), linearly
    interpolated between known points and held flat outside their range. Used for
    illiquid/delisted holdings (e.g. a private fund) so the position isn't dropped
    from portfolio value entirely between known prices, which would otherwise
    create a value discontinuity at its buy/sell dates.
    """
    idx = pd.date_range(start, end, freq="D")
    if not tickers:
        return pd.DataFrame(index=idx)

    priced = transactions[
        transactions["ticker"].isin(tickers)
        & transactions["type"].isin({"BUY", "SELL"})
        & (transactions["price_native"].astype(float) > 0)
    ].copy()
    if priced.empty:
        return pd.DataFrame(index=idx)

    priced["date"] = pd.to_datetime(priced["date"])
    out = {}
    for ticker, group in priced.groupby("ticker"):
        currency = group["currency"].iloc[-1]
        fx = fx_to_cad[currency]
        native = group.groupby("date")["price_native"].last().astype(float).reindex(idx)
        native = native.interpolate(method="time").ffill().bfill()
        out[ticker] = pd.Series([p * fx(ts) for ts, p in native.items()], index=idx)
    return pd.DataFrame(out)


def daily_cash_balance_cad(
    transactions: pd.DataFrame,
    start: date,
    end: date,
    fx_to_cad: Mapping[str, Callable[[pd.Timestamp], float]],
) -> pd.Series:
    """Cumulative cash balance in CAD on each day in [start, end]."""
    idx = pd.date_range(start, end, freq="D")
    if transactions.empty:
        return pd.Series(0.0, index=idx)

    t = transactions.copy()
    t["date"] = pd.to_datetime(t["date"])
    t["cad"] = [
        row["cash_native"] * fx_to_cad[row["currency"]](row["date"])
        for _, row in t.iterrows()
    ]

    daily = t.groupby("date")["cad"].sum()
    return daily.reindex(idx, fill_value=0.0).cumsum()
