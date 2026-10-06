"""Tests for CLI helpers — positions table assembly, exposure table assembly,
and the 'skip unidentifiable' filter that drops tickers Yahoo couldn't price.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from portfolio_tracker.cli import (
    _build_account_breakdown,
    _build_account_positions_table,
    _build_performance_table,
    _build_positions_table,
    _cash_position_record,
    _compute_account_acb,
    _effective_inception_date,
    _first_transaction_date,
    _parse_args,
    _write_payload_cache,
)


def test_default_workbook_is_in_project_root():
    assert _parse_args([]).workbook == "portfolio.xlsx"


def test_default_payload_output_path():
    assert _parse_args([]).payload_out == "data/portfolio_payload.json"


def test_write_payload_cache_wraps_payload_with_a_generation_timestamp(tmp_path):
    """finance_hub.portfolio_api reads this file to serve the Portfolio tab
    without re-running the (slow, LSEG-dependent) pipeline on every request."""
    path = tmp_path / "sub" / "portfolio_payload.json"

    _write_payload_cache({"total_value_cad": 100.0}, path)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["payload"] == {"total_value_cad": 100.0}
    assert "generated_at" in document


def test_inception_moves_forward_to_first_transaction():
    transactions = pd.DataFrame({"date": ["2021-06-15", "2021-07-01"]})
    first = _first_transaction_date(transactions)

    assert first == date(2021, 6, 15)
    assert _effective_inception_date(date(2018, 2, 14), first) == date(2021, 6, 15)


def test_inception_keeps_later_configured_date():
    assert _effective_inception_date(
        date(2022, 1, 1), date(2021, 6, 15)
    ) == date(2022, 1, 1)


def test_performance_table_annualizes_long_horizons_and_keeps_ytd_cumulative():
    dates = pd.to_datetime([
        "2022-06-30", "2023-06-30", "2024-06-30", "2025-06-30",
    ])
    portfolio = pd.Series([float("nan"), 0.10, 0.10, 0.10], index=dates)
    benchmark = pd.Series([float("nan"), 0.05, 0.05, 0.05], index=dates)

    table = _build_performance_table(
        portfolio, benchmark, date(2022, 6, 30), date(2025, 6, 30)
    ).set_index("Horizon")

    assert table.loc["YTD", "Return Basis"] == "Cumulative"
    assert table.loc["YTD", "Portfolio TWR"] == pytest.approx(0.10)
    assert table.loc["1Y", "Return Basis"] == "Annualized"
    assert table.loc["1Y", "Portfolio TWR"] == pytest.approx(0.10)
    expected_portfolio = (1.10 ** 3) ** (365 / 1096) - 1
    expected_benchmark = (1.05 ** 3) ** (365 / 1096) - 1
    assert table.loc["3Y", "Portfolio TWR"] == pytest.approx(expected_portfolio)
    assert table.loc["3Y", "Benchmark TWR"] == pytest.approx(expected_benchmark)
    assert table.loc["3Y", "Difference"] == pytest.approx(
        expected_portfolio - expected_benchmark
    )
    assert pd.isna(table.loc["YTD", "Portfolio Return"])


def test_performance_table_portfolio_return_is_money_weighted():
    dates = pd.to_datetime(["2023-12-31", "2024-01-01", "2024-12-31"])
    twr_daily = pd.Series([float("nan"), float("nan"), 0.10], index=dates)
    bench_daily = pd.Series([float("nan"), float("nan"), 0.05], index=dates)
    pv = pd.Series([100.0, 100.0, 110.0], index=dates)
    ext_cf = pd.Series([0.0, 0.0, 0.0], index=dates)

    table = _build_performance_table(
        twr_daily, bench_daily, date(2024, 1, 1), date(2024, 12, 31), pv, ext_cf
    ).set_index("Horizon")

    assert table.loc["Since-inception", "Portfolio Return"] == pytest.approx(0.10)


def test_account_positions_split_shared_ticker_and_reconcile():
    transactions = pd.DataFrame([
        {"date": "2024-01-02", "account": "TFSA|1", "ticker": "AAA", "type": "BUY",
         "units": 10.0, "price_native": 80.0, "currency": "CAD", "cash_native": -800.0},
        {"date": "2024-01-03", "account": "RRSP|2", "ticker": "AAA", "type": "BUY",
         "units": 5.0, "price_native": 90.0, "currency": "CAD", "cash_native": -450.0},
    ])
    positions = pd.DataFrame([
        {"ticker": "AAA", "units": 15.0, "value_cad": 1500.0, "weight": 1.0},
    ])

    result = _build_account_positions_table(
        transactions, positions, date(2024, 1, 1), date(2024, 1, 31), 1500.0
    )

    values = result.set_index("account_type")["value_cad"].to_dict()
    assert values == {"RRSP": 500.0, "TFSA": 1000.0}
    assert result["value_cad"].sum() == positions["value_cad"].sum()


def test_account_breakdown_adds_cash_and_acb_is_account_specific():
    transactions = pd.DataFrame([
        {"date": "2024-01-02", "account": "TFSA|1", "ticker": "AAA", "type": "BUY",
         "units": 10.0, "price_native": 80.0, "currency": "CAD", "cash_native": -800.0},
        {"date": "2024-01-03", "account": "RRSP|2", "ticker": "AAA", "type": "BUY",
         "units": 5.0, "price_native": 90.0, "currency": "CAD", "cash_native": -450.0},
    ])
    account_positions = pd.DataFrame([
        {"account_type": "TFSA", "value_cad": 1000.0},
        {"account_type": "RRSP", "value_cad": 500.0},
    ])

    breakdown = _build_account_breakdown(
        account_positions, {"TFSA": 100.0, "RRSP": 50.0}
    )
    assert {r["account_type"]: r["value_cad"] for r in breakdown} == {
        "RRSP": 550.0,
        "TFSA": 1100.0,
    }

    acb = _compute_account_acb(transactions, {"CAD": lambda _ts: 1.0})
    assert acb["TFSA"]["AAA"]["total_acb"] == 800.0
    assert acb["RRSP"]["AAA"]["total_acb"] == 450.0


def test_cash_position_record_ties_holdings_to_total():
    """Holdings previously excluded cash, so their sum never matched the headline
    total portfolio value whenever cash was nonzero. A CASH pseudo-position closes
    that gap."""
    record = _cash_position_record(250.0, total_value=1250.0)

    assert record["ticker"] == "CASH"
    assert record["value_cad"] == 250.0
    assert record["weight"] == pytest.approx(0.2)
    assert record["total_acb"] == 250.0
    assert record["accrued_gain_cad"] == 0.0
    assert record["accrued_gain_pct"] == 0.0
    assert "account_type" not in record


def test_cash_position_record_handles_negative_balance_and_account_type():
    record = _cash_position_record(-243.61, total_value=520984.88, account_type="RRSP")

    assert record["value_cad"] == -243.61
    assert record["units"] == -243.61
    assert record["account_type"] == "RRSP"


def test_cash_position_record_omits_negligible_balance():
    assert _cash_position_record(0.0, total_value=1000.0) is None
    assert _cash_position_record(0.001, total_value=1000.0) is None


def test_positions_table_drops_unpriceable_ticker():
    """Tickers with no price in prices_cad are unidentifiable — drop them entirely
    rather than emitting a zero-value row."""
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    units = pd.DataFrame({"AAPL": [10.0, 10.0, 10.0], "MYSTERY": [5.0, 5.0, 5.0]}, index=dates)
    prices_native = pd.DataFrame({"AAPL": [150.0, 151.0, 152.0]}, index=dates)
    prices_cad = pd.DataFrame({"AAPL": [200.0, 201.0, 202.0]}, index=dates)

    df = _build_positions_table(
        units=units,
        prices_native=prices_native,
        prices_cad=prices_cad,
        ticker_currencies={"AAPL": "USD"},
        total_value=2020.0,
    )

    assert "MYSTERY" not in df["ticker"].tolist()
    assert "AAPL" in df["ticker"].tolist()
