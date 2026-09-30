from __future__ import annotations

import sqlite3

import pytest

from journal_entry.store import Store


def test_existing_database_gets_additive_month_close_metadata(tmp_path):
    path = tmp_path / "journal.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE transactions (
               fingerprint TEXT PRIMARY KEY, account TEXT NOT NULL,
               txn_date TEXT NOT NULL, description TEXT NOT NULL,
               amount REAL NOT NULL, category TEXT, note TEXT,
               status TEXT NOT NULL DEFAULT 'new', transfer_peer TEXT,
               batch_id TEXT, imported_at TEXT NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO transactions VALUES
           ('old', 'Cash', '2026-08-01', 'existing', 1, NULL, NULL,
            'new', NULL, NULL, '2026-08-01T00:00:00')"""
    )
    conn.commit()
    conn.close()

    store = Store(path)
    try:
        row = store.get_transaction("old")
        assert row["origin"] == "csv"
        assert row["close_month"] is None
        assert row["close_kind"] is None
    finally:
        store.close()


def test_source_round_trip(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    assert store.get_source("sig1") is None

    store.save_source("sig1", "Secondary Checking export", "Secondary Checking",
                       {"date_col": "Date", "desc_col": "Details", "amount_col": "Amount"}, "%Y-%m-%d")
    source = store.get_source("sig1")
    assert source["label"] == "Secondary Checking export"
    assert source["mapping"]["date_col"] == "Date"

    store.save_source("sig1", "Renamed", "Secondary Checking",
                       {"date_col": "Date", "desc_col": "Details", "amount_col": "Amount"}, "%Y-%m-%d")
    assert store.get_source("sig1")["label"] == "Renamed"
    assert len(store.list_sources()) == 1
    store.close()


def test_insert_transactions_dedupes_by_fingerprint(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    row = dict(fingerprint="fp1", account="Secondary Checking", txn_date="2026-08-01",
               description="EXAMPLE MARKET", amount=-41.2, category=None, note=None,
               status="new", transfer_peer=None, batch_id=None, imported_at="2026-08-30T00:00:00")
    assert store.insert_transactions([row]) == 1
    assert store.insert_transactions([row]) == 0
    assert store.known_fingerprints(["fp1", "fp2"]) == {"fp1"}
    assert len(store.list_transactions()) == 1
    store.close()


def test_update_and_bulk_update_transaction(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    row = dict(fingerprint="fp1", account="Secondary Checking", txn_date="2026-08-01",
               description="EXAMPLE MARKET", amount=-41.2, category=None, note=None,
               status="new", transfer_peer=None, batch_id=None, imported_at="2026-08-30T00:00:00")
    row2 = dict(row, fingerprint="fp2", description="CORNER STORE")
    store.insert_transactions([row, row2])

    store.update_transaction("fp1", category="Food", note="Groceries", status="categorized")
    tx = store.get_transaction("fp1")
    assert tx["category"] == "Food"
    assert tx["status"] == "categorized"

    store.bulk_update(["fp1", "fp2"], category="Food", status="categorized")
    assert all(t["category"] == "Food" for t in store.list_transactions(status="categorized"))
    store.close()


def test_list_transactions_filters_by_status_and_account(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    rows = [
        dict(fingerprint="fp1", account="A", txn_date="2026-08-01", description="x", amount=-1,
             category=None, note=None, status="new", transfer_peer=None, batch_id=None,
             imported_at="t"),
        dict(fingerprint="fp2", account="B", txn_date="2026-08-02", description="y", amount=-2,
             category=None, note=None, status="posted", transfer_peer=None, batch_id=None,
             imported_at="t"),
    ]
    store.insert_transactions(rows)
    assert [t["fingerprint"] for t in store.list_transactions(status="new")] == ["fp1"]
    assert [t["fingerprint"] for t in store.list_transactions(account="B")] == ["fp2"]
    assert [t["fingerprint"] for t in store.list_transactions(status=["new", "posted"])] == ["fp1", "fp2"]
    store.close()


def test_rules_upsert_touch_delete(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    store.upsert_rule("UBER EATS", "contains", "Food", "Food")
    rules = store.list_rules()
    assert len(rules) == 1
    assert rules[0]["hits"] == 0

    store.touch_rule(rules[0]["id"], "2026-08-30T00:00:00")
    assert store.list_rules()[0]["hits"] == 1

    store.upsert_rule("UBER EATS", "contains", "Entertainment", "Food")
    rules = store.list_rules()
    assert len(rules) == 1
    assert rules[0]["category"] == "Entertainment"

    store.delete_rule(rules[0]["id"])
    assert store.list_rules() == []
    store.close()


def test_batches_recorded_and_listed(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    store.record_batch("b1", "2026-08-30", "2026-08-30T12:00:00", 5583, 5586,
                        "data/backups/x.xlsx", 4)
    batches = store.list_batches()
    assert len(batches) == 1
    assert batches[0]["line_count"] == 4
    store.close()


def test_delete_new_transactions_removes_only_new_status(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    rows = [
        dict(fingerprint="fp1", account="A", txn_date="2026-08-01", description="x", amount=-1,
             category=None, note=None, status="new", transfer_peer=None, batch_id=None, imported_at="t"),
        dict(fingerprint="fp2", account="A", txn_date="2026-08-02", description="y", amount=-2,
             category=None, note=None, status="new", transfer_peer=None, batch_id=None, imported_at="t"),
    ]
    store.insert_transactions(rows)
    store.update_transaction("fp2", status="categorized")

    deleted = store.delete_new_transactions(["fp1", "fp2"])

    assert deleted == 1
    assert store.get_transaction("fp1") is None
    assert store.get_transaction("fp2") is not None
    store.close()


@pytest.mark.parametrize("status", ["categorized", "ignored", "transfer", "posted"])
def test_delete_new_transactions_protects_reviewed_status(tmp_path, status):
    store = Store(tmp_path / "journal.sqlite")
    store.insert_transactions([
        dict(fingerprint="fp1", account="A", txn_date="2026-08-01", description="x", amount=-1,
             category=None, note=None, status="new", transfer_peer=None, batch_id=None, imported_at="t"),
    ])
    store.update_transaction("fp1", status=status)

    deleted = store.delete_new_transactions(["fp1"])

    assert deleted == 0
    assert store.get_transaction("fp1") is not None
    store.close()


def test_delete_new_transactions_empty_list_is_noop(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    assert store.delete_new_transactions([]) == 0
    store.close()


def test_delete_new_transactions_ignores_unknown_fingerprints(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    assert store.delete_new_transactions(["nope"]) == 0
    store.close()


def test_get_batch_returns_recorded_batch(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    store.record_batch("b1", "2026-08-30", "2026-08-30T12:00:00", 6, 7, "backups/x.xlsx", 2)

    batch = store.get_batch("b1")

    assert batch["batch_id"] == "b1"
    assert batch["first_row"] == 6
    assert batch["last_row"] == 7
    store.close()


def test_get_batch_returns_none_when_missing(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    assert store.get_batch("nope") is None
    store.close()
