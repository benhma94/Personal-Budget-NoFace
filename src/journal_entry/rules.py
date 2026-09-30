"""Merchant-pattern rules: suggest categories and learn from reviewed transactions."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .store import Store


@dataclass(frozen=True)
class Suggestion:
    category: str
    note: str
    rule_id: int


def _matches(text: str, rule: dict[str, Any]) -> bool:
    pattern = rule["pattern"]
    if rule["match_type"] == "regex":
        return re.search(pattern, text, re.IGNORECASE) is not None
    return pattern.casefold() in text


def suggest(description: str, rules: list[dict[str, Any]]) -> Suggestion | None:
    """Best matching rule for `description`, preferring the most specific match."""
    text = description.casefold()
    candidates = [rule for rule in rules if _matches(text, rule)]
    if not candidates:
        return None
    best = max(candidates, key=lambda r: len(r["pattern"]))
    return Suggestion(category=best["category"], note=best["note"] or "", rule_id=best["id"])


def learn(store: Store, pattern: str, category: str, note: str, match_type: str = "contains") -> None:
    pattern = pattern.strip()
    if pattern:
        store.upsert_rule(pattern, match_type, category, note)


_TRAILING_REFERENCE = re.compile(r"\s*[#*]?\d{3,}\S*$")


def merchant_pattern(description: str) -> str:
    """A conservative merchant fragment for learning a new rule from a
    description: strips a trailing reference/store number (e.g. "EXAMPLE MARKET
    #1042" -> "EXAMPLE MARKET") so the rule generalizes to the next statement.
    Rules are editable in the UI, so this only needs to be a fair guess.
    """
    text = description.strip()
    stripped = _TRAILING_REFERENCE.sub("", text).strip()
    return stripped or text


def seed_rules(store: Store) -> int:
    """Load optional private seed rules beside the database when it is empty."""
    if store.list_rules():
        return 0
    path = store.path.parent / "merchant_seed_rules.json"
    if not path.is_file():
        return 0
    rules = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rules, list) or any(
        not isinstance(rule, list)
        or len(rule) != 3
        or not all(isinstance(value, str) for value in rule)
        for rule in rules
    ):
        raise ValueError(f"Invalid merchant seed rules in {path}")
    for pattern, category, note in rules:
        store.upsert_rule(pattern, "contains", category, note)
    return len(rules)
