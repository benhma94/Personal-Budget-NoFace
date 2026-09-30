"""Shared test fixtures.

`FakeDataSource` lets every test run deterministically with no network access.
Pass it pre-loaded price, FX, classification, and fund-allocation data and it
satisfies the DataSource ABC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import os
from pathlib import Path

os.environ["FINANCE_PROFILE_PATH"] = str(
    Path(__file__).resolve().parents[1] / "src/budget_dashboard/profile.example.json"
)
os.environ["PORTFOLIO_SYMBOLS_PATH"] = str(
    Path(__file__).resolve().parents[1] / "src/portfolio_tracker/symbols.example.json"
)

import pandas as pd
import pytest

from portfolio_tracker.data.base import DataSource, FundSnapshot


@dataclass
class FakeDataSource(DataSource):
    prices: dict[str, pd.Series] = field(default_factory=dict)
    fx: dict[str, pd.Series] = field(default_factory=dict)
    classifications: dict[str, dict] = field(default_factory=dict)
    fund_snapshots: dict[str, FundSnapshot] = field(default_factory=dict)

    def get_prices(self, tickers, start, end):
        cols = {}
        for t in tickers:
            if t not in self.prices:
                raise KeyError(f"FakeDataSource has no prices for {t!r}")
            s = self.prices[t]
            mask = (s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))
            cols[t] = s.loc[mask]
        return pd.DataFrame(cols)

    def get_fx(self, base, quote, start, end):
        pair = f"{base}{quote}"
        if pair not in self.fx:
            raise KeyError(f"FakeDataSource has no FX for {pair!r}")
        s = self.fx[pair]
        mask = (s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))
        return s.loc[mask]

    def get_classification(self, ticker):
        if ticker not in self.classifications:
            raise KeyError(f"FakeDataSource has no classification for {ticker!r}")
        return self.classifications[ticker]

    def get_fund_snapshot(self, ticker):
        return self.fund_snapshots.get(ticker, FundSnapshot(ticker=ticker))


def _daily_series(start: str, end: str, value: float) -> pd.Series:
    idx = pd.date_range(start, end, freq="D")
    return pd.Series(value, index=idx)


@pytest.fixture
def fake_source():
    return FakeDataSource()


@pytest.fixture
def daily_series():
    return _daily_series
