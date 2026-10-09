"""Tests for Yahoo-derived fund allocation snapshots."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio_tracker.data.yahoo import YahooDataSource


class _YahooLibrary:
    def __init__(self, funds_data):
        self._funds_data = funds_data

    def Ticker(self, _ticker):
        return SimpleNamespace(funds_data=self._funds_data)


def test_fund_snapshot_aggregates_top_holdings_without_retaining_constituents():
    top_holdings = pd.DataFrame(
        {
            "Holding Percent": [0.5, 0.3],
        },
        index=["AAA", "BBB"],
    )
    funds_data = SimpleNamespace(
        top_holdings=top_holdings,
        asset_classes={"stockPosition": 0.8, "bondPosition": 0.2},
    )
    classifications = {
        "AAA": {"quote_type": "EQUITY", "country": "Canada", "sector": "Technology"},
        "BBB": {"quote_type": "BOND", "country": "United States", "sector": "Fixed Income"},
        "FUND": {"country": "Canada", "sector": "Diversified"},
    }

    source = object.__new__(YahooDataSource)
    source._yf = _YahooLibrary(funds_data)
    source.get_classification = classifications.__getitem__

    snapshot = source.get_fund_snapshot("FUND")

    assert snapshot.geography == {
        "Canada": pytest.approx(0.7),
        "United States": pytest.approx(0.3),
    }
    assert snapshot.sector == {
        "Technology": pytest.approx(0.5),
        "Fixed Income": pytest.approx(0.3),
        "Diversified": pytest.approx(0.2),
    }
    assert snapshot.asset_class == {"Equity": pytest.approx(0.7), "Debt": pytest.approx(0.3)}
    assert snapshot.sources == {
        "geography": "yahoo",
        "sector": "yahoo",
        "asset_class": "yahoo",
    }
    assert not hasattr(snapshot, "holdings")
