from __future__ import annotations

from datetime import date

import pytest

from journal_entry.app import JournalApp
from journal_entry.xlsx_append import JournalLine, append_journal_rows

from .journal_fixture import build_fixture_workbook

_ACCOUNTS = [
    "Cash",
    "Rewards Card",
    "Investment Account",
    "Transit Card",
    "Investment Income",
    "Transit",
]


@pytest.fixture
def app(tmp_path):
    workbook = tmp_path / "Personal Budget.xlsx"
    build_fixture_workbook(
        workbook, _ACCOUNTS, {"Investment Account": 1000, "Transit Card": 50}
    )
    instance = JournalApp.create(workbook, tmp_path / "journal.sqlite")
    yield instance
    instance.close()


def _queue(
    app: JournalApp,
    fingerprint: str,
    *,
    account: str,
    txn_date: str,
    amount: float,
    category: str | None,
    status: str = "categorized",
) -> None:
    app.store.insert_transactions([
        {
            "fingerprint": fingerprint,
            "account": account,
            "txn_date": txn_date,
            "description": fingerprint,
            "amount": amount,
            "category": category,
            "note": "",
            "status": status,
            "transfer_peer": None,
            "batch_id": None,
            "imported_at": "2026-08-30T12:00:00",
        }
    ])


def _selection(investment=None, transit=None):
    return {
        "investment": {
            "enabled": investment is not None,
            "ending_balance": investment,
        },
        "transit": {
            "enabled": transit is not None,
            "ending_balance": transit,
        },
    }


def _portfolio(total=1200, as_of="2026-08-31"):
    return {
        "total_value_cad": total,
        "as_of_date": as_of,
        "generated_at": "2026-08-31T18:00:00-04:00",
    }


def test_preview_reconciles_posted_and_pending_activity(app):
    for line in (
        JournalLine(date(2026, 7, 31), "Investment Account", "Cash", [100], "Contribution"),
        JournalLine(date(2026, 8, 10), "Transit Card", "Cash", [20], "Load"),
        JournalLine(date(2026, 9, 1), "Transit Card", "Cash", [999], "Later load"),
    ):
        append_journal_rows(app.workbook_path, [line])
    # A categorized cash outflow whose category is an asset becomes a debit
    # to that asset in the eventual Post line.
    _queue(
        app, "portfolio-contribution", account="Cash", txn_date="2026-08-20",
        amount=-50, category="Investment Account",
    )
    _queue(
        app, "transit-load", account="Rewards Card", txn_date="2026-08-21",
        amount=-20, category="Transit Card",
    )
    # Confirmed transfers are included too.
    _queue(
        app, "transfer-out", account="Cash", txn_date="2026-08-22",
        amount=-30, category=None, status="new",
    )
    _queue(
        app, "transfer-in", account="Investment Account", txn_date="2026-08-22",
        amount=30, category=None, status="new",
    )
    app.confirm_transfer("transfer-out", "transfer-in", "Contribution")

    result = app.month_close_preview(
        "2026-08", _portfolio(), _selection(investment=1200, transit=40)
    )

    investment = result["reconciliations"]["investment"]
    assert investment["workbook_balance"] == pytest.approx(1100)
    assert investment["pending_effect"] == pytest.approx(80)
    assert investment["projected_balance"] == pytest.approx(1180)
    assert investment["adjustment"] == pytest.approx(20)
    assert (investment["debit"], investment["credit"]) == (
        "Investment Account", "Investment Income"
    )

    transit = result["reconciliations"]["transit"]
    assert transit["workbook_balance"] == pytest.approx(70)
    assert transit["pending_effect"] == pytest.approx(20)
    assert transit["projected_balance"] == pytest.approx(90)
    assert transit["adjustment"] == pytest.approx(-50)
    assert (transit["debit"], transit["credit"]) == ("Transit", "Transit Card")
    assert result["blockers"] == []


def test_preview_reverses_negative_income_and_positive_transit_adjustments(app):
    result = app.month_close_preview(
        "2026-08", _portfolio(900), _selection(investment=900, transit=75)
    )

    investment = result["reconciliations"]["investment"]
    assert investment["adjustment"] == -100
    assert (investment["debit"], investment["credit"]) == (
        "Investment Income", "Investment Account"
    )
    transit = result["reconciliations"]["transit"]
    assert transit["adjustment"] == 25
    assert (transit["debit"], transit["credit"]) == ("Transit Card", "Transit")


