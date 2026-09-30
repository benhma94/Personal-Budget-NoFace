"""The distributable example workbook must work with the app's XML writers."""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from budget_dashboard.workbook import build_dashboard_data
from finance_hub.budget_plan_api import read_budget_plan, write_budget_plan
from journal_entry.xlsx_append import JournalLine, append_journal_rows


TEMPLATE = Path(__file__).resolve().parents[1] / "examples/Personal Budget Template.xlsx"


def test_public_template_can_be_read_and_extended(tmp_path):
    workbook = tmp_path / "Personal Budget.xlsx"
    shutil.copy2(TEMPLATE, workbook)

    report, transactions = build_dashboard_data(workbook)
    assert report["periods"] == ["2026-01"]
    assert len(transactions) == 5
    assert read_budget_plan(workbook, 2026)["cells"]["Salary"]["2026-01"]["saved"] == 3000

    result = append_journal_rows(workbook, [
        JournalLine(date(2026, 1, 10), "Food", "Primary Checking", [25], "Example snack"),
    ])
    assert result.first_row == 8
    assert len(build_dashboard_data(workbook)[1]) == 6

    write_budget_plan(workbook, [("Food", "2026-01", -225), ("Food", "2026-02", -230)])
    plan = read_budget_plan(workbook, 2026)
    assert plan["cells"]["Food"]["2026-01"]["saved"] == -225
    assert plan["cells"]["Food"]["2026-02"]["saved"] == -230
