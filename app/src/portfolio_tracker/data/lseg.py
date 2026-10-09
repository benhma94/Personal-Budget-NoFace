"""LSEG Workspace market data, Lipper fund data, and Yahoo fallback routing."""
from __future__ import annotations

import os
import warnings
from datetime import date, datetime
from typing import Any

import pandas as pd

from portfolio_tracker.data.base import DataSource, FundSnapshot
from portfolio_tracker.data.cache import Cache


_PRICE_ADJUSTMENTS = [
    "exchangeCorrection", "manualCorrection", "CCH", "CRE", "RPO", "RTS",
]
_ASSET_MAP = {
    "EQUITY": "Equity",
    "FIXED INCOME": "Debt",
    "CASH": "Cash",
    "OTHER": "Other",
}
_ALLOCATION_FIELDS = {
    "geography": "TR.FundCountryAllocation",
    "sector": "TR.FundIndustrySectorAllocation",
    "asset_class": "TR.FundAssetAllocation",
}

_DEFAULT_YAHOO_FALLBACKS = {
    # XIC adjusted prices include distributions and provide an investable
    # approximation when the direct Canadian total-return index is unavailable.
    ".TRGSPTSE": "XIC.TO",
    # Direct LSEG index history may require a separate FTSE fixed-income
    # entitlement. XBB tracks this same index and keeps reports operational.
    ".FT26045CADT": "XBB.TO",
}


class IdentifierMap:
    def __init__(self, mappings: dict[str, dict[str, str]] | None = None):
        self._mappings = mappings or {}

    def lseg(self, ticker: str) -> str:
        return self._mappings.get(ticker, {}).get("lseg_ric") or ticker

    def yahoo(self, ticker: str) -> str:
        return (
            self._mappings.get(ticker, {}).get("yahoo_ticker")
            or _DEFAULT_YAHOO_FALLBACKS.get(ticker.upper())
            or ticker
        )