def test_configured_accounts_resolve_case_insensitively(tmp_path):
    workbook = tmp_path / "Personal Budget.xlsx"
    accounts = [
        "Cash", "Investment Account", "Transit Card", "Investment Income", "Transit"
    ]
    build_fixture_workbook(workbook, accounts, {"Investment Account": 1000, "Transit Card": 25})
    app = JournalApp.create(workbook, tmp_path / "journal.sqlite")
    try:
        result = app.month_close_preview(
            "2026-08", _portfolio(), _selection(investment=1200, transit=10)
        )
    finally:
        app.close()

    assert result["reconciliations"]["investment"]["account"] == "Investment Account"
    assert result["reconciliations"]["investment"]["adjustment"] == 200
    assert result["reconciliations"]["transit"]["adjustment"] == -15


def test_cache_default_is_editable_and_date_mismatch_is_only_a_warning(app):
    result = app.month_close_preview("2026-08", _portfolio(1234.555, "2026-08-30"))

    investment = result["reconciliations"]["investment"]
    assert investment["enabled"] is True
    assert investment["ending_balance"] == pytest.approx(1234.56)
    assert result["can_stage"] is True
    assert "not 2026-08-31" in result["warnings"][0]

    missing = app.month_close_preview("2026-08", None)
    assert missing["reconciliations"]["investment"]["enabled"] is False
    assert "No cached portfolio" in missing["warnings"][0]


def test_unconfirmed_and_future_postable_rows_block_staging(app):
    _queue(
        app, "unreviewed", account="Cash", txn_date="2026-08-20",
        amount=-10, category=None, status="new",
    )
    preview = app.month_close_preview(
        "2026-08", _portfolio(), _selection(investment=1200)
    )
    assert "unconfirmed" in preview["blockers"][0]
    with pytest.raises(ValueError, match="unconfirmed"):
        app.stage_month_close(
            "2026-08", _portfolio(), _selection(investment=1200)
        )

    app.set_status(["unreviewed"], "ignored")
    _queue(
        app, "future", account="Cash", txn_date="2026-09-01",
        amount=-10, category="Investment Account",
    )
    preview = app.month_close_preview(
        "2026-08", _portfolio(), _selection(investment=1200)
    )
    assert "after 2026-08-31" in preview["blockers"][0]


def test_stage_replaces_active_rows_and_preserves_posted_corrections(app):
    first = app.stage_month_close(
        "2026-08", _portfolio(), _selection(investment=1200, transit=30)
    )
    assert first["staged_count"] == 2
    assert len(app.store.list_month_close_transactions(statuses=["categorized"])) == 2

    second = app.stage_month_close(
        "2026-08", _portfolio(1250), _selection(investment=1250)
    )
    assert second["staged_count"] == 1
    active = app.store.list_month_close_transactions(statuses=["categorized"])
    assert len(active) == 1
    assert active[0]["close_kind"] == "investment"
    assert active[0]["amount"] == pytest.approx(250)

    with pytest.raises(ValueError, match="2026-08-31"):
        app.post_preview(date(2026, 8, 30))

    result = app.post(date(2026, 8, 31))
    assert result["line_count"] == 1
    posted = app.store.list_month_close_transactions(statuses=["posted"])
    assert len(posted) == 1

    reconciled = app.month_close_preview(
        "2026-08", _portfolio(1250), _selection(investment=1250)
    )
    assert reconciled["reconciliations"]["investment"]["adjustment"] == 0

    correction = app.stage_month_close(
        "2026-08", _portfolio(1260), _selection(investment=1260)
    )
    assert correction["staged_count"] == 1
    assert len(app.store.list_month_close_transactions(statuses=["posted"])) == 1
    active = app.store.list_month_close_transactions(statuses=["categorized"])
    assert active[0]["amount"] == pytest.approx(10)


def test_optional_zero_adjustment_removes_an_active_close_row(app):
    app.stage_month_close(
        "2026-08", _portfolio(), _selection(investment=1200, transit=40)
    )
    assert len(app.store.list_month_close_transactions(statuses=["categorized"])) == 2

    result = app.stage_month_close(
        "2026-08", _portfolio(1000), _selection(investment=1000)
    )

    assert result["staged_count"] == 0
    assert app.store.list_month_close_transactions(statuses=["categorized"]) == []


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_ending_balances_are_rejected(app, value):
    with pytest.raises(ValueError):
        app.month_close_preview(
            "2026-08", _portfolio(), _selection(investment=value)
        )


@pytest.mark.parametrize("month", ["2026", "2026-13", "August 2026", ""])
def test_invalid_months_are_rejected(app, month):
    with pytest.raises(ValueError, match="YYYY-MM"):
        app.month_close_preview(month, _portfolio())
