from __future__ import annotations

import zipfile
import re
from datetime import date as _date
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from finance_hub.budget_plan_api import read_budget_plan, write_budget_plan
from journal_entry.xlsx_append import VerificationError, WorkbookLockedError

from .budget_plan_fixture import build_budget_plan_fixture


def _workbook(path):
    """Two years of Salary/Utilities/Food budget history plus a mismatched
    January actual, so the trend math has something real to compute."""
    wb = Workbook()
    journal = wb.active
    journal.title = "Journal"
    journal.append(["Journal"])
    journal.append(["Date", "Debit", "Credit", "Amount", "Note"])
    # 2025: budgeted 900 salary/month, actual 950 -- consistent +50 trend.
    journal.append([datetime(2025, 1, 5), "Primary Checking", "Salary", 950, "Pay"])
    journal.append([datetime(2025, 1, 8), "Food", "Primary Checking", 240, "Groceries"])
    journal.append([datetime(2025, 2, 5), "Primary Checking", "Salary", 950, "Pay"])
    journal.append([datetime(2025, 2, 8), "Food", "Primary Checking", 240, "Groceries"])
    # 2026: same pattern continues, latest actual is Feb 2026.
    journal.append([datetime(2026, 1, 5), "Primary Checking", "Salary", 950, "Pay"])
    journal.append([datetime(2026, 1, 8), "Food", "Primary Checking", 240, "Groceries"])
    journal.append([datetime(2026, 2, 5), "Primary Checking", "Salary", 950, "Pay"])
    journal.append([datetime(2026, 2, 8), "Food", "Primary Checking", 240, "Groceries"])

    budget = wb.create_sheet("Budget")
    budget.cell(13, 2, datetime(2025, 1, 31))
    budget.cell(13, 3, datetime(2025, 2, 28))
    budget.cell(13, 4, datetime(2026, 1, 31))
    budget.cell(13, 5, datetime(2026, 2, 28))
    labels = {16: "Salary", 28: "Food", 27: "Utilities"}
    for row, label in labels.items():
        budget.cell(row, 1, label)
    # Salary: budgeted 900 every month for both years -> trend = 950-900 = +50.
    for col in (2, 3, 4, 5):
        budget.cell(16, col, 900)
    # Food: budgeted 2025 only; 2026 columns left blank (None) on purpose.
    budget.cell(28, 2, -250)
    budget.cell(28, 3, -250)
    budget.cell(13, 6, datetime(2025, 12, 31))
    budget.cell(28, 6, -250)  # Food budgeted -250 in Dec 2025 -- inside the trailing window ending 2026-02
    # Utilities: no budget entries at all, ever -- exercises the "no
    # suggestion possible" path.

    ledger = wb.create_sheet("Ledger")
    ledger.append(["Ledger"])
    ledger.append(["Account", "Beginning Balance"])
    ledger.append(["Primary Checking", 100])
    wb.save(path)


def test_saved_is_raw_signed_value_or_none(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)

    plan = read_budget_plan(path, 2026)

    assert plan["cells"]["Salary"]["2026-01"]["saved"] == 900.0
    assert plan["cells"]["Food"]["2026-01"]["saved"] is None  # blank cell, not 0
    assert plan["cells"]["Utilities"]["2026-01"]["saved"] is None


def test_suggestion_uses_prior_year_budget_plus_trailing_trend(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)

    plan = read_budget_plan(path, 2026)

    # prior_year_budget for Salary Jan 2026 = Jan 2025's 900; trend over the
    # trailing 12 months ending at the latest actual (2026-02) = +50 every
    # month with a budget entry -> suggested = 900 + 50 = 950.
    salary_jan = plan["cells"]["Salary"]["2026-01"]
    assert salary_jan["prior_year_budget"] == 900.0
    assert salary_jan["prior_year_actual"] == 950.0
    assert salary_jan["suggested"] == pytest.approx(950.0)


def test_suggestion_falls_back_to_trailing_average_when_no_prior_year_budget(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)

    plan = read_budget_plan(path, 2026)

    # Food has no budget entry in 2026 at all, and prior_year_budget for
    # March 2026 (-> March 2025) was never entered either, so there's no
    # prior-year value to add a trend to. But the fixture does have one
    # Food budget entry inside the real trailing-12-month window ending at
    # the latest actual (2026-02): Dec 2025 at -250. That's the only
    # within-window entry, so the fallback average is exactly -250.0.
    food_march = plan["cells"]["Food"]["2026-03"]
    assert food_march["prior_year_budget"] is None
    assert food_march["suggested"] == pytest.approx(-250.0)


