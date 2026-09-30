from __future__ import annotations

import json

import pytest

from portfolio_tracker.symbols import load_symbols


def test_local_symbol_settings_replace_example_lists(tmp_path):
    path = tmp_path / "symbols.json"
    path.write_text(json.dumps({
        "us_listed": ["CUSTOM"],
        "ticker_overrides": {"OLD": "NEW"},
        "internal_tickers": ["NEW"],
    }), encoding="utf-8")

    settings = load_symbols(path)

    assert settings["us_listed"] == {"CUSTOM"}
    assert settings["ticker_overrides"] == {"OLD": "NEW"}
    assert settings["internal_tickers"] == {"NEW"}


def test_explicit_symbol_settings_path_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError, match="Symbol settings not found"):
        load_symbols(tmp_path / "missing.json")
