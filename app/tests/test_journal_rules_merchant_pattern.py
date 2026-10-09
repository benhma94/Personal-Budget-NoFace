from __future__ import annotations

from journal_entry.rules import merchant_pattern


def test_strips_trailing_store_number():
    assert merchant_pattern("EXAMPLE MARKET #1042") == "EXAMPLE MARKET"


def test_strips_trailing_long_digit_run():
    assert merchant_pattern("SQ *COFFEE SHOP 1234567") == "SQ *COFFEE SHOP"


def test_leaves_short_digit_runs_alone():
    assert merchant_pattern("7-ELEVEN") == "7-ELEVEN"


def test_leaves_plain_description_unchanged():
    assert merchant_pattern("PAYROLL DEPOSIT") == "PAYROLL DEPOSIT"


def test_falls_back_to_original_when_stripping_empties_it():
    assert merchant_pattern("123456789") == "123456789"
