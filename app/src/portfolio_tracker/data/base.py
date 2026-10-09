"""Abstract base class for portfolio data sources.

Implementations: LSEGDataSource (primary), ProviderChain, and YahooDataSource (fallback).
Tests use a FakeDataSource (see tests/conftest.py) with pre-loaded fixtures.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass
class FundSnapshot:
    """Provider-neutral fund exposure distributions."""

    ticker: str
    ric: str = ""
    geography: dict[str, float] = field(default_factory=dict)
    sector: dict[str, float] = field(default_factory=dict)
    asset_class: dict[str, float] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    fetched_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)

@runtime_checkable
class ExposureSource(Protocol):
    """Narrower contract used by exposure aggregation.

    Implemented structurally by market-data sources and provider chains.
    """

    def get_classification(self, ticker: str) -> dict: ...
    def get_fund_snapshot(self, ticker: str) -> FundSnapshot: ...


class DataSource(ABC):
    """Adapter over an external market-data provider.

    All prices returned in NATIVE listing currency. Callers convert to the
    portfolio base currency separately via the FX module.
    """

    @abstractmethod
    def get_prices(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        """Daily adjusted-close prices in native currency.

        Returns a DataFrame indexed by date with one column per ticker.
        Missing days are gaps (no forward-fill). Callers handle alignment.
        """

    @abstractmethod
    def get_fx(self, base: str, quote: str, start: date, end: date) -> pd.Series:
        """Daily FX rate to convert `base` into `quote` (e.g. base=USD, quote=CAD)."""

    @abstractmethod
    def get_classification(self, ticker: str) -> dict:
        """Lightweight metadata. Required keys:

        - quote_type: 'EQUITY' | 'ETF' | 'MUTUALFUND' | 'BOND' | other
        - country: HQ / listing country, e.g. 'Canada', 'United States'
        - sector: GICS-style sector or '' if unavailable
        - currency: listing currency, e.g. 'CAD', 'USD'
        """

    @abstractmethod
    def get_fund_snapshot(self, ticker: str) -> FundSnapshot:
        """Fund-level geography, sector, and asset-class allocations."""
