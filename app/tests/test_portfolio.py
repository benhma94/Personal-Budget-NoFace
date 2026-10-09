"""Tests for daily portfolio reconstruction from a transactions log."""
from __future__ import annotations

from datetime import date
import pandas as pd
import pytest

from portfolio_tracker.portfolio import (
    reconstruct_daily_units,
    daily_external_cash_flows_cad,
    daily_cash_balance_cad,
    interpolated_prices_from_transactions,
)


def _txn(
    date_,
    type_,
    ticker="",
    units=0.0,
    price_native=0.0,
    currency="CAD",
    cash_native=0.0,
    fees=0.0,
):
    return {
        "date": pd.Timestamp(date_),
        "account": "TestAcct",
        "ticker": ticker,
        "type": type_,
        "units": units,
        "price_native": price_native,
        "currency": currency,
        "cash_native": cash_native,
        "fees": fees,
        "note": "",
    }


def _to_txn_df(rows):
    return pd.DataFrame(rows)


# ----- reconstruct_daily_units -----

def test_units_forward_filled_after_buy():
    txns = _to_txn_df([
        _txn("2024-01-02", "BUY", ticker="AAA", units=10, price_native=5, cash_native=-50),
    ])
    units = reconstruct_daily_units(txns, date(2024, 1, 1), date(2024, 1, 5))

    assert units.loc[pd.Timestamp("2024-01-01"), "AAA"] == 0
    assert units.loc[pd.Timestamp("2024-01-02"), "AAA"] == 10
    assert units.loc[pd.Timestamp("2024-01-05"), "AAA"] == 10


def test_units_decrease_on_sell():
    txns = _to_txn_df([
        _txn("2024-01-02", "BUY", ticker="AAA", units=10, price_native=5, cash_native=-50),
        _txn("2024-01-04", "SELL", ticker="AAA", units=-3, price_native=6, cash_native=18),
    ])
    units = reconstruct_daily_units(txns, date(2024, 1, 1), date(2024, 1, 5))

    assert units.loc[pd.Timestamp("2024-01-03"), "AAA"] == 10
    assert units.loc[pd.Timestamp("2024-01-04"), "AAA"] == 7
    assert units.loc[pd.Timestamp("2024-01-05"), "AAA"] == 7


def test_units_multi_ticker():
    txns = _to_txn_df([
        _txn("2024-01-02", "BUY", ticker="AAA", units=10, price_native=5, cash_native=-50),
        _txn("2024-01-03", "BUY", ticker="BBB", units=4, price_native=10, cash_native=-40),
    ])
    units = reconstruct_daily_units(txns, date(2024, 1, 1), date(2024, 1, 5))

    assert set(units.columns) == {"AAA", "BBB"}
    assert units.loc[pd.Timestamp("2024-01-03"), "AAA"] == 10
    assert units.loc[pd.Timestamp("2024-01-03"), "BBB"] == 4


def test_contrib_withdraw_do_not_change_units():
    txns = _to_txn_df([
        _txn("2024-01-02", "CONTRIB", cash_native=1000),
        _txn("2024-01-04", "WITHDRAW", cash_native=-200),
    ])
    units = reconstruct_daily_units(txns, date(2024, 1, 1), date(2024, 1, 5))
    # No tickers held → columns may be empty, but no error
    assert units.shape[0] == 5  # 5 days


# ----- daily_external_cash_flows_cad -----

def test_external_cf_only_contrib_withdraw():
    txns = _to_txn_df([
        _txn("2024-01-02", "CONTRIB", cash_native=1000, currency="CAD"),
        _txn("2024-01-03", "BUY", ticker="AAA", units=10, price_native=5, currency="CAD", cash_native=-50),
        _txn("2024-01-04", "DIV", ticker="AAA", cash_native=2, currency="CAD"),
        _txn("2024-01-05", "WITHDRAW", cash_native=-300, currency="CAD"),
    ])
    fx_to_cad = {"CAD": lambda d: 1.0}
    cf = daily_external_cash_flows_cad(txns, date(2024, 1, 1), date(2024, 1, 5), fx_to_cad)

    assert cf.loc[pd.Timestamp("2024-01-01")] == 0
    assert cf.loc[pd.Timestamp("2024-01-02")] == 1000
    assert cf.loc[pd.Timestamp("2024-01-03")] == 0  # BUY is internal
    assert cf.loc[pd.Timestamp("2024-01-04")] == 0  # DIV is internal
    assert cf.loc[pd.Timestamp("2024-01-05")] == -300


def test_external_cf_fx_converted():
    txns = _to_txn_df([
        _txn("2024-01-02", "CONTRIB", cash_native=1000, currency="USD"),
    ])
    fx_to_cad = {"USD": lambda d: 1.35}
    cf = daily_external_cash_flows_cad(txns, date(2024, 1, 1), date(2024, 1, 3), fx_to_cad)
    assert cf.loc[pd.Timestamp("2024-01-02")] == pytest.approx(1350.0)


