"""Concrete DataSource backed by yfinance, with optional SQLite caching.

Caching strategy:
- Past-date prices/FX never expire (market close is immutable).
- Today is always refetched.
- Classifications expire per `classification_ttl_days`.
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from portfolio_tracker.data.base import DataSource, FundSnapshot
from portfolio_tracker.data.cache import Cache


class YahooDataSource(DataSource):
    def __init__(self, cache: Cache | None = None, classification_ttl_days: int = 7):
        self.cache = cache
        self.classification_ttl_days = classification_ttl_days
        cache_root = cache.path.parent if cache is not None else Path("data")
        self._yf = self._import_yfinance(cache_root / ".yfinance-cache")

    @staticmethod
    def _import_yfinance(cache_location: Path):
        import yfinance as yf
        cache_location.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_location))
        return yf

    # ----- prices -----

    def get_prices(self, tickers, start, end):
        results = {}
        to_fetch = []

        for t in tickers:
            cached = self.cache.get_prices(
                t, start, end, provider="yahoo", max_edge_gap_days=7
            ) if self.cache else None
            if cached is not None:
                results[t] = cached
            else:
                to_fetch.append(t)

        if to_fetch:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = self._yf.download(
                    to_fetch,
                    start=start,
                    end=end + timedelta(days=1),
                    auto_adjust=True,
                    progress=False,
                    group_by="ticker",
                )

            for t in to_fetch:
                series = self._extract_adj_close(df, t)
                if series.empty:
                    raise KeyError(f"No price data for {t} between {start} and {end}")
                if self.cache:
                    ccy = self._get_currency(t)
                    self.cache.put_prices(t, series, currency=ccy, provider="yahoo")
                results[t] = series

        return pd.DataFrame({t: results[t] for t in tickers})

    @staticmethod
    def _extract_adj_close(df: pd.DataFrame, ticker: str) -> pd.Series:
        if df.empty:
            return pd.Series(dtype=float)
        if isinstance(df.columns, pd.MultiIndex):
            # Layout A: (ticker, price) — from group_by='ticker' multi-ticker download
            if ticker in df.columns.get_level_values(0):
                return df[ticker]["Close"].dropna()
            # Layout B: (price, ticker) — from single-ticker download
            if "Close" in df.columns.get_level_values(0):
                close = df["Close"]
                if isinstance(close, pd.DataFrame) and ticker in close.columns:
                    return close[ticker].dropna()
                if isinstance(close, pd.Series):
                    return close.dropna()
            return pd.Series(dtype=float)
        # Flat columns: single-ticker, no group_by
        if "Close" in df.columns:
            return df["Close"].dropna()
        return pd.Series(dtype=float)

    # ----- fx -----

    def get_fx(self, base, quote, start, end):
        pair = f"{base}{quote}"
        cached = self.cache.get_fx(
            pair, start, end, provider="yahoo", max_edge_gap_days=7
        ) if self.cache else None
        if cached is not None:
            return cached

        symbol = f"{base}{quote}=X"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = self._yf.download(
                symbol,
                start=start,
                end=end + timedelta(days=1),
                auto_adjust=True,
                progress=False,
            )
        series = self._extract_adj_close(df, symbol)
        if series.empty:
            raise KeyError(f"No FX data for {pair} between {start} and {end}")
        # FX trades 24/5; reindex to daily and forward-fill weekends
        idx = pd.date_range(start, end, freq="D")
        series = series.reindex(idx).ffill().bfill()
        if self.cache:
            self.cache.put_fx(pair, series, provider="yahoo")
        return series

    # ----- classification -----

    def get_classification(self, ticker):
        if self.cache:
            cached = self.cache.get_classification(ticker, self.classification_ttl_days, provider="yahoo")
            if cached:
                return cached

        info = self._yf.Ticker(ticker).info or {}
        payload = {
            "quote_type": info.get("quoteType", "") or "",
            "country": info.get("country", "") or "",
            "sector": info.get("sector", "") or "",
            "currency": info.get("currency", "") or "",
            "long_name": info.get("longName", "") or "",
        }
        if self.cache:
            self.cache.put_classification(ticker, payload, provider="yahoo")
        return payload

    def _get_currency(self, ticker: str) -> str:
        return self.get_classification(ticker).get("currency") or "USD"

    # ----- fund allocations -----

    def get_fund_snapshot(self, ticker: str) -> FundSnapshot:
        """Derive aggregate fund allocations from Yahoo's top-holdings data."""
        try:
            funds_data = self._yf.Ticker(ticker).funds_data
            top = funds_data.top_holdings
            asset_classes = funds_data.asset_classes or {}
        except Exception:
            return FundSnapshot(ticker=ticker)

        if top is None or top.empty:
            return FundSnapshot(ticker=ticker)

        geography: dict[str, float] = {}
        sectors: dict[str, float] = {}
        allocation: dict[str, float] = {}
        total_weight = 0.0
        for symbol, row in top.iterrows():
            try:
                weight = float(row["Holding Percent"])
            except (KeyError, ValueError, TypeError):
                continue
            total_weight += weight
            try:
                cls = self.get_classification(symbol)
            except Exception:
                cls = {}
            qt = (cls.get("quote_type") or "").upper()
            _add_weight(geography, cls.get("country") or "Other", weight)
            _add_weight(sectors, cls.get("sector") or "Diversified", weight)
            _add_weight(allocation, _quote_type_to_asset_class(qt, asset_classes), weight)

        residual = max(0.0, 1.0 - total_weight)
        if residual > 1e-6:
            etf_self = self.get_classification(ticker)
            _add_weight(geography, etf_self.get("country") or "Other", residual)
            _add_weight(sectors, etf_self.get("sector") or "Diversified", residual)
            _add_weight(allocation, _dominant_asset_class(asset_classes), residual)

        distributions = {
            "geography": geography,
            "sector": sectors,
            "asset_class": allocation,
        }
        return FundSnapshot(
            ticker=ticker,
            geography=geography,
            sector=sectors,
            asset_class=allocation,
            sources={name: "yahoo" for name, values in distributions.items() if values},
            fetched_at=datetime.now(),
        )


_EQUITY_TYPES = {"EQUITY", "ETF", "MUTUALFUND"}


def _add_weight(distribution: dict[str, float], bucket: str, weight: float) -> None:
    distribution[bucket] = distribution.get(bucket, 0.0) + weight


def _quote_type_to_asset_class(quote_type: str, asset_classes: dict) -> str:
    if quote_type in {"BOND", "FIXEDINCOME"}:
        return "Debt"
    if quote_type in {"CURRENCY", "CASH"}:
        return "Cash"
    if quote_type in _EQUITY_TYPES:
        return "Equity"
    return _dominant_asset_class(asset_classes)


def _dominant_asset_class(asset_classes: dict) -> str:
    if not asset_classes:
        return "Equity"
    stock = asset_classes.get("stockPosition", 0) or 0
    bond = asset_classes.get("bondPosition", 0) or 0
    cash = asset_classes.get("cashPosition", 0) or 0
    pairs = [("Equity", stock), ("Debt", bond), ("Cash", cash)]
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs[0][0]
