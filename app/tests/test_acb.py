"""Tests for ACB (Adjusted Cost Base) computation."""
from __future__ import annotations

from datetime import date
import pandas as pd

from portfolio_tracker.acb import compute_acb


def _txns(rows):
    """Build a minimal transactions DataFrame for testing."""
    return pd.DataFrame(
        rows,
        columns=["date", "account", "ticker", "type", "units",
                 "price_native", "currency", "cash_native", "fees", "note"],
    )


_CAD_FX = {"CAD": lambda _ts: 1.0}
_USD_FX = {"CAD": lambda _ts: 1.0, "USD": lambda _ts: 1.35}


def test_single_buy():
    txns = _txns([
        (date(2024, 1, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 65.0, "CAD", -6500.0, 0.0, ""),
    ])
    result = compute_acb(txns, _CAD_FX)
    assert "ZSP.TO" in result
    r = result["ZSP.TO"]
    assert abs(r["acb_per_unit"] - 65.0) < 1e-9
    assert abs(r["total_acb"] - 6500.0) < 1e-9
    assert abs(r["total_units"] - 100.0) < 1e-9


def test_multiple_buys_blended_average():
    txns = _txns([
        (date(2024, 1, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 60.0, "CAD", -6000.0, 0.0, ""),
        (date(2024, 2, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 70.0, "CAD", -7000.0, 0.0, ""),
    ])
    result = compute_acb(txns, _CAD_FX)
    r = result["ZSP.TO"]
    assert abs(r["acb_per_unit"] - 65.0) < 1e-9  # (6000+7000) / 200
    assert abs(r["total_acb"] - 13000.0) < 1e-9
    assert abs(r["total_units"] - 200.0) < 1e-9


def test_buy_then_partial_sell():
    txns = _txns([
        (date(2024, 1, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 60.0, "CAD", -6000.0, 0.0, ""),
        (date(2024, 3, 1), "TFSA|1", "ZSP.TO", "SELL", -50.0, 80.0, "CAD", 4000.0, 0.0, ""),
    ])
    result = compute_acb(txns, _CAD_FX)
    r = result["ZSP.TO"]
    assert abs(r["acb_per_unit"] - 60.0) < 1e-9   # unchanged after partial sell
    assert abs(r["total_acb"] - 3000.0) < 1e-9     # 50 * 60
    assert abs(r["total_units"] - 50.0) < 1e-9


def test_stock_split_zero_price():
    """2-for-1 split arrives as BUY at price=0; ACB per unit should halve."""
    txns = _txns([
        (date(2024, 1, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 60.0, "CAD", -6000.0, 0.0, ""),
        (date(2024, 6, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 0.0, "CAD", 0.0, 0.0, "split"),
    ])
    result = compute_acb(txns, _CAD_FX)
    r = result["ZSP.TO"]
    assert abs(r["total_units"] - 200.0) < 1e-9
    assert abs(r["total_acb"] - 6000.0) < 1e-9     # cost unchanged
    assert abs(r["acb_per_unit"] - 30.0) < 1e-9    # 6000 / 200


def test_full_sell_excluded():
    """Fully sold ticker must not appear in the result."""
    txns = _txns([
        (date(2024, 1, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 60.0, "CAD", -6000.0, 0.0, ""),
        (date(2024, 6, 1), "TFSA|1", "ZSP.TO", "SELL", -100.0, 80.0, "CAD", 8000.0, 0.0, ""),
    ])
    result = compute_acb(txns, _CAD_FX)
    assert "ZSP.TO" not in result


def test_usd_buy_converted_to_cad():
    """USD-denominated purchase must convert to CAD using fx_callables."""
    txns = _txns([
        (date(2024, 1, 1), "TFSA|1", "VTI", "BUY", 10.0, 200.0, "USD", -2000.0, 0.0, ""),
    ])
    result = compute_acb(txns, _USD_FX)
    r = result["VTI"]
    assert abs(r["acb_per_unit"] - 200.0 * 1.35) < 1e-9
    assert abs(r["total_acb"] - 10.0 * 200.0 * 1.35) < 1e-9


def test_non_buy_sell_ignored():
    """DIV / CONTRIB / WITHDRAW / FEE / INTERNAL rows must not affect ACB."""
    txns = _txns([
        (date(2024, 1, 1), "TFSA|1", "ZSP.TO", "BUY", 100.0, 60.0, "CAD", -6000.0, 0.0, ""),
        (date(2024, 2, 1), "TFSA|1", "", "DIV", 0.0, 0.0, "CAD", 50.0, 0.0, "dividend"),
        (date(2024, 3, 1), "TFSA|1", "", "CONTRIB", 0.0, 0.0, "CAD", 1000.0, 0.0, "deposit"),
    ])
    result = compute_acb(txns, _CAD_FX)
    r = result["ZSP.TO"]
    assert abs(r["total_acb"] - 6000.0) < 1e-9