def test_external_cf_uses_explicit_in_kind_transfer_value():
    txns = _to_txn_df([
        _txn("2024-01-02", "BUY", ticker="AAA", units=10, cash_native=0, currency="CAD"),
    ])
    txns["external_flow_native"] = [750.0]
    fx_to_cad = {"CAD": lambda d: 1.0}

    cf = daily_external_cash_flows_cad(
        txns, date(2024, 1, 1), date(2024, 1, 3), fx_to_cad
    )

    assert cf.loc[pd.Timestamp("2024-01-02")] == 750.0


# ----- daily_cash_balance_cad -----

def test_cash_balance_accumulates_all_flows():
    """CONTRIB +, BUY -, DIV +, SELL +, WITHDRAW -, FEE - all affect cash."""
    txns = _to_txn_df([
        _txn("2024-01-02", "CONTRIB", cash_native=1000, currency="CAD"),
        _txn("2024-01-03", "BUY", ticker="AAA", units=10, price_native=50, currency="CAD", cash_native=-500),
        _txn("2024-01-04", "DIV", ticker="AAA", cash_native=20, currency="CAD"),
        _txn("2024-01-05", "SELL", ticker="AAA", units=-2, price_native=60, currency="CAD", cash_native=120),
    ])
    fx_to_cad = {"CAD": lambda d: 1.0}
    cash = daily_cash_balance_cad(txns, date(2024, 1, 1), date(2024, 1, 6), fx_to_cad)

    assert cash.loc[pd.Timestamp("2024-01-01")] == 0
    assert cash.loc[pd.Timestamp("2024-01-02")] == 1000
    assert cash.loc[pd.Timestamp("2024-01-03")] == 500
    assert cash.loc[pd.Timestamp("2024-01-04")] == 520
    assert cash.loc[pd.Timestamp("2024-01-05")] == 640
    assert cash.loc[pd.Timestamp("2024-01-06")] == 640


# ----- interpolated_prices_from_transactions -----

def test_interpolated_price_linearly_bridges_known_trade_prices():
    """A $0.00 filler BUY between two priced trades doesn't break interpolation."""
    txns = _to_txn_df([
        _txn("2024-01-01", "BUY", ticker="PRIV", units=100, price_native=10.0, cash_native=-1000),
        _txn("2024-01-03", "BUY", ticker="PRIV", units=5, price_native=0.0, cash_native=0),
        _txn("2024-01-05", "SELL", ticker="PRIV", units=-50, price_native=12.0, cash_native=600),
    ])
    fx_to_cad = {"CAD": lambda d: 1.0}
    prices = interpolated_prices_from_transactions(
        txns, ["PRIV"], date(2024, 1, 1), date(2024, 1, 5), fx_to_cad
    )

    assert prices.loc[pd.Timestamp("2024-01-01"), "PRIV"] == pytest.approx(10.0)
    assert prices.loc[pd.Timestamp("2024-01-03"), "PRIV"] == pytest.approx(11.0)
    assert prices.loc[pd.Timestamp("2024-01-05"), "PRIV"] == pytest.approx(12.0)


def test_interpolated_price_holds_flat_outside_known_range():
    txns = _to_txn_df([
        _txn("2024-01-03", "BUY", ticker="PRIV", units=100, price_native=10.0, cash_native=-1000),
    ])
    fx_to_cad = {"CAD": lambda d: 1.0}
    prices = interpolated_prices_from_transactions(
        txns, ["PRIV"], date(2024, 1, 1), date(2024, 1, 5), fx_to_cad
    )

    assert prices.loc[pd.Timestamp("2024-01-01"), "PRIV"] == pytest.approx(10.0)
    assert prices.loc[pd.Timestamp("2024-01-05"), "PRIV"] == pytest.approx(10.0)


def test_interpolated_price_converts_fx_to_cad():
    txns = _to_txn_df([
        _txn("2024-01-01", "BUY", ticker="PRIV", units=100, price_native=10.0, currency="USD", cash_native=-1000),
    ])
    fx_to_cad = {"USD": lambda d: 1.35}
    prices = interpolated_prices_from_transactions(
        txns, ["PRIV"], date(2024, 1, 1), date(2024, 1, 2), fx_to_cad
    )

    assert prices.loc[pd.Timestamp("2024-01-01"), "PRIV"] == pytest.approx(13.5)


def test_interpolated_price_empty_when_no_priced_trades():
    txns = _to_txn_df([
        _txn("2024-01-01", "BUY", ticker="PRIV", units=100, price_native=0.0, cash_native=0),
    ])
    fx_to_cad = {"CAD": lambda d: 1.0}
    prices = interpolated_prices_from_transactions(
        txns, ["PRIV"], date(2024, 1, 1), date(2024, 1, 2), fx_to_cad
    )

    assert "PRIV" not in prices.columns