def test_suggestion_is_none_with_no_budget_history_at_all(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)

    plan = read_budget_plan(path, 2026)

    utilities_jan = plan["cells"]["Utilities"]["2026-01"]
    assert utilities_jan["saved"] is None
    assert utilities_jan["suggested"] is None
    assert utilities_jan["prior_year_budget"] is None


def test_planning_a_year_past_the_sheets_end_returns_all_blanks_with_suggestions(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)

    plan = read_budget_plan(path, 2027)

    assert plan["cells"]["Salary"]["2027-01"]["saved"] is None
    assert plan["months"] == [f"2027-{m:02d}" for m in range(1, 13)]


def test_payload_shape_and_latest_saved_month(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)

    plan = read_budget_plan(path, 2026)

    assert plan["workbook"] == "Personal Budget.xlsx"
    assert plan["year"] == 2026
    assert plan["income_categories"][0] == "Salary"
    assert "Rent" in plan["expense_categories"]
    assert plan["latest_saved_month"] == "2026-02"


def test_missing_budget_sheet_raises_value_error(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    wb = Workbook()
    journal = wb.active
    journal.title = "Journal"
    journal.append(["Date", "Debit", "Credit", "Amount", "Note"])
    journal.append([datetime(2026, 1, 5), "A", "B", 5, ""])
    wb.save(path)

    with pytest.raises(ValueError, match="Budget"):
        read_budget_plan(path, 2026)


def test_missing_workbook_raises_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_budget_plan(tmp_path / "nope.xlsx", 2026)


def test_read_raises_on_duplicate_category_label_like_the_write_path_does(tmp_path):
    """write_budget_plan (via _read_grid_layout) already refuses to touch a
    Budget sheet with a duplicate category label. The read path silently
    picked the last-seen row instead, so a malformed sheet looked fine to
    load but every save against it failed -- read should fail the same way
    the write does instead of appearing to work."""
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)
    wb = load_workbook(path)
    wb["Budget"].cell(30, 1, "Salary")  # duplicates the existing row-16 label
    wb.save(path)

    with pytest.raises(ValueError, match="duplicate category"):
        read_budget_plan(path, 2026)


def test_read_raises_on_duplicate_month_header_like_the_write_path_does(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)
    wb = load_workbook(path)
    wb["Budget"].cell(13, 7, datetime(2026, 1, 31))  # duplicates column D's 2026-01
    wb.save(path)

    with pytest.raises(ValueError, match="duplicate month header"):
        read_budget_plan(path, 2026)


@pytest.mark.parametrize("year", [1899, 10000, True, "2026"])
def test_read_rejects_year_outside_excel_datetime_range(tmp_path, year):
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)
    with pytest.raises(ValueError, match="year"):
        read_budget_plan(path, year)


def test_budget_plan_source_cache_reuses_parsed_journal_until_invalidated(tmp_path, monkeypatch):
    """read_budget_plan re-parses the whole Journal sheet on every call
    (expensive on the real multi-year workbook), even though the sibling
    Report tab already avoids this via BudgetPayloadCache. An optional
    cache should let repeated reads (e.g. every GET while the tab is open)
    skip the reparse until the workbook actually changes."""
    path = tmp_path / "Personal Budget.xlsx"
    _workbook(path)

    import finance_hub.budget_plan_api as bp_api

    calls = []
    real_read_journal = bp_api._read_journal

    def spy(*args, **kwargs):
        calls.append(1)
        return real_read_journal(*args, **kwargs)

    monkeypatch.setattr(bp_api, "_read_journal", spy)

    cache = bp_api.BudgetPlanSourceCache(path)
    first = read_budget_plan(path, 2026, cache=cache)
    second = read_budget_plan(path, 2026, cache=cache)
    assert len(calls) == 1
    assert first == second

    cache.invalidate()
    read_budget_plan(path, 2026, cache=cache)
    assert len(calls) == 2

    uncached = read_budget_plan(path, 2026)
    assert uncached == first


def _write_fixture(path):
    build_budget_plan_fixture(
        path,
        month_end_dates=[_date(2026, 1, 31), _date(2026, 2, 28)],
        category_rows={16: "Salary", 22: "Other Income", 27: "Utilities", 28: "Food", 47: "Other"},
        values={(16, 0): 900, (16, 1): 900, (27, 0): -60, (27, 1): -60, (28, 0): -250},
    )


