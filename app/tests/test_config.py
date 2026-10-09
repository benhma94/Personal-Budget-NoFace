"""Tests for workbook configuration migrations."""

from portfolio_tracker.config import _lseg_benchmark_ticker


def test_legacy_equity_benchmarks_are_replaced_with_lseg_indices():
    assert _lseg_benchmark_ticker("XIC.TO") == ".TRGSPTSE"
    assert _lseg_benchmark_ticker(".GSPTSE") == ".TRGSPTSE"
    assert _lseg_benchmark_ticker("^GSPC") == ".SPXTR"
    assert _lseg_benchmark_ticker("XAW.TO") == ".FTDLMSXNA"
    assert _lseg_benchmark_ticker("XBB.TO") == ".FT26045CADT"