class LSEGDataSource(DataSource):
    """LSEG-first source using a lazy Workspace desktop session."""

    def __init__(
        self,
        identifiers: IdentifierMap,
        cache: Cache | None = None,
        classification_ttl_days: int = 7,
        refresh_funds: bool = False,
        ld_module: Any | None = None,
    ):
        self.identifiers = identifiers
        self.cache = cache
        self.classification_ttl_days = classification_ttl_days
        self.refresh_funds = refresh_funds
        self._ld = ld_module
        self._session = None
        self._session_error: Exception | None = None
        self.warnings: list[str] = []

    def _library(self):
        if self._ld is None:
            import lseg.data as ld
            self._ld = ld
        return self._ld

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        if self._session_error is not None:
            raise RuntimeError(f"LSEG session unavailable: {self._session_error}")
        try:
            ld = self._library()
            app_key = os.environ.get("LSEG_APP_KEY")
            if not app_key:
                raise RuntimeError("LSEG_APP_KEY is not set")
            self._session = ld.session.desktop.Definition(app_key=app_key).get_session()
            self._session.open()
            ld.session.set_default(self._session)
            return self._session
        except Exception as exc:
            self._session_error = exc
            raise

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _request(self, operation: str, callback):
        """Retry one transient Workspace/API failure; do not retry entitlement gaps."""
        try:
            return callback()
        except Exception as exc:
            message = str(exc).lower()
            permanent = any(text in message for text in (
                "notpermission", "no permission", "universe is not found", "not found",
            ))
            if permanent:
                raise
            self.warnings.append(f"Retrying transient LSEG {operation} request: {exc}")
            return callback()

    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        results: dict[str, pd.Series] = {}
        for ticker in tickers:
            cached = self.cache.get_prices(
                ticker, start, end, provider="lseg", max_edge_gap_days=7
            ) if self.cache else None
            if cached is not None:
                results[ticker] = cached
                continue
            self._ensure_session()
            ric = self.identifiers.lseg(ticker)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                df = self._request("price", lambda: self._library().get_history(
                    universe=ric, fields=["TRDPRC_1"], interval="daily",
                    start=start, end=end, adjustments=_PRICE_ADJUSTMENTS,
                ))
            series = _history_series(df, "TRDPRC_1")
            if series.empty:
                raise KeyError(f"LSEG returned no price history for {ticker} ({ric})")
            series.name = ticker
            try:
                currency = self.get_classification(ticker).get("currency") or ""
            except Exception as exc:
                currency = ""
                self.warnings.append(f"Price retrieved but LSEG currency metadata failed for {ticker}: {exc}")
            if self.cache:
                self.cache.put_prices(ticker, series, currency, provider="lseg")
            results[ticker] = series
        return pd.DataFrame(results)

    def get_fx(self, base: str, quote: str, start: date, end: date) -> pd.Series:
        pair = f"{base}/{quote}"
        cached = self.cache.get_fx(
            pair, start, end, provider="lseg", max_edge_gap_days=7
        ) if self.cache else None
        if cached is not None:
            return cached
        self._ensure_session()
        ric = self.identifiers.lseg(pair)
        if ric == pair:
            raise KeyError(f"No LSEG RIC mapping for FX pair {pair}")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            df = self._request("FX", lambda: self._library().get_history(
                universe=ric, fields=["MID_PRICE"], interval="daily", start=start, end=end
            ))
        series = _history_series(df, "MID_PRICE")
        if series.empty:
            raise KeyError(f"LSEG returned no FX history for {pair} ({ric})")
        if self.cache:
            self.cache.put_fx(pair, series, provider="lseg")
        return series

    def get_classification(self, ticker: str) -> dict:
        cached = (
            self.cache.get_classification(ticker, self.classification_ttl_days, provider="lseg")
            if self.cache else None
        )
        if cached:
            return cached
        self._ensure_session()
        ric = self.identifiers.lseg(ticker)
        fields = [
            "TR.CommonName", "TR.AssetCategory", "TR.HeadquartersCountry",
            "TR.TRBCIndustryGroup", "TR.PriceClose.currency", "TR.FundType",
        ]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            df = self._request("metadata", lambda: self._library().get_data(universe=ric, fields=fields))
        if df is None or df.empty:
            raise KeyError(f"LSEG returned no metadata for {ticker} ({ric})")
        row = df.iloc[0]
        asset = _value(row, "Asset Category Description", "TR.AssetCategory")
        fund_type = _value(row, "Fund Type", "TR.FundType")
        asset_upper = asset.upper()
        if fund_type or "ETF" in asset_upper:
            quote_type = "ETF"
        elif "FUND" in asset_upper:
            quote_type = "MUTUALFUND"
        elif "BOND" in asset_upper or "FIXED INCOME" in asset_upper:
            quote_type = "BOND"
        elif asset:
            quote_type = "EQUITY"
        else:
            quote_type = ""
        payload = {
            "quote_type": quote_type,
            "country": _value(row, "Country of Headquarters", "TR.HeadquartersCountry"),
            "sector": _value(row, "TRBC Industry Group Name", "TR.TRBCIndustryGroup"),
            "currency": _value(row, "Currency", "TR.PriceClose.currency"),
            "long_name": _value(row, "Company Common Name", "TR.CommonName") or ticker,
            "asset_category": asset,
            "fund_type": fund_type,
            "ric": ric,
        }
        if self.cache:
            self.cache.put_classification(ticker, payload, provider="lseg")
        return payload

    def get_fund_snapshot(self, ticker: str) -> FundSnapshot:
        cached = self.cache.get_fund_snapshot(ticker, provider="lseg") if self.cache else None
        if cached and not self.refresh_funds and cached[0].date() == datetime.now().date():
            return _snapshot_from_payload(ticker, cached[0], cached[1])
        try:
            snapshot = self._fetch_fund_snapshot(ticker)
            if self.cache:
                self.cache.put_fund_snapshot(
                    ticker, _snapshot_to_payload(snapshot), snapshot.fetched_at or datetime.now(), provider="lseg"
                )
            return snapshot
        except Exception as exc:
            if cached:
                snapshot = _snapshot_from_payload(ticker, cached[0], cached[1])
                age = datetime.now() - cached[0]
                snapshot.warnings.append(
                    f"Using stale LSEG fund snapshot for {ticker} ({age.days} days old): {exc}"
                )
                return snapshot
            raise

    def _fetch_fund_snapshot(self, ticker: str) -> FundSnapshot:
        self._ensure_session()
        ric = self.identifiers.lseg(ticker)
        fetched_at = datetime.now()
        snapshot_warnings: list[str] = []
        allocations = {
            dimension: self._fetch_allocation(ric, field, dimension, snapshot_warnings)
            for dimension, field in _ALLOCATION_FIELDS.items()
        }
        if not any(allocations.values()):
            raise KeyError(f"No Lipper fund data for {ticker} ({ric})")
        sources = {
            name: "lseg" if values else ""
            for name, values in allocations.items()
        }
        return FundSnapshot(
            ticker=ticker,
            ric=ric,
            geography=allocations["geography"],
            sector=allocations["sector"],
            asset_class=allocations["asset_class"],
            sources=sources,
            fetched_at=fetched_at,
            warnings=snapshot_warnings,
        )

    def _fetch_allocation(
        self, ric: str, field: str, dimension: str, snapshot_warnings: list[str]
    ) -> dict[str, float]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                df = self._request(f"{dimension} allocation", lambda: self._library().get_data(
                    universe=ric, fields=[field, "TR.FundAllocationName"]
                ))
        except Exception as exc:
            snapshot_warnings.append(f"{dimension} allocation unavailable for {ric}: {exc}")
            return {}
        if df is None or df.empty:
            return {}
        name_col = _find_column(df, "Allocation Name", "TR.FundAllocationName")
        value_col = next((c for c in df.columns if c not in {"Instrument", name_col}), None)
        if name_col is None or value_col is None:
            return {}
        values = pd.to_numeric(df[value_col], errors="coerce") / 100.0
        distribution: dict[str, float] = {}
        for label, weight in zip(df[name_col], values):
            if pd.isna(weight) or float(weight) <= 0:
                continue
            name = str(label).strip() if not pd.isna(label) else "Unknown"
            if not name:
                name = "Unknown"
            if dimension == "asset_class":
                name = _ASSET_MAP.get(name.upper(), name.title())
            else:
                name = name.title()
            distribution[name] = distribution.get(name, 0.0) + float(weight)
        if not distribution:
            return {}
        total = sum(distribution.values())
        if total > 1.0:
            distribution = {k: v / total for k, v in distribution.items()}
        elif total < 1.0 - 1e-9:
            distribution["Unknown"] = distribution.get("Unknown", 0.0) + (1.0 - total)
            snapshot_warnings.append(
                f"{dimension} allocation for {ric} reported {total:.2%}; residual assigned to Unknown"
            )
        return distribution


