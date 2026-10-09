"""FX conversion to CAD (the portfolio base currency)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from portfolio_tracker.data.base import DataSource


def fx_series_to_cad(
    currency: str,
    start: date,
    end: date,
    source: DataSource | None,
) -> pd.Series:
    """Daily FX series to convert `currency` → CAD over [start, end]."""
    idx = pd.date_range(start, end, freq="D")
    if currency == "CAD":
        return pd.Series(1.0, index=idx)
    if source is None:
        raise ValueError(f"Need a DataSource to fetch FX for {currency} → CAD")
    return source.get_fx(currency, "CAD", start, end)


def convert_prices_to_cad(
    prices_native: pd.DataFrame,
    ticker_currencies: dict[str, str],
    source: DataSource,
) -> pd.DataFrame:
    """Element-wise convert native prices to CAD.

    For CAD-listed tickers passes through; for non-CAD pulls the daily FX
    series and multiplies. Missing FX raises KeyError from the source.
    """
    if prices_native.empty:
        return prices_native.copy()

    start = prices_native.index.min().date()
    end = prices_native.index.max().date()
    out = prices_native.copy()

    for ticker in prices_native.columns:
        ccy = ticker_currencies[ticker]
        if ccy == "CAD":
            continue
        fx = source.get_fx(ccy, "CAD", start, end)
        fx_aligned = fx.reindex(out.index).ffill().bfill()
        out[ticker] = out[ticker] * fx_aligned

    return out
