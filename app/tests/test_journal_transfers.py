from __future__ import annotations

from journal_entry.transfers import find_matches


def _tx(fp, account, txn_date, amount, status="categorized"):
    return {"fingerprint": fp, "account": account, "txn_date": txn_date, "amount": amount,
            "description": "x", "status": status}


def test_matches_opposite_sign_equal_magnitude_across_accounts():
    txs = [
        _tx("out1", "Brokerage Cash", "2026-08-12", -631.11),
        _tx("in1", "Rewards Card", "2026-08-12", 631.11),
    ]
    matches = find_matches(txs)
    assert len(matches) == 1
    assert matches[0].outgoing["fingerprint"] == "out1"
    assert matches[0].incoming["fingerprint"] == "in1"
    assert matches[0].amount == 631.11


def test_does_not_match_same_account():
    txs = [_tx("out1", "Cash", "2026-08-12", -50), _tx("in1", "Cash", "2026-08-12", 50)]
    assert find_matches(txs) == []


def test_does_not_match_outside_window():
    txs = [
        _tx("out1", "Secondary Checking", "2026-08-01", -100),
        _tx("in1", "Credit Card", "2026-08-10", 100),
    ]
    assert find_matches(txs) == []


def test_ignores_posted_or_ignored_rows():
    txs = [
        _tx("out1", "Secondary Checking", "2026-08-01", -100, status="posted"),
        _tx("in1", "Credit Card", "2026-08-01", 100, status="new"),
    ]
    assert find_matches(txs) == []


def test_each_row_used_at_most_once_prefers_closest_date():
    txs = [
        _tx("out1", "Chequing", "2026-08-01", -100),
        _tx("in-far", "Credit", "2026-08-03", 100),
        _tx("in-near", "Credit", "2026-08-01", 100),
    ]
    matches = find_matches(txs)
    assert len(matches) == 1
    assert matches[0].incoming["fingerprint"] == "in-near"


def test_no_match_when_amounts_differ():
    txs = [_tx("out1", "Chequing", "2026-08-01", -100), _tx("in1", "Credit", "2026-08-01", 99.99)]
    assert find_matches(txs) == []
