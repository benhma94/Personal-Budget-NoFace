"""Tests for TWR computations and blended benchmark returns."""
from __future__ import annotations

from datetime import date
import math
import numpy as np
import pandas as pd
import pytest

from portfolio_tracker.returns import (
    cash_flow_matched_values,
    daily_portfolio_value_cad,
    daily_twr,
    money_weighted_return,
    period_twr,
    annualize_return,
    blended_benchmark_daily_returns,
    xirr,
)


# ----- daily_portfolio_value_cad -----

def test_value_single_position():
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    units = pd.DataFrame({"AAA": [10, 10, 10]}, index=dates)
    prices = pd.DataFrame({"AAA": [5.0, 6.0, 7.0]}, index=dates)
    cash = pd.Series([100, 100, 100], index=dates, dtype=float)

    value = daily_portfolio_value_cad(units, prices, cash)
    assert value.iloc[0] == 150  # 10*5 + 100
    assert value.iloc[1] == 160
    assert value.iloc[2] == 170


def test_value_multi_position():
    dates = pd.date_range("2024-01-01", "2024-01-02", freq="D")
    units = pd.DataFrame({"AAA": [10, 10], "BBB": [4, 4]}, index=dates)
    prices = pd.DataFrame({"AAA": [5.0, 6.0], "BBB": [10.0, 11.0]}, index=dates)
    cash = pd.Series([0, 0], index=dates, dtype=float)

    value = daily_portfolio_value_cad(units, prices, cash)
    assert value.iloc[0] == 10 * 5 + 4 * 10  # 90
    assert value.iloc[1] == 10 * 6 + 4 * 11  # 104


# ----- daily_twr -----

def test_twr_zero_when_value_flat_no_flows():
    dates = pd.date_range("2024-01-01", "2024-01-05", freq="D")
    values = pd.Series([100.0] * 5, index=dates)
    flows = pd.Series([0.0] * 5, index=dates)

    twr = daily_twr(values, flows)
    # First day undefined → NaN; rest should be 0
    assert math.isnan(twr.iloc[0]) or twr.iloc[0] == 0
    assert twr.iloc[1:].abs().max() < 1e-12


def test_twr_pure_contribution_yields_zero_return():
    """Contribute $50 on day 2 with no price movement → TWR = 0 (Modified Dietz, start-of-day flow)."""
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    values = pd.Series([100.0, 150.0, 150.0], index=dates)
    flows = pd.Series([0.0, 50.0, 0.0], index=dates)

    twr = daily_twr(values, flows)
    # Day 2 factor: (150 - 50) / 100 = 1.0 → return = 0
    assert twr.iloc[1] == pytest.approx(0.0)
    assert twr.iloc[2] == pytest.approx(0.0)


def test_twr_pure_appreciation():
    dates = pd.date_range("2024-01-01", "2024-01-02", freq="D")
    values = pd.Series([100.0, 110.0], index=dates)
    flows = pd.Series([0.0, 0.0], index=dates)

    twr = daily_twr(values, flows)
    assert twr.iloc[1] == pytest.approx(0.10)


# ----- period_twr -----

def test_period_twr_compounds_daily_returns():
    dates = pd.date_range("2024-01-01", "2024-01-04", freq="D")
    daily = pd.Series([float("nan"), 0.01, 0.02, -0.005], index=dates)
    # Expected: (1.01)*(1.02)*(0.995) - 1
    expected = 1.01 * 1.02 * 0.995 - 1

    result = period_twr(daily, date(2024, 1, 1), date(2024, 1, 4))
    assert result == pytest.approx(expected)


def test_period_twr_respects_range():
    dates = pd.date_range("2024-01-01", "2024-01-04", freq="D")
    daily = pd.Series([float("nan"), 0.01, 0.02, -0.005], index=dates)

    # Just 01-02 and 01-03
    result = period_twr(daily, date(2024, 1, 2), date(2024, 1, 3))
    assert result == pytest.approx(1.01 * 1.02 - 1)


# ----- annualize_return -----

def test_annualize_one_year_is_identity():
    assert annualize_return(0.10, 365) == pytest.approx(0.10)


def test_annualize_half_year_doubles_compounded():
    # 10% over ~half a year → (1.10)^2 - 1 ≈ 21%
    result = annualize_return(0.10, 182)
    expected = (1.10) ** (365 / 182) - 1
    assert result == pytest.approx(expected)


# ----- money-weighted return / XIRR -----

def test_xirr_one_year_ten_percent_return():
    flows = pd.Series(
        [-100.0, 110.0],
        index=pd.to_datetime(["2024-01-01", "2024-12-31"]),
    )

    assert xirr(flows) == pytest.approx(0.10)


def test_money_weighted_return_neutralizes_contribution():
    dates = pd.to_datetime(["2024-01-01", "2024-07-01", "2024-12-31"])
    values = pd.Series([100.0, 200.0, 200.0], index=dates)
    external = pd.Series([100.0, 100.0, 0.0], index=dates)

    period, annualized = money_weighted_return(
        values, external, date(2024, 1, 1), date(2024, 12, 31)
    )

    assert period == pytest.approx(0.0, abs=1e-9)
    assert annualized == pytest.approx(0.0, abs=1e-9)


def test_money_weighted_custom_window_uses_opening_value():
    dates = pd.to_datetime(["2023-12-31", "2024-01-01", "2024-12-31"])
    values = pd.Series([100.0, 100.0, 110.0], index=dates)
    external = pd.Series(0.0, index=dates)

    period, annualized = money_weighted_return(
        values, external, date(2024, 1, 1), date(2024, 12, 31)
    )

    assert period == pytest.approx(0.10)
    assert annualized == pytest.approx(0.10)


def test_cash_flow_matched_benchmark_invests_start_of_day_flow():
    dates = pd.to_datetime(["2024-01-01", "2024-01-02"])
    returns = pd.Series([float("nan"), 0.10], index=dates)
    external = pd.Series([100.0, 0.0], index=dates)

    values = cash_flow_matched_values(returns, external)

    assert values.iloc[0] == pytest.approx(100.0)
    assert values.iloc[1] == pytest.approx(110.0)


# ----- blended_benchmark_daily_returns -----

def test_blended_benchmark_daily_rebalanced():
    """50/50 blend of two assets, each returning a known pattern."""
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    a = pd.Series([100.0, 110.0, 121.0], index=dates)  # +10%, +10%
    b = pd.Series([50.0, 50.0, 50.0], index=dates)  # flat

    blend = [("A", 0.5), ("B", 0.5)]
    prices = {"A": a, "B": b}

    blended = blended_benchmark_daily_returns(blend, prices)

    # Day 2: 0.5 * 0.10 + 0.5 * 0 = 0.05
    assert blended.iloc[1] == pytest.approx(0.05)
    # Day 3: 0.5 * 0.10 + 0.5 * 0 = 0.05
    assert blended.iloc[2] == pytest.approx(0.05)


def test_blended_benchmark_single_component():
    dates = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    a = pd.Series([100.0, 110.0, 99.0], index=dates)
    blend = [("A", 1.0)]
    prices = {"A": a}

    blended = blended_benchmark_daily_returns(blend, prices)
    assert blended.iloc[1] == pytest.approx(0.10)
    assert blended.iloc[2] == pytest.approx(-0.10)
