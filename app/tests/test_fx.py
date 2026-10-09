"""Tests for FX conversion."""
from __future__ import annotations

from datetime import date
import pandas as pd
import pytest

from portfolio_tracker.fx import convert_prices_to_cad, fx_series_to_cad


def test_convert_native_cad_passthrough(fake_source):
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    prices = pd.DataFrame({"VBAL.TO": [10.0, 11.0, 12.0]}, index=dates)
    currencies = {"VBAL.TO": "CAD"}

    result = convert_prices_to_cad(prices, currencies, fake_source)
    pd.testing.assert_series_equal(result["VBAL.TO"], prices["VBAL.TO"])


def test_convert_usd_to_cad(fake_source):
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    prices = pd.DataFrame({"AAPL": [100.0, 110.0, 120.0]}, index=dates)
    currencies = {"AAPL": "USD"}
    fake_source.fx["USDCAD"] = pd.Series([1.35, 1.36, 1.37], index=dates)

    result = convert_prices_to_cad(prices, currencies, fake_source)
    assert result["AAPL"].iloc[0] == pytest.approx(135.0)
    assert result["AAPL"].iloc[1] == pytest.approx(149.6)
    assert result["AAPL"].iloc[2] == pytest.approx(164.4)


def test_convert_missing_fx_raises(fake_source):
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    prices = pd.DataFrame({"AAPL": [100.0, 110.0, 120.0]}, index=dates)
    currencies = {"AAPL": "USD"}
    # No fx in fake_source

    with pytest.raises(KeyError, match="USD"):
        convert_prices_to_cad(prices, currencies, fake_source)


def test_fx_series_to_cad_for_cad_returns_ones():
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    result = fx_series_to_cad("CAD", date(2024, 1, 1), date(2024, 1, 3), source=None)
    assert (result == 1.0).all()
    assert len(result) == 3