class ProviderChain(DataSource):
    """Route each request through LSEG first and Yahoo second."""

    def __init__(self, primary: LSEGDataSource, fallback: DataSource, identifiers: IdentifierMap):
        self.primary = primary
        self.fallback = fallback
        self.identifiers = identifiers
        self.provenance: dict[str, str] = {}
        self.warnings: list[str] = []
        self._fund_snapshots: dict[str, FundSnapshot] = {}

    def close(self) -> None:
        self.primary.close()

    def _warn(self, operation: str, ticker: str, exc: Exception) -> None:
        self.warnings.append(f"LSEG {operation} fallback for {ticker}: {exc}")

    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        out = {}
        for ticker in tickers:
            try:
                out[ticker] = self.primary.get_prices([ticker], start, end)[ticker]
                self.provenance[f"price:{ticker}"] = "lseg"
            except Exception as exc:
                self._warn("price", ticker, exc)
                yahoo_ticker = self.identifiers.yahoo(ticker)
                df = self.fallback.get_prices([yahoo_ticker], start, end)
                out[ticker] = df[yahoo_ticker]
                self.provenance[f"price:{ticker}"] = "yahoo"
        return pd.DataFrame(out)

    def get_fx(self, base: str, quote: str, start: date, end: date) -> pd.Series:
        pair = f"{base}/{quote}"
        try:
            result = self.primary.get_fx(base, quote, start, end)
            self.provenance[f"fx:{pair}"] = "lseg"
            return result
        except Exception as exc:
            self._warn("FX", pair, exc)
            result = self.fallback.get_fx(base, quote, start, end)
            self.provenance[f"fx:{pair}"] = "yahoo"
            return result

    def get_classification(self, ticker: str) -> dict:
        try:
            result = self.primary.get_classification(ticker)
            self.provenance[f"metadata:{ticker}"] = "lseg"
            return result
        except Exception as exc:
            self._warn("metadata", ticker, exc)
            yahoo_ticker = self.identifiers.yahoo(ticker)
            result = self.fallback.get_classification(yahoo_ticker).copy()
            self.provenance[f"metadata:{ticker}"] = "yahoo"
            return result

    def _store_fund_snapshot(self, ticker: str, snapshot: FundSnapshot) -> FundSnapshot:
        for warning in snapshot.warnings:
            if warning not in self.warnings:
                self.warnings.append(warning)
        for dimension, provider in snapshot.sources.items():
            if provider and dimension in {"geography", "sector", "asset_class"}:
                self.provenance[f"fund_{dimension}:{ticker}"] = provider
        self._fund_snapshots[ticker] = snapshot
        return snapshot

    def get_fund_snapshot(self, ticker: str) -> FundSnapshot:
        if ticker in self._fund_snapshots:
            return self._fund_snapshots[ticker]
        primary_snapshot = None
        try:
            primary_snapshot = self.primary.get_fund_snapshot(ticker)
        except Exception as exc:
            self._warn("fund data", ticker, exc)

        need_fallback = primary_snapshot is None or any(
            not getattr(primary_snapshot, d) for d in ("geography", "sector", "asset_class")
        )
        fallback_snapshot = None
        if need_fallback:
            try:
                yahoo_ticker = self.identifiers.yahoo(ticker)
                fallback_snapshot = self.fallback.get_fund_snapshot(yahoo_ticker)
            except Exception as exc:
                self.warnings.append(f"Yahoo fund fallback unavailable for {ticker}: {exc}")

        if primary_snapshot is None:
            if fallback_snapshot is None:
                return self._store_fund_snapshot(ticker, FundSnapshot(ticker=ticker))
            fallback_snapshot.ticker = ticker
            fallback_snapshot.ric = self.identifiers.lseg(ticker)
            fallback_snapshot.sources = {
                k: "yahoo" for k in ("geography", "sector", "asset_class")
                if getattr(fallback_snapshot, k)
            }
            return self._store_fund_snapshot(ticker, fallback_snapshot)

        if fallback_snapshot is not None:
            for dimension in ("geography", "sector", "asset_class"):
                if not getattr(primary_snapshot, dimension) and getattr(fallback_snapshot, dimension):
                    setattr(primary_snapshot, dimension, getattr(fallback_snapshot, dimension))
                    primary_snapshot.sources[dimension] = "yahoo"
        return self._store_fund_snapshot(ticker, primary_snapshot)