def test_patches_an_existing_cell_and_leaves_other_cells_unchanged(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)

    write_budget_plan(path, [("Food", "2026-02", -275.0)])

    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    budget = wb["Budget"]
    row28 = list(budget.iter_rows(min_row=28, max_row=28, values_only=True))[0]
    row16 = list(budget.iter_rows(min_row=16, max_row=16, values_only=True))[0]
    wb.close()
    assert row28[2] == -275.0
    assert row16[1:3] == (900, 900)


def test_patches_a_blank_cell_that_had_no_value(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)

    write_budget_plan(path, [("Food", "2026-02", -260.0)])

    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    row28 = list(wb["Budget"].iter_rows(min_row=28, max_row=28, values_only=True))[0]
    wb.close()
    assert row28[2] == -260.0


def test_appends_new_month_column_with_existing_styles_and_blank_rows(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)

    write_budget_plan(path, [("Salary", "2026-03", 950.0)])

    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    budget = wb["Budget"]
    header = list(budget.iter_rows(min_row=13, max_row=13, values_only=True))[0]
    row16 = list(budget.iter_rows(min_row=16, max_row=16, values_only=True))[0]
    row27 = list(budget.iter_rows(min_row=27, max_row=27, values_only=True))[0]
    wb.close()
    assert header[3] == datetime(2026, 3, 31)
    assert row16[3] == 950.0
    assert row27[3] is None

    with zipfile.ZipFile(path) as zf:
        xml = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert '<c r="D16" s="137"><v>950</v></c>' in xml
    assert '<c r="D27" s="137"/>' in xml


def test_reuses_preformatted_blank_column_instead_of_creating_duplicate_cells(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)
    # Real Excel workbooks often contain the next formatted, blank column
    # immediately after the last dated month. Add that shape to the fixture.
    staged = path.with_name("preformatted.xlsx")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet2.xml":
                xml = data.decode("utf-8")
                for row_number, style in [(13, "4"), *[(row, "137") for row in range(16, 48)]]:
                    xml = re.sub(
                        rf'(<row r="{row_number}"[^>]*>.*?)(</row>)',
                        rf'\1<c r="D{row_number}" s="{style}"/>\2',
                        xml,
                        count=1,
                        flags=re.S,
                    )
                xml = xml.replace('ref="A1:C47"', 'ref="A1:D47"')
                data = xml.encode("utf-8")
            target.writestr(item, data)
    staged.replace(path)

    write_budget_plan(path, [("Salary", "2026-03", 950.0)])

    with zipfile.ZipFile(path) as zf:
        xml = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert xml.count('<c r="D13"') == 1
    assert xml.count('<c r="D16"') == 1
    assert '<c r="D16" s="137"><v>950</v></c>' in xml


