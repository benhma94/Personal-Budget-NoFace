"""Ticker normalization settings kept separate from personal holdings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_SYMBOLS: dict[str, Any] = json.loads(
    Path(__file__).with_name("symbols.example.json").read_text(encoding="utf-8")
)


def load_symbols(path: str | Path | None = None) -> dict[str, Any]:
    explicit = path is not None or bool(os.environ.get("PORTFOLIO_SYMBOLS_PATH"))
    source = Path(path or os.environ.get("PORTFOLIO_SYMBOLS_PATH") or "data/symbols.json")
    if source.is_file():
        overrides = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise ValueError(f"Symbol settings must be a JSON object: {source}")
        unknown = set(overrides) - set(DEFAULT_SYMBOLS)
        if unknown:
            raise ValueError(f"Unknown symbol setting(s): {', '.join(sorted(unknown))}")
    elif explicit:
        raise FileNotFoundError(f"Symbol settings not found: {source}")
    else:
        overrides = {}

    config = {**DEFAULT_SYMBOLS, **overrides}
    for field in ("us_listed", "internal_tickers"):
        names = config[field]
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not name.strip() for name in names
        ):
            raise ValueError(f"Symbol setting {field} must be a list of tickers")
    aliases = config["ticker_overrides"]
    if not isinstance(aliases, dict) or any(
        not isinstance(name, str) or not name.strip()
        or not isinstance(target, str) or not target.strip()
        for name, target in aliases.items()
    ):
        raise ValueError("Symbol setting ticker_overrides must map tickers to tickers")
    return {
        "us_listed": set(config["us_listed"]),
        "internal_tickers": set(config["internal_tickers"]),
        "ticker_overrides": aliases,
    }


SYMBOLS = load_symbols()
