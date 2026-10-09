from __future__ import annotations

from datetime import date

from journal_entry.aggregate import build_posting_lines


def _tx(account, amount, category, note="", fp="fp"):
    return {"fingerprint": fp, "account": account, "amount": amount, "category": category, "note": note}


def test_groups_negative_amounts_as_category_debit_account_credit():
    rows = [
        _tx("Rewards Card", -41.20, "Food", "Groceries", "a"),
        _tx("Rewards Card", -18.75, "Food", "Groceries", "b"),
    ]
    lines, skipped = build_posting_lines(rows, [], date(2026, 8, 30))
    assert skipped == []
    assert len(lines) == 1
    line = lines[0]
    assert line.debit == "Food"
    assert line.credit == "Rewards Card"
    assert line.amount == 59.95
    assert line.note == "Groceries"


def test_groups_positive_amounts_as_account_debit_category_credit():
    rows = [_tx("Primary Checking", 1200.0, "Salary", "Pay", "a")]
    lines, _ = build_posting_lines(rows, [], date(2026, 8, 30))
    assert lines[0].debit == "Primary Checking"
    assert lines[0].credit == "Salary"


def test_separate_groups_stay_separate_lines():
    rows = [
        _tx("Rewards Card", -41.20, "Food", "", "a"),
        _tx("Rewards Card", -1.25, "Transit", "", "b"),
        _tx("Credit Card", -11.85, "Food", "", "c"),
    ]
    lines, _ = build_posting_lines(rows, [], date(2026, 8, 30))
    keys = {(line.debit, line.credit) for line in lines}
    assert keys == {("Food", "Rewards Card"), ("Transit", "Rewards Card"), ("Food", "Credit Card")}


def test_rows_without_category_are_skipped_not_dropped_silently():
    rows = [_tx("Cash", -10, None, "", "a"), _tx("Cash", -5, "Food", "", "b")]
    lines, skipped = build_posting_lines(rows, [], date(2026, 8, 30))
    assert len(skipped) == 1
    assert skipped[0]["fingerprint"] == "a"
    assert len(lines) == 1


def test_zero_amount_rows_are_skipped():
    rows = [_tx("Cash", 0, "Food", "", "a")]
    _, skipped = build_posting_lines(rows, [], date(2026, 8, 30))
    assert len(skipped) == 1


def test_different_notes_on_same_account_pair_stay_separate_lines():
    rows = [
        _tx("Cash", -932, "Shopping", "Ikea", "a"),
        _tx("Cash", -60, "Shopping", "Eric", "b"),
        _tx("Cash", -8, "Shopping", "Ikea ", "c"),
    ]
    lines, _ = build_posting_lines(rows, [], date(2026, 8, 30))
    by_note = {line.note: line.amount for line in lines}
    assert by_note == {"Ikea": 940, "Eric": 60}


def test_note_falls_back_to_category_when_blank():
    rows = [_tx("Rewards Card", -1, "Food", "", "a")]
    lines, _ = build_posting_lines(rows, [], date(2026, 8, 30))
    assert lines[0].note == "Food"


def test_confirmed_transfer_becomes_single_line():
    transfer = {
        "outgoing": {"account": "Brokerage Cash", "amount": -631.11},
        "incoming": {"account": "Rewards Card", "amount": 631.11},
        "note": "Pay Bill",
    }
    lines, skipped = build_posting_lines([], [transfer], date(2026, 8, 30))
    assert skipped == []
    assert len(lines) == 1
    line = lines[0]
    assert line.debit == "Rewards Card"
    assert line.credit == "Brokerage Cash"
    assert line.amount == 631.11
    assert line.note == "Pay Bill"


def test_transfer_note_defaults_when_missing():
    transfer = {"outgoing": {"account": "A", "amount": -10}, "incoming": {"account": "B", "amount": 10}}
    lines, _ = build_posting_lines([], [transfer], date(2026, 8, 30))
    assert lines[0].note == "Transfer"


def test_all_lines_carry_the_same_posting_date():
    rows = [_tx("Cash", -1, "Food", "", "a"), _tx("Cash", 1, "Salary", "", "b")]
    lines, _ = build_posting_lines(rows, [], date(2026, 8, 30))
    assert all(line.posting_date == date(2026, 8, 30) for line in lines)