def test_new_year_starts_after_annual_total_and_spacer_columns(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    build_budget_plan_fixture(
        path,
        month_end_dates=[
            _date(2026, month, 31 if month in (1, 3, 5, 7, 8, 10, 12) else 30)
            if month != 2 else _date(2026, 2, 28)
            for month in range(1, 13)
        ],
        category_rows={16: "Salary", 28: "Food"},
        values={(16, month): 900 for month in range(12)},
    )

    staged = path.with_name("with-annual-total.xlsx")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet2.xml":
                xml = data.decode("utf-8")
                xml = re.sub(
                    r'(<row r="16"[^>]*>.*?)(</row>)',
                    r'\1<c r="N16" s="137"><f>SUM(B16:M16)</f><v>10800</v></c>\2',
                    xml,
                    count=1,
                    flags=re.S,
                )
                xml = xml.replace('ref="A1:M47"', 'ref="A1:N47"')
                data = xml.encode("utf-8")
            target.writestr(item, data)
    staged.replace(path)

    write_budget_plan(path, [("Salary", "2027-01", 950.0)])

    wb = load_workbook(path, read_only=True, data_only=False)
    budget = wb["Budget"]
    assert budget["N16"].value == "=SUM(B16:M16)"
    assert budget["O13"].value is None
    assert budget["P13"].value == datetime(2027, 1, 31)
    assert budget["P16"].value == 950.0
    wb.close()

    plan = read_budget_plan(path, 2027)
    assert plan["cells"]["Salary"]["2027-01"]["saved"] == 950.0


def test_gap_between_sheet_end_and_requested_month_fills_intermediate_headers(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)

    write_budget_plan(path, [("Salary", "2026-05", 950.0)])

    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    header = list(wb["Budget"].iter_rows(min_row=13, max_row=13, values_only=True))[0]
    wb.close()
    assert header[1:6] == (
        datetime(2026, 1, 31), datetime(2026, 2, 28), datetime(2026, 3, 31),
        datetime(2026, 4, 30), datetime(2026, 5, 31),
    )


def test_backup_created_before_write(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)

    backup_path = write_budget_plan(path, [("Food", "2026-01", -300.0)])

    assert backup_path.is_file()
    from openpyxl import load_workbook
    wb = load_workbook(backup_path, read_only=True, data_only=True)
    row28 = list(wb["Budget"].iter_rows(min_row=28, max_row=28, values_only=True))[0]
    wb.close()
    assert row28[1] == -250


def test_locked_workbook_refused_without_touching_workbook(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)
    before = path.read_bytes()
    lock_file = path.with_name(f"~${path.name}")
    lock_file.write_text("lock")

    with pytest.raises(WorkbookLockedError):
        write_budget_plan(path, [("Food", "2026-01", -300.0)])
    assert path.read_bytes() == before


def test_invalid_updates_rejected_without_touching_workbook(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)
    before = path.read_bytes()

    for updates, message in [
        ([("Not A Real Category", "2026-01", 5.0)], "unknown budget categories"),
        ([("Food", "2026-00", 5.0)], "invalid month"),
        ([("Food", "2026-01", float("nan"))], "non-finite"),
        ([("Food", "2026-01", 1.0), ("Food", "2026-01", 2.0)], "duplicate"),
    ]:
        with pytest.raises(ValueError, match=message):
            write_budget_plan(path, updates)
        assert path.read_bytes() == before


def test_empty_updates_rejected(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)
    with pytest.raises(ValueError):
        write_budget_plan(path, [])


def test_other_sheets_untouched_byte_for_byte(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)
    with zipfile.ZipFile(path) as zf:
        before_journal = zf.read("xl/worksheets/sheet1.xml")
        before_ledger = zf.read("xl/worksheets/sheet3.xml")

    write_budget_plan(path, [("Food", "2026-01", -300.0)])

    with zipfile.ZipFile(path) as zf:
        assert zf.read("xl/worksheets/sheet1.xml") == before_journal
        assert zf.read("xl/worksheets/sheet3.xml") == before_ledger


def test_calc_chain_dropped_and_full_calc_on_load_set(tmp_path):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)

    write_budget_plan(path, [("Food", "2026-01", -300.0)])

    with zipfile.ZipFile(path) as zf:
        assert "xl/calcChain.xml" not in zf.namelist()
        assert 'fullCalcOnLoad="1"' in zf.read("xl/workbook.xml").decode("utf-8")


def test_verification_failure_restores_from_backup(tmp_path, monkeypatch):
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)
    before = path.read_bytes()

    import finance_hub.budget_plan_api as bp_api
    monkeypatch.setattr(
        bp_api,
        "_verify_budget_updates",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("forced failure")),
    )

    with pytest.raises(VerificationError):
        write_budget_plan(path, [("Food", "2026-01", -300.0)])
    assert path.read_bytes() == before


def test_rollback_restores_workbook_via_atomic_replace_not_in_place_copy(tmp_path, monkeypatch):
    """A reader without the write lock (e.g. an unlocked GET) must never be
    able to observe a torn file mid-restore, so rollback must swap the
    backup into place atomically rather than overwrite it byte-by-byte."""
    path = tmp_path / "Personal Budget.xlsx"
    _write_fixture(path)
    before = path.read_bytes()

    import finance_hub.budget_plan_api as bp_api
    monkeypatch.setattr(
        bp_api,
        "_verify_budget_updates",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("forced failure")),
    )

    copy2_destinations = []
    real_copy2 = bp_api.shutil.copy2

    def spy_copy2(src, dst, *a, **k):
        copy2_destinations.append(Path(dst))
        return real_copy2(src, dst, *a, **k)

    monkeypatch.setattr(bp_api.shutil, "copy2", spy_copy2)

    with pytest.raises(VerificationError):
        write_budget_plan(path, [("Food", "2026-01", -300.0)])

    assert path.read_bytes() == before
    # The live workbook path must only ever be reached via an atomic
    # rename/replace during restore, never as a shutil.copy2 destination
    # (the backup itself may still be created with copy2).
    assert path not in copy2_destinations