def _history_series(df: pd.DataFrame, preferred: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)
    if isinstance(df.columns, pd.MultiIndex):
        matches = [c for c in df.columns if preferred in tuple(map(str, c))]
        series = df[matches[0]] if matches else df.iloc[:, 0]
    else:
        column = preferred if preferred in df.columns else df.columns[0]
        series = df[column]
    return pd.to_numeric(series, errors="coerce").dropna().sort_index()


def _find_column(df: pd.DataFrame, *names: str):
    lowered = {str(c).lower(): c for c in df.columns}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _value(row: pd.Series, *names: str) -> str:
    for name in names:
        if name in row.index and not pd.isna(row[name]):
            return str(row[name]).strip()
    return ""


def _snapshot_to_payload(snapshot: FundSnapshot) -> dict:
    return {
        "ric": snapshot.ric,
        "geography": snapshot.geography,
        "sector": snapshot.sector,
        "asset_class": snapshot.asset_class,
        "sources": snapshot.sources,
        "warnings": snapshot.warnings,
    }


def _snapshot_from_payload(ticker: str, fetched_at: datetime, payload: dict) -> FundSnapshot:
    return FundSnapshot(
        ticker=ticker,
        ric=payload.get("ric", ""),
        geography={k: float(v) for k, v in payload.get("geography", {}).items()},
        sector={k: float(v) for k, v in payload.get("sector", {}).items()},
        asset_class={k: float(v) for k, v in payload.get("asset_class", {}).items()},
        sources=payload.get("sources", {}),
        fetched_at=fetched_at,
        warnings=list(payload.get("warnings", [])),
    )
