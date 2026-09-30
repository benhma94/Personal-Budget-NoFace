from __future__ import annotations

import json

from journal_entry.rules import learn, seed_rules, suggest
from journal_entry.store import Store


def _rule(id_, pattern, category, note="", match_type="contains"):
    return {"id": id_, "pattern": pattern, "match_type": match_type, "category": category,
             "note": note, "hits": 0, "last_used": None}


def test_suggest_prefers_more_specific_pattern():
    rules = [_rule(1, "UBER", "Transit", "Uber"), _rule(2, "UBER EATS", "Food", "Food")]
    result = suggest("UBER EATS TORONTO", rules)
    assert result.category == "Food"
    assert result.rule_id == 2


def test_suggest_case_insensitive_contains():
    rules = [_rule(1, "example market", "Food", "Groceries")]
    result = suggest("EXAMPLE MARKET #1042", rules)
    assert result.category == "Food"


def test_suggest_returns_none_when_no_match():
    assert suggest("MYSTERY MERCHANT", [_rule(1, "UBER", "Transit")]) is None


def test_suggest_regex_match_type():
    rules = [_rule(1, r"^PC\s*\d+$", "Shopping", "PC Purchase", match_type="regex")]
    assert suggest("PC 1234", rules).category == "Shopping"
    assert suggest("PCX 1234", rules) is None


def test_learn_creates_rule_reusable_by_suggest(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    learn(store, "UBER *EATS", "Food", "Food")
    rules = store.list_rules()
    assert suggest("UBER *EATS TORONTO", rules).category == "Food"
    store.close()


def test_learn_overwrites_existing_pattern(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    learn(store, "UBER", "Transit", "Uber")
    learn(store, "UBER", "Entertainment", "Fun")
    rules = store.list_rules()
    assert len(rules) == 1
    assert rules[0]["category"] == "Entertainment"
    store.close()


def test_learn_ignores_blank_pattern(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    learn(store, "   ", "Food", "x")
    assert store.list_rules() == []
    store.close()


def test_seed_rules_only_applied_once(tmp_path):
    rules = [["EXAMPLE MARKET", "Food", "Groceries"]]
    (tmp_path / "merchant_seed_rules.json").write_text(json.dumps(rules), encoding="utf-8")
    store = Store(tmp_path / "journal.sqlite")
    added = seed_rules(store)
    assert added == 1
    assert suggest("EXAMPLE MARKET #1234", store.list_rules()).category == "Food"

    added_again = seed_rules(store)
    assert added_again == 0
    assert len(store.list_rules()) == 1
    store.close()


def test_seed_rules_are_optional(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    assert seed_rules(store) == 0
    assert store.list_rules() == []
    store.close()
