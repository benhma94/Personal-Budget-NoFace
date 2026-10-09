"""Public defaults and optional local chart-of-accounts configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_PROFILE: dict[str, Any] = json.loads(
    Path(__file__).with_name("profile.example.json").read_text(encoding="utf-8")
)


def _names(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(
        not isinstance(name, str) or not name.strip() for name in value
    ):
        raise ValueError(f"Profile {field} must be a nonempty list of names")
    names = tuple(value)
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError(f"Profile {field} contains duplicate names")
    return names


def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    """Load local overrides; a missing default path uses synthetic examples."""
    explicit = path is not None or bool(os.environ.get("FINANCE_PROFILE_PATH"))
    source = Path(path or os.environ.get("FINANCE_PROFILE_PATH") or "data/profile.json")
    if source.is_file():
        overrides = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise ValueError(f"Profile must be a JSON object: {source}")
        unknown = set(overrides) - set(DEFAULT_PROFILE)
        if unknown:
            raise ValueError(f"Unknown profile field(s): {', '.join(sorted(unknown))}")
    elif explicit:
        raise FileNotFoundError(f"Profile not found: {source}")
    else:
        overrides = {}

    profile = {**DEFAULT_PROFILE, **overrides}
    groups = {
        field: _names(profile[field], field)
        for field in (
            "income_categories", "expense_categories", "asset_accounts",
            "liability_accounts",
        )
    }
    all_names = [name for names in groups.values() for name in names]
    if len({name.casefold() for name in all_names}) != len(all_names):
        raise ValueError("Profile names must be unique across categories and accounts")

    aliases = profile["category_aliases"]
    if not isinstance(aliases, dict) or any(
        not isinstance(alias, str) or not alias.strip()
        or not isinstance(target, str) or target not in (
            *groups["income_categories"], *groups["expense_categories"]
        )
        for alias, target in aliases.items()
    ):
        raise ValueError("Profile category_aliases must map names to categories")

    close = profile["month_close"]
    if not isinstance(close, dict) or set(close) != {"investment", "transit"}:
        raise ValueError("Profile month_close must define investment and transit")
    for kind, config in close.items():
        required = {"label", "account", "category", "description", "note"}
        if not isinstance(config, dict) or set(config) != required or any(
            not isinstance(config[key], str) or not config[key].strip() for key in required
        ):
            raise ValueError(f"Profile month_close.{kind} has invalid fields")
        if config["account"] not in groups["asset_accounts"]:
            raise ValueError(f"Profile month_close.{kind}.account is not an asset account")
        if config["category"] not in (
            *groups["income_categories"], *groups["expense_categories"], "Investment Income"
        ):
            raise ValueError(f"Profile month_close.{kind}.category is not a category")

    return {**groups, "category_aliases": aliases, "month_close": close}


PROFILE = load_profile()
