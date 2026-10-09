"""Tests for the DataSource ABC contract."""
from __future__ import annotations

from datetime import date
import pandas as pd
import pytest

from portfolio_tracker.data.base import DataSource


def test_cannot_instantiate_abstract_data_source():
    with pytest.raises(TypeError):
        DataSource()


def test_fake_source_get_prices_returns_dataframe(fake_source):
    fake_source.prices["AAA"] = pd.Series(
        [10.0, 11.0, 12.0],
        index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    )
    result = fake_source.get_prices(["AAA"], date(2024, 1, 1), date(2024, 1, 3))

    assert list(result.columns) == ["AAA"]
    assert len(result) == 3
    assert result["AAA"].iloc[0] == 10.0
    assert result["AAA"].iloc[-1] == 12.0


def test_fake_source_get_prices_slices_by_date(fake_source):
    fake_source.prices["AAA"] = pd.Series(
        [10.0, 11.0, 12.0, 13.0],
        index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    result = fake_source.get_prices(["AAA"], date(2024, 1, 2), date(2024, 1, 3))

    assert len(result) == 2
    assert result["AAA"].iloc[0] == 11.0
    assert result["AAA"].iloc[1] == 12.0


def test_fake_source_missing_ticker_raises(fake_source):
    with pytest.raises(KeyError, match="MISSING"):
        fake_source.get_prices(["MISSING"], date(2024, 1, 1), date(2024, 1, 2))


def test_fake_source_get_fx(fake_source):
    fake_source.fx["USDCAD"] = pd.Series(
        [1.35, 1.36, 1.37],
        index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    )
    result = fake_source.get_fx("USD", "CAD", date(2024, 1, 1), date(2024, 1, 3))

    assert len(result) == 3
    assert result.iloc[0] == 1.35


def test_fake_source_classification(fake_source):
    fake_source.classifications["AAPL"] = {
        "quote_type": "EQUITY",
        "country": "United States",
        "sector": "Technology",
        "currency": "USD",
    }
    result = fake_source.get_classification("AAPL")
    assert result["sector"] == "Technology"
    assert result["currency"] == "USD"


def test_fake_source_fund_snapshot_empty_by_default(fake_source):
    result = fake_source.get_fund_snapshot("XAW.TO")
    assert result.ticker == "XAW.TO"
    assert not result.geography
    assert not result.sector
    assert not result.asset_class
